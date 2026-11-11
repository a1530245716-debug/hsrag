# -*- coding: utf-8 -*-
"""文档内嵌图片的按需提取。

chunk 文本里的 `[image1.png]` 是文档内部的图片编号。对 docx/pptx/xlsx（本质是 zip）
就是 `word/media/image1.png` / `ppt/media/image1.png` / `xl/media/image1.png`。
所以只要知道 chunk 属于哪份文档，就能按编号把原图取出来。

策略是**按需提取 + 落盘缓存**：第一次访问某张图时从原文档解压出来存到
`app/data/<kb_id>/images/<文档路径md5>/<图片名>`，之后直接读缓存。
这样不用重建索引，也不用预先解压几万张图占满磁盘。
"""
import hashlib
import json
import os
import re
import threading
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_DATA = _ROOT / "data"

# chunk 文本里的图片标记： [image1.png] / [image2.jpeg] / [插图 image3.png]
_IMG_REF = re.compile(r"\[(?:插图\s*)?((?:image|media|img)[-_]?\d+\.(?:png|jpe?g|gif|bmp|webp|emf|wmf|tiff?))\]",
                      re.I)

# 不是 zip 的格式取不出图（老 .doc、.pdf 走别的路径）
_ZIP_DOC_EXT = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm", ".dotx"}
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".emf", ".wmf", ".tif", ".tiff"}
_MEDIA_PREFIX = {"word/media/", "ppt/media/", "xl/media/"}  # 兼容不同 office 部件

_lock = threading.Lock()
_kb_cache = {}   # kb_id -> 文档目录（Path）


