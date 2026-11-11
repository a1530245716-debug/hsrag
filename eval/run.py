# -*- coding: utf-8 -*-
"""检索质量评测: 跑评测集, 算 recall@k / MRR / 平均排名。

为什么需要这个:
    调 chunk 大小、改 BM25 权重、加查询规划……每次改动到底变好还是变坏?
    "感觉好像好一点" 不可靠。有了固定评测集, 改完跑一遍就知道数字。

两种运行方式:
    --via-api (默认)  通过 HTTP 调正在运行的服务, 复用服务已加载的索引 → 快(秒级)
    --local          在脚本进程里自己加载索引 → 慢(首次 3~5 分钟), 但不依赖服务

不调大模型(只跑检索), 所以不受网关抽风影响, 结果可复现。

用法:
    python eval/run.py                       默认(API 模式), 跑 questions.json 指定的库
    python eval/run.py --kb 某资管              按名字模糊匹配知识库
    python eval/run.py --topk 8 --verbose    看每题命中了哪个片段
    python eval/run.py --save baseline.json  存基线
    python eval/run.py --compare baseline.json   与基线对比
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT))


def load_set(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _reg():
    import indexer
    return indexer.load_registry()


def pick_kb(name):
    reg = _reg()
    if not reg:
        return None
    if not name:
        return reg[0]
    for k in reg:
        if name == k.get("id") or name in (k.get("name") or ""):
            return k
    parts = [p for p in name.split() if p]
    cand = [k for k in reg if any(p in (k.get("name") or "") for p in parts)]
    return cand[0] if cand else None


def hit_ok(item, results, topk):
    """判断这道题是否召回成功, 返回 (ok, 最佳排名)。"""
    file_any = [s.lower() for s in (item.get("file_any") or [])]
    kw_any = [s.lower() for s in (item.get("keywords_any") or [])]
    for rank, r in enumerate(results[:topk], 1):
        path = (r.get("path") or "").lower()
        text = (r.get("text") or "").lower()
        if file_any and not any(f in path for f in file_any):
            continue
        if kw_any and not any(w in text or w in path for w in kw_any):
            continue
        return True, rank
    # 放宽判定: 文件名标注可能过严, 只要关键词出现在任意片段就算
    if kw_any:
        for rank, r in enumerate(results[:topk], 1):
            blob = ((r.get("path") or "") + " " + (r.get("text") or "")).lower()
            if any(w in blob for w in kw_any):
                return True, rank
    return False, 0


# ---------------- 两种检索后端 ----------------

class ApiBackend:
    """通过 HTTP 复用服务已加载的索引(快)。"""
    def __init__(self, kb_id):
        import httpx
        import auth
        self.kb_id = kb_id
        self.c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=180)
        pwd = ""
        try:
            pwd = auth.load_auth_cfg().get("password") or ""
        except Exception:
            pass
        if pwd:
            r = self.c.post("/api/login", json={"password": pwd})
            if r.status_code != 200:
                raise RuntimeError("登录失败, 检查 config.auth.password")
        self.kind = "模式: API(复用服务已加载索引)"

    def prepare(self):
        pass

    def search(self, q, topk):
        r = self.c.get(f"/api/kb/{self.kb_id}/debug_search",
                       params={"q": q, "top_k": topk})
        d = r.json()
        if "sources" not in d:
            raise RuntimeError(d.get("msg") or f"HTTP {r.status_code}")
        return d.get("sources") or []


class LocalBackend:
    """在脚本进程里自己加载索引(慢, 但不依赖服务)。"""
    def __init__(self, kb_id):
        self.kb_id = kb_id
        self.kind = "模式: 本地(自己加载索引)"

    def prepare(self):
        import kb
        print("索引: 加载中…", end="", flush=True)
        t0 = time.time()
        ca = kb.load_kb_indexes(self.kb_id)
        if ca is None:
            raise RuntimeError("知识库没有索引, 先重建索引")
        print(f" 完成({time.time()-t0:.1f}s, {ca.N} 块)", flush=True)

    def search(self, q, topk):
        import kb
        return kb.hybrid_search(self.kb_id, q, topk=topk) or []


def run(args):
    qset = load_set(args.set)
    name = args.kb or qset.get("kb", "")
    k = pick_kb(name)
    if not k:
        print(f"[错误] 找不到知识库: {name}")
        print("现有:", [(x["name"], x["id"]) for x in _reg()])
        return 2

    backend = LocalBackend(k["id"]) if args.local else ApiBackend(k["id"])
    print(f"知识库: {k['name']}  ({k['id']})", flush=True)
    print(f"评测集: {args.set}  ({len(qset['items'])} 题)  top-k={args.topk}  {backend.kind}", flush=True)
    backend.prepare()
    print(flush=True)

    rows, ranks = [], []
    t_all = time.time()
    n_items = len(qset["items"])
    for i, item in enumerate(qset["items"], 1):
        q = item["q"]
        t1 = time.time()
        try:
            res = backend.search(q, args.topk)
        except Exception as e:
            print(f"  [{i:>2}/{n_items}] ERR  {q}  ({e})", flush=True)
            rows.append({"q": q, "ok": False, "rank": 0, "ms": 0, "top": [], "err": str(e)})
            continue
        dt = time.time() - t1
        ok, rank = hit_ok(item, res, args.topk)
        if ok:
            ranks.append(rank)
        rows.append({"q": q, "ok": ok, "rank": rank, "ms": int(dt * 1000),
                     "top": [[r.get("path"), round(float(r.get("score") or 0), 1)] for r in res[:3]]})
        mark = "OK " if ok else "MISS"
        print(f"  [{i:>2}/{n_items}] {mark} r={rank or '-'} {dt:>5.1f}s  {q}", flush=True)
        if args.verbose:
            for p, sc in rows[-1]["top"]:
                print(f"          · {sc}  {p}", flush=True)

    total_time = time.time() - t_all
    n = n_items
    hits = len(ranks)
    recall = hits / n if n else 0
    mrr = sum(1.0 / r for r in ranks) / n if n else 0
    avg_rank = (sum(ranks) / len(ranks)) if ranks else 0

    print("\n" + "=" * 62, flush=True)
    print(f"  recall@{args.topk}   : {recall*100:.1f}%   ({hits}/{n})", flush=True)
    print(f"  MRR           : {mrr:.3f}", flush=True)
    print(f"  命中平均排名  : {avg_rank:.2f}", flush=True)
    print(f"  总耗时        : {total_time:.1f}s", flush=True)
    print("=" * 62, flush=True)

    misses = [r["q"] for r in rows if not r["ok"]]
    if misses:
        print("\n未命中(优先补这些内容, 或调检索):", flush=True)
        for q in misses:
            print(f"  · {q}", flush=True)

    result = {"kb": k["name"], "kb_id": k["id"], "set": str(args.set),
              "topk": args.topk, "n": n, "recall": round(recall, 4), "mrr": round(mrr, 4),
              "avg_rank": round(avg_rank, 2), "total_s": round(total_time, 1),
              "backend": "local" if args.local else "api",
              "rows": rows, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    if args.save:
        p = Path(args.save)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已保存: {p}", flush=True)

    if args.compare:
        cp = Path(args.compare)
        if not cp.is_absolute():
            cp = ROOT / cp
        try:
            base = json.loads(cp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"\n[警告] 读不到基线: {e}", flush=True)
            return 0
        print(f"\n与基线对比（{base.get('ts')}: recall {base['recall']*100:.1f}%, MRR {base['mrr']:.3f}）", flush=True)
        d_recall = (recall - base["recall"]) * 100
        d_mrr = mrr - base["mrr"]
        arrow = lambda x: ("↑" if x > 0.05 else ("↓" if x < -0.05 else "→"))
        print(f"  recall : {recall*100:.1f}%  {arrow(d_recall)} {d_recall:+.1f} 个百分点", flush=True)
        print(f"  MRR    : {mrr:.3f}     {arrow(d_mrr)} {d_mrr:+.3f}", flush=True)
        bmap = {r["q"]: r for r in base.get("rows", [])}
        changed = [(r, bmap[r["q"]]) for r in rows if r["q"] in bmap and bmap[r["q"]]["ok"] != r["ok"]]
        if changed:
            print("\n  状态变化:", flush=True)
            for r, b in changed:
                print(f"    {'✅ 变好' if r['ok'] else '❌ 变差'}  {r['q']}", flush=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "eval" / "questions.json"))
    ap.add_argument("--kb", default="")
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--save", default="")
    ap.add_argument("--compare", default="")
    ap.add_argument("--local", action="store_true", help="自己加载索引(慢), 默认走服务 API")
    sys.exit(run(ap.parse_args()))
