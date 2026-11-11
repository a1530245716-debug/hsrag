# -*- coding: utf-8 -*-
"""本地语义向量化(Embedding)模块。

- 模型: BAAI/bge-m3 (ONNX, 1024 维, 中文强)
- 推理: onnxruntime。ExecutionProvider 默认 CPU;
        若系统装有 NPU/DirectML 运行时可在 config 里切换(见 retrieval.embedding.provider)。
- 职责: 文本 -> 向量; 向量库加载/增量追加; 余弦相似度 top-k 召回。

向量库落盘: app/data/<kb_id>/vectors.npy  (float32, 行=chunk 顺序, 与 chunks.jsonl 行序对应)
            app/data/<kb_id>/vectors_meta.json {count, dim, provider, model}
"""
import hashlib, json, os, sys, threading, time
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))

try:
    import onnxruntime as ort
except Exception:
    ort = None


# ---------------- 模型加载 ----------------

DEFAULT_MODEL = "bge-small-zh-onnx"     # 快速,中文够用; 也可 "bge-m3-onnx" (慢但最强)


def _model_dir():
    """模型目录: 默认 bge-small-zh-onnx; 可用 config retrieval.embedding.model 覆盖。"""
    import llm
    try:
        name = llm.load_config().get("retrieval", {}).get("embedding", {}).get("model", DEFAULT_MODEL)
    except Exception:
        name = DEFAULT_MODEL
    base = Path(__file__).parent.parent / "models"
    cand = base / name
    return cand if cand.exists() else base / DEFAULT_MODEL


_SESS = None
_SESS_LOCK = __import__("threading").Lock()


def _providers(prefer=None):
    """返回 onnxruntime 可用的 ExecutionProvider 列表(优先 prefer)。CPU 兜底。"""
    if ort is None:
        return []
    avail = ort.get_available_providers()
    if prefer:
        # 例如 ["CPUExecutionProvider"] 或将来 ["DmlExecutionProvider", "CPUExecutionProvider"]
        ordered = list(prefer) + [p for p in avail if p not in prefer]
        return ordered
    # 默认: 若有 GPU/NPU 类加速则优先, 否则纯 CPU
    fast = [p for p in avail if p not in ("CPUExecutionProvider",)]
    return fast + ["CPUExecutionProvider"] if fast else ["CPUExecutionProvider"]


def get_session(prefer=None):
    global _SESS
    if _SESS is not None:
        return _SESS
    with _SESS_LOCK:
        if _SESS is not None:
            return _SESS
        if ort is None:
            raise RuntimeError("onnxruntime 未安装")
        model_path = _model_dir() / "model.onnx"
        if not model_path.exists():
            raise RuntimeError(f"embedding 模型不存在: {model_path}")
        provs = _providers(prefer)
        _SESS = ort.InferenceSession(str(model_path), providers=provs)
        return _SESS


def _tokenizer():
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(_model_dir() / "tokenizer.json"))


# ---------------- 文本 -> 向量 ----------------

def embed_texts(texts, batch_size=16, prefer=None):
    """批量文本 -> 向量 (L2 归一化后的 float32, N x D)。
    返回 None 表示模型未就绪(上层降级为纯 BM25)。"""
    if ort is None:
        return None
    try:
        sess = get_session(prefer)
        tok = _tokenizer()
        inputs = [tok.encode(t) for t in texts]
        max_len = max((len(x.ids) for x in inputs), default=1)
        max_len = min(max(max_len, 8), 512)  # 保守取 512 提速(覆盖绝大多数块)
        ids, mask = [], []
        for x in inputs:
            n = min(len(x.ids), max_len)
            ids.append(x.ids[:max_len] + [0] * (max_len - n))
            mask.append(x.attention_mask[:max_len] + [0] * (max_len - len(x.attention_mask[:max_len])))
        in_names = {i.name for i in sess.get_inputs()}
        out = []
        for i in range(0, len(texts), batch_size):
            sl = slice(i, i + batch_size)
            feeds = {
                "input_ids": np.array(ids[sl], dtype=np.int64),
                "attention_mask": np.array(mask[sl], dtype=np.int64),
            }
            if "token_type_ids" in in_names:
                feeds["token_type_ids"] = np.zeros((len(range(i, min(i + batch_size, len(texts)))), max_len),
                                                   dtype=np.int64)
            outputs = sess.run(None, feeds)
            # 池化: 优先取 sentence_embedding/CLS 类输出(2维); 否则对 token_embeddings 取 CLS
            if len(outputs) >= 2 and outputs[-1].ndim == 2:
                res = outputs[-1]
            else:
                tok_emb = outputs[-1]  # [B, L, D]
                res = tok_emb[:, 0]    # CLS
            out.append(res)
        vec = np.concatenate(out, axis=0).astype(np.float32)
        norms = np.linalg.norm(vec, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vec / norms).astype(np.float32)
    except Exception as e:
        print(f"[embeddings] 向量化失败: {e}", file=sys.stderr)
        return None


