# -*- coding: utf-8 -*-
"""注册验证码邮件。

没配 SMTP 就 `configured() == False`，注册自动退化成「邮箱域名白名单」模式
（照样要邮箱，只是不验证邮箱是不是你的）。配了就可以发验证码。

SMTP 配置放 app/data/smtp.json：
    {"host": "smtp.exmail.qq.com", "port": 465, "user": "bot@xxx.com",
     "password": "授权码", "from": "bot@xxx.com", "tls": true}

验证码只放内存，10 分钟过期，错 5 次作废——进程重启就得重新获取，够用。
"""
import json
import random
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from pathlib import Path

CFG_PATH = Path(__file__).parent / "data" / "smtp.json"
TTL = 600
MAX_TRIES = 5

_LOCK = threading.Lock()
_CODES = {}          # email -> (code, expire_ts, tries)


def load_cfg():
    try:
        if CFG_PATH.exists():
            d = json.loads(CFG_PATH.read_text(encoding="utf-8"))
            if d.get("host") and d.get("user") and d.get("password"):
                return d
    except Exception:
        pass
    return None


def configured():
    return load_cfg() is not None


def save_cfg(d):
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CFG_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _send(to, code, cfg):
    msg = EmailMessage()
    msg["Subject"] = "【智能问答助手】注册验证码"
    msg["From"] = cfg.get("from") or cfg["user"]
    msg["To"] = to
    msg.set_content(
        f"你的注册验证码是：{code}\n\n10 分钟内有效，仅限本人使用。\n"
        "如果这不是你本人操作，忽略这封邮件即可。")
    host = cfg["host"]
    port = int(cfg.get("port") or 465)
    try:
        if cfg.get("tls", True):
            with smtplib.SMTP_SSL(host, port, timeout=15,
                                  context=ssl.create_default_context()) as s:
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(cfg["user"], cfg["password"])
                s.send_message(msg)
        return True, ""
    except Exception as e:
        return False, f"发信失败：{e}"


def issue(email):
    """生成验证码并寄出。返回 (ok, msg, code)。"""
    e = (email or "").strip().lower()
    cfg = load_cfg()
    if not cfg:
        return False, "没有配置发件服务器", ""
    code = f"{random.randint(0, 999999):06d}"
    ok, msg = _send(e, code, cfg)
    if not ok:
        return False, msg, ""
    with _LOCK:
        _CODES[e] = (code, time.time() + TTL, 0)
    return True, "", code


def verify(email, code):
    """校验验证码。返回 (ok, msg)。"""
    e = (email or "").strip().lower()
    with _LOCK:
        item = _CODES.get(e)
        if not item:
            return False, "请先获取验证码"
        c, exp, tries = item
        if time.time() > exp:
            _CODES.pop(e, None)
            return False, "验证码已过期，重新获取"
        if tries >= MAX_TRIES:
            _CODES.pop(e, None)
            return False, "错误次数太多，重新获取"
        if str(code or "").strip() != c:
            _CODES[e] = (c, exp, tries + 1)
            return False, "验证码不对"
        _CODES.pop(e, None)
        return True, ""
