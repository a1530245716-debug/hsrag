# -*- coding: utf-8 -*-
"""抽取文档文本并分块,构建用于 BM25 检索的索引。
用法: python index_docs.py [--root 源目录] [--out 输出目录]
输出: --out/chunks.jsonl  每行一个 json 块;  --out/manifest.json  统计信息
"""
import os, sys, json, glob, shutil, hashlib, re, subprocess
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent / "app"))

try:
    import docx
except Exception:
    docx = None
try:
    from pptx import Presentation
except Exception:
    Presentation = None
try:
    import openpyxl
except Exception:
    openpyxl = None
try:
    import xlrd
except Exception:
    xlrd = None
try:
    import pymupdf as fitz
except Exception:
    try:
        import fitz
    except Exception:
        fitz = None

ROOT = Path(os.environ.get("RAG_ROOT") or os.getcwd())
OUT = ROOT / "index"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
ANTIWORD = shutil.which("antiword") or r"\mingw64\bin\antiword.exe"

SKIP_EXT = {".vsdx", ".rar", ".rp", ".lic", ".cfg", ".js", ".html", ".jpg", ".jpeg", ".png", ".baiduyun.uploading.cfg"}
SKIP_DIRS = {".git", "_rag", "app", "index", "__pycache__", "node_modules"}
SKIP_FILES = {"~$"}  # office 临时文件前缀


def norm_text(t: str) -> str:
    if not t:
        return ""
    t = re.sub(r"[\xa0　]", " ", t)
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ---------- 各类型抽取 ----------

def _img_text(p: Path) -> str:
    """文档里图片的文字描述(离线用视觉模型生成, 缓存在 app/data/_imgcap)。
    索引时只读文件, 不会变慢。没有就跳过。"""
    try:
        import img_caption
        return img_caption.load_doc_text(p)
    except Exception:
        return ""


def _join_with_images(txt: str, p: Path) -> str:
    img = _img_text(p)
    if not img:
        return txt
    return (txt.rstrip() + "\n\n" + img) if txt.strip() else img


# ---- 图片位置标记：让 chunk 里留下 [image1.png]，前端才能把原图调出来 ----

# 前端每次最多展示 4 张，标记就按这个量给，多余的纯属噪声
_MAX_MARKS_PER_PAGE = 2     # pptx 每页（前端只展示 4 张，2 个够用又不撑大正文）
_MAX_MARKS_DOC = 4          # docx/xlsx 文档末尾清单

def _docimg(p: Path):
    """拿 DocImages 解析器；失败返回 None，绝不让它影响正常抽取。"""
    try:
        import sys as _sys
        _app = str(Path(__file__).parent / "app")
        if _app not in _sys.path:
            _sys.path.insert(0, _app)
        from docimg import DocImages
        d = DocImages(p)
        return d if d.ok() else None
    except Exception:
        return None


def _marker_line(p: Path) -> str:
    """文档末尾的图片清单行：[image1.png] [image2.jpeg] ...

    只留前几个：前端每次最多展示 4 张，多出来的标记只是噪声，
    还会把块撑大、稀释关键词。
    """
    d = _docimg(p)
    if not d:
        return ""
    names = d.all_names()[:_MAX_MARKS_DOC]
    if not names:
        return ""
    return " ".join(f"[{n}]" for n in names)


def extract_docx(p: Path) -> str:
    try:
        d = docx.Document(str(p))
        parts = []
        for para in d.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        for tbl in d.tables:
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.append(" | ".join(cells))
        txt = "\n".join(parts)
    except Exception:
        txt = ""
    marks = _marker_line(p)
    if marks:
        txt = (txt.rstrip() + "\n" + marks) if txt.strip() else marks
    if txt.strip():
        return _join_with_images(txt, p)
    # 文档无文本层(扫描/签字版),对嵌图 OCR
    try:
        import ocr_extract
        return ocr_extract.ocr_docx_images(p)
    except Exception:
        return ""


