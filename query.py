# -*- coding: utf-8 -*-
"""基于索引的 BM25 检索。给定问题,返回最相关的若干文档片段。
用法: python query.py "你的问题" [-k 8]
"""
import os, sys, json, math, argparse
from pathlib import Path
from collections import Counter

try:
    import jieba
    jieba.initialize()
except Exception:
    jieba = None

ROOT = Path(os.environ.get("RAG_ROOT") or os.getcwd())
INDEX = ROOT / "index" / "chunks.jsonl"


def tokenize(text: str):
    if jieba:
        return [w for w in jieba.cut(text.lower()) if len(w.strip()) > 0 and not w.isspace()]
    return list(text.lower())


class BM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = []        # 每项: (id, path, chunk_idx, text, tokens)
        self.doc_ids = []
        self.tokens_list = []
        self.df = Counter()   # 词 -> 出现文档数
        self.N = 0
        self.avgdl = 0.0

    def add(self, doc_id, path, chunk, text):
        toks = tokenize(text)
        self.docs.append((doc_id, path, chunk, text))
        self.doc_ids.append(doc_id)
        self.tokens_list.append(toks)
        for w in set(toks):
            self.df[w] += 1

    def build(self):
        self.N = len(self.tokens_list)
        if self.N:
            self.avgdl = sum(len(t) for t in self.tokens_list) / self.N

    def query(self, q, topk=8):
        qtoks = tokenize(q)
        qcnt = Counter(qtoks)
        scores = []
        for i, toks in enumerate(self.tokens_list):
            dl = len(toks)
            tf_c = Counter(toks)
            score = 0.0
            for w, qw in qcnt.items():
                tf = tf_c.get(w, 0)
                if tf == 0:
                    continue
                df = self.df.get(w, 0)
                idf = math.log((self.N - df + 0.5) / (df + 0.5) + 1)
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += idf * (tf * (self.k1 + 1)) / (denom or 1)
            scores.append((score, i))
        scores.sort(key=lambda x: -x[0])
        out = []
        for score, i in scores[:topk]:
            if score <= 0:
                break
            d = self.docs[i]
            out.append({"score": round(score, 3), "id": d[0], "path": d[1],
                        "chunk": d[2], "text": d[3]})
        return out


def load_index():
    bm = BM25()
    if not INDEX.exists():
        print(f"未找到索引:{INDEX}\n请先在项目根目录运行: python index_docs.py", file=sys.stderr)
        sys.exit(1)
    with open(INDEX, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            bm.add(r["id"], r["path"], r["chunk"], r["text"])
    bm.build()
    return bm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="要检索的问题")
    ap.add_argument("-k", type=int, default=8, help="返回片段数")
    ap.add_argument("--group", action="store_true", help="按文件分组输出")
    args = ap.parse_args()

    bm = load_index()
    results = bm.query(args.query, topk=max(args.k, len(bm.doc_ids)*0 + args.k))
    if not results:
        print("未检索到相关片段。")
        return

    if args.group:
        by = {}
        for r in results:
            by.setdefault(r["path"], []).append(r)
        for path, items in by.items():
            print(f"\n===== {path} ({len(items)} 片段) =====")
            for r in items:
                print(f"\n[{r['id']}] 得分{r['score']}\n{r['text'][:500]}")
    else:
        for r in results:
            print(f"===== {r['id']} | 得分{r['score']} | {r['path']} =====")
            print(r["text"])


if __name__ == "__main__":
    main()
