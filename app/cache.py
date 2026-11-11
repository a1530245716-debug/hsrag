# -*- coding: utf-8 -*-
"""答案缓存: 相同问题直接秒回, 省一次检索 + 一次大模型调用。

只缓存"安全"的情形:
    - 首问(没有多轮历史) —— 追问依赖上下文, 不能缓存
    - 知识库命中的答案   —— 兜底答案(网搜/模型知识)时效性强, 不缓存
失效策略:
    - 该知识库重建索引 → 清空该库缓存(内容变了, 旧答案可能过时)
    - TTL 默认 24 小时
    - 容量上限, LRU 淘汰

命中时仍会写 qa_log(标 cached), 这样统计里能看到缓存收益。
"""
import hashlib, threading, time

_TTL = 24 * 3600
_MAX = 400
_LOCK = threading.Lock()
_STORE = {}     # key -> {"answer","sources","ts","hits"}
_STATS = {"hit": 0, "miss": 0, "put": 0}


def _key(kb_id, q):
    norm = " ".join((q or "").strip().lower().split())
    if not norm:
        return ""
    return hashlib.md5(f"{kb_id}::{norm}".encode()).hexdigest()


def get(kb_id, q):
    k = _key(kb_id, q)
    if not k:
        return None
    with _LOCK:
        item = _STORE.get(k)
        if not item:
            _STATS["miss"] += 1
            return None
        if time.time() - item["ts"] > _TTL:
            _STORE.pop(k, None)
            _STATS["miss"] += 1
            return None
        item["hits"] += 1
        _STATS["hit"] += 1
        return {"answer": item["answer"], "sources": item["sources"],
                "cached": True, "age": int(time.time() - item["ts"])}


def put(kb_id, q, answer, sources):
    k = _key(kb_id, q)
    if not k or not answer or len(answer) < 20:
        return False
    if not sources:
        return False
    with _LOCK:
        if len(_STORE) >= _MAX:
            # 淘汰最久未用的 10%
            olds = sorted(_STORE.items(), key=lambda kv: (kv[1]["hits"], kv[1]["ts"]))[:max(1, _MAX // 10)]
            for kk, _ in olds:
                _STORE.pop(kk, None)
        _STORE[k] = {"answer": answer, "sources": sources[:8],
                     "ts": time.time(), "hits": 0, "kb": kb_id}
        _STATS["put"] += 1
        return True


def clear(kb_id=None):
    """清缓存。kb_id=None 清全部; 否则只清该库。"""
    with _LOCK:
        if kb_id is None:
            n = len(_STORE)
            _STORE.clear()
            return n
        # 缓存 key 是 md5, 无法反查库; 所以 put 时额外记录 kb_id 映射
        n = 0
        for k, v in list(_STORE.items()):
            if v.get("kb") == kb_id:
                _STORE.pop(k, None)
                n += 1
        return n


def stats():
    with _LOCK:
        total = _STATS["hit"] + _STATS["miss"]
        return {"size": len(_STORE), "hit": _STATS["hit"], "miss": _STATS["miss"],
                "put": _STATS["put"],
                "hit_rate": round(100.0 * _STATS["hit"] / total, 1) if total else 0}
