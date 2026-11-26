# -*- coding: utf-8 -*-
"""用户点赞的回答 -> 评测集候选。

目的：把“用户实际觉得有用”的问答沉淀成测试集素材，减少从零手写用例。
注意：点赞只代表用户觉得有用，**不等于标准答案**；管理员需要人工核对后再收录。

存储：<data_dir>/eval_candidates.jsonl，每行一条 JSON。
  {"t":"case", "id":..., "qa_id":..., "ts":..., "uid":..., "user":...,
   "kb":..., "kb_name":..., "question":..., "answer":...,
   "sources":[{"path":..., "score":..., "text":...}], "modules":[...],
   "status":"pending"}                       # pending / included / ignored
  {"t":"status", "id":..., "status":..., "ts":..., "by":...}

环境变量 RAG_EVAL_DIR 可覆盖存储目录（自测用）。
"""
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).parent
_LOCK = threading.Lock()
MAX_MB = 20
MAX_QUESTION = 2000
MAX_ANSWER = 20000
MAX_SOURCES = 12
MAX_SOURCE_TEXT = 3000
STATUSES = ("pending", "included", "ignored")


def _data_dir():
    env = os.environ.get("RAG_EVAL_DIR")
    if env:
        return Path(env)
    try:
        import qa_log
        return Path(qa_log._data_dir())
    except Exception:
        return APP / "data"


def _log_path():
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "eval_candidates.jsonl"


def _rotate(p: Path):
    try:
        if p.exists() and p.stat().st_size > MAX_MB * 1024 * 1024:
            bak = p.with_name(p.name.replace(".jsonl", f"-{time.strftime('%Y%m%d')}.jsonl"))
            p.replace(bak)
    except Exception:
        pass


def _new_id():
    return time.strftime("%Y%m%d%H%M%S") + "-" + hashlib.md5(
        f"{time.time_ns()}-{os.getpid()}".encode()).hexdigest()[:6]


def _hash(question, answer):
    return hashlib.sha1(((question or "") + "\n" + (answer or "")).encode("utf-8")).hexdigest()


def _norm_sources(sources):
    out = []
    for s in (sources or [])[:MAX_SOURCES]:
        if not isinstance(s, dict):
            continue
        text = (s.get("text_full") or s.get("text") or "").strip()
        out.append({
            "path": (s.get("path") or "")[:500],
            "score": s.get("score"),
            "text": text[:MAX_SOURCE_TEXT],
        })
    return out


def add_candidate(qa_id, question, answer, sources=None, kb="", kb_name="", user=None):
    """记录一个点赞候选；同一 qa_id 或相同问答内容不重复写。"""
    q = (question or "").strip()[:MAX_QUESTION]
    a = (answer or "").strip()[:MAX_ANSWER]
    if not q or not a:
        raise ValueError("问题和答案都不能为空")
    h = _hash(q, a)
    existing = None
    for r in _iter_records():
        if r.get("t") != "case":
            continue
        if (qa_id and r.get("qa_id") == qa_id) or r.get("hash") == h:
            existing = r
            break
    if existing:
        return existing
    try:
        import module_router
        modules = module_router.detect_modules(q) or []
    except Exception:
        modules = []
    rec = {
        "t": "case",
        "id": _new_id(),
        "qa_id": qa_id or "",
        "hash": h,
        "ts": int(time.time()),
        "uid": (user or {}).get("id", ""),
        "user": (user or {}).get("name", "") or (user or {}).get("username", ""),
        "kb": kb or "",
        "kb_name": kb_name or "",
        "question": q,
        "answer": a,
        "sources": _norm_sources(sources),
        "modules": modules,
        "status": "pending",
    }
    p = _log_path()
    with _LOCK:
        _rotate(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _iter_records():
    p = _log_path()
    if not p.exists():
        return
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception as e:
        print(f"[eval_store] 读取失败: {e}", file=sys.stderr)


def list_all(limit=300, status=None):
    items, order = {}, []
    for r in _iter_records():
        t = r.get("t")
        if t == "case":
            cid = r.get("id") or ""
            if not cid:
                continue
            r = dict(r)
            r["status"] = r.get("status") or "pending"
            items[cid] = r
            order.append(cid)
        elif t == "status":
            cid = r.get("id") or ""
            if cid in items:
                items[cid]["status"] = r.get("status") or items[cid].get("status")
                items[cid]["status_ts"] = int(r.get("ts") or 0)
                items[cid]["status_by"] = r.get("by", "")
    out = [items[c] for c in order]
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    if status:
        out = [x for x in out if x.get("status") == status]
    try:
        limit = max(1, min(int(limit or 300), 2000))
    except Exception:
        limit = 300
    return out[:limit]


def set_status(cid, status, user=None):
    if status not in STATUSES:
        raise ValueError("status 不合法")
    cid = (cid or "").strip()
    if not cid:
        return False
    rec = {"t": "status", "id": cid, "status": status, "ts": int(time.time()),
           "by": (user or {}).get("name", "")}
    p = _log_path()
    with _LOCK:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def summary():
    items = list_all(limit=2000)
    by = {s: 0 for s in STATUSES}
    for it in items:
        by[it.get("status") if it.get("status") in STATUSES else "pending"] += 1
    return {"total": len(items), **by}


def export_cases(ids=None):
    """导出为可人工整理的 JSON；保留问题、答案、引用，模块由问题自动识别。"""
    items = list_all(limit=2000)
    if ids:
        want = set(ids)
        items = [x for x in items if x.get("id") in want]
    cases = []
    for it in items:
        cases.append({
            "id": it.get("id"),
            "kb_id": it.get("kb"),
            "kb_name": it.get("kb_name"),
            "question": it.get("question"),
            "answer": it.get("answer"),
            "expected_modules": it.get("modules") or [],
            "must_include_any": [],
            "min_len": 80,
            "sources": [{"path": s.get("path"), "text": (s.get("text") or "")[:300]}
                        for s in (it.get("sources") or [])],
            "status": it.get("status"),
        })
    return {"name": "用户点赞候选（需人工核对）", "cases": cases}
