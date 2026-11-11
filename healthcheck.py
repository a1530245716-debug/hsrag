# -*- coding: utf-8 -*-
"""健康检查: 服务进程 / 接口 / 外网入口 / 磁盘空间, 异常时告警。

检查项:
  1. 本机端口是否在监听(进程挂了)
  2. /api/health 是否 200(应用层异常)
  3. 外网地址是否可达(Tailscale Funnel 挂了)  ← 客户能看到但服务内部无感
  4. 磁盘剩余空间(备份和索引会持续增长)
  5. 最近一次备份是否过期(超过 N 天没备份)

告警方式(可组合, 配了哪个用哪个):
  - 写日志 health.log(总是)
  - Windows 事件日志(默认开, 可在任务计划器/事件查看器里看到)
  - 企业微信/钉钉/飞书 webhook(在 config.json 配 health.webhook, 或环境变量 RAG_ALERT_WEBHOOK)
  - 写入 data/health_status.json(供页面显示)

用法:
    python healthcheck.py            执行一次检查
    python healthcheck.py --quiet     只在异常时输出
    python healthcheck.py --install   注册为 Windows 计划任务(每 5 分钟)
"""
import argparse, json, os, socket, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent      # 项目根
APP = ROOT / "app"                # 应用目录
LOG = ROOT / "health.log"
STATUS = APP / "data" / "health_status.json"
MAX_LOG_LINES = 2000


def _cfg():
    try:
        return json.loads((APP / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _data_dir():
    d = _cfg().get("data_dir", "app/data")
    p = Path(d)
    return p if p.is_absolute() else (ROOT / p if (ROOT / p).exists() else APP / p)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 各项检查 ----------------

def check_port():
    cfg = _cfg().get("server", {})
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 8000))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        return s.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "") else host, port)) == 0, \
            f"端口 {host}:{port}"
    except Exception as e:
        return False, f"端口检查异常: {e}"
    finally:
        s.close()


def check_health_api():
    cfg = _cfg().get("server", {})
    port = int(cfg.get("port", 8000))
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status == 200, f"/api/health → HTTP {r.status}"
    except Exception as e:
        return False, f"/api/health 失败: {type(e).__name__}: {e}"


def check_public():
    """外网入口(Tailscale Funnel)。未配置则跳过。"""
    url = (_cfg().get("health", {}) or {}).get("public_url") or \
        os.environ.get("RAG_PUBLIC_URL", "")
    if not url:
        return None, "未配置外网地址(跳过)"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rag-healthcheck"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status in (200, 401, 403)   # 401/403 说明服务活着(只是要登录)
            return ok, f"外网 {url} → HTTP {r.status}"
    except Exception as e:
        return False, f"外网 {url} 失败: {type(e).__name__}: {e}"


def check_disk():
    try:
        import shutil
        total, used, free = shutil.disk_usage(str(ROOT))
        gb = free / 1024 ** 3
        min_gb = float((_cfg().get("health", {}) or {}).get("min_free_gb", 5))
        return gb >= min_gb, f"磁盘剩余 {gb:.1f} GB(下限 {min_gb:.0f} GB)"
    except Exception as e:
        return None, f"磁盘检查失败: {e}"


def check_backup():
    bdir = ROOT / "backups"
    if not bdir.exists():
        return False, "从未备份过"
    files = list(bdir.glob("rag-backup-*.zip"))
    if not files:
        return False, "从未备份过"
    newest = max(p.stat().st_mtime for p in files)
    hours = (time.time() - newest) / 3600
    max_hours = float((_cfg().get("health", {}) or {}).get("backup_max_age_hours", 48))
    ts = datetime.fromtimestamp(newest).strftime("%m-%d %H:%M")
    return hours <= max_hours, f"最近备份 {ts}({hours:.0f} 小时前, 上限 {max_hours:.0f}h)"


# ---------------- 告警 ----------------

def _alert_webhook(text, webhook):
    if not webhook:
        return False
    try:
        body = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode()
        req = urllib.request.Request(webhook, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception as e:
        print(f"[health] webhook 告警失败: {e}", file=sys.stderr)
        return False


def _alert_eventlog(text):
    """写 Windows 事件日志(不需要管理员权限用 powershell 的方式较稳, 失败就忽略)。"""
    try:
        import subprocess
        safe = text.replace("'", "''")[:2000]
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Write-EventLog -LogName Application -Source 'RAG-QA' -EventId 9001 "
             f"-EntryType Error -Message '{safe}'"],
            capture_output=True, timeout=15)
        return True
    except Exception:
        return False


def _append_log(line):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 防止无限增长
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        if len(lines) > MAX_LOG_LINES:
            LOG.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _write_status(checks, bad):
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps(
            {"ts": _now(), "ok": not bad, "bad": bad,
             "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


# ---------------- 主流程 ----------------

def run(quiet=False):
    checks = [
        ("服务端口", *check_port()),
        ("健康接口", *check_health_api()),
        ("外网入口", *check_public()),
        ("磁盘空间", *check_disk()),
        ("备份时效", *check_backup()),
    ]
    bad = [c for c in checks if c[1] is False]      # None = 跳过, 不算异常
    stamp = _now()

    for name, ok, detail in checks:
        if ok is None:
            mark = "SKIP"
        elif ok:
            mark = "OK  "
        else:
            mark = "FAIL"
        if not quiet or mark == "FAIL":
            print(f"[{mark}] {stamp}  {name}: {detail}")

    if bad:
        summary = "RAG 系统告警(" + stamp + ")\n" + \
                  "\n".join(f"· {n}: {d}" for n, _, d in bad)
        print("\n" + summary, file=sys.stderr)
        _append_log(summary.replace("\n", " | "))
        _alert_eventlog(summary)
        cfg = _cfg().get("health", {}) or {}
        _alert_webhook(summary, cfg.get("webhook") or os.environ.get("RAG_ALERT_WEBHOOK", ""))
    else:
        _append_log(f"{stamp} OK 全部正常")

    _write_status(checks, [n for n, _, _ in bad])
    return 1 if bad else 0


def install_task(minutes=5):
    """注册 Windows 计划任务(需要管理员权限)。"""
    py = sys.executable
    script = Path(__file__).resolve()
    tr = f'"{py}" "{script}" --quiet'
    cmd = [
        "schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes),
        "/TN", "RAG-HealthCheck", "/TR", tr,
    ]
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", errors="ignore")
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode == 0:
        print(f"\n已注册计划任务 RAG-HealthCheck, 每 {minutes} 分钟检查一次。")
        print("查看/删除: schtasks /Query /TN RAG-HealthCheck    schtasks /Delete /TN RAG-HealthCheck /F")
    else:
        print("\n注册失败(通常需要管理员权限)。可手动创建任务, 命令:")
        print(f'  {tr}')
    return r.returncode


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--install", action="store_true", help="注册为计划任务(每5分钟)")
    ap.add_argument("--minutes", type=int, default=5)
    a = ap.parse_args()
    sys.exit(install_task(a.minutes) if a.install else run(a.quiet))
