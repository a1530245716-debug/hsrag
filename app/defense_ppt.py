# -*- coding: utf-8 -*-
"""答辩 PPT 解析：把新人上传的 .pptx 拆成「按页 + 文字 + 图片」。

为什么和 index_docs.extract_pptx 不一样
--------------------------------------
建知识库时是把整个文档拼成一大段文字去切块检索，文档结构不重要。
答辩 PPT 不一样：**提问要对得上某一页**——"你第 5 页画的这个流程，第三步为什么这么走"
才是好问题，笼统地问"你 PPT 讲了什么"没用。

所以这里保留页的概念：

    [
      {"no": 1, "title": "试用期工作总结", "text": "...", "images": ["p1_1.png", ...]},
      {"no": 2, "title": "环境搭建", "text": "...", "images": [...]},
    ]

图片同时**导出到磁盘**，前端能直接显示——评委看 PPT 提问，不能只看文字。

输出目录：data_dir/defense/ppt/<upload_id>/，里面是解析结果 + 图片。
"""
import hashlib
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

APP = Path(__file__).parent

# 只导出这些位图，矢量图（emf/wmf）前端显示不了，跳过
_IMG_OK = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_MAX_IMG_PER_PAGE = 6
_MAX_TEXT_PER_PAGE = 2000


def _data_dir():
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = APP / "data"
    if not d.is_absolute():
        alt = APP.parent / d
        d = alt if alt.exists() else APP / d
    return d


def store_dir() -> Path:
    d = _data_dir() / "defense" / "ppt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rels(z, names):
    """ppt/slides/_rels/slideN.xml.rels → {rId: 媒体文件名}"""
    from docimg import _parse_rels
    out = {}
    for n in names:
        if n.startswith("ppt/slides/_rels/") and n.endswith(".rels"):
            k = n.rsplit("/", 1)[-1].replace(".xml.rels", "")
            out[k] = _parse_rels(z, n)
    return out


def _title_of(texts):
    """这一页的标题：取第一个像样的短句（超长的当正文）。"""
    for t in texts:
        t = t.strip().replace("\n", " ")
        if 2 <= len(t) <= 40:
            return t
    return (texts[0].strip()[:40] if texts else "")


