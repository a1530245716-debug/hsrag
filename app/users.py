# -*- coding: utf-8 -*-
"""多用户: 每个用户一把「访问 key」, 用 key 登录即身份, 无需用户名。

数据模型 (app/data/users.json):
  {"users": [
     {"id": "u_xxxx", "name": "管理员", "role": "admin",
      "salt": "<hex>", "hash": "<hex>", "created": "2026-10-28 16:00", "note": ""}
  ]}

设计要点:
- **key 即身份**: 登录时只输 key, 系统比对到哪个用户就是谁; 前端保持原来的单输入框体验。
- 口令不存明文: PBKDF2-HMAC-SHA256(20000 轮) + 每用户独立 salt。
- 角色: admin 可管理用户、查看/操作所有知识库; user 只能看到并操作自己的库。
- 首次启动自动引导: 把 config.json 里原有的 auth.password 建成一个普通用户(沿用老 key),
  现有知识库都归他; 另建一个 admin 账号, 随机 key 写到 app/data/admin_key.txt 供主人查阅。
"""
import hashlib, json, secrets, threading, time
from pathlib import Path

APP_DIR = Path(__file__).parent
_LOCK = threading.RLock()
_ITER = 20000
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def _data_dir():
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = APP_DIR / "data"
    if not d.is_absolute():
        d = APP_DIR.parent / d if (APP_DIR.parent / d).exists() else APP_DIR / d
    return d


def _users_file():
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "users.json"


def _hash(key: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"),
                               bytes.fromhex(salt), _ITER).hex()


def gen_key(n: int = 16) -> str:
    """生成随机 key(去掉易混淆字符 0/O/1/l/I)。"""
    return "".join(secrets.choice(_KEY_ALPHABET) for _ in range(n))


def _new_id():
    return "u_" + secrets.token_hex(5)


def load_users():
    p = _users_file()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("users", [])
    except Exception:
        return []


def save_users(users):
    p = _users_file()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def get_user(uid):
    return next((u for u in load_users() if u["id"] == uid), None)


def verify_key(key: str):
    """用 key 找用户。返回 user dict(不含 hash) 或 None。"""
    key = (key or "").strip()
    if not key:
        return None
    for u in load_users():
        try:
            if _hash(key, u["salt"]) == u["hash"]:
                return _public(u)
        except Exception:
            continue
    return None


def _norm_perms(p):
    p = p or {}
    return {"kb": bool(p.get("kb")), "defense": bool(p.get("defense"))}


def perms_of(user):
    """用户的入口权限。admin 全开；老数据没有 perms 字段时默认全开（兼容）。"""
    if not user:
        return {"kb": False, "defense": False}
    if user.get("role") == "admin":
        return {"kb": True, "defense": True}
    if "perms" in user:
        return _norm_perms(user.get("perms"))
    return {"kb": True, "defense": True}


def _public(u):
    out = {k: v for k, v in u.items() if k not in ("hash", "salt")}
    out["perms"] = perms_of(u)
    return out


def set_perms(uid, perms):
    """管理员设置用户的入口权限。"""
    with _LOCK:
        us = load_users()
        for u in us:
            if u["id"] == uid:
                u["perms"] = _norm_perms(perms)
                save_users(us)
                return _public(u)
    return None


def _norm_username(s):
    return (s or "").strip().lower()


def find_by_username(username):
    """按账号查用户(不分大小写)。"""
    n = _norm_username(username)
    if not n:
        return None
    for u in load_users():
        if _norm_username(u.get("username")) == n:
            return u
    return None


def verify_password(username, password):
    """账号 + 密码校验, 成功返回用户(不含 hash)。"""
    u = find_by_username(username)
    if not u or not password:
        return None
    try:
        if _hash(password, u["salt"]) == u["hash"]:
            return _public(u)
    except Exception:
        pass
    return None


def create_account(username, password, name="", role="user", note="", perms=None):
    """建账号(账号 + 密码)。账号重复/太短抛 ValueError。"""
    un = (username or "").strip()
    pw = password or ""
    if len(un) < 2:
        raise ValueError("账号至少 2 个字符")
    if len(pw) < 2:
        raise ValueError("密码至少 2 个字符")
    if not all(c.isalnum() or c in "_-.@" for c in un):
        raise ValueError("账号只能用字母、数字和 _ - . @")
    if find_by_username(un):
        raise ValueError("这个账号已经被占用了")
    salt = secrets.token_hex(16)
    with _LOCK:
        us = load_users()
        u = {"id": _new_id(),
             "username": un,
             "name": (name or "").strip() or un,
             "role": "admin" if role == "admin" else "user",
             "salt": salt, "hash": _hash(pw, salt),
             "created": time.strftime("%Y-%m-%d %H:%M"),
             "note": note or "",
             "perms": _norm_perms(perms)}
        us.append(u)
        save_users(us)
        return _public(u)


def set_password(uid, password=None):
    """重置密码(不传则随机生成)。返回 (user, plain_password)。"""
    with _LOCK:
        us = load_users()
        for u in us:
            if u["id"] == uid:
                plain = password or gen_key(12)
                salt = secrets.token_hex(16)
                u["salt"] = salt
                u["hash"] = _hash(plain, salt)
                save_users(us)
                out = _public(u)
                out["password"] = plain      # 只在重置时回显一次
                return out, plain
    return None, None


def set_username(uid, username):
    un = (username or "").strip()
    if len(un) < 2:
        raise ValueError("账号至少 2 个字符")
    other = find_by_username(un)
    if other and other["id"] != uid:
        raise ValueError("这个账号已经被占用了")
    with _LOCK:
        us = load_users()
        for u in us:
            if u["id"] == uid:
                u["username"] = un
                save_users(us)
                return _public(u)
    return None


