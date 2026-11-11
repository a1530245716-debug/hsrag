# -*- coding: utf-8 -*-
"""把 Office 文件渲染成**保留排版和图片**的 HTML，供预览页使用。

原来的预览是把抽取出来的纯文本塞进 <pre>：没有分段、没有表格线、图片全丢，
用户看到的就是一坨糊在一起的字。这里改成按文档原本的结构渲染：

- docx: 按 body 顺序走段落/表格；标题用原样式层级；跑内保留粗体/斜体；
        run 里的 a:blip（DrawingML 图片）和 v:imagedata（VML 图片）就地输出
- pptx: 一页一节，标题+正文分段，图片就地输出
- xlsx: 一个 sheet 一张表，首行当表头

图片**不内联 base64**（一份 79 张图的 docx 会撑出十几 MB 的 HTML），
而是引用 `/api/kb/<id>/img`，由 imgserve 按需从文档里解出来并落盘缓存，
EMF/WMF 也走同一条路自动转 PNG。
"""
import html as _html
from pathlib import Path
from urllib.parse import quote

# 直接用完整命名空间，省得 docx/pptx 两套 qn 混用
_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_R_LINK = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}link"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_V_IMAGEDATA = "{urn:schemas-microsoft-com:vml}imagedata"
_W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
_W_TBL = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl"

_MAX_XLSX_ROWS = 300
_MAX_XLSX_COLS = 40


def _esc(s):
    return _html.escape(str(s if s is not None else ""))


def _img_url(kb_id, doc_path, name):
    return (f"/api/kb/{quote(kb_id, safe='')}/img"
            f"?path={quote(doc_path, safe='')}&name={quote(name, safe='')}")


def _rid_name(part, rid):
    """rId -> 包内媒体文件名（如 image7.png）。

    python-docx 用 part.related_parts，python-pptx 只有 part.rels[...].target_part，
    两边都得试一次，否则 pptx 里的图一张都取不到。
    """
    if not rid:
        return ""
    try:
        return Path(str(part.related_parts[rid].partname)).name
    except Exception:
        pass
    try:
        return Path(str(part.rels[rid].target_part.partname)).name
    except Exception:
        return ""


def _img_tag(kb_id, doc_path, name, extra=""):
    if not name:
        return ""
    return (f"<img src='{_img_url(kb_id, doc_path, name)}' alt='{_esc(name)}' "
            f"loading='lazy'{extra} title='{_esc(name)}（点击看原图）'>")


def render(fp, kb_id, doc_path):
    """返回 (kind, content_html)。不支持的类型抛 ValueError。"""
    ext = fp.suffix.lower()
    if ext in (".docx", ".docm", ".dotx"):
        return "office", _docx(fp, kb_id, doc_path)
    if ext in (".pptx", ".pptm", ".potx"):
        return "office", _pptx(fp, kb_id, doc_path)
    if ext in (".xlsx", ".xlsm", ".xltx"):
        return "office", _xlsx(fp, kb_id, doc_path)
    raise ValueError(f"docpreview 不支持 {ext}")


# ---------------------------------------------------------------- docx
def _docx(fp, kb_id, doc_path):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(fp))
    chunks = []

    def para_html(p):
        lvl = _heading_level(p)
        pieces = []
        # 图在 run 里：先出图，再出文字（图常单独占一段）
        for r in p.runs:
            for blip in r._r.iter(_A_BLIP):
                name = _rid_name(p.part, blip.get(_R_EMBED) or blip.get(_R_LINK))
                pieces.append(_img_tag(kb_id, doc_path, name))
            for vml in r._r.iter(_V_IMAGEDATA):
                pieces.append(_img_tag(kb_id, doc_path, _rid_name(p.part, vml.get(_R_ID))))
            t = r.text
            if not t:
                continue
            t = _esc(t)
            if r.bold:
                t = f"<b>{t}</b>"
            if r.italic:
                t = f"<i>{t}</i>"
            if r.underline:
                t = f"<u>{t}</u>"
            pieces.append(t)

        inner = "".join(pieces).strip()
        if not inner:
            return ""
        if lvl:
            n = min(lvl + 1, 6)          # 文档里的 Heading 1 → 页面 h2
            return f"<h{n}>{inner}</h{n}>"
        if _is_list(p):
            return f"<div class='li'>• {inner}</div>"
        return f"<p>{inner}</p>"

    def table_html(tb):
        rows = []
        for ri, row in enumerate(tb.rows):
            tds = []
            for cell in row.cells:
                txt = "<br>".join(_esc(pp.text) for pp in cell.paragraphs if pp.text.strip())
                # 单元格里的图也要出
                imgs = []
                for pp in cell.paragraphs:
                    for blip in pp._p.iter(_A_BLIP):
                        imgs.append(_img_tag(kb_id, doc_path,
                                             _rid_name(pp.part, blip.get(_R_EMBED) or blip.get(_R_LINK))))
                tag = "th" if ri == 0 else "td"
                tds.append(f"<{tag}>{txt}{''.join(imgs)}</{tag}>")
            if tds:
                rows.append("<tr>" + "".join(tds) + "</tr>")
        if not rows:
            return ""
        return "<div class='tw'><table>" + "".join(rows) + "</table></div>"

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == _W_P:
            chunks.append(para_html(Paragraph(child, doc)))
        elif child.tag == _W_TBL:
            chunks.append(table_html(Table(child, doc)))

    out = "".join(c for c in chunks if c)
    if not out.strip():
        raise ValueError("文档里没解析出内容")
    return f"<div class='dp dp-docx'>{out}</div>"