def extract_doc(p: Path) -> str:
    """提取 .doc。先试 antiword,空/失败则回退到纯 Python OLE 分片表解析。"""
    s = ""
    if ANTIWORD and os.path.exists(ANTIWORD):
        try:
            r = subprocess.run([ANTIWORD, str(p)], capture_output=True, timeout=120)
            s = r.stdout.decode("utf-8", "ignore") or r.stdout.decode("gbk", "ignore")
        except Exception:
            s = ""
    if s.strip():
        return s
    try:
        import doc_ole_extract as ole_ext
        return ole_ext.extract_ole(p.read_bytes())
    except Exception:
        return ""


def extract_pptx(p: Path) -> str:
    prs = Presentation(str(p))
    di = _docimg(p)
    pages = di.markers_pptx() if (di and di.ext == ".pptx") else {}
    parts = []
    for i, slide in enumerate(prs.slides):
        parts.append(f"[幻灯片 {i+1}]")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        parts.append(t)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        # 这一页上的图：架构图/界面截图都在这里，必须留下引用，前端才调得出来。
        # 关键是**别单独占一行** —— 一行一个标记会让 chunk 边界被图片撑开，
        # 还会产生"整块只有 [image5.jpeg]"这种垃圾块（踩过：评测 6/6 掉到 3/6）。
        # 所以紧贴在该页最后一句文字后面，且每页最多 2 个。
        got = pages.get(i + 1, [])[:_MAX_MARKS_PER_PAGE]
        if got:
            marks = " ".join(f"[{nm}]" for nm in got)
            if parts and parts[-1].strip():
                parts[-1] = parts[-1] + " " + marks
            else:
                parts.append(marks)
    return _join_with_images("\n".join(parts), p)


def extract_xlsx(p: Path) -> str:
    wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"[工作表: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            vals = [str(v).strip() if v is not None else "" for v in row]
            if any(vals):
                parts.append(" | ".join(vals))
    wb.close()
    txt = "\n".join(parts)
    marks = _marker_line(p)
    if marks:
        txt = (txt.rstrip() + "\n" + marks) if txt.strip() else marks
    return txt


def extract_xls_xlrd(p: Path) -> str:
    if not xlrd:
        return ""
    wb = xlrd.open_workbook(str(p))
    parts = []
    for ws in wb.sheets():
        parts.append(f"[工作表: {ws.name}]")
        for r in range(ws.nrows):
            vals = [str(ws.cell_value(r, c)).strip() for c in range(ws.ncols)]
            if any(vals):
                parts.append(" | ".join(vals))
    return "\n".join(parts)


def extract_pdf(p: Path) -> str:
    if not fitz:
        return ""
    text = ""
    try:
        doc = fitz.open(str(p))
        if doc.page_count == 0:
            doc.close()
            return ""
        for page in doc:
            text += page.get_text("text") + "\n"
        has_pages = doc.page_count > 0
        doc.close()
    except Exception:
        return ""
    if text.strip():
        return _join_with_images(text, p)
    if not has_pages:
        return ""
    # 无文本层(扫描件),整册 OCR
    try:
        import ocr_extract
        return ocr_extract.ocr_pdf(p)
    except Exception:
        return ""


def extract_txt(p: Path) -> str:
    for enc in ("utf-8", "gbk", "utf-8-sig", "gb18030"):
        try:
            return p.read_text(encoding=enc)
        except Exception:
            continue
    return ""


EXTRACTORS = {".docx": extract_docx, ".doc": extract_doc, ".pptx": extract_pptx,
              ".ppt": extract_pptx, ".xlsx": extract_xlsx, ".xls": extract_xls_xlrd,
              ".pdf": extract_pdf, ".txt": extract_txt, ".csv": extract_txt,
              ".json": extract_txt, ".md": extract_txt}


