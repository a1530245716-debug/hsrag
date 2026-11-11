# -*- coding: utf-8 -*-
"""把 docs/项目智能问答助手-操作手册.md 转成排版好的 Word（.docx）。

用法：
  python tools/md_to_docx.py [输入.md] [输出.docx]
"""
import re
import sys
from pathlib import Path

from PIL import Image
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "项目智能问答助手-操作手册.md"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else SRC.with_suffix(".docx")

CONTENT_W = 16.6  # cm，A4 左右各 2.2cm 边距
FONT_CN = "宋体"
FONT_HEAD = "微软雅黑"
FONT_CODE = "Consolas"
BLUE = RGBColor(0x1F, 0x4E, 0x79)
GRAY = RGBColor(0x59, 0x59, 0x59)
LIGHT = RGBColor(0x6B, 0x72, 0x80)


def set_cn_font(run, name, size=None, bold=None, color=None, italic=None):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    if italic is not None:
        run.font.italic = italic


def shade(el, hexcolor):
    if hasattr(el, "_tc"):
        tcPr = el._tc.get_or_add_tcPr()
    elif hasattr(el, "_p"):
        tcPr = el._p.get_or_add_pPr()
    elif hasattr(el, "get_or_add_tcPr"):
        tcPr = el.get_or_add_tcPr()
    else:
        tcPr = el.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def para_border(p, edge="bottom", color="D9E2F3", size=8):
    pPr = p._p.get_or_add_pPr()
    borders = pPr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        pPr.append(borders)
    b = OxmlElement(f"w:{edge}")
    b.set(qn("w:val"), "single")
    b.set(qn("w:sz"), str(size))
    b.set(qn("w:space"), "4")
    b.set(qn("w:color"), color)
    borders.append(b)


def cell_margins(cell, top=50, bottom=50, left=100, right=100):
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for k, v in (("top", top), ("bottom", bottom), ("start", left), ("end", right)):
        e = OxmlElement(f"w:{k}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        mar.append(e)
    tcPr.append(mar)


def table_borders(table, color="BFBFBF"):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "4")
        e.set(qn("w:space"), "0")
        e.set(qn("w:color"), color)
        borders.append(e)
    tblPr.append(borders)


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    e = OxmlElement("w:tblHeader")
    e.set(qn("w:val"), "true")
    trPr.append(e)


def add_field(paragraph, instr):
    r1 = paragraph.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    r1._r.append(fld)
    r2 = paragraph.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    r2._r.append(it)
    r3 = paragraph.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    r3._r.append(sep)
    r4 = paragraph.add_run("…")
    r5 = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    r5._r.append(end)


def add_toc(paragraph):
    r = paragraph.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    r._r.append(fld)
    r = paragraph.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = ' TOC \\o "1-3" \\h \\z \\u '
    r._r.append(it)
    r = paragraph.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    r._r.append(sep)
    p = paragraph.add_run("右键此处 →「更新域」，生成目录")
    p.font.color.rgb = LIGHT
    p.font.size = Pt(9)
    r = paragraph.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    r._r.append(end)


def add_inline(p, text, size=None, color=None, bold_all=False, italic=False):
    """处理 **加粗**、`代码`，其余按普通文本。"""
    text = text.replace("<br>", "\n")
    token = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
    for part in token.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            r = p.add_run(part[2:-2])
            set_cn_font(r, FONT_CN, size=size, bold=True, color=color)
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            r = p.add_run(part[1:-1])
            set_cn_font(r, FONT_CODE, size=(size - 0.8) if size else 9.2, color=RGBColor(0xA3, 0x1D, 0x1D))
        else:
            r = p.add_run(part)
            set_cn_font(r, FONT_CN, size=size, bold=(True if bold_all else None), color=color)
            if italic:
                r.font.italic = True


def style_doc(doc):
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.2)
    sec.bottom_margin = Cm(2.2)
    sec.left_margin = Cm(2.2)
    sec.right_margin = Cm(2.2)
    sec.header_distance = Cm(1.2)
    sec.footer_distance = Cm(1.2)

    normal = doc.styles["Normal"]
    normal.font.name = FONT_CN
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT_CN)
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.45
    normal.paragraph_format.space_after = Pt(4)

    for name, size, before, after in (("Heading 1", 17, 16, 8),
                                      ("Heading 2", 14, 12, 6),
                                      ("Heading 3", 12, 10, 4)):
        st = doc.styles[name]
        st.font.name = FONT_HEAD
        st._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT_HEAD)
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = BLUE
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True

    for name in ("List Bullet", "List Bullet 2", "List Number"):
        try:
            st = doc.styles[name]
            st.font.name = FONT_CN
            st._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT_CN)
            st.font.size = Pt(10.5)
            st.paragraph_format.space_after = Pt(2)
        except KeyError:
            pass


