# -*- coding: utf-8 -*-
"""Cloudflare Tunnel 守护进程。

背景：cloudflared 会因 DNS 解析超时（region*.v2.argotunnel.com i/o timeout）
自己退出，此时外网变成 530，没人知道。计划任务需要管理员权限，建不了，
所以用这个常驻脚本代替。

行为：
  - 每 120 秒检查一次 cloudflared 是否在跑
  - 不在 + 代理端口 7890 有监听 → 拉起来
  - 不在 + 代理没起 → 跳过（等 VPN），不刷日志
  - 拉起后等 20 秒，验证域名是否真的恢复，结果写日志

开机自启：start_cloudflared.bat 之外，把本脚本也放进启动文件夹。
"""
import subprocess
import sys
import time
import socket
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "watchdog.log"
EXE = HERE / "cloudflared.exe"
CFG = HERE / "config.yml"
PROXY = "http://127.0.0.1:7890"
INTERVAL = 120
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Windows 控制台默认 GBK，日志里别用 emoji，否则 print 抛异常把守护搞崩
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tunnel_alive():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="ignore",
            creationflags=_NO_WINDOW).stdout.lower()
        return "cloudflared.exe" in out
    except Exception:
        return False


def proxy_alive():
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("127.0.0.1", 7890))
        return True
    except Exception:
        return False
    finally:
        s.close()


def start_tunnel():
    env = {"HTTPS_PROXY": PROXY, "HTTP_PROXY": PROXY}
    try:
        import os
        e = dict(os.environ)
        e.update(env)
        subprocess.Popen(
            [str(EXE), "--config", str(CFG), "tunnel", "run"],
            cwd=str(HERE), env=e,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True)
        return True
    except Exception as ex:
        log(f"拉起失败: {ex}")
        return False


def domain_ok():
    try:
        import httpx
        with httpx.Client(timeout=25, trust_env=False) as c:
            r = c.get("https://rag.example.com/api/health")
            return r.status_code == 200
    except Exception:
        return False


def main():
    log("守护进程启动，每 %d 秒检查一次" % INTERVAL)
    was_alive = None
    down_since = None
    while True:
        alive = tunnel_alive()
        if alive != was_alive:
            log("隧道状态: " + ("在线" if alive else "已停止"))
            was_alive = alive

        if not alive:
            if down_since is None:
                down_since = datetime.now()
            if not proxy_alive():
                time.sleep(INTERVAL)
                continue
            log("隧道不在，代理正常，正在拉起")
            if start_tunnel():
                time.sleep(25)
                ok = tunnel_alive()
                log("拉起结果: " + ("成功" if ok else "失败"))
                if ok:
                    time.sleep(10)
                    log("域名可用性: " + ("是" if domain_ok() else "否"))
                    down_since = None
        else:
            down_since = None

        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("被手动中断")