def embed_query(text, prefer=None):
    v = embed_texts([text], prefer=prefer)
    return v[0] if v is not None else None


# ---------------- 向量库管理 ----------------

def vdb_path(kb_id):
    d = Path(__file__).parent / "data" / str(kb_id)
    return d / "vectors.npy", d / "vectors_meta.json"


def save_vectors(kb_id, vecs: np.ndarray, model="bge-m3-onnx", provider="cpu", **extra):
    np_path, meta_path = vdb_path(kb_id)
    np_path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(vecs, dtype=np.float32)
    # 先写临时文件再原子替换: 正在提问的进程不会读到半截 vectors.npy
    tmp = np_path.with_name(np_path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.save(f, arr)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, np_path)
    meta = {"count": int(len(arr)), "dim": int(arr.shape[1]) if arr.ndim > 1 else 0,
            "model": model, "provider": provider,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    meta.update(extra)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


_VDB_CACHE = {}   # kb_id -> {"mtime": float, "v": ndarray, "meta": dict}


def load_vectors(kb_id):
    """读向量库,带 mtime 缓存: 文件未变则复用内存,避免每次提问全量读盘。"""
    np_path, meta_path = vdb_path(kb_id)
    if not np_path.exists():
        return None, None
    mtime = np_path.stat().st_mtime_ns
    hit = _VDB_CACHE.get(kb_id)
    if hit and hit["mtime"] == mtime:
        return hit["v"], hit["meta"]
    v = np.load(np_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    _VDB_CACHE[kb_id] = {"mtime": mtime, "v": v, "meta": meta}
    return v, meta


def search_vectors(kb_id, qvec, topk=20):
    """余弦相似度召回。返回 [(idx, score)] score∈[0,1], 已降序。"""
    v, _ = load_vectors(kb_id)
    if v is None or qvec is None:
        return []
    sims = v @ qvec  # 已 L2 归一化 => 点积即余弦
    order = np.argsort(-sims)[:topk]
    return [(int(i), float(sims[i])) for i in order]


# ---------------- 增量构建向量库 ----------------
# 关键点: 向量行必须与 chunks.jsonl 行序严格对齐。
# 增量重建索引会新增/删除/替换分块, 仅按"行数"续跑会错位,
# 所以这里按**每条分块的内容哈希**复用旧向量 —— 行序怎么变都不会串。

_BUILD_LOCK = threading.Lock()          # 全局串行: 同时只建一个库, 避免打满 CPU
_BUILDING = set()
_BUILD_GUARD = threading.Lock()


def is_building(kb_id):
    with _BUILD_GUARD:
        return kb_id in _BUILDING


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _provider_name():
    try:
        import llm
        return llm.load_config().get("retrieval", {}).get("embedding", {}).get("provider", "cpu")
    except Exception:
        return "cpu"


def _save_normalized(kb_id, vecs, model, provider, ids, hashes, complete):
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = (vecs / norms).astype(np.float32)
    save_vectors(kb_id, vecs, model=model, provider=provider,
                 ids=ids, hashes=hashes, complete=bool(complete))


def ensure_vectors(kb_id, batch_size=32, checkpoint_secs=120.0, progress=None):
    """确保该知识库的 vectors.npy 与 chunks.jsonl 对齐(增量、可续跑)。

    返回 {"ok", "count", "reused", "built", "skipped"?, "msg"?}
    - 按内容哈希复用旧向量; 重排/删除/新增都不会错位
    - 旧 meta 没有 hashes 时退化为"前缀可信"复用(兼容历史向量库)
    - 大库每 checkpoint_secs 秒落盘一次, 中断后下次按哈希继续, 不会白跑
    """
    import indexer
    np_path, meta_path = vdb_path(kb_id)
    chunk_path = indexer._kb_dir(kb_id) / "chunks.jsonl"
    if not chunk_path.exists():
        return {"ok": False, "msg": "还没有索引(chunks.jsonl 不存在)"}

    ids, texts = [], []
    with open(chunk_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            ids.append(r["id"])
            texts.append(r.get("text") or "")
    n = len(texts)
    if n == 0:
        return {"ok": False, "msg": "索引为空"}
    hashes = [_md5(t) for t in texts]

    old_vecs, old_meta = None, {}
    if np_path.exists():
        try:
            old_vecs = np.load(np_path)
        except Exception:
            old_vecs = None
        if meta_path.exists():
            try:
                old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                old_meta = {}

    reuse = {}
    if old_vecs is not None and old_vecs.ndim == 2:
        oh = old_meta.get("hashes")
        if isinstance(oh, list) and len(oh) == len(old_vecs):
            for h, v in zip(oh, old_vecs):
                if float(np.linalg.norm(v)) > 0:
                    reuse.setdefault(h, v)
        else:
            # 旧格式(只有 count): 只能信任"前 N 行与原顺序一致"
            k = min(int(old_meta.get("count", 0) or 0), len(old_vecs), n)
            for i in range(k):
                v = old_vecs[i]
                if float(np.linalg.norm(v)) > 0:
                    reuse.setdefault(hashes[i], v)

    miss = [i for i, h in enumerate(hashes) if h not in reuse]
    if not miss and old_meta.get("complete") and old_vecs is not None and len(old_vecs) == n:
        return {"ok": True, "count": n, "reused": n, "built": 0, "skipped": True}

    provider = _provider_name()
    model_name = _model_dir().name

    vecs = None
    if reuse:
        dim = next(iter(reuse.values())).shape[0]
        vecs = np.zeros((n, dim), dtype=np.float32)
        for i, h in enumerate(hashes):
            v = reuse.get(h)
            if v is not None:
                vecs[i] = v

    total = len(miss)
    built = 0
    t0 = t_last = time.time()

    _BUILD_LOCK.acquire()
    with _BUILD_GUARD:
        _BUILDING.add(kb_id)
    try:
        for start in range(0, total, batch_size):
            batch_idx = miss[start:start + batch_size]
            v = embed_texts([texts[i] for i in batch_idx], batch_size=len(batch_idx))
            if v is None:
                return {"ok": False, "msg": "向量模型不可用或推理失败",
                        "count": n, "reused": n - total, "built": built}
            if vecs is None:
                vecs = np.zeros((n, v.shape[1]), dtype=np.float32)
            for k, i in enumerate(batch_idx):
                vecs[i] = v[k]
            built += len(batch_idx)
            if progress:
                progress(f"向量化 {built}/{total}")
            if n > 5000 and (time.time() - t_last) >= checkpoint_secs:
                _save_normalized(kb_id, vecs, model_name, provider, ids, hashes, complete=False)
                t_last = time.time()

        if vecs is None:
            return {"ok": False, "msg": "没有可处理的分块"}
        _save_normalized(kb_id, vecs, model_name, provider, ids, hashes, complete=True)
        return {"ok": True, "count": n, "reused": n - total, "built": built,
                "skipped": False, "elapsed": round(time.time() - t0, 1)}
    finally:
        with _BUILD_GUARD:
            _BUILDING.discard(kb_id)
        _BUILD_LOCK.release()
