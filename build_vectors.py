# -*- coding: utf-8 -*-
"""为知识库构建/更新向量(增量)。用法:
  python build_vectors.py <kb_id> [batch_size]

- 与 chunks.jsonl 行序严格对齐(按内容哈希复用, 重排/增删不会错位)
- 断点续跑: 中断后重跑只补缺的, 已算过的直接复用
- 索引流程已自动触发本逻辑, 一般无需手动执行; 此脚本用于手动补建/排障
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

import indexer
import embeddings


def main():
    kb_id = sys.argv[1] if len(sys.argv) > 1 else ""
    batch = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    if not kb_id:
        print("用法: python build_vectors.py <kb_id> [batch_size]")
        sys.exit(1)

    kb = indexer.get_kb(kb_id)
    if not kb:
        print(f"知识库不存在: {kb_id}")
        sys.exit(1)
    print(f"KB {kb_id} ({kb.get('name', '')}): 开始对齐向量库 (batch={batch})")

    st = embeddings.ensure_vectors(kb_id, batch_size=batch,
                                   progress=lambda m: print(m, flush=True))
    if not st.get("ok"):
        print(f"[FAIL] {st.get('msg')}")
        sys.exit(2)
    if st.get("skipped"):
        print(f"向量已是最新({st['count']} 条), 无需重建。")
        return
    print(f"完成: {st['count']} 向量 (复用 {st['reused']}, 新算 {st['built']}) "
          f"用时 {st.get('elapsed')}s")


if __name__ == "__main__":
    main()
