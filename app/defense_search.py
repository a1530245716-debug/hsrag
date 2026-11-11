# -*- coding: utf-8 -*-
"""在历史答辩记录里找"类似的问题"，给 AI 出题做参照。

为什么单独写、不复用 kb.py
--------------------------
kb.py 是给**文档块**用的：BM25 + 向量 + 重排，几万块，走的是 `chunks.jsonl`。
答辩记录是**几百到几千条**的结构化问答对，量级完全不同，
而且检索目标是"找评委怎么问的"，不是"找文档里的某段话"。

硬要复用的话，得把记录拼成文本塞进 chunks 结构——那就把"问题/回答/点评"
的归属关系拆散了。这里用轻量实现：jieba 分词 + BM25，够了。

对外只暴露 search()，返回按相关度排序的记录。
"""
import math
import re
import threading

import defense

_LOCK = threading.Lock()
_cache = {"n": -1, "docs": [], "bm25": None, "df": {}}


# ---------------- 分词 ----------------

_STOP = set("的 了 是 在 和 与 或 有 没有 怎么 如何 什么 哪些 这个 那个 一个 我们 你们 他们 请 问 那 这 会 能 要 对 把 被 从 到 为".split())


def _tokens(text: str) -> list:
    """jieba 分词，去掉标点和停用词。jieba 没装就退化成按字切。"""
    t = (text or "").strip()
    if not t:
        return []
    try:
        import jieba
        words = jieba.lcut(t)
    except Exception:
        words = list(t)
    out = []
    for w in words:
        w = w.strip()
        if len(w) < 2:          # 单字噪音太大
            continue
        if w in _STOP:
            continue
        if re.fullmatch(r"[\W_]+", w):
            continue
        out.append(w.lower())
    return out


# ---------------- BM25 ----------------

def _file_sig():
    """记录文件的签名（mtime + size）。只看条数会漏掉"条数没变但内容改了"的情况。"""
    try:
        st = defense.records_path().stat()
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return (0, 0)


def _build():
    """把全部记录建成 BM25 索引。数据变了才重建。"""
    recs = defense.load_all()
    n = len(recs)
    sig = (n,) + _file_sig()
    if _cache.get("sig") == sig and _cache["docs"]:
        return _cache

    docs = []
    for r in recs:
        # 问题权重最高，其次点评（点评里常包含"应该怎么答"），再是人工标的 tags，回答最后。
        # tags 必须进索引：很多主题词只出现在标签里（"排障""业务知识"），
        # 不进索引的话按这些词根本搜不到对应记录。
        comment_text = " ".join(str((c or {}).get("text", "")) for c in (r.get("comments") or []))
        tags_text = " ".join(str(t) for t in (r.get("tags") or []))
        toks = (_tokens(r.get("question", "")) * 3
                + _tokens(comment_text) * 2
                + _tokens(tags_text) * 2
                + _tokens(r.get("answer", "")))
        docs.append({"rec": r, "toks": toks, "len": len(toks)})

    df = {}
    for d in docs:
        for t in set(d["toks"]):
            df[t] = df.get(t, 0) + 1

    avgdl = sum(d["len"] for d in docs) / max(1, len(docs))
    _cache.update({"n": n, "docs": docs, "df": df, "avgdl": avgdl, "sig": sig})
    return _cache


def _score(qtoks, d, df, n, avgdl, k1=1.5, b=0.75):
    if not d["len"]:
        return 0.0
    tf = {}
    for t in d["toks"]:
        tf[t] = tf.get(t, 0) + 1
    s = 0.0
    for t in qtoks:
        f = tf.get(t)
        if not f:
            continue
        idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
        s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d["len"] / max(1, avgdl)))
    return s


# ---------------- 对外 ----------------

def search(query, topk=8, rtype=None, min_score=0.0):
    """按相关度返回记录列表，每条带上 `_score`。

    rtype: 只看某类（"应届生" / "社招"），None 表示不限。
    """
    qtoks = _tokens(query)
    c = _build()
    docs = c["docs"]
    if not docs:
        return []
    if rtype:
        docs = [d for d in docs if (d["rec"].get("type") or "") == rtype]
    if not docs:
        return []
    if not qtoks:
        # 没有可用关键词（比如材料是空的），退化成按时间倒序给最近的
        return [dict(d["rec"], _score=0.0)
                for d in sorted(docs, key=lambda x: -(x["rec"].get("ts") or 0))[:topk]]

    df = c["df"]
    n = c["n"]
    avgdl = c.get("avgdl", 1.0)
    scored = []
    for d in docs:
        s = _score(qtoks, d, df, n, avgdl)
        if s > min_score:
            scored.append((s, d["rec"]))
    scored.sort(key=lambda x: -x[0])
    return [dict(r, _score=round(s, 3)) for s, r in scored[:topk]]


def sample(n=6, rtype=None, seed=None):
    """随机抽几条做示例（给"看看历史评委都问什么"用）。"""
    import random
    recs = defense.load_all()
    if rtype:
        recs = [r for r in recs if (r.get("type") or "") == rtype]
    if not recs:
        return []
    rnd = random.Random(seed)
    return rnd.sample(recs, min(n, len(recs)))


def tags_summary(topk=30):
    """问题主题分布，让 AI 知道"评委关注哪些方面"。"""
    recs = defense.load_all()
    cnt = {}
    for r in recs:
        for tg in (r.get("tags") or []):
            cnt[tg] = cnt.get(tg, 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])[:topk]


def invalidate():
    """数据变了，下次检索重建索引。"""
    with _LOCK:
        _cache.update({"n": -1, "docs": [], "df": {}, "sig": None})
