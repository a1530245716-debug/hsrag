# -*- coding: utf-8 -*-
"""测网关并发能力：同时发 N 个请求，看是否排队 / 报错 / 变慢。"""
import concurrent.futures as cf
import sys
import time

sys.path.insert(0, "app")
import llm  # noqa: E402

PROMPT = "用一句话说明什么是数据库索引。"
N = 5


def one(i):
    t0 = time.time()
    first = None
    chars = 0
    err = ""
    meta = {}
    try:
        for tok in llm.stream([{"role": "user", "content": PROMPT}], meta=meta):
            if first is None:
                first = time.time() - t0
            chars += len(tok)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:60]}"
    return {
        "i": i, "total": time.time() - t0, "first": first, "chars": chars,
        "err": err, "sr": meta.get("stop_reason", ""),
    }


print(f"模型: {llm.llm_settings().get('model')}")
print(f"配置: max_tokens={llm.read_timeout() if hasattr(llm,'read_timeout') else '?'}\n")

# 1) 先测单个作为基线
print("【基线】单个请求")
t0 = time.time()
r = one(0)
print(f"  首字 {r['first']:.1f}s  总 {r['total']:.1f}s  {r['chars']}字  {r['err'] or 'OK'}\n")
base = r["total"]

# 2) 并发 N 个
print(f"【并发】同时发 {N} 个")
t0 = time.time()
with cf.ThreadPoolExecutor(max_workers=N) as ex:
    rs = list(ex.map(one, range(1, N + 1)))
wall = time.time() - t0

ok = [r for r in rs if not r["err"]]
bad = [r for r in rs if r["err"]]
print(f"  墙钟总耗时 {wall:.1f}s  成功 {len(ok)}/{N}")
for r in rs:
    st = f"首字{r['first']:.1f}s 总{r['total']:.1f}s {r['chars']}字" if not r["err"] else f"失败 {r['err']}"
    print(f"    #{r['i']}  {st}")

if ok:
    avg = sum(r["total"] for r in ok) / len(ok)
    print(f"\n  单个平均 {avg:.1f}s  vs 基线 {base:.1f}s  → 慢 {avg/base:.1f} 倍")
    serial = sum(r["total"] for r in ok)
    print(f"  串行需要 {serial:.1f}s，并发实际 {wall:.1f}s  → 提速 {serial/wall:.1f}x")
    print(f"  结论：{'可以并行' if wall < serial*0.7 and not bad else '并行受限（疑似排队或限流）'}")
