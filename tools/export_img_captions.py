# -*- coding: utf-8 -*-
"""把 imgindex 的图片摘要导出成 img_caption sidecar，供 index_docs 拼进正文。

背景：
  - `app/imgindex.py` 把每张图的 OCR/摘要写进 `app/data/<kb>/image_index.json`；
  - `index_docs._join_with_images()` 只读 `img_caption` 的 sidecar 文本；
  - 两条管道以前没打通，所以 1640 张图的描述进不了 chunks，检索答不到。

这个脚本按文档把 image_index.json 里的 summary 写成
`app/data/_imgcap/text/<文档路径md5>.txt`（`[插图 名字] 摘要`）。
写完需要重新索引该库，描述才会进入 chunks；脚本本身不动 chunks/vectors。

用法：
  python tools/export_img_captions.py --kb <kb_id> [--kb ...] [--dry-run]
  python tools/export_img_captions.py --all
"""
import argparse
import json
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

import img_caption  # noqa: E402
import imgindex     # noqa: E402
import indexer      # noqa: E402


def export_kb(kb_id: str, dry_run: bool = False) -> dict:
    data = imgindex.load_index(kb_id)
    images = data.get("images") or {}
    per_doc = {}   # rel -> {hash: (name, summary, kw, ocr)}
    for h, v in images.items():
        if v.get("useful") is False:
            continue
        summary = (v.get("summary") or "").strip()
        if not summary:
            continue
        # 具体参数/文件名往往只在 OCR 里；单独拆成纯文本行，
        # 否则模型会把整条当成"图片说明"而不去引用里面的明细。
        ocr = re.sub(r"\s+", " ", (v.get("ocr") or "")).strip()[:800]
        kw = " ".join(str(x) for x in (v.get("keywords") or [])).strip()
        for doc in (v.get("docs") or []):
            rel = doc.get("path")
            if not rel:
                continue
            per_doc.setdefault(rel, {})[h] = (doc.get("name") or "", summary, kw, ocr)

    docs_written = 0
    images_written = 0
    docs_missing = 0
    for rel, items in sorted(per_doc.items()):
        p = indexer.resolve_kb_file(kb_id, rel)
        if not p:
            docs_missing += 1
            continue
        lines = []
        for name, summary, kw, ocr in sorted(items.values(), key=lambda x: x[0]):
            line = f"[插图 {name}] {summary}"
            if kw:
                line += " | 关键词: " + kw
            lines.append(line)
            if ocr:
                # 纯文本行：模型会把它当正文引用，而不是图片描述
                lines.append(f"图片文字 {name}（可直接引用）: {ocr}")
        if not dry_run:
            img_caption.doc_text_path(p).write_text("\n".join(lines), encoding="utf-8")
        docs_written += 1
        images_written += len(items)
    return {
        "kb_id": kb_id,
        "docs_with_captions": docs_written,
        "images_written": images_written,
        "docs_unresolved": docs_missing,
        "dry_run": dry_run,
        "caption_enabled": img_caption.enabled(),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", action="append", default=[], help="知识库 id，可重复")
    ap.add_argument("--all", action="store_true", help="处理注册表里的全部库")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    kbs = list(args.kb)
    if args.all:
        kbs = [k.get("id") for k in indexer.load_registry() if k.get("id")]
    if not kbs:
        ap.error("需要 --kb 或 --all")

    out = [export_kb(k, dry_run=args.dry_run) for k in kbs]
    print(json.dumps({"results": out}, ensure_ascii=False, indent=2))
    if any(not r["caption_enabled"] for r in out):
        print("WARN: index.image_caption=false，索引时不会读取 sidecar", file=sys.stderr)
    if any(r["images_written"] for r in out) and not args.dry_run:
        print("下一步：重新索引对应库，图片描述才会进入 chunks。", file=sys.stderr)


if __name__ == "__main__":
    main()
