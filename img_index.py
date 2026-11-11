# -*- coding: utf-8 -*-
"""批量把文档里的图片转成文字描述(供索引使用)。

为什么单独一个脚本:
    识图很慢(每张 2~5 秒), 2593 张图要 20 分钟以上。
    放进索引流程会让人以为系统卡死。所以这里离线跑, 结果进缓存;
    之后的索引只读缓存(毫秒级), 不阻塞任何操作。

用法:
    python img_index.py --kb 某资管              # 给该库所有文档生成图片描述
    python img_index.py --kb 某资管 --workers 6  # 并发(默认 4, 网关扛得住可以调高)
    python img_index.py --stats                # 看缓存进度
    python img_index.py --list                 # 列出哪些文档含图、各多少张

跑完记得重建索引, 描述才会进 chunks。
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT))

import img_caption, indexer  # noqa: E402

OFFICE = {".docx", ".pptx", ".xlsx"}


def pick_kb(name):
    reg = indexer.load_registry()
    if not name:
        return reg
    out = [k for k in reg if name == k.get("id") or name in (k.get("name") or "")]
    if not out:
        parts = name.split()
        out = [k for k in reg if any(p in (k.get("name") or "") for p in parts)]
    return out


def _count_images(p: Path) -> int:
    """只数不解压: office 读 zip 目录, pdf 用 fitz 数。"""
    ext = p.suffix.lower()
    if ext in OFFICE:
        try:
            import zipfile
            with zipfile.ZipFile(p) as z:
                return sum(1 for n in z.namelist()
                           if "/media/" in n and Path(n).suffix.lower() in img_caption.IMG_EXT)
        except Exception:
            return 0
    if ext == ".pdf":
        try:
            import fitz
            d = fitz.open(str(p))
            n = sum(len(pg.get_images(full=True)) for pg in d)
            d.close()
            return n
        except Exception:
            return 0
    return 0


def docs_with_images(kb_id):
    up = indexer.upload_dir(kb_id)
    out = []
    for p in Path(up).rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in OFFICE and p.suffix.lower() != ".pdf":
            continue
        n = _count_images(p)
        if n:
            out.append((p, n))
    out.sort(key=lambda x: -x[1])
    return out


def run(args):
    if args.stats:
        st = img_caption.stats()
        print(f"图片描述缓存: {st['cached']} 张已处理, 其中 {st['with_text']} 张有文字描述")
        print(f"缓存目录: {img_caption.cache_dir()}")
        return 0

    kbs = pick_kb(args.kb)
    if not kbs:
        print("找不到知识库")
        return 2

    total_imgs = total_done = total_skip = 0
    t_all = time.time()
    for kb in kbs:
        docs = docs_with_images(kb["id"])
        if not docs:
            print(f"\n【{kb['name']}】不含图片, 跳过")
            continue
        n_img = sum(n for _, n in docs)
        print(f"\n【{kb['name']}】{len(docs)} 个含图文档, 共 {n_img} 张图", flush=True)
        if args.list:
            for p, n in docs:
                print(f"    {n:>4} 张  {p.relative_to(indexer.upload_dir(kb['id']))}")
            continue

        done = 0
        for p, n in docs:
            if p.suffix.lower() in OFFICE:
                imgs = img_caption.images_from_office(p)
            else:
                imgs = img_caption.images_from_pdf(p)
            # 先按缓存过滤, 只处理没缓存的
            todo = []
            for label, raw in imgs:
                pre = img_caption._prep(raw)
                if not pre:
                    total_skip += 1
                    continue
                png, key = pre
                cp = img_caption.cache_dir() / f"{key}.json"
                if cp.exists() and not args.force:
                    done += 1
                    total_done += 1
                    continue
                todo.append((label, png, key))
            total_imgs += n
            if not todo:
                print(f"    ✓ {p.name[:52]} ({n} 张, 全部已缓存)")
                continue
            print(f"    → {p.name[:52]} 待处理 {len(todo)}/{n} 张", flush=True)

            def make_progress(total):
                state = {"i": 0}

                def cb():
                    state["i"] += 1
                    if state["i"] % 10 == 0 or state["i"] == total:
                        print(f"        {state['i']}/{total}", flush=True)
                return cb

            results = img_caption.describe_images(
                todo,   # 已是 (label, png, key)
                workers=args.workers, progress=make_progress(len(todo)))
            ok = sum(1 for _, d in results if d)
            done += len(todo)
            total_done += len(todo)
            print(f"      完成 {ok}/{len(todo)} 张有描述", flush=True)
            # 把该文档所有图片描述(含已缓存的)汇总写盘, 索引时直接读
            full = []
            for label, raw in imgs:
                pre = img_caption._prep(raw)
                if not pre:
                    continue
                _png, key2 = pre
                c2 = img_caption.cache_dir() / f"{key2}.json"
                if c2.exists():
                    try:
                        d2 = json.loads(c2.read_text(encoding="utf-8")).get("desc", "")
                    except Exception:
                        d2 = ""
                    if d2:
                        full.append((label, d2))
            img_caption.save_doc_text(p, full)
            print(f"      已写文档描述: {len(full)} 张图", flush=True)
            del imgs, full, results

    if not args.list:
        dt = time.time() - t_all
        st = img_caption.stats()
        print("\n" + "=" * 56)
        print(f"  本次处理: {total_done} 张 (跳过小图 {total_skip})  用时 {dt/60:.1f} 分钟")
        print(f"  缓存总计: {st['cached']} 张, {st['with_text']} 张有描述")
        print("  下一步: 重建索引，描述才会进入检索")
        print("=" * 56)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default="", help="知识库名字(模糊)或 id，不填=全部")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略缓存重跑")
    sys.exit(run(ap.parse_args()))
