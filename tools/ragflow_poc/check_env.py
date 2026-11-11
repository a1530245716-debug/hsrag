# -*- coding: utf-8 -*-
"""RAGFlow / 企业 RAG 部署前环境自检。

不依赖第三方库，Linux/Windows 均可运行：
  python tools/ragflow_poc/check_env.py

检查：Python、CPU、内存、磁盘、Docker/Compose、端口、环境变量、上游连通性。
"""
import ctypes
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results = []


def add(level, name, detail):
    results.append((level, name, detail))
    print(f"[{level}] {name}: {detail}", flush=True)


def total_ram_gb():
    try:
        if platform.system() == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1024 ** 3, m.ullAvailPhys / 1024 ** 3
        with open("/proc/meminfo", encoding="utf-8") as f:
            data = {line.split(":")[0]: int(line.split()[1]) for line in f if ":" in line}
        total = data.get("MemTotal", 0) / 1024 ** 2
        avail = data.get("MemAvailable", 0) / 1024 ** 2
        return total, avail
    except Exception:
        return None, None


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) != 0


def tcp_ok(url, timeout=2.0):
    try:
        u = urlparse(url)
        host = u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    print("=" * 60)
    print("RAG / FDE 部署前环境自检")
    print("=" * 60)

    v = sys.version_info
    if v >= (3, 10):
        add(PASS, "Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        add(FAIL, "Python", f"{v.major}.{v.minor}.{v.micro}，建议 3.10+")

    add(PASS, "操作系统", f"{platform.system()} {platform.release()} ({platform.machine()})")

    cpus = os.cpu_count() or 0
    add(PASS if cpus >= 8 else WARN, "CPU", f"{cpus} 核（最低 8，推荐 16+）")

    total, avail = total_ram_gb()
    if total is None:
        add(WARN, "内存", "无法读取")
    else:
        level = PASS if total >= 30 else (WARN if total >= 15 else FAIL)
        add(level, "内存", f"总 {total:.1f} GB，可用 {avail:.1f} GB（最低 16，推荐 32+）")

    try:
        du = shutil.disk_usage(str(ROOT))
        free_gb = du.free / 1024 ** 3
        add(PASS if free_gb >= 100 else (WARN if free_gb >= 50 else FAIL),
            "磁盘", f"剩余 {free_gb:.1f} GB（推荐 100GB+）")
    except Exception as e:
        add(WARN, "磁盘", f"读取失败: {e}")

    docker = shutil.which("docker")
    if not docker:
        add(FAIL, "Docker", "未安装；目标服务器需要 Docker 24+ / Compose v2")
    else:
        try:
            ver = subprocess.run([docker, "--version"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            add(PASS, "Docker", ver)
        except Exception as e:
            add(FAIL, "Docker", f"无法执行: {e}")
        try:
            cver = subprocess.run([docker, "compose", "version"], capture_output=True,
                                  text=True, timeout=10).stdout.strip()
            add(PASS, "Compose", cver)
        except Exception:
            add(WARN, "Compose", "docker compose 不可用，或仍是旧版 docker-compose")

    for name, port in (("BFF", 8000), ("RAGFlow Web/API", 9380), ("HTTPS", 443)):
        if port_free("127.0.0.1", port):
            add(WARN, "端口", f"127.0.0.1:{port} 空闲（{name}）")
        else:
            add(WARN, "端口", f"127.0.0.1:{port} 已被占用（{name}），确认是否冲突")

    envs = [
        ("RAGFLOW_BASE_URL", "RAGFlow 地址"),
        ("RAGFLOW_API_KEY", "RAGFlow API Key"),
        ("LLM_BASE_URL", "LLM 网关地址"),
        ("LLM_API_KEY", "LLM Key"),
        ("PG_HOST", "Postgres"),
        ("REDIS_HOST", "Redis"),
        ("MINIO_ENDPOINT", "MinIO"),
    ]
    for key, label in envs:
        val = os.environ.get(key, "")
        if val:
            add(PASS, f"环境变量 {key}", f"已设置（{label}）")
        else:
            add(WARN, f"环境变量 {key}", f"未设置（{label}）")

    for key in ("RAGFLOW_BASE_URL", "LLM_BASE_URL"):
        val = os.environ.get(key)
        if not val:
            continue
        ok, detail = tcp_ok(val)
        add(PASS if ok else WARN, f"连通性 {key}", detail)

    print("-" * 60)
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print(f"结果：PASS {len(results) - len(fails) - len(warns)} / WARN {len(warns)} / FAIL {len(fails)}")
    if fails:
        print("先解决 FAIL 项，再开始 PoC。")
        return 1
    if warns:
        print("WARN 项不阻塞，但上线前建议逐项确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
