# -*- coding: utf-8 -*-
"""用户反馈 / 需求登记。

和问答后的 👍/👎（qa_log.feedback）分开：这里收「内容补充、改进建议、新需求」
这类自由文本，是管理员改进系统的直接线索。

存储：<data_dir>/feedback.jsonl，每行一条 JSON（追加写，不阻塞请求）。
两种记录：
  {"t":"fb",   "id":..., "ts":..., "uid":..., "user":..., "source":"qa|defense",
   "kind":"content|improve|feature|other", "content":..., "contact":..., "page":...}
  {"t":"done", "id":..., "ts":..., "by":...}      # 管理员标记已处理

环境变量 RAG_FEEDBACK_DIR 可把存储目录指到别处（自测时避免写进共享数据）。
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
MAX_MB = 20                 # 日志超过就轮转一次
MAX_CONTENT = 2000          # 服务端硬上限（前端 1000）

KINDS = {
    "content": "内容补充",
    "improve": "改进建议",
    "feature": "新需求",
    "other": "其他",
}
SOURCES = {"qa": "智能问答", "defense": "模拟答辩"}


def _data_dir():
    env = os.environ.get("RAG_FEEDBACK_DIR")
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
    return d / "feedback.jsonl"


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


def add(user, kind="improve", content="", contact="", source="qa", page=""):
    """新增一条反馈。返回记录 dict；参数不合法抛 ValueError。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("反馈内容不能为空")
    if len(text) > MAX_CONTENT:
        raise ValueError(f"反馈内容最多 {MAX_CONTENT} 字")
    if kind not in KINDS:
        kind = "other"
    if source not in SOURCES:
        source = "qa"
    rec = {
        "t": "fb",
        "id": _new_id(),
        "ts": int(time.time()),
        "uid": (user or {}).get("id", ""),
        "user": (user or {}).get("name", "") or (user or {}).get("username", ""),
        "source": source,
        "kind": kind,
        "content": text,
        "contact": (contact or "").strip()[:120],
        "page": (page or "").strip()[:120],
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
        print(f"[feedback] 读取失败: {e}", file=sys.stderr)


def list_all(limit=300, days=0, only_unhandled=False):
    """按时间倒序返回反馈；handled 记录合并进对应条目。"""
    cutoff = int(time.time()) - days * 86400 if days else 0
    items = {}
    order = []
    for r in _iter_records():
        t = r.get("t")
        if t == "fb":
            if cutoff and int(r.get("ts") or 0) < cutoff:
                continue
            fid = r.get("id") or ""
            if not fid:
                continue
            r = dict(r)
            r["handled"] = False
            r["handled_ts"] = 0
            r["handled_by"] = ""
            items[fid] = r
            order.append(fid)
        elif t == "done":
            fid = r.get("id") or ""
            if fid in items:
                items[fid]["handled"] = True
                items[fid]["handled_ts"] = int(r.get("ts") or 0)
                items[fid]["handled_by"] = r.get("by", "")
    out = [items[fid] for fid in order]
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    if only_unhandled:
        out = [x for x in out if not x.get("handled")]
    try:
        limit = max(1, min(int(limit or 300), 2000))
    except Exception:
        limit = 300
    return out[:limit]


def summary(days=0):
    """给管理员看的计数：总数 / 未处理 / 各类型。"""
    items = list_all(limit=2000, days=days)
    by_kind = {k: 0 for k in KINDS}
    by_source = {k: 0 for k in SOURCES}
    for it in items:
        k = it.get("kind") if it.get("kind") in KINDS else "other"
        by_kind[k] += 1
        s = it.get("source") if it.get("source") in SOURCES else "qa"
        by_source[s] += 1
    return {
        "total": len(items),
        "unhandled": sum(1 for it in items if not it.get("handled")),
        "by_kind": by_kind,
        "by_source": by_source,
    }


def mark_handled(fid, user=None):
    """管理员标记已处理（幂等）。"""
    fid = (fid or "").strip()
    if not fid:
        return False
    done = any(r.get("t") == "done" and r.get("id") == fid for r in _iter_records())
    if done:
        return True
    rec = {"t": "done", "id": fid, "ts": int(time.time()),
           "by": (user or {}).get("name", "") or (user or {}).get("username", "")}
    p = _log_path()
    with _LOCK:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True
