# -*- coding: utf-8 -*-
"""把业务手册建成一个独立知识库。

手册是 O32/资管业务的培训资料（股债基、回购、风控、清算…），
AI 模拟评委出题时要用它来把握"业务知识范围"——问的问题必须落在这套体系里。

和现有知识库的关系：**独立一个库**，不与客户现场资料混。
手册是通用培训材料，客户资料是具体项目，检索目标完全不同。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import indexer  # noqa: E402

NAME = "业务手册（O32/资管培训）"
FOLDER = str(ROOT / "demo" / "业务手册_docs")


def find():
    for kb in indexer.load_registry():
        if kb.get("name") == NAME or kb.get("folder") == FOLDER:
            return kb
    return None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    folder = Path(FOLDER)
    if not folder.exists():
        print(f"源目录不存在: {folder}")
        sys.exit(1)
    n = len(list(folder.rglob("*.*")))
    print(f"源目录: {folder}（{n} 个文件）")

    kb = find()
    if kb:
        print(f"已存在知识库: {kb['id']} / {kb['name']}")
    else:
        print("新建知识库…")
        kb = indexer.create_kb(NAME, FOLDER)
        print(f"  已创建: {kb['id']}")

    print("\n开始建索引（首次会比较久，PPT 要抽文字和图片）…")
    indexer.index_kb(kb, progress=lambda m: print(f"  {m}", flush=True))
    st = indexer.get_kb(kb["id"]) or {}
    print(f"\n完成：{st.get('file_count')} 个文件, {st.get('chunk_count')} 个块")
    print(f"kb_id = {kb['id']}")


if __name__ == "__main__":
    main()