def _heading_level(p):
    try:
        nm = (p.style.name or "").strip()
    except Exception:
        return 0
    low = nm.lower()
    if low.startswith("heading") or nm.startswith("标题"):
        digits = "".join(ch for ch in nm if ch.isdigit())
        try:
            return int(digits) if digits else 1
        except Exception:
            return 1
    if low in ("title", "标题"):
        return 1
    return 0


def _is_list(p):
    try:
        nm = (p.style.name or "").lower()
    except Exception:
        return False
    return nm.startswith("list") or nm.startswith("列表")


# ---------------------------------------------------------------- pptx
def _walk_shapes(shapes):
    """递归展开组合(组合里的图和文字不展开就全丢)。"""
    for sh in shapes:
        yield sh
        inner = getattr(sh, "shapes", None)
        if inner is not None:
            try:
                yield from _walk_shapes(inner)
            except Exception:
                continue


def _pptx(fp, kb_id, doc_path):
    from pptx import Presentation

    prs = Presentation(str(fp))
    out = []
    for i, slide in enumerate(prs.slides, 1):
        title_shape = None
        try:
            title_shape = slide.shapes.title
        except Exception:
            title_shape = None
        title = ""
        if title_shape is not None:
            try:
                title = (title_shape.text or "").strip()
            except Exception:
                title = ""

        body = []
        first_line = ""
        first_idx = -1
        for sh in _walk_shapes(slide.shapes):
            if title_shape is not None and sh is title_shape:
                continue
            # 表格
            if getattr(sh, "has_table", False):
                try:
                    rows = []
                    for ri, row in enumerate(sh.table.rows):
                        tag = "th" if ri == 0 else "td"
                        rows.append("<tr>" + "".join(
                            f"<{tag}>{_esc(c.text)}</{tag}>" for c in row.cells) + "</tr>")
                    if rows:
                        body.append("<div class='tw'><table>" + "".join(rows) + "</table></div>")
                except Exception:
                    pass
            # 文字
            if getattr(sh, "has_text_frame", False):
                try:
                    for para in sh.text_frame.paragraphs:
                        t = "".join(r.text for r in para.runs).strip()
                        if not t:
                            continue
                        if not first_line:
                            first_line = t
                            first_idx = len(body)
                        lv = getattr(para, "level", 0) or 0
                        body.append(f"<div class='li' style='margin-left:{6 + lv * 14}px'>{_esc(t)}</div>")
                except Exception:
                    pass
            # 图片（组合里的也走这里，blip 只看元素树）
            try:
                for blip in sh._element.iter(_A_BLIP):
                    name = _rid_name(slide.part, blip.get(_R_EMBED) or blip.get(_R_LINK))
                    tag = _img_tag(kb_id, doc_path, name)
                    if tag and tag not in body:
                        body.append(tag)
            except Exception:
                pass

        # 没有标题占位符的页：用第一行文字当标题，别让整页看起来没头没尾
        if not title and first_line:
            title = first_line
            if 0 <= first_idx < len(body):
                body.pop(first_idx)

        out.append(f"<section class='slide'><div class='sno'>第 {i} 页</div>")
        if title:
            out.append(f"<h3>{_esc(title)}</h3>")
        out.extend(body)
        out.append("</section>")

    if len(out) <= 1:
        raise ValueError("演示文稿里没解析出内容")
    return f"<div class='dp dp-pptx'>{''.join(out)}</div>"


# ---------------------------------------------------------------- xlsx
def _xlsx(fp, kb_id, doc_path):
    import openpyxl

    wb = openpyxl.load_workbook(str(fp), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        rows_html = []
        truncated = False
        for n, row in enumerate(ws.iter_rows(values_only=True), 1):
            if n > _MAX_XLSX_ROWS:
                truncated = True
                break
            cells = ["" if c is None else str(c) for c in row[:_MAX_XLSX_COLS]]
            while cells and not cells[-1].strip():
                cells.pop()
            if not cells:
                continue
            tag = "th" if n == 1 else "td"
            rows_html.append("<tr>" + "".join(
                f"<{tag}>{_esc(c)}</{tag}>" for c in cells) + "</tr>")
        if not rows_html:
            continue
        if truncated:
            rows_html.append(f"<tr><td colspan='{_MAX_XLSX_COLS}'>"
                             f"…（仅显示前 {_MAX_XLSX_ROWS} 行，完整内容请下载原文件）</td></tr>")
        out.append(f"<h3>{_esc(ws.title)}</h3><div class='tw'><table>{''.join(rows_html)}</table></div>")

    if not out:
        raise ValueError("工作簿里没解析出内容")
    return f"<div class='dp dp-xlsx'>{''.join(out)}</div>"