_IMG_TOKEN = re.compile(
    r"\[(?:插图\s*)?(?:image|media|img)[-_]?\d+\.(?:png|jpe?g|gif|bmp|webp|emf|wmf|tiff?)\]",
    re.I)


def _only_images(txt: str) -> bool:
    """这个块除了图片标记还有没有实质内容。

    纯标记块（长度为十几、内容就是 `[image5.jpeg]`）必须丢掉：
    它对检索毫无价值，却会被 BM25/向量当成正文，把真正的答案块挤下去
    （踩过：加了图片标记后评测从 6/6 掉到 3/6）。
    """
    rest = _IMG_TOKEN.sub("", txt or "").strip()
    return len(rest) < 15


def chunks(text: str):
    text = norm_text(text)
    if len(text) < 1:
        return []
    # 按行分块,尽量在段落边界切分
    lines = text.split("\n")
    cur = ""
    for ln in lines:
        if not ln.strip():
            continue
        if len(cur) + len(ln) > CHUNK_SIZE and cur:
            if not _only_images(cur):
                yield cur
            cur = ln
        else:
            cur += ("\n" + ln if cur else ln)
    if cur and not _only_images(cur):
        yield cur


def main():
    root = ROOT
    out_dir = OUT
    os.makedirs(out_dir, exist_ok=True)
    all_files = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if any(f.startswith(s) for s in SKIP_FILES):
                continue
            if f.endswith(tuple(SKIP_EXT)):
                continue
            all_files.append(Path(dp) / f)

    total_chunks = 0
    stats = {"files": 0, "indexed": 0, "skipped": 0, "parsed": 0, "failed": 0}
    dump_path = out_dir / "chunks.jsonl"
    manifest = {"built": "", "sources": [], "by_type": {}}
    failed_files = []  # 记录解析失败/为空的文件,便于追踪覆盖缺口

    with open(dump_path, "w", encoding="utf-8") as fh:
        for p in sorted(all_files):
            rel = p.relative_to(root).as_posix()
            ext = p.suffix.lower()
            stats["files"] += 1
            if ext not in EXTRACTORS:
                stats["skipped"] += 1
                continue
            try:
                text = EXTRACTORS[ext](p)
                if not text or not text.strip():
                    stats["failed"] += 1
                    failed_files.append({"path": rel, "ext": ext, "reason": "empty"})
                    continue
            except Exception as e:
                stats["failed"] += 1
                failed_files.append({"path": rel, "ext": ext, "reason": str(e)})
                print(f"  [FAIL] {rel}: {e}", file=sys.stderr)
                continue
            # 若文本明显过短(疑似抽取不全),记入 manifests 供人工复核
            if len(text) < 40:
                failed_files.append({"path": rel, "ext": ext, "reason": f"short({len(text)})"})
            stats["parsed"] += 1
            manifest["by_type"][ext] = manifest["by_type"].get(ext, 0) + 1
            doc_id = hashlib.md5(rel.encode("utf-8")).hexdigest()[:12]
            chunks_ = list(chunks(text))
            for i, c in enumerate(chunks_):
                rec = {"id": f"{doc_id}_{i:04d}", "path": rel, "chunk": i, "text": c}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                total_chunks += 1
                stats["indexed"] += 1
            manifest["sources"].append(
                {"path": rel, "ext": ext, "chunks": len(chunks_)})

    # 单独再算一次 sources chunks(上面已写,此处补统计)
    manifest["built"] = "ok"
    manifest["total_chunks"] = total_chunks
    manifest["failed_files"] = failed_files
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump({**stats, "total_chunks": total_chunks, "by_type": manifest["by_type"]},
                  fh, ensure_ascii=False, indent=2)
    print(f"完成: 文件 {stats['files']}, 解析 {stats['parsed']}, 跳过 {stats['skipped']}, "
          f"失败 {stats['failed']}, 分块 {total_chunks}")
    print(f"索引已输出到: {out_dir}")


if __name__ == "__main__":
    main()
