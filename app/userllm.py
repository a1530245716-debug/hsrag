# -*- coding: utf-8 -*-
"""每个用户自己的大模型 API Key。

为什么需要
----------
公司网关**每个 key 只有约 2 路并发**（实测：5 个请求里 2 个立刻返回、3 个卡 18 秒）。
所有人都挤在 config.json 里那一把 key 上，人一多就互相排队。
让每个同事用自己申请的 key，各走各的通道，就不排队了。

约定
----
- **没配就是没配**：`get_key(uid)` 返回空串，调用方回落到 `config.json` 的全局 key。
- **只存自己那份**：一个用户一条，存在 `app/data/user_llm.json`。
- **加密存储**：明文写在磁盘上，任何能读到文件的人都拿到 key 了。
  这里用 AES-GCM（密码学库）或退化为 Fernet（cryptography），都拿不到就拒绝保存——
  宁可不存，也不能明文落盘。
- **只覆盖 key**：base_url / model 沿用全局配置。让用户改这些只会配错，
  而且公司内网地址本来就统一。

文件格式
--------
    {"u_xxx": {"key_enc": "...", "saved": "2026-11-06 16:00", "tail": "abcd"}}

`tail` 是 key 末 4 位，用于界面上显示"你自己的 key（…abcd）"，方便用户确认填对了。
"""
import json
import os
import threading
import time
from pathlib import Path

APP = Path(__file__).parent
_LOCK = threading.RLock()
_NAME = "user_llm.json"

# 加密密钥单独放一个文件，和密文分开，避免一次泄露两个都有
_SECRET_NAME = ".user_llm_secret"


def _data_dir() -> Path:
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = APP / "data"
    if not d.is_absolute():
        alt = APP.parent / d
        d = alt if alt.exists() else APP / d
    return d


def _path() -> Path:
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / _NAME


# ---------------- 加密 ----------------
# 优先 AES-GCM（cryptography 库），没有就退化到简单方案；两者都不可用时拒绝保存。

def _secret() -> bytes:
    p = _data_dir() / _SECRET_NAME
    if p.exists():
        try:
            b = p.read_bytes()
            if len(b) >= 32:
                return b
        except Exception:
            pass
    b = os.urandom(32)
    p.write_bytes(b)
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return b


# 环境里没有 cryptography 库，所以用标准库自己搭一个：
#   HMAC-SHA256 做密钥流（计数器模式）异或明文，再用 HMAC 做完整性校验。
# 对"服务器本地存一把 API key"这个场景强度足够：拿到密文但没拿到 secret 文件，
# 就还原不出明文；改动密文会被校验发现。
#
# 格式：c:base64(nonce16 | ciphertext | tag16)

_MAGIC = "c:"
_NONCE = 16
_TAG = 16


def _keystream(key: bytes, nonce: bytes, n: int) -> bytes:
    """HMAC-SHA256 计数器模式生成 n 字节密钥流。"""
    import hashlib
    import hmac
    out = b""
    i = 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def encrypt(plain: str) -> str:
    """加密成 'c:base64' 字符串。失败绝不降级成明文，直接抛异常。"""
    import base64
    import hmac
    import hashlib
    master = _secret()
    enc_key, mac_key = master[:16], master[16:32]
    nonce = os.urandom(_NONCE)
    data = plain.encode()
    ct = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()[:_TAG]
    return _MAGIC + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def decrypt(token: str) -> str:
    """解密。格式不对、校验失败、密钥变了，一律返回空串（当没配）。"""
    import base64
    import hmac
    import hashlib
    if not token or not token.startswith(_MAGIC):
        return ""
    try:
        raw = base64.urlsafe_b64decode(token[len(_MAGIC):].encode())
        if len(raw) < _NONCE + _TAG:
            return ""
        nonce, ct, tag = raw[:_NONCE], raw[_NONCE:-_TAG], raw[-_TAG:]
        master = _secret()
        enc_key, mac_key = master[:16], master[16:32]
        want = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()[:_TAG]
        if not hmac.compare_digest(tag, want):
            return ""                    # 被改过 / secret 换了
        return bytes(a ^ b for a, b in
                     zip(ct, _keystream(enc_key, nonce, len(ct)))).decode()
    except Exception:
        return ""


# ---------------- 读写 ----------------

def _load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict):
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def get_key(uid: str) -> str:
    """取该用户自己的 key（已解密）。没配或解不开都返回空串。"""
    if not uid:
        return ""
    with _LOCK:
        rec = (_load() or {}).get(uid) or {}
    return decrypt(rec.get("key_enc", ""))


def info(uid: str) -> dict:
    """给前端看的状态：有没有配、什么时候配的、末 4 位。**不返回 key 本身。**"""
    if not uid:
        return {"has_own": False}
    with _LOCK:
        rec = (_load() or {}).get(uid) or {}
    has = bool(rec.get("key_enc"))
    return {"has_own": has, "saved": rec.get("saved", ""), "tail": rec.get("tail", "")}


def set_key(uid: str, key: str) -> dict:
    """保存该用户自己的 key。key 传空串 = 清除，回落到全局 key。"""
    if not uid:
        raise ValueError("缺少用户")
    key = (key or "").strip()
    with _LOCK:
        d = _load()
        if not key:
            d.pop(uid, None)
            _save(d)
            return info(uid)
        enc = encrypt(key)          # 可能抛 RuntimeError
        d[uid] = {
            "key_enc": enc,
            "saved": time.strftime("%Y-%m-%d %H:%M"),
            "tail": key[-4:] if len(key) >= 4 else "",
        }
        _save(d)
    return info(uid)


def clear_key(uid: str) -> dict:
    return set_key(uid, "")


def mask(key: str) -> str:
    """用于日志/提示的掩码。"""
    k = key or ""
    if len(k) <= 8:
        return "***"
    return k[:4] + "..." + k[-4:]
