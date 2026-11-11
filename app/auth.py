# -*- coding: utf-8 -*-
"""外网访问鉴权(多用户版)。

- **key 即身份**: 登录只需输入访问 key, 由 users.verify_key 比对到具体用户。
- 登录成功签发无状态 token(HMAC) 存 HttpOnly Cookie(rag_token, 7 天), token 内带用户 id。
- 兼容旧格式 token(exp.sig): 视为原用户(第一个被创建的用户), 免得升级后所有人被踢下线。
- API 兼容请求头 X-RAG-Token(脚本/命令行调用用)。
- fs 目录浏览(/api/fs/*)默认禁用: auth.allow_fs_browse=false(外网部署必须关闭)。
- 单口令回退: 若用户表为空(未初始化), 仍可用 config.auth.password 登录, 视为管理员。
"""
import hashlib, hmac, os, time

COOKIE_NAME = "rag_token"
TOKEN_TTL = 7 * 24 * 3600  # 7 天


def load_auth_cfg():
    """读取鉴权配置。返回 dict: enabled, password, allow_fs_browse, ttl。"""
    import llm
    cfg = llm.load_config()
    a = cfg.get("auth") or {}
    password = os.environ.get("RAG_AUTH_PASSWORD") or a.get("password") or ""
    enabled = bool(os.environ.get("RAG_AUTH_PASSWORD") or a.get("password"))
    allow_fs = bool(a.get("allow_fs_browse", False))
    ttl = int(a.get("token_ttl", TOKEN_TTL))
    return {"enabled": enabled, "password": password,
            "allow_fs_browse": allow_fs, "ttl": ttl}


def _secret() -> str:
    """签名密钥: 优先环境变量, 否则取配置口令; 都空则用固定串(仅本地无鉴权模式)。"""
    return os.environ.get("RAG_AUTH_PASSWORD") or load_auth_cfg()["password"] or "rag-local-secret"


def _sign(payload: str, key: str = "") -> str:
    return hmac.new((key or _secret()).encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(uid: str, ttl: int = TOKEN_TTL) -> str:
    """生成 token: exp.uid.sig"""
    exp = int(time.time()) + ttl
    return f"{exp}.{uid}.{_sign(f'{exp}:{uid}')}"


def verify_token(token: str):
    """校验 token, 返回 uid; 失败返回 None。

    兼容旧格式 exp.sig(升级前签发的 token) → 返回 "legacy" 占位, 由上层映射到原用户。
    """
    if not token:
        return None
    parts = token.split(".")
    try:
        if len(parts) == 3:
            exp_s, uid, sig = parts
            exp = int(exp_s)
            if exp < time.time():
                return None
            if hmac.compare_digest(sig, _sign(f"{exp}:{uid}")):
                return uid
            return None
        if len(parts) == 2:                      # 旧格式
            exp_s, sig = parts
            exp = int(exp_s)
            if exp < time.time():
                return None
            if hmac.compare_digest(sig, hmac.new(_secret().encode(), exp_s.encode(),
                                                 hashlib.sha256).hexdigest()):
                return "legacy"
    except Exception:
        return None
    return None


def check_password(given: str, cfg: dict) -> bool:
    """旧单口令校验(仅当用户表不可用时回退)。"""
    pwd = cfg.get("password") or ""
    if not pwd:
        return False
    return hmac.compare_digest((given or "").encode(), pwd.encode())


def cookie_kwargs(ttl: int):
    return {"key": COOKIE_NAME, "httponly": True, "samesite": "lax",
            "max_age": ttl, "path": "/"}
