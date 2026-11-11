# -*- coding: utf-8 -*-
"""问答日志 + 用户反馈。

目的: 知道**用户在问什么、什么答不上来、哪些答案不好** —— 这是优化知识库的依据,
比闷头调参数有效得多。

存储: data/qa_log.jsonl, 每行一条 JSON(追加写, 不阻塞请求)。
两种记录:
  {"t":"ask", "id":"...", "ts":..., "user":..., "kb":..., "q":..., ...}
  {"t":"fb",  "id":"...", "ts":..., "vote":1|-1, "user":...}

统计口径(见 stats()):
  - 兜底率      = fallback 为 true 的比例(说明知识库缺内容)
  - 差评率      = 反馈为 -1 的比例
  - 平均命中分  = 检索质量趋势
  - 高频问题    = 出现最多的问句(可考虑缓存/做成 FAQ)
"""
import hashlib, json, os, time
from collections import Counter
from pathlib import Path

APP = Path(__file__).parent
_LOCK = __import__("threading").Lock()
MAX_MB = 20          # 日志超过就轮转一次


def _data_dir():
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = APP / "data"
    if not d.is_absolute():
        alt = APP.parent / d
        d = alt if alt.exists() else APP / d
    return d


def _log_path():
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "qa_log.jsonl"


def _rotate(p: Path):
    try:
        if p.exists() and p.stat().st_size > MAX_MB * 1024 * 1024:
            bak = p.with_name(p.name.replace(".jsonl", f"-{time.strftime('%Y%m%d')}.jsonl"))
            p.replace(bak)
    except Exception:
        pass


def new_id():
    return time.strftime("%Y%m%d%H%M%S") + "-" + hashlib.md5(
        f"{time.time_ns()}-{os.getpid()}".encode()).hexdigest()[:6]


def record(q, kb_id="", user=None, answer="", sources=None, source_type="kb",
           fallback=False, ms=0, rewritten="", split=False, model=""):
    """记一条问答。失败绝不抛异常(不能因为记日志把问答搞挂)。"""
    try:
        sources = sources or []
        top = 0.0
        if sources:
            top = float(sources[0].get("score") or 0)
        rec = {
            "t": "ask",
            "id": new_id(),
            "ts": int(time.time()),
            "user": (user or {}).get("name", ""),
            "uid": (user or {}).get("id", ""),
            "kb": kb_id,
            "q": (q or "")[:500],
            "rewritten": (rewritten or "")[:300],
            "split": bool(split),
            "hits": len(sources),
            "top_score": round(top, 3),
            "source_type": source_type,
            "fallback": bool(fallback),
            "answer_len": len(answer or ""),
            "answer_head": (answer or "")[:200],
            "ms": int(ms),
            "model": model,
        }
        p = _log_path()
        with _LOCK:
            _rotate(p)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec["id"]
    except Exception as e:
        print(f"[qa_log] 记录失败: {e}", file=__import__("sys").stderr)
        return ""


def feedback(qa_id, vote, user=None):
    """记录 👍(1) / 👎(-1)。"""
    try:
        if vote not in (1, -1):
            return False
        rec = {"t": "fb", "id": qa_id, "ts": int(time.time()), "vote": int(vote),
               "user": (user or {}).get("name", ""), "uid": (user or {}).get("id", "")}
        p = _log_path()
        with _LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"[qa_log] 反馈记录失败: {e}", file=__import__("sys").stderr)
        return False


def _iter(days=None):
    p = _log_path()
    if not p.exists():
        return
    cutoff = 0 if not days else int(time.time()) - days * 86400
    with open(p, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("ts", 0) >= cutoff:
                yield r


def stats(days=7):
    """汇总统计, 供页面/命令行看。"""
    asks, fbs = [], {}
    for r in _iter(days):
        if r.get("t") == "ask":
            asks.append(r)
        elif r.get("t") == "fb":
            fbs[r.get("id")] = r.get("vote")
    n = len(asks)
    if not n:
        return {"days": days, "asks": 0, "msg": "该时间段没有问答记录"}

    fb_vals = list(fbs.values())
    fb_total = len(fb_vals)
    good = sum(1 for v in fb_vals if v > 0)
    bad = fb_total - good
    fb_asks = [a for a in asks if a["id"] in fbs]
    fallback_n = sum(1 for a in asks if a.get("fallback"))
    scores = [a.get("top_score", 0) for a in asks if a.get("hits")]
    lat = sorted(a.get("ms", 0) for a in asks if a.get("ms"))
    qs = Counter((a.get("q") or "").strip() for a in asks if (a.get("q") or "").strip())

    def pct(x, d):
        return round(100.0 * x / d, 1) if d else 0.0

    return {
        "days": days,
        "asks": n,
        "feedback": {"total": fb_total, "good": good, "bad": bad,
                     "bad_rate": pct(bad, fb_total)},
        "fallback": {"count": fallback_n, "rate": pct(fallback_n, n)},
        "avg_top_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "latency_ms": {"p50": lat[len(lat) // 2] if lat else 0,
                       "p90": lat[int(len(lat) * 0.9)] if lat else 0,
                       "avg": int(sum(lat) / len(lat)) if lat else 0},
        "rewritten": sum(1 for a in asks if a.get("rewritten")),
        "split": sum(1 for a in asks if a.get("split")),
        "top_questions": qs.most_common(10),
        "worst": sorted([a for a in fb_asks if fbs.get(a["id"]) == -1],
                        key=lambda x: x.get("ts", 0), reverse=True)[:10],
        "fallback_questions": [a.get("q") for a in asks if a.get("fallback")][:10],
    }


def load(days=7, limit=200):
    """取最近若干条问答(含反馈), 供页面查看。"""
    fbs = {}
    rows = []
    for r in _iter(days):
        if r.get("t") == "fb":
            fbs[r.get("id")] = r.get("vote")
        else:
            rows.append(r)
    for r in rows:
        r["vote"] = fbs.get(r.get("id"))
    return rows[-limit:][::-1]
