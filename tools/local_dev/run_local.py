# -*- coding: utf-8 -*-
"""一键启动本地演示：假 RAG 后端 + 异步 BFF。

  python tools/local_dev/run_local.py            # 跑冒烟，然后退出
  python tools/local_dev/run_local.py --keep     # 保持运行，浏览器打开 http://127.0.0.1:8020
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "app"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) != 0


def wait_http(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def tail(path, n=20):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def stop(p):
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def smoke(bff_port):
    text = ""
    sources = []
    with httpx.stream("POST", f"http://127.0.0.1:{bff_port}/api/ask/stream",
                      json={"question": "部署需要哪些安装包？"},
                      timeout=30) as r:
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {r.read()[:200]!r}")
            return False
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if obj.get("type") == "sources":
                sources = obj.get("sources") or []
            elif obj.get("type") in ("delta", "answer"):
                text += obj.get("text") or ""
    print(f"  来源: {[s.get('document') for s in sources]}")
    print(f"  回答前 200 字: {text[:200].replace(chr(10), ' ')}")
    return bool(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rag-port", type=int, default=9390)
    ap.add_argument("--bff-port", type=int, default=8020)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    for port, name in ((args.rag_port, "mock RAG"), (args.bff_port, "BFF")):
        if not port_free("127.0.0.1", port):
            raise SystemExit(f"{name} 端口 {port} 已被占用")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["RAG_URL"] = f"http://127.0.0.1:{args.rag_port}"
    env["MAX_CONCURRENT"] = env.get("MAX_CONCURRENT", "20")

    rag_log = open(HERE / "mock_rag.log", "w", encoding="utf-8")
    bff_log = open(HERE / "bff.log", "w", encoding="utf-8")
    rag = bff = None
    try:
        rag = subprocess.Popen(
            [sys.executable, str(HERE / "mock_rag.py"),
             "--port", str(args.rag_port)],
            cwd=str(HERE), env=env, stdout=rag_log, stderr=subprocess.STDOUT)
        if not wait_http(f"http://127.0.0.1:{args.rag_port}/health"):
            raise SystemExit(f"mock RAG 启动失败:\n{tail(HERE / 'mock_rag.log')}")

        bff = subprocess.Popen(
            [sys.executable, str(HERE / "demo_bff.py"),
             "--port", str(args.bff_port)],
            cwd=str(HERE), env=env, stdout=bff_log, stderr=subprocess.STDOUT)
        if not wait_http(f"http://127.0.0.1:{args.bff_port}/api/health"):
            raise SystemExit(f"BFF 启动失败:\n{tail(HERE / 'bff.log')}")

        print(f"本地演示已启动：http://127.0.0.1:{args.bff_port}")
        print("正在冒烟测试…")
        ok = smoke(args.bff_port)
        print("冒烟结果：" + ("PASS" if ok else "FAIL"))

        if args.keep:
            print("保持运行，Ctrl+C 退出。")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(bff)
        stop(rag)
        rag_log.close()
        bff_log.close()


if __name__ == "__main__":
    main()