def header_footer(doc):
    sec = doc.sections[0]
    sec.different_first_page_header_footer = True

    hp = sec.header.paragraphs[0]
    hp.paragraph_format.tab_stops.add_tab_stop(Cm(CONTENT_W), WD_ALIGN_PARAGRAPH.RIGHT)
    r = hp.add_run("项目智能问答助手 · 应届生使用手册")
    set_cn_font(r, FONT_HEAD, 9, color=LIGHT)
    hp.add_run("\t")
    r = hp.add_run("v2.0 · 2026-11-09")
    set_cn_font(r, FONT_HEAD, 9, color=LIGHT)
    para_border(hp, "bottom", "D9E2F3", 6)

    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run("第 ")
    set_cn_font(r, FONT_CN, 9, color=LIGHT)
    add_field(fp, "PAGE")
    r = fp.add_run(" 页 / 共 ")
    set_cn_font(r, FONT_CN, 9, color=LIGHT)
    add_field(fp, "NUMPAGES")
    r = fp.add_run(" 页")
    set_cn_font(r, FONT_CN, 9, color=LIGHT)


def cover(doc):
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("项目智能问答助手")
    set_cn_font(r, FONT_HEAD, 30, bold=True, color=BLUE)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("应 届 生 使 用 手 册")
    set_cn_font(r, FONT_HEAD, 22, bold=True, color=RGBColor(0x2E, 0x75, 0xB6))

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    r = p.add_run("面向应届生与新入职同事")
    set_cn_font(r, FONT_CN, 12, color=GRAY)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(28)
    para_border(p, "bottom", "2E75B6", 12)

    rows = [
        ("适用对象", "应届生、新入职同事、实习生"),
        ("文档版本", "v2.0"),
        ("更新日期", "2026-11-09"),
        ("系统形态", "内网 Web 服务，浏览器访问，无需安装客户端"),
        ("说明", "文中截图使用演示知识库，不代表任何真实客户资料"),
    ]
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    table_borders(t, "D9E2F3")
    t.columns[0].width = Cm(3.4)
    t.columns[1].width = Cm(10.6)
    for k, v in rows:
        row = t.add_row()
        c0, c1 = row.cells
        c0.width = Cm(3.4)
        c1.width = Cm(10.6)
        shade(c0, "EAF1F8")
        cell_margins(c0, 80, 80, 120, 120)
        cell_margins(c1, 80, 80, 120, 120)
        p0 = c0.paragraphs[0]
        r = p0.add_run(k)
        set_cn_font(r, FONT_HEAD, 9.5, bold=True, color=BLUE)
        p1 = c1.paragraphs[0]
        add_inline(p1, v, size=9.5)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def toc_page(doc):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(14)
    r = p.add_run("目  录")
    set_cn_font(r, FONT_HEAD, 19, bold=True, color=BLUE)

    p = doc.add_paragraph()
    add_toc(p)

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    r = p.add_run("提示：在 Word 中右键目录选择「更新域」，或按 F9 更新页码（本文件已设置为打开时自动更新）。")
    set_cn_font(r, FONT_CN, 9, color=LIGHT)

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_quote(doc, lines):
    text = " ".join(l.lstrip("> ").strip() for l in lines)
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(8)
    para_border(p, "left", "2E75B6", 18)
    shade(p._p, "F2F6FC")
    add_inline(p, text, size=9.8, color=RGBColor(0x33, 0x41, 0x55), italic=True)


