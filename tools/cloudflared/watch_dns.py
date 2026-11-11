# -*- coding: utf-8 -*-
"""等 NS 生效并自动完成收尾。

用户睡下时启动, 它每 2 分钟探一次 https://rag.example.com，
一旦通了就:
  1. 记录成功信息（地址、耗时、证书状态）
  2. 检查 cloudflared 是否在跑, 不在就拉起来
  3. 写结论到 watch.log，并生成一个 READY 标记文件
  4. 退出

最多守 10 小时，之后自己退出（不无限占用）。
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "watch.log"
READY = HERE / "READY.txt"
CFG = HERE / "config.yml"
EXE = HERE / "cloudflared.exe"
DOMAIN = "rag.example.com"
PROXY = "http://127.0.0.1:7890"
MAX_HOURS = 10
INTERVAL = 120          # 秒


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


def check_http():
    """返回 (ok, 详情)。走系统代理, 因为 CF 边缘在国内一般需要。"""
    try:
        import httpx
        with httpx.Client(timeout=25, proxy=PROXY, verify=True) as c:
            r = c.get(f"https://{DOMAIN}/api/health",
                      headers={"User-Agent": "rag-watch/1.0"})
            if r.status_code == 200:
                return True, f"HTTP {r.status_code} {r.text[:120]}"
            return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def check_ns():
    """用 nslookup 看 NS 是否已切到 cloudflare。"""
    try:
        out = subprocess.run(
            ["nslookup", "-type=NS", "example.icu", "223.5.5.5"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="ignore").stdout.lower()
        return "cloudflare" in out
    except Exception:
        return False


def cloudflared_alive():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"],
                             capture_output=True, text=True, timeout=15,
                             encoding="utf-8", errors="ignore").stdout.lower()
        return "cloudflared.exe" in out
    except Exception:
        return False


def start_cloudflared():
    """以独立进程拉起隧道（带代理环境变量）。"""
    env_prefix = (
        f'set "HTTPS_PROXY={PROXY}" && set "HTTP_PROXY={PROXY}" && '
    )
    cmd = (f'cd /d "{HERE}" && {env_prefix}"{EXE}" --config "{CFG}" tunnel run')
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "/min", "cmd.exe", "/c", cmd],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True)
        return True
    except Exception as e:
        log(f"拉起 cloudflared 失败: {e}")
        return False


def main():
    start = datetime.now()
    deadline = start + timedelta(hours=MAX_HOURS)
    log(f"巡检启动，目标 https://{DOMAIN} ，最多守 {MAX_HOURS} 小时")
    log(f"隧道进程: {'在跑' if cloudflared_alive() else '没在跑'}")

    round_no = 0
    last_ns = None
    while datetime.now() < deadline:
        round_no += 1
        ns_ok = check_ns()
        if ns_ok != last_ns:
            log(f"NS 状态变化: {'已切到 Cloudflare ✅' if ns_ok else '仍是 DNSPod ⏳'}")
            last_ns = ns_ok

        ok, detail = check_http()
        if ok:
            elapsed = datetime.now() - start
            log(f"✅ 域名已可用！用时 {elapsed}")
            log(f"   详情: {detail}")
            # 收尾
            if not cloudflared_alive():
                log("发现 cloudflared 没在跑，重新拉起")
                start_cloudflared()
                time.sleep(20)
                log(f"   重启后状态: {check_http()[1]}")
            else:
                log("   cloudflared 运行正常")
            READY.write_text(
                f"域名可用: https://{DOMAIN}\n"
                f"发现时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"从启动算起: {elapsed}\n"
                f"健康检查: {detail}\n",
                encoding="utf-8")
            log(f"已写 {READY.name} ，巡检结束")
            return 0

        if round_no % 5 == 0:
            log(f"第 {round_no} 轮仍不可用（NS={'已切换' if ns_ok else '未切换'}）: {detail}")
        time.sleep(INTERVAL)

    log(f"守了 {MAX_HOURS} 小时仍不可用，退出。最后一轮: {detail}")
    return 1


if __name__ == "__main__":
    # Windows 控制台默认 GBK，直接 print emoji 会抛 UnicodeEncodeError 把巡检搞崩
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("被手动中断")
        sys.exit(2)
