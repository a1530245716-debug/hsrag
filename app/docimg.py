# -*- coding: utf-8 -*-
"""按文档排版顺序给出图片标记，让 chunk 里能留下 `[image1.png]` 这种引用。

为什么要自己解析 XML：
`python-docx` / `python-pptx` 都不暴露"第几段是哪张图"，只知道文档里有哪些图。
但 chunk 文本里的图片引用必须是**位置正确**的，否则用户问"配置界面长什么样"，
命中的块里挂着的是完全无关的另一张截图。

做法是直接读 OOXML：
  - docx: word/document.xml 里每个 `<a:blip r:embed="rIdN">` 按出现顺序
  - pptx: 每张 slideN.xml 里的 `<a:blip>`，按页分组
再通过 `*/_rels/*.rels` 把 rId 映射成 `media/imageN.png` 的真实文件名。

对外只暴露两个函数：
  p = DocImages(path)      # 解析一次，缓存
  p.markers_docx()         -> {段落序号: [图名]}  # docx
  p.markers_pptx()         -> {页内序号: [图名]}  # pptx（key 是从 1 开始的页号）
  p.all_names()            -> [图名]              # 该文档全部图片
"""
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# rId -> 目标文件（相对 zip 根），从 .rels 里读
_REL_RE = re.compile(rb'<Relationship[^>]*Id="(rId\d+)"[^>]*Target="([^"]+)"')

# docx: 按顺序找 blip 与段落边界
_DOCX_P_RE = re.compile(rb'<w:p[ >]')
_DOCX_BLIP_RE = re.compile(rb'<a:blip[^>]*r:embed="(rId\d+)"')
# pptx: 一页里的所有 blip
_PPTX_BLIP_RE = re.compile(rb'<a:blip[^>]*r:embed="(rId\d+)"')

_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".emf", ".wmf", ".tif", ".tiff"}


def _parse_rels(z: zipfile.ZipFile, rels_name: str):
    """读一份 .rels，返回 {rId: 'media/imageN.png'}（只看图片）。"""
    out = {}
    try:
        raw = z.read(rels_name)
    except Exception:
        return out
    base = rels_name.rsplit("_rels/", 1)[0]  # 例如 word/
    for m in _REL_RE.finditer(raw):
        rid = m.group(1).decode()
        tgt = m.group(2).decode()
        name = tgt.rsplit("/", 1)[-1]
        if Path(name).suffix.lower() in _IMG_EXT:
            out[rid] = name
    return out


class DocImages:
    """一次解析，多处查询。文件不存在或不是 zip 时全部返回空。"""

    def __init__(self, p):
        self.path = Path(p)
        self.ext = self.path.suffix.lower()
        self._rels = {}        # {rels文件: {rId: name}}
        self._docx = None      # [(段落序号, rId)]
        self._pptx = None      # {页号: [rId]}
        self._all = None       # [name]
        self._ok = False
        self._load()

    # ---------- 加载 ----------
    def _load(self):
        if self.ext not in {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm", ".dotx"}:
            return
        try:
            with zipfile.ZipFile(self.path) as z:
                names = z.namelist()
                if self.ext in {".docx", ".docm", ".dotx"}:
                    self._load_docx(z, names)
                elif self.ext in {".pptx", ".pptm"}:
                    self._load_pptx(z, names)
                elif self.ext in {".xlsx", ".xlsm"}:
                    self._load_xlsx(z, names)
            self._ok = True
        except Exception:
            self._ok = False
            self._docx = None
            self._pptx = None
            self._all = None

    def _load_docx(self, z, names):
        rels = _parse_rels(z, "word/_rels/document.xml.rels")
        self._rels["docx"] = rels
        try:
            xml = z.read("word/document.xml")
        except Exception:
            return
        # 按 <w:p 切段：每段里出现的图片记到该段的序号上。
        # 注意这里的段号是"XML 里的 <w:p> 顺序"，与 python-docx 的 d.paragraphs
        # 不完全一致（后者不含表格单元格内的段落），所以只用来排顺序，不做精确对齐。
        seq, last, para_idx = [], 0, 0
        for m in _DOCX_P_RE.finditer(xml):
            seg = xml[last:m.start()]
            for b in _DOCX_BLIP_RE.finditer(seg):
                nm = rels.get(b.group(1).decode())
                if nm:
                    seq.append((para_idx, nm))
            para_idx += 1
            last = m.start()
        for b in _DOCX_BLIP_RE.finditer(xml[last:]):
            nm = rels.get(b.group(1).decode())
            if nm:
                seq.append((para_idx, nm))

        # 页眉/页脚里的图（签字版文档的公司 logo 常在这里），单独收进来
        seen = {n for _, n in seq}
        for n in sorted(names):
            m2 = re.match(r"word/(header|footer)\d*\.xml$", n)
            if not m2:
                continue
            rel_name = f"word/_rels/{n.rsplit('/', 1)[-1]}.rels"
            hrels = _parse_rels(z, rel_name)
            try:
                hx = z.read(n)
            except Exception:
                continue
            for b in _DOCX_BLIP_RE.finditer(hx):
                nm = hrels.get(b.group(1).decode())
                if nm and nm not in seen:
                    seen.add(nm)
                    seq.append((para_idx, nm))   # 归到文档末尾
        self._docx = seq
        self._all = [n for _, n in seq]

    def _load_pptx(self, z, names):
        rels_map = {}
        for n in names:
            if n.startswith("ppt/slides/_rels/") and n.endswith(".rels"):
                k = n.rsplit("/", 1)[-1].replace(".xml.rels", "")
                rels_map[k] = _parse_rels(z, n)
        pages = {}
        allnames = []
        for n in sorted(names):
            m = re.match(r"ppt/slides/slide(\d+)\.xml$", n)
            if not m:
                continue
            page = int(m.group(1))
            rels = rels_map.get(f"slide{page}", {})
            try:
                xml = z.read(n)
            except Exception:
                continue
            got = []
            for b in _PPTX_BLIP_RE.finditer(xml):
                nm = rels.get(b.group(1).decode())
                if nm:
                    got.append(nm)
                    allnames.append(nm)
            if got:
                pages[page] = got
        self._rels["pptx"] = rels_map
        self._pptx = pages
        self._all = allnames

    def _load_xlsx(self, z, names):
        # xlsx 的图挂在 drawing 上，位置更难还原；这里只收集全部图片名
        allnames = []
        for n in names:
            if n.startswith("xl/media/") and Path(n).suffix.lower() in _IMG_EXT:
                allnames.append(n.rsplit("/", 1)[-1])
        self._all = sorted(allnames)

    # ---------- 查询 ----------
    def all_names(self):
        return list(self._all or [])

    def markers_docx(self):
        """[(段落出现序号, 图名)]，顺序即文档顺序。"""
        return list(self._docx or [])

    def markers_pptx(self):
        """{页号: [图名]}"""
        return dict(self._pptx or {})

    def ok(self):
        return bool(self._ok)
