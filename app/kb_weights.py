# -*- coding: utf-8 -*-
"""每个知识库自己的文件检索权重。

默认全部 1.0（不改变现有行为）。用户在知识库「文件权重」里给某些文件设
0.5～2.0 的系数：>1 优先引用，<1 降权。只影响当前知识库。

存储：<kb_dir>/retrieval_settings.json
  {"file_weights": {"相对路径": 1.5, ...}}
只保存非 1.0 的项；文件路径与 chunks.jsonl 的 path 一致。
"""
import json
import os
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()
_CACHE = {}          # kb_id -> (mtime, {path: weight})
MIN_W, MAX_W = 0.5, 2.0


def _path(kb_id):
    import indexer
    return Path(indexer._kb_dir(kb_id)) / "retrieval_settings.json"


def _clean(raw):
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        k = str(k or "").strip()
        if not k:
            continue
        try:
            w = float(v)
        except Exception:
            continue
        w = max(MIN_W, min(MAX_W, w))
        if abs(w - 1.0) > 1e-6:
            out[k] = round(w, 2)
    return dict(list(out.items())[:5000])


def load(kb_id):
    """返回 {path: weight}；没有配置/读取失败返回空 dict。"""
    if not kb_id:
        return {}
    try:
        p = _path(kb_id)
        mt = p.stat().st_mtime
    except Exception:
        return {}
    with _LOCK:
        hit = _CACHE.get(kb_id)
        if hit and hit[0] == mt:
            return dict(hit[1])
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = _clean(data.get("file_weights") if isinstance(data, dict) else {})
    with _LOCK:
        _CACHE[kb_id] = (mt, out)
    return dict(out)


def save(kb_id, weights):
    """保存权重（只留非 1.0）；返回清理后的 dict。"""
    if not kb_id:
        raise ValueError("kb_id 为空")
    clean = _clean(weights)
    p = _path(kb_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"file_weights": clean}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        _CACHE.pop(kb_id, None)
    return clean


def invalidate(kb_id):
    with _LOCK:
        _CACHE.pop(kb_id, None)
