# -*- coding: utf-8 -*-
"""把单个知识库（默认业务手册 O32/资管培训）打成 Docker 部署数据包。

只复制这个库的 chunks/vectors/image_index/upload/images + 源文档 + 模型，
并过滤 registry/users；不会带上其他知识库。

产物布局：
  dist/o32-bundle/
    data/registry.json        # 只含目标库；owner 指到指定账号
    data/users.json           # 只含该账号
    data/<kb_id>/             # chunks/vectors/image_index/images/upload/manifest...
    data/_imgcap/             # 可选；重新索引图片文本时需要
    docs/<源目录名>/          # 源文档（预览/图片/重索引）
    models/                   # bge-small + reranker
    MANIFEST.json
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KB = "20261108_174939_69c8b5"   # 业务手册（O32/资管培训）


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-id", default=DEFAULT_KB)
    ap.add_argument("--out", default=str(ROOT / "dist" / "o32-bundle"))
    ap.add_argument("--owner", default="admin", help="知识库归属账号（users.json 里的 username）")
    ap.add_argument("--data-src", default=str(ROOT / "app" / "data"))
    ap.add_argument("--no-models", action="store_true")
    ap.add_argument("--no-imgcap", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    data_src = Path(args.data_src)
    out = Path(args.out)
    registry = json.loads((data_src / "registry.json").read_text(encoding="utf-8"))
    users = json.loads((data_src / "users.json").read_text(encoding="utf-8"))["users"]

    kb = next((k for k in registry if k.get("id") == args.kb_id), None)
    if not kb:
        raise SystemExit(f"知识库不存在: {args.kb_id}")
    owner = next((u for u in users if u.get("username") == args.owner), None)
    if not owner:
        raise SystemExit(f"账号不存在: {args.owner}")

    if out.exists():
        if args.clean:
            shutil.rmtree(out)
        else:
            raise SystemExit(f"输出目录已存在: {out}（加 --clean 覆盖）")
    (out / "data").mkdir(parents=True)
    (out / "docs").mkdir(parents=True)

    kb_src = data_src / args.kb_id
    if not kb_src.is_dir():
        raise SystemExit(f"缺少库数据目录: {kb_src}")
    shutil.copytree(kb_src, out / "data" / args.kb_id)

    if not args.no_imgcap and (data_src / "_imgcap").is_dir():
        shutil.copytree(data_src / "_imgcap", out / "data" / "_imgcap")

    # 注册邮箱域名白名单属于部署配置；不带上会退回默认域名
    email_domains = data_src / "email_domains.txt"
    if email_domains.is_file():
        shutil.copy2(email_domains, out / "data" / "email_domains.txt")

    docs_roots, missing = [], []
    for raw in str(kb.get("folder") or "").split("|"):
        raw = raw.strip()
        if not raw:
            continue
        src = Path(raw)
        if not src.is_dir():
            missing.append(raw)
            continue
        dest = out / "docs" / src.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        docs_roots.append(f"/opt/rag/docs/{src.name}")

    kb = dict(kb)
    kb["owner"] = owner["id"]
    kb["owner_name"] = owner.get("name") or owner.get("username")
    kb["shared_with"] = []
    kb["folder"] = "|".join(docs_roots)
    (out / "data" / "registry.json").write_text(
        json.dumps([kb], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "data" / "users.json").write_text(
        json.dumps({"users": [owner]}, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.no_models:
        shutil.copytree(ROOT / "models", out / "models")

    files = {}
    for name in ("chunks.jsonl", "vectors.npy", "vectors_meta.json", "image_index.json"):
        p = out / "data" / args.kb_id / name
        if p.exists():
            files[f"{name}_sha256"] = sha256(p)
            files[f"{name}_bytes"] = p.stat().st_size

    def tree_size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0

    manifest = {
        "kb_id": kb["id"],
        "kb_name": kb.get("name"),
        "owner": owner.get("username"),
        "chunk_count": kb.get("chunk_count"),
        "docs_roots": docs_roots,
        "missing_source_roots": missing,
        "data_bytes": tree_size(out / "data"),
        "docs_bytes": tree_size(out / "docs"),
        "models_bytes": tree_size(out / "models") if not args.no_models else 0,
        "files": files,
        "notes": [
            "registry.json 里只保留这一个库，folder 已改成容器内路径 /opt/rag/docs/<name>。",
            "users.json 只保留 owner 账号；上线后请轮换密码。",
            "data/_imgcap 里的 sidecar 按原 Windows 路径命名；若在服务器重新索引，"
            "请先用 tools/export_img_captions.py 重新生成。",
            "镜像不含 data/models；运行时分别挂载 /opt/rag/data、/opt/rag/models。",
            "data/email_domains.txt 为注册邮箱白名单，当前只允许 @hundsun.com。",
        ],
    }
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