def parse_pptx(path, upload_id=None, keep=True):
    """解析一个 .pptx。

    keep=True 时把图片导出到 store_dir()/<upload_id>/img/，
    返回的每张图带 url（相对 /api/defense/ppt/<id>/img/<file>）。
    """
    from pptx import Presentation

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.suffix.lower() not in (".pptx", ".ppt"):
        raise ValueError("只支持 .pptx / .ppt")

    upload_id = upload_id or ("ppt_" + hashlib.md5(
        (str(path) + str(time.time())).encode()).hexdigest()[:10])
    out_dir = store_dir() / upload_id
    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    slides = []
    try:
        prs = Presentation(str(path))
        for i, slide in enumerate(list(prs.slides)):
            texts, table_rows = [], []
            for shape in slide.shapes:
                try:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            t = "".join(r.text for r in para.runs).strip()
                            if t:
                                texts.append(t)
                    if getattr(shape, "has_table", False):
                        for row in shape.table.rows:
                            cells = [c.text.strip() for c in row.cells]
                            if any(cells):
                                table_rows.append(" | ".join(cells))
                    if getattr(shape, "has_chart", False):
                        try:
                            ch = shape.chart
                            cats = [str(c) for c in (ch.plots[0].categories or [])][:12]
                            if cats:
                                texts.append("（图表数据项）" + "、".join(cats))
                        except Exception:
                            pass
                except Exception:
                    continue
            slides.append({"no": i + 1, "texts": texts,
                           "tables": table_rows, "images": []})
    except Exception as e:
        raise ValueError(f"PPT 解析失败：{type(e).__name__}: {e}")

    # ---- 图片：从 zip 里按页取出并落盘 ----
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            rel = _rels(z, names)
            for s in slides:
                r = rel.get(f"slide{s['no']}", {})
                page_imgs = []
                # slideN.xml 里 <a:blip r:embed="rIdX">，顺序即出现顺序
                try:
                    xml = z.read(f"ppt/slides/slide{s['no']}.xml")
                except Exception:
                    xml = b""
                seen = set()
                for rid in re.findall(rb'r:embed="(rId\d+)"', xml):
                    nm = r.get(rid.decode())
                    if not nm or nm in seen:
                        continue
                    seen.add(nm)
                    if Path(nm).suffix.lower() not in _IMG_OK:
                        continue
                    src = None
                    for cand in (f"ppt/media/{nm}", f"ppt/slides/media/{nm}"):
                        if cand in names:
                            src = cand
                            break
                    if not src:
                        continue
                    try:
                        data = z.read(src)
                    except Exception:
                        continue
                    if len(data) < 3000:        # 图标/装饰小图不要
                        continue
                    fn = f"p{s['no']}_{len(page_imgs) + 1}{Path(nm).suffix.lower()}"
                    try:
                        (img_dir / fn).write_bytes(data)
                    except Exception:
                        continue
                    page_imgs.append(fn)
                    if len(page_imgs) >= _MAX_IMG_PER_PAGE:
                        break
                s["images"] = page_imgs
    except Exception as e:
        print(f"[ppt] 抽图失败: {type(e).__name__}: {e}", file=sys.stderr)

    # ---- 组装成前端能直接用的结构 ----
    out_slides = []
    for s in slides:
        body = "\n".join(s["texts"] + s["tables"])
        title = _title_of(s["texts"])
        out_slides.append({
            "no": s["no"],
            "title": title,
            "text": body[:_MAX_TEXT_PER_PAGE],
            "has_image": bool(s["images"]),
            "images": [{"file": f,
                        "url": f"/api/defense/ppt/{upload_id}/img/{f}"}
                       for f in s["images"]],
        })

    # 给 AI 看的纯文本（按页，图用页码标注）
    lines = []
    for s in out_slides:
        head = f"[第{s['no']}页] {s['title']}"
        lines.append(head)
        if s["text"].strip():
            lines.append(s["text"])
        if s["images"]:
            lines.append(f"（本页有 {len(s['images'])} 张图：流程/界面/架构截图）")
    plain = "\n".join(lines)

    meta = {
        "id": upload_id,
        "name": path.name,
        "pages": len(out_slides),
        "images": sum(len(s["images"]) for s in out_slides),
        "chars": len(plain),
        "parsed": time.strftime("%Y-%m-%d %H:%M"),
        "slides": out_slides,
        "text": plain,
    }
    try:
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return meta


def load(upload_id):
    """读回之前解析过的 PPT。"""
    p = store_dir() / upload_id / "meta.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def img_path(upload_id, filename):
    """图片的真实路径，供接口返回。防目录穿越。"""
    if not re.fullmatch(r"[\w.\-]+", filename or ""):
        return None
    if not re.fullmatch(r"ppt_[\w]+", upload_id or ""):
        return None
    p = store_dir() / upload_id / "img" / filename
    return p if p.exists() else None


def cleanup(days=3):
    """清掉 N 天前的上传（PPT 是临时的，不用长期留）。"""
    n = 0
    now = time.time()
    for d in store_dir().iterdir():
        if not d.is_dir():
            continue
        try:
            if now - d.stat().st_mtime > days * 86400:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except Exception:
            continue
    return n


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("用法: python app/defense_ppt.py <文件.pptx>")
        sys.exit(1)
    m = parse_pptx(sys.argv[1])
    print(f"页数 {m['pages']}，图片 {m['images']} 张，文字 {m['chars']} 字")
    print(f"id = {m['id']}")
    for s in m["slides"][:5]:
        print(f"  第{s['no']}页 [{s['title']}] 文字{len(s['text'])}字 图{len(s['images'])}张")
