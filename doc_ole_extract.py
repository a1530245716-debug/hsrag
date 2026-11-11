# -*- coding: utf-8 -*-
"""纯 Python 提取 Word97+ 二进制 .doc 文本(针对 WPS 生成的复杂格式).
原理: 在表格流(0Table/1Table)中扫描合法的 CLX 分片表(Pcdt),再按分片从
WordDocument 流抽取文本。非压缩分片用 UTF-16,压缩分片优先 GBK 再回退 cp1252。
不依赖 antiword / LibreOffice。
"""
import io, struct

try:
    import olefile
except Exception:
    olefile = None


def _u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def _decode_piece(word, fc, fcomp, nchar):
    if nchar <= 0:
        return ""
    if fcomp:
        seg = word[fc + 1: fc + 1 + nchar]  # 压缩分片前有 1 个填充字节
        for enc in ("gbk", "cp1252"):
            try:
                return seg.decode(enc)
            except Exception:
                continue
        return ""
    seg = word[fc: fc + nchar * 2]
    try:
        return seg.decode("utf-16-le", "ignore")
    except Exception:
        return ""


def _decode_pcdt(tbl, wlen, word, lcb_off):
    """读取并解码一个 Pcdt。失败返回 None。"""
    lcb = _u32(tbl, lcb_off)
    n = (lcb - 4) // 12
    if not (1 <= n <= 200000):
        return None
    cp_off = lcb_off + 4
    pcd_off = cp_off + (n + 1) * 4
    cps = [_u32(tbl, cp_off + i * 4) for i in range(n + 1)]
    if any(cps[i] >= cps[i + 1] for i in range(n)):
        return None
    out = []
    for i in range(n):
        fcraw = _u32(tbl, pcd_off + i * 8)
        fc = fcraw & 0x3FFFFFFF
        if fc >= wlen:
            return None
        out.append(_decode_piece(word, fc, (fcraw >> 30) & 1, cps[i + 1] - cps[i]))
    return "".join(out)


def extract_ole(content: bytes) -> str:
    if olefile is None:
        return ""
    try:
        ole = olefile.OleFileIO(io.BytesIO(content))
    except Exception:
        return ""
    try:
        if not ole.exists("WordDocument"):
            return ""
        word = ole.openstream("WordDocument").read()
        tbl = None
        for name in ("0Table", "1Table"):
            if ole.exists(name):
                tbl = ole.openstream(name).read()
                break
        if not tbl:
            return ""
        wlen = len(word)
        lent = len(tbl)
        best, best_score = None, -1.0
        for off in range(max(0, lent - 8)):
            lcb = _u32(tbl, off)
            if lcb <= 0 or (lcb - 4) % 12 != 0:
                continue
            if off + 4 + lcb > lent:
                continue
            try:
                text = _decode_pcdt(tbl, wlen, word, off)
            except Exception:
                continue
            if not text:
                continue
            if not any('一' <= ch <= '鿿' for ch in text):
                continue  # 只接受含中文的结果
            printable = sum(1 for ch in text if ch.isprintable()) / max(len(text), 1)
            score = printable + (0.5 if len(text) > 800 else 0) + (n_ch := len(text) / 1000000.0)
            if printable > 0.55 and score > best_score:
                best_score, best = score, text
        return best or ""
    finally:
        ole.close()
