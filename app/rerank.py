# -*- coding: utf-8 -*-
"""Rerank 精排模块。

- 模型: BAAI/bge-reranker-base (ONNX 交叉编码器, CPU 推理)
  - 默认加载 int8 量化版(小/快); 缺失时回退 fp32 版。
- 职责: 对召回候选 (query, 片段) 逐一打分, 按相关性降序重排。
- 并发: 模型推理串行, 用信号量限流(同时最多 RERANK_CONCURRENCY 个),
  超出的调用排队等待, 避免并发打满 CPU 互相拖慢。
- 若无模型或推理失败, 返回 None => 上层保持原顺序(不降级出错)。
"""
import sys, threading, time
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))

try:
    import onnxruntime as ort
except Exception:
    ort = None

_INT8_DIR = Path(__file__).parent.parent / "models" / "bge-reranker-base-int8-onnx"
_FP32_DIR = Path(__file__).parent.parent / "models" / "bge-reranker-base-onnx"

RERANK_CONCURRENCY = 2          # 同时允许的推理数(超出排队)
RERANK_SLOT_TIMEOUT = 120.0     # 排队等位最久(秒), 超时放弃重排(降级为 BM25 排序)
_SESS = None
_SESS_LOCK = threading.Lock()
_SLOT = None                    # 懒初始化 BoundedSemaphore


def _model_dir():
    if (_INT8_DIR / "model.onnx").exists():
        return _INT8_DIR
    return _FP32_DIR


def _providers():
    if ort is None:
        return []
    avail = ort.get_available_providers()
    fast = [p for p in avail if p != "CPUExecutionProvider"]
    return (fast + ["CPUExecutionProvider"]) if fast else ["CPUExecutionProvider"]


def get_session():
    global _SESS
    if _SESS is not None:
        return _SESS
    with _SESS_LOCK:
        if _SESS is not None:
            return _SESS
        if ort is None:
            return None
        model_path = _model_dir() / "model.onnx"
        if not model_path.exists():
            print(f"[rerank] 模型不存在: {model_path}", file=sys.stderr)
            return None
        try:
            _SESS = ort.InferenceSession(str(model_path), providers=_providers())
        except Exception as e:
            print(f"[rerank] 加载失败: {e}", file=sys.stderr)
            return None
        return _SESS


def _tokenizer():
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(_model_dir() / "tokenizer.json"))


def rerank(query: str, candidates, topk=None, batch_size=8):
    """对 candidates 精排。candidates: [{path, score, text}, ...]

    返回新列表(含 rerank_score), 或 None(不可用/排队超时)。topk 默认全部排序。
    """
    global _SLOT
    sess = get_session()
    if sess is None or not candidates:
        return None
    if _SLOT is None:
        _SLOT = threading.BoundedSemaphore(RERANK_CONCURRENCY)
    if not _SLOT.acquire(timeout=RERANK_SLOT_TIMEOUT):
        print(f"[rerank] 排队超时({RERANK_SLOT_TIMEOUT}s), 本次跳过重排", file=sys.stderr)
        return None
    try:
        return _do_rerank(sess, query, candidates, topk=topk, batch_size=batch_size)
    finally:
        _SLOT.release()


def _do_rerank(sess, query, candidates, topk=None, batch_size=8):
    try:
        tok = _tokenizer()
        # 交叉编码: query 与片段作为 pair(tokenizer 加 [SEP] 区分)
        pairs = [tok.encode(query, pair=c["text"][:2000]) for c in candidates]
        max_len = min(max((len(x.ids) for x in pairs), default=1), 512)
        ids, mask = [], []
        for x in pairs:
            ids.append(x.ids[:max_len] + [0] * (max_len - min(len(x.ids), max_len)))
            mask.append(x.attention_mask[:max_len] + [0] * (max_len - min(len(x.attention_mask), max_len)))
        scores = []
        for i in range(0, len(candidates), batch_size):
            n = min(batch_size, len(candidates) - i)
            feeds = {
                "input_ids": np.array(ids[i:i + n], dtype=np.int64),
                "attention_mask": np.array(mask[i:i + n], dtype=np.int64),
            }
            res = sess.run(None, feeds)
            # reranker 输出 logits 一维
            raw = res[-1]
            if raw.ndim == 2:
                raw = raw[:, 0]
            scores.extend(float(x) for x in raw)
        out = []
        for c, s in zip(candidates, scores):
            item = dict(c)
            item["rerank_score"] = round(s, 4)
            item.setdefault("score", item.get("score", 0.0))  # 保留原 BM25 分数
            out.append(item)
        out.sort(key=lambda x: -x["rerank_score"])
        return out[:topk] if topk else out
    except Exception as e:
        print(f"[rerank] 推理失败: {e}", file=sys.stderr)
        return None
