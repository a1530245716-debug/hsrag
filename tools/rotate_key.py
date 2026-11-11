# -*- coding: utf-8 -*-
"""重置访问 key（忘记 key / 怀疑泄露时用）。

必须在这台机器上跑 —— 能跑这个脚本，本身就等于拥有服务器权限。

做三件事：
  1. 备份 users.json 和 config.json 到 app/data/_rotate_backup_<时间>/
  2. 给用户重新生成随机 key（默认全部用户，可用 --name 只改一个）
  3. 换掉 config.json 里的 auth.password（它是 token 签名密钥，换了之后
     **所有人当前的登录状态立即失效**，必须用新 key 重新登录）

跑完终端会打印新 key，只打印这一次。丢了就再跑一遍。
"""
import argparse
import json
import secrets
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import users  # noqa: E402

ALPHA = users._KEY_ALPHABET


def new_key(n=20):
    return "".join(secrets.choice(ALPHA) for _ in range(n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="", help="只重置这个用户名的 key（默认全部）")
    ap.add_argument("--length", type=int, default=20, help="key 长度，默认 20")
    args = ap.parse_args()

    data = ROOT / "app" / "data"
    cfg_path = ROOT / "app" / "config.json"
    if not (data / "users.json").exists():
        print("找不到 app/data/users.json，先启动一次服务让它初始化")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = data / f"_rotate_backup_{stamp}"
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(data / "users.json", bak / "users.json")
    shutil.copy2(cfg_path, bak / "config.json")

    rows = []
    us = users.load_users()          # 拿到列表本身，改的就是它，不能重新 load
    for u in us:
        if args.name and (u.get("name") or "") != args.name:
            continue
        key = new_key(max(12, args.length))
        salt = secrets.token_hex(16)
        u["salt"] = salt
        u["hash"] = users._hash(key, salt)
        rows.append((u.get("role") or "user", u.get("name") or "", key))
    users.save_users(us)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    a = cfg.setdefault("auth", {})
    a["password"] = secrets.token_urlsafe(24)   # token 签名密钥，换掉 = 全员掉线
    a["enabled"] = True
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    legacy = data / "admin_key.txt"
    if legacy.exists():
        legacy.unlink()
        print(f"已删除明文 key 文件: {legacy}")

    print(f"备份目录: {bak}")
    print("新访问 key（只显示这一次，记下来）：")
    for role, name, key in rows:
        print(f"  [{role}] {name}: {key}")
    print("\n服务需要重启才生效（token 签名密钥换了，所有人要重新登录）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