def _kbs():
    """registry.json 是个 list，每项有 id / folder。"""
    try:
        raw = json.loads((_DATA / "registry.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return raw
    return raw.get("kbs") or raw.get("knowledge_bases") or []


def kb_folders(kb_id: str):
    """知识库的文档根目录列表（按查找优先级）。

    一个库的文档可能来自两个地方：
      1. 源文件夹（folder，可用 | 分隔多个）
      2. 拖拽上传目录（app/data/<kb_id>/upload）—— **纯上传型知识库只有这个**，
         之前这里漏了，导致某资管的图一张都取不出来。
    """
    if kb_id in _kb_cache:
        return _kb_cache[kb_id]
    roots = []
    for k in _kbs():
        if k.get("id") == kb_id:
            for r in str(k.get("folder") or "").split("|"):
                if r.strip():
                    p = Path(r.strip())
                    if p.is_dir():
                        roots.append(p)
            break
    up = _DATA / kb_id / "upload"
    if up.is_dir():
        roots.append(up)
    _kb_cache[kb_id] = roots
    return roots


def kb_folder(kb_id: str):
    """源文件夹（兼容旧调用）。纯上传型知识库返回 None。"""
    roots = kb_folders(kb_id)
    for r in roots:
        if r.name != "upload" or r.parent.name != kb_id:
            return r
    return roots[0] if roots else None


def doc_full_path(kb_id: str, doc_rel_path: str):
    """把 chunk 里的相对路径拼成磁盘绝对路径，并做目录逃逸检查。"""
    rel = (doc_rel_path or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    for root in kb_folders(kb_id):
        try:
            full = (root / rel).resolve()
            full.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        if full.is_file():
            return full
    return None


def images_in_text(text: str):
    """从 chunk 文本里抽出它引用了哪些图（保持出现顺序、去重）。"""
    seen, out = set(), []
    for m in _IMG_REF.finditer(text or ""):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def list_doc_media(full: Path):
    """列出文档 zip 里所有媒体文件，返回 {小写文件名: (zip内完整路径, 解压后字节数)}。"""
    if not full or full.suffix.lower() not in _ZIP_DOC_EXT:
        return {}
    out = {}
    try:
        with zipfile.ZipFile(full) as z:
            for info in z.infolist():
                n = info.filename
                if not any(n.startswith(pre) for pre in _MEDIA_PREFIX):
                    continue
                if Path(n).suffix.lower() in _IMG_EXT:
                    out.setdefault(n.rsplit("/", 1)[-1].lower(), (n, info.file_size))
    except Exception:
        return {}
    return out


def _cache_path(kb_id: str, full: Path, name: str):
    key = hashlib.md5(str(full).encode("utf-8")).hexdigest()[:16]
    d = _DATA / kb_id / "images" / key
    return d / name.lower()


# ---------------- EMF/WMF → PNG ----------------
# 全库统计：emf 116 张、wmf 3 张。这些是 Windows 矢量图（架构图、流程图居多，
# 恰恰是最有价值的一类），但 Chrome/Edge 都不渲染。Windows 自带的 GDI+ 能读，
# 借 PowerShell 转成 PNG 后缓存 —— 每张只转一次，之后走缓存秒回。
_PS_EMF = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$src = '{SRC}'; $dst = '{DST}'
$img = [System.Drawing.Image]::FromFile($src)
try {
  $w = [Math]::Max($img.Width, 1); $h = [Math]::Max($img.Height, 1)
  # 限制最大边，避免个别超大矢量图把内存撑爆
  $max = 2400
  if ($w -gt $max -or $h -gt $max) {
    $k = $max / [Math]::Max($w, $h)
    $w = [Math]::Max([int]($w * $k), 1); $h = [Math]::Max([int]($h * $k), 1)
  }
  $bmp = New-Object System.Drawing.Bitmap($w, $h)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.Clear([System.Drawing.Color]::White)
  $g.InterpolationMode = 'HighQualityBicubic'
  $g.DrawImage($img, 0, 0, $w, $h)
  $bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
} finally { $img.Dispose() }
"""


def _emf_to_png(raw: bytes, dst: Path):
    """把 EMF/WMF 字节渲染成 PNG 写到 dst。成功返回 True。"""
    import subprocess
    import tempfile
    src = None
    try:
        fd, src_name = tempfile.mkstemp(suffix=dst.suffix[:4] or ".emf")
        os.close(fd)
        src = Path(src_name)
        src.write_bytes(raw)
        dst.parent.mkdir(parents=True, exist_ok=True)
        script = _PS_EMF.replace("{SRC}", str(src).replace("'", "''")) \
                        .replace("{DST}", str(dst).replace("'", "''"))
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return dst.is_file() and dst.stat().st_size > 0
    except Exception:
        return False
    finally:
        try:
            if src:
                src.unlink(missing_ok=True)
        except Exception:
            pass


# 需要先转成位图才能给浏览器看的矢量格式
_VECTOR_EXT = {".emf", ".wmf"}


def extract(kb_id: str, doc_rel_path: str, img_name: str):
    """取出单张图的字节。返回 (bytes, 后缀) 或 (None, None)。

    先看缓存目录，没有再回原文档 zip 里解压一份存进去。
    EMF/WMF 会顺手转成 PNG（缓存成 xxx.emf.png），这样前端拿到的永远是浏览器认识的格式。
    """
    full = doc_full_path(kb_id, doc_rel_path)
    if not full:
        return None, None

    name = (img_name or "").strip().rsplit("/", 1)[-1]
    if not name or Path(name).suffix.lower() not in _IMG_EXT:
        return None, None

    cp = _cache_path(kb_id, full, name)
    is_vec = cp.suffix.lower() in _VECTOR_EXT
    # 矢量图的成品是转出来的 PNG
    png_cp = cp.with_name(cp.name + ".png") if is_vec else None

    with _lock:
        if png_cp and png_cp.is_file() and png_cp.stat().st_size > 0:
            return png_cp.read_bytes(), ".png"
        if cp.is_file() and cp.stat().st_size > 0:
            if not is_vec:
                return cp.read_bytes(), cp.suffix
            raw = cp.read_bytes()
            if _emf_to_png(raw, png_cp):
                return png_cp.read_bytes(), ".png"
            return None, None

    media = list_doc_media(full)
    hit = media.get(name.lower())
    if not hit:
        return None, None
    zpath = hit[0]

    try:
        with zipfile.ZipFile(full) as z:
            data = z.read(zpath)
    except Exception:
        return None, None
    if not data:
        return None, None

    with _lock:
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            tmp = cp.with_suffix(cp.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, cp)
        except Exception:
            pass

    if is_vec:
        # 首次遇到这张矢量图：当场转成 PNG 并缓存，之后直接走上面的缓存分支
        if _emf_to_png(data, png_cp):
            return png_cp.read_bytes(), ".png"
        return None, None
    return data, cp.suffix


# 能让浏览器看到的格式。emf/wmf 本身浏览器不认，但 extract() 会当场转成 PNG，
# 所以这里算"可展示"，只是排序时排后面（转换要 200ms，别让它抢占首屏）。
# wdp/tif 没有转换路径，直接排除。
_BROWSER_OK = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".emf", ".wmf"}

# 小于这个大小的基本是图标/装饰线，不当截图展示
_MIN_IMG_BYTES = 8 * 1024

# 光看字节数不够：Excel 里那种条件格式的小红旗/红三角，转出来也可能有十几 KB，
# 但它们没有任何信息量，放进引用区纯占地方。这里再按**像素尺寸**兜一道。
_MIN_IMG_EDGE = 120        # 最长边小于这个 → 当图标丢掉
_DIM_CACHE = {}            # (文档指纹, 图片名) -> (w, h) 或 None


def _doc_key(full: Path) -> str:
    return hashlib.md5(str(full).encode("utf-8", "replace")).hexdigest()[:16]


def _dims_of(full: Path, names):
    """一次开 zip 批量取像素尺寸。返回 {图片名: (w,h) 或 None}。

    None 表示解析不出来（矢量图、损坏头），那种情况**保留**，
    宁可多显示一张，也别把架构图误杀。
    """
    out = {}
    if not names:
        return out
    key = _doc_key(full)
    with _lock:
        for n in names:
            if (key, n.lower()) in _DIM_CACHE:
                out[n] = _DIM_CACHE[(key, n.lower())]
    missing = [n for n in names if n not in out]
    if not missing:
        return out
    media = list_doc_media(full)
    try:
        from PIL import Image
        import io
        with zipfile.ZipFile(full) as z:
            for n in missing:
                hit = media.get(n.lower())
                wh = None
                if hit:
                    try:
                        with z.open(hit[0]) as fh:
                            raw = fh.read()
                        with Image.open(io.BytesIO(raw)) as im:
                            wh = im.size
                    except Exception:
                        wh = None
                out[n] = wh
                with _lock:
                    _DIM_CACHE[(key, n.lower())] = wh
    except Exception:
        for n in missing:
            out.setdefault(n, None)
    return out


def doc_images(kb_id: str, doc_rel_path: str, only_names=None, limit=12):
    """一份文档里可展示的图片，供引用区显示缩略图。

    这里**只确认图片存在于文档里并给出 URL**，不读取字节 —— 真正解压交给
    `/api/kb/<id>/img` 那个端点，浏览器加载到哪张才解哪张。
    only_names 通常是 chunk 文本里引用到的那几张。
    """
    full = doc_full_path(kb_id, doc_rel_path)
    if not full:
        return []
    media = list_doc_media(full)
    if not media:
        return []

    if only_names:
        names = [n for n in only_names if n.lower() in media]
    else:
        names = sorted(media.keys())

    # 过滤掉两类没展示价值的图：
    #   1) 浏览器不认的格式（emf/wmf/wdp/tif）—— 塞进去只会显示破图
    #   2) 太小的图（图标、项目符号、分隔线），缩略图里就是个小点
    def _usable(n):
        if Path(n).suffix.lower() not in _BROWSER_OK:
            return False
        size = (media.get(n.lower()) or ("", 0))[1]
        return size >= _MIN_IMG_BYTES

    names = [n for n in names if _usable(n)]

    # 图标过滤，两道：
    #  1) 尺寸极小的小红旗/红三角之类
    #  2) 大画布但几乎没有内容：周报 xlsx 里那种 2500x2500、整片底色、
    #     只压了几 KB 的图标（字节/像素低到 0.007，正常截图是 0.05~1.0）
    dims = _dims_of(full, [n for n in names if Path(n).suffix.lower() not in _VECTOR_EXT])

    def _not_icon(n):
        wh = dims.get(n)
        if not wh:
            return True                     # 解析不了就保留，宁可多显示
        w, h = wh
        if max(w, h) < _MIN_IMG_EDGE:
            return False
        size = (media.get(n.lower()) or ("", 0))[1]
        if w * h >= 1_000_000 and size / float(w * h) < 0.02:
            return False                    # 大画布 + 压得极少 = 纯色底加个小图标
        return True

    names = [n for n in names if _not_icon(n)]

    def _rank(n):
        e = Path(n).suffix.lower()
        # 位图优先（打开即显示），矢量图排后面（要现转一次 PNG）
        tier = 0 if e in {".png", ".jpg", ".jpeg"} else (2 if e in _VECTOR_EXT else 1)
        return (tier, -((media.get(n.lower()) or ("", 0))[1]))

    names = sorted(names, key=_rank)[:limit]

    return [{
        "name": n,
        "url": f"/api/kb/{kb_id}/img?path={_quote(doc_rel_path)}&name={_quote(n)}",
    } for n in names]


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".emf": "image/emf", ".wmf": "image/wmf",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


def mime_of(ext: str) -> str:
    return MIME.get((ext or "").lower(), "application/octet-stream")
