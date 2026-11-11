# -*- coding: utf-8 -*-
"""预热矢量图缓存：把全库的 EMF/WMF 提前转成 PNG。

为什么需要它：
`imgserve.extract()` 遇到 EMF/WMF 时会调 PowerShell + GDI+ 现转一张 PNG，
单张约 1.6 秒（主要是 PowerShell 进程启动）。用户点开引用时等 1.6 秒不好受，
所以提前转好放进缓存，之后请求就是 1ms。

用法：
  python warm_images.py              # 全部知识库
  python warm_images.py <kb_id>      # 指定知识库
  python warm_images.py --stat       # 只看还有多少没转
"""
import sys
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

import imgserve
import indexer


def _docs_with_vectors(kb_id):
    """该库里包含 EMF/WMF 的文档，返回 [(rel_path, [图名])]。"""
    kb = indexer.get_kb(kb_id)
    if not kb:
        return []
    roots = [Path(r) for r in str(kb.get("folder") or "").split("|") if r.strip()]
    up = indexer.upload_dir(kb_id)
    roots.append(up)

    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() not in {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"}:
                continue
            try:
                media = imgserve.list_doc_media(p)
            except Exception:
                continue
            vec = [n for n, (zp, sz) in media.items()
                   if Path(n).suffix.lower() in imgserve._VECTOR_EXT]
            if vec:
                out.append((p.relative_to(root).as_posix(), vec, p))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stat_only = "--stat" in sys.argv

    kbs = indexer.load_registry()
    if args:
        kbs = [k for k in kbs if k["id"] == args[0]]
    if not kbs:
        print("没有匹配的知识库")
        return 1

    for kb in kbs:
        kb_id, name = kb["id"], kb.get("name", "")
        docs = _docs_with_vectors(kb_id)
        total = sum(len(v) for _, v, _ in docs)
        if not docs:
            print(f"【{name}】无 EMF/WMF，跳过")
            continue

        # 已有缓存的先跳过
        todo = []
        for rel, names, full in docs:
            for nm in names:
                cp = imgserve._cache_path(kb_id, full, nm)
                png = cp.with_name(cp.name + ".png")
                if not (png.is_file() and png.stat().st_size > 0):
                    todo.append((rel, nm))

        print(f"【{name}】{len(docs)} 份文档, {total} 张矢量图, 待转 {len(todo)} 张")
        if stat_only:
            continue
        if not todo:
            print("  全部已缓存 ✓")
            continue

        done = fail = 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(imgserve.extract, kb_id, rel, nm): (rel, nm)
                    for rel, nm in todo}
            for i, f in enumerate(as_completed(futs), 1):
                rel, nm = futs[f]
                try:
                    data, ext = f.result()
                except Exception:
                    data, ext = None, None
                if data:
                    done += 1
                else:
                    fail += 1
                if i % 10 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}  成功 {done} 失败 {fail}", flush=True)

        print(f"  完成: 成功 {done}, 失败 {fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