def add_hr(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    para_border(p, "bottom", "E5E7EB", 6)


def add_code(doc, lines):
    t = doc.add_table(rows=1, cols=1)
    t.autofit = False
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.columns[0].width = Cm(CONTENT_W)
    cell = t.rows[0].cells[0]
    cell.width = Cm(CONTENT_W)
    shade(cell, "F7F7F7")
    table_borders(t, "E0E0E0")
    cell_margins(cell, 80, 80, 140, 140)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.05
    for i, line in enumerate(lines):
        if i:
            p = cell.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.05
        r = p.add_run(line if line else " ")
        set_cn_font(r, FONT_CODE, 8.6, color=RGBColor(0x1F, 0x29, 0x37))
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def disp_len(s):
    s = re.sub(r"[*`]", "", s)
    return sum(2 if ord(ch) > 127 else 1 for ch in s)


def add_table(doc, rows):
    if not rows:
        return
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    weights = [max(6, max(disp_len(r[i]) for r in rows)) for i in range(ncol)]
    total = sum(weights)
    widths = [max(1.9, CONTENT_W * w / total) for w in weights]
    scale = CONTENT_W / sum(widths)
    widths = [w * scale for w in widths]

    t = doc.add_table(rows=0, cols=ncol)
    t.autofit = False
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    table_borders(t, "BFBFBF")
    for ci, w in enumerate(widths):
        t.columns[ci].width = Cm(w)
    for ri, rowdata in enumerate(rows):
        row = t.add_row()
        if ri == 0:
            repeat_header(row)
        for ci, text in enumerate(rowdata):
            cell = row.cells[ci]
            cell.width = Cm(widths[ci])
            cell_margins(cell, 55, 55, 90, 90)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.25
            if ri == 0:
                shade(cell, "1F4E79")
                add_inline(p, text, size=9.3, color=RGBColor(0xFF, 0xFF, 0xFF), bold_all=True)
            else:
                if ri % 2 == 0:
                    shade(cell, "F2F6FA")
                add_inline(p, text, size=9.3)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def add_image(doc, alt, rel):
    path = (SRC.parent / rel).resolve()
    if not path.exists():
        print("missing image", path)
        return
    w, h = Image.open(path).size
    max_w, max_h = 15.8, 18.0
    width = max_w
    height = width * h / w
    if height > max_h:
        height = max_h
        width = height * w / h
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(3)
    p.add_run().add_picture(str(path), width=Cm(width), height=Cm(height))

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(10)
    r = cap.add_run(f"图 {add_image.n}  {alt}")
    add_image.n += 1
    set_cn_font(r, FONT_CN, 9, color=LIGHT, italic=True)


add_image.n = 1


def build():
    lines = SRC.read_text(encoding="utf-8").splitlines()
    doc = Document()
    style_doc(doc)
    header_footer(doc)
    cover(doc)
    toc_page(doc)

    i = 0
    first_h1 = True
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            add_code(doc, block)
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1 and first_h1:
                first_h1 = False
            else:
                doc.add_heading(text, level=min(level - 1, 3))
            i += 1
            continue

        if stripped == "---":
            add_hr(doc)
            i += 1
            continue

        m = re.match(r"^!\[(.*?)\]\((.*?)\)$", stripped)
        if m:
            add_image(doc, m.group(1), m.group(2))
            i += 1
            continue

        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                raw = lines[i].strip().strip("|")
                cells = [c.strip() for c in raw.split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells):
                    rows.append(cells)
                i += 1
            add_table(doc, rows)
            continue

        if stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip())
                i += 1
            add_quote(doc, block)
            continue

        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            depth = len(m.group(1).replace("\t", "    ")) // 2
            style = "List Bullet" if depth == 0 else "List Bullet 2"
            p = doc.add_paragraph(style=style)
            p.paragraph_format.left_indent = Cm(0.75 + 0.55 * depth)
            add_inline(p, m.group(2))
            i += 1
            continue

        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.95)
            p.paragraph_format.first_line_indent = Cm(-0.55)
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(f"{m.group(2)}. ")
            set_cn_font(r, FONT_CN, 10.5, bold=True, color=BLUE)
            add_inline(p, m.group(3))
            i += 1
            continue

        p = doc.add_paragraph()
        add_inline(p, stripped)
        i += 1

    # 打开时自动更新目录与页码
    settings = doc.settings.element
    uf = OxmlElement("w:updateFields")
    uf.set(qn("w:val"), "true")
    settings.append(uf)

    cp = doc.core_properties
    cp.title = "项目智能问答助手 · 应届生使用手册"
    cp.subject = "项目交付知识库问答系统操作指南"
    cp.author = "某公司 · 项目智能问答助手"
    cp.comments = "使用演示数据截图；配套 Markdown 版见 docs/项目智能问答助手-操作手册.md"

    doc.save(OUT)
    print("saved", OUT)


if __name__ == "__main__":
    build()
