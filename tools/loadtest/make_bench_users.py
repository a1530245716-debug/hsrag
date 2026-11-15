# -*- coding: utf-8 -*-
"""创建压测账号，并把目标知识库共享给它们（幂等）。

为什么要多账号：服务端 per_user_max=2，如果压测都用同一个账号，
并发一高就先撞每用户限制，永远测不到 global_max。

用法：
  python tools/loadtest/make_bench_users.py --count 16
  python tools/loadtest/make_bench_users.py --count 16 --kb-id 20261019_121148_5a7a33
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

import indexer  # noqa: E402
import users    # noqa: E402


def pick_kb(kb_id: str) -> dict:
    reg = indexer.load_registry()
    if kb_id:
        for kb in reg:
            if kb.get("id") == kb_id:
                return kb
        raise SystemExit(f"知识库不存在: {kb_id}")
    ready = [k for k in reg if (k.get("chunk_count") or 0) > 0]
    if not ready:
        raise SystemExit("没有可用的知识库")
    return max(ready, key=lambda k: k.get("chunk_count") or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=16)
    ap.add_argument("--prefix", default="bench")
    ap.add_argument("--password", default="benchPass2026")
    ap.add_argument("--kb-id", default="")
    ap.add_argument("--no-share", action="store_true", help="只建账号，不共享知识库")
    args = ap.parse_args()

    data_dir = APP / "data"
    stamp = datetime.now().strftime("%Y%m%d")
    for name in ("users.json", "registry.json"):
        src = data_dir / name
        if src.exists():
            bak = src.with_name(f"{name}.bak-bench-{stamp}")
            if not bak.exists():
                shutil.copy2(src, bak)

    kb = pick_kb(args.kb_id)
    created, reused, ids = [], [], []
    for i in range(1, args.count + 1):
        username = f"{args.prefix}{i:02d}"
        u = users.find_by_username(username)
        if u:
            reused.append(username)
        else:
            u = users.create_account(
                username, args.password, name=username, role="user",
                note="loadtest bench account", perms={"kb": True, "defense": False})
            created.append(username)
        ids.append(u["id"])

    shared = []
    if not args.no_share:
        reg = indexer.load_registry()
        for k in reg:
            if k.get("id") == kb["id"]:
                old = k.get("shared_with") or []
                k["shared_with"] = users.set_shared_with(k, list(old) + ids)
                shared = k["shared_with"]
                break
        indexer.save_registry(reg)

    print(json.dumps({
        "kb_id": kb["id"],
        "kb_name": kb.get("name"),
        "chunks": kb.get("chunk_count"),
        "accounts": [f"{args.prefix}{i:02d}" for i in range(1, args.count + 1)],
        "password": args.password,
        "created": created,
        "reused": reused,
        "shared_count": len(shared) if not args.no_share else None,
        "backup": f"users.json/registry.json -> *.bak-bench-{stamp}",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