def create_user(name="", role="user", key=None, note="", username=None, password=None, perms=None):
    """新建用户; key 不传则自动生成。返回 (user_with_key, plain_key)。"""
    with _LOCK:
        users = load_users()
        plain = key or password or gen_key()
        salt = secrets.token_hex(16)
        un = (username or "").strip()
        if un and find_by_username(un):
            raise ValueError("这个账号已经被占用了")
        u = {"id": _new_id(),
             "username": un,
             "name": (name or "").strip() or un or f"用户{len(users) + 1}",
             "role": "admin" if role == "admin" else "user",
             "salt": salt, "hash": _hash(plain, salt),
             "created": time.strftime("%Y-%m-%d %H:%M"),
             "note": note or "",
             "perms": _norm_perms(perms)}
        users.append(u)
        save_users(users)
        out = _public(u)
        out["key"] = plain          # 只在创建/重置时回显一次
        return out


def set_key(uid, key=None):
    """重置某用户的 key。返回 (user, plain_key) 或 (None, None)。"""
    with _LOCK:
        users = load_users()
        for u in users:
            if u["id"] == uid:
                plain = key or gen_key()
                salt = secrets.token_hex(16)
                u["salt"] = salt
                u["hash"] = _hash(plain, salt)
                save_users(users)
                out = _public(u)
                out["key"] = plain
                return out, plain
    return None, None


def update_user(uid, name=None, role=None, note=None):
    with _LOCK:
        users = load_users()
        for u in users:
            if u["id"] == uid:
                if name is not None and str(name).strip():
                    u["name"] = str(name).strip()
                if role in ("admin", "user"):
                    u["role"] = role
                if note is not None:
                    u["note"] = str(note)
                save_users(users)
                return _public(u)
    return None


def delete_user(uid):
    with _LOCK:
        users = load_users()
        target = next((u for u in users if u["id"] == uid), None)
        if not target:
            return False
        # 不允许删掉最后一个管理员
        if target.get("role") == "admin":
            admins = [u for u in users if u.get("role") == "admin"]
            if len(admins) <= 1:
                raise ValueError("不能删除唯一的管理员")
        save_users([u for u in users if u["id"] != uid])
        return True


def ensure_bootstrap(legacy_password=""):
    """首次启动引导。

    - 无用户: 用旧口令建一个普通用户(沿用老 key, 现有库归他), 再建一个管理员(随机 key)。
    - 管理员 key 写入 app/data/admin_key.txt, 并在控制台打印一次。
    返回 {"created": bool, "admin_key": str|None}
    """
    with _LOCK:
        users = load_users()
        if users:
            return {"created": False, "admin_key": None}
        created_msg = []
        owner_id = None
        if legacy_password:
            u = create_user(name="主人（原访问口令）", role="user", key=legacy_password,
                            note="由原 config.auth.password 迁移而来",
                            perms={"kb": True, "defense": True})
            owner_id = u["id"]
            created_msg.append(f"普通用户「{u['name']}」沿用原口令")
        admin = create_user(name="管理员", role="admin",
                            note="可管理用户、查看所有知识库")
        admin_key = admin["key"]
        # 把现有知识库归到原用户(没有原口令则归管理员)
        try:
            import indexer
            reg = indexer.load_registry()
            target = owner_id or admin["id"]
            changed = False
            for kb in reg:
                if not kb.get("owner"):
                    kb["owner"] = target
                    changed = True
            if changed:
                indexer.save_registry(reg)
        except Exception as e:
            print(f"[users] 迁移知识库归属失败: {e}")
        try:
            p = _data_dir() / "admin_key.txt"
            p.write_text(
                "RAG 管理员账号（请妥善保管，可删除本文件但请先记下 key）\n"
                f"管理员 key: {admin_key}\n"
                "登录方式: 在网页登录框输入上面这串 key 即可，无需用户名。\n"
                "权限: 创建/删除用户、重置 key、查看和操作所有知识库。\n",
                encoding="utf-8")
        except Exception:
            pass
        print("=" * 60)
        print("  已初始化多用户系统")
        for m in created_msg:
            print("  ·", m)
        print(f"  · 管理员 key: {admin_key}  (已存 app/data/admin_key.txt)")
        print("=" * 60)
        return {"created": True, "admin_key": admin_key}


def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def resolve_owner_of_kb(kb, user):
    """判断 user 能否操作该知识库: admin 全部可以; 普通用户仅限自己的。"""
    if not user:
        return False
    if is_admin(user):
        return True
    return (kb or {}).get("owner") == user.get("id")


# ---------------- 知识库共享（只读分发） ----------------
# 管理员可以把某个库分给多个用户看。被分到的人能提问、能看引用，但不能改这个库。
# 权限记在知识库记录自己的 "shared_with" 字段里（用户 id 列表），
# 不另建表——库没了权限自然没了，不用同步清理。

def shared_ids(kb):
    """这个库分给了哪些用户（只返回存在的 id）。"""
    raw = (kb or {}).get("shared_with") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(x) for x in raw if x]


def can_read_kb(kb, user):
    """能不能"看"这个库（提问、看引用）。

    注意和 resolve_owner_of_kb 的区别：
    那个是"能不能改"，这个只管"能不能用"。
    """
    if not user:
        return False
    if is_admin(user):
        return True
    if (kb or {}).get("owner") == user.get("id"):
        return True
    return user.get("id") in shared_ids(kb)


def set_shared_with(kb, user_ids):
    """设置这个库分给谁。去重、只留字符串。"""
    seen, out = set(), []
    for x in (user_ids or []):
        x = str(x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    kb["shared_with"] = out
    return out
