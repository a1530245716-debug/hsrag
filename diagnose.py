# -*- coding: utf-8 -*-
"""扫描源文件,尝试抽取,报告失败文件及原因。用于排查 parse 失败的文件。
用法: python diagnose.py
"""
import os, sys, glob, traceback
from pathlib import Path

ROOT = Path(os.environ.get("RAG_ROOT") or os.getcwd())
SKIP_EXT = {".vsdx", ".rar", ".rp", ".lic", ".cfg", ".js", ".html", ".jpg", ".jpeg", ".png", ".baiduyun.uploading.cfg"}
SKIP_DIRS = {".git", "_rag", "app", "index", "__pycache__", "node_modules"}
SKIP_FILES = {"~$"}

EXTR_CFG = {
    ".docx": "extract_docx", ".doc": "extract_doc", ".pptx": "extract_pptx",
    ".xlsx": "extract_xlsx", ".xls": "extract_xls_xlrd", ".pdf": "extract_pdf",
    ".txt": "extract_txt", ".csv": "extract_txt", ".json": "extract_txt", ".md": "extract_txt",
}

def load(fn, path):
    sys.path.insert(0, str(ROOT))
    import index_docs as m
    return getattr(m, EXTR_CFG[fn])(path)

def main():
    keepers = {}
    for dp, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if any(f.startswith(s) for s in SKIP_FILES):
                continue
            if f.endswith(tuple(SKIP_EXT)):
                continue
            ext = Path(f).suffix.lower()
            if ext in EXTR_CFG:
                keepers.setdefault(ext, []).append(Path(dp) / f)

    fails, empty, ok, total = [], [], 0, 0
    for ext, files in sorted(keepers.items()):
        for p in files:
            total += 1
            try:
                text = load(ext, p)
            except Exception as e:
                tb = traceback.format_exc().strip().splitlines()[-1]
                fails.append((p, f"EXC {type(e).__name__}: {e} | {tb}"))
                continue
            if not text or not text.strip():
                empty.append((p, ""))
                continue
            ok += 1

    # 只打印当前索引里缺失的文件(即上次失败的),否则太多
    print(f"总计扫描 {total};  成功 {ok};  空/失败 {len(fails)+len(empty)}")
    print("\n### 抛异常失败:")
    for p, r in fails:
        print(f"  {p.relative_to(ROOT).as_posix()}  ->  {r}")
    print("\n### 抽取结果为空:")
    for p, r in empty:
        print(f"  {p.relative_to(ROOT).as_posix()}")

if __name__ == "__main__":
    main()
