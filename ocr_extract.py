# -*- coding: utf-8 -*-
"""对扫描型 PDF / Docx(截图/图片)做中文 OCR,供 index_docs.py 作为文本抽取兜底。
- ocr_pdf(path):       将 PDF 每页渲染为图像并 OCR(rgb 扫描件)。
- ocr_docx_images(path):解包 docx,对其 word/media 下的嵌图 OCR(如"签字版"扫描报告)。
- 依赖: rapidocr_onnxruntime, PyMuPDF(fitz), Pillow, opencv。
"""
import io, zipfile, threading
from pathlib import Path

ENGINE = None
_LOCK = threading.Lock()


def _engine():
    global ENGINE
    if ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        ENGINE = RapidOCR()
    return ENGINE


def _pad_narrow(img, target_ratio=0.4):
    """上下补白边，把过扁的截图（如单行输入框）补到正常宽高比。

    RapidOCR 对 1271×105 这类窄条会整张漏检，放大无效；补白边实测有效。
    """
    try:
        import numpy as np
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return img
        want_h = int(w * target_ratio)
        if h < want_h:
            pad = want_h - h
            top, bottom = pad // 2, pad - pad // 2
            img = np.pad(img, ((top, bottom), (0, 0), (0, 0)), constant_values=255)
    except Exception:
        pass
    return img


def ocr_array(img):
    """OCR 一个 numpy(BGR/HWC)图像,返回文本字符串。"""
    out = []
    try:
        engine = _engine()
        result, _ = engine(img)
        if result:
            out.append("\n".join(it[1] for it in result))
    except Exception:
        pass
    return "\n".join(out).strip()


def ocr_pdf(path: Path) -> str:
    try:
        import fitz
        import numpy as np
    except Exception:
        return ""
    parts = []
    try:
        doc = fitz.open(str(path))
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            elif pix.n == 1:
                img = np.repeat(img[:, :, :1], 3, axis=2)
            # PyMuPDF 给的是 RGB；RapidOCR 按 OpenCV 惯例要 BGR
            img = np.ascontiguousarray(img[:, :, ::-1])
            img = _pad_narrow(img)
            t = ocr_array(img)
            if t:
                parts.append(f"[第{page.number + 1}页]" + t)
        doc.close()
    except Exception:
        return ""
    return "\n".join(parts)


def ocr_docx_images(path: Path) -> str:
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return ""
    parts = []
    try:
        with zipfile.ZipFile(str(path)) as z:
            medias = {n for n in z.namelist()
                      if n.startswith("word/media/") and n.rsplit(".", 1)[-1].lower()
                          in {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "gif", "webp"}}
            ocr_images = [n for n in sorted(medias) if "wps" not in n.lower()]
            for n in ocr_images:
                data = z.read(n)
                try:
                    img = np.array(Image.open(io.BytesIO(data)).convert("RGB"))
                except Exception:
                    continue
                # PIL 是 RGB；RapidOCR 要 BGR
                img = np.ascontiguousarray(img[:, :, ::-1])
                img = _pad_narrow(img)
                t = ocr_array(img)
                if t:
                    parts.append(f"[{Path(n).name}]" + t)
    except Exception:
        return ""
    return "\n".join(parts)
