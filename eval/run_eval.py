# -*- coding: utf-8 -*-
"""跑评测集，把每条问答的回答、引用、耗时、命中情况记下来。

用法：
    python eval/run_eval.py                 # 跑默认评测集
    python eval/run_eval.py -f 其他.json    # 跑指定评测集
    python eval/run_eval.py -n 3            # 只跑前 3 条

结果写到 eval/results/<时间戳>.json 和 .md，便于改动前后对比。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = "http://127.0.0.1:8000"
# 评测用普通用户身份。账号密码登录（旧的单口令 key 已作废）
USER_NAME = "1000"
USER_PASS = "xhy200326"


def check(case, answer, sources):
    """按 must_include_any 检查命中情况。每组里命中任意一个词即算该组通过。"""
    hit, miss = [], []
    low = (answer or "").lower()
    for group in case.get("must_include_any") or []:
        if any(w.lower() in low for w in group):
            hit.append("/".join(group))
        else:
            miss.append("/".join(group))
    min_len = case.get("min_len") or 0
    max_len = case.get("max_len") or 0
    n = len(answer or "")
    long_enough = n >= min_len
    short_enough = (n <= max_len) if max_len else True
    truncated = "⚠️ 回答未完" in (answer or "")
    passed = (not miss) and long_enough and short_enough and not truncated
    return {
        "hit": hit, "miss": miss,
        "len": n,
        "min_len": min_len,
        "max_len": max_len,
        "long_enough": long_enough,
        "short_enough": short_enough,
        "truncated": truncated,
        "passed": passed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", "--file", default=str(HERE / "xinyuan_cases.json"))
    ap.add_argument("-n", "--limit", type=int, default=0)
    args = ap.parse_args()

    import httpx
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    cases = spec["cases"][:args.limit or None]
    kb = spec["kb_id"]

    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    rows = []
    with httpx.Client(base_url=BASE, timeout=600, trust_env=False) as c:
        r = c.post("/api/login", json={"username": USER_NAME, "password": USER_PASS})
        if r.status_code != 200:
            print("登录失败:", r.status_code, r.text[:200])
            return 1
        print(f"评测集: {spec.get('name')}  知识库: {spec.get('kb_name')}  共 {len(cases)} 条\n")

        for i, case in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {case['id']} {case['question'][:44]}")
            t0 = time.time()
            try:
                r = c.post(f"/api/kb/{kb}/ask", json={"question": case["question"]}, timeout=600)
                d = r.json()
                answer = d.get("answer") or ""
                sources = d.get("sources") or []
            except Exception as e:
                answer, sources = f"[请求异常] {type(e).__name__}: {e}", []
            cost = time.time() - t0
            res = check(case, answer, sources)
            rows.append({
                "id": case["id"], "topic": case.get("topic"), "question": case["question"],
                "answer": answer, "sources": [s.get("path") for s in sources],
                "seconds": round(cost, 1), **res,
            })
            flag = "PASS" if res["passed"] else "FAIL"
            why = []
            if res["miss"]:
                why.append("缺:" + ",".join(res["miss"]))
            if not res["long_enough"]:
                why.append(f"太短(<{res['min_len']})")
            if not res.get("short_enough", True):
                why.append(f"太长(>{res['max_len']})")
            if res["truncated"]:
                why.append("被截断")
            print(f"      {flag}  {res['len']}字 {cost:.0f}s 引用{len(sources)}条"
                  f"  {'; '.join(why) or 'OK'}")
            time.sleep(1)

    (out_dir / f"{stamp}.json").write_text(
        json.dumps({"spec": spec["name"], "kb": kb, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 可读版
    md = [f"# 评测结果 {stamp}\n", f"评测集: {spec.get('name')}  知识库: {spec.get('kb_name')}\n"]
    ok = sum(1 for r in rows if r["passed"])
    md.append(f"**通过 {ok}/{len(rows)}**\n")
    for r in rows:
        md.append(f"\n## {r['id']} {r['topic']} — {'PASS' if r['passed'] else 'FAIL'}\n")
        md.append(f"**问**：{r['question']}\n\n")
        md.append(f"耗时 {r['seconds']}s · 回答 {r['len']} 字 · 引用 {len(r['sources'])} 条")
        if r["miss"]:
            md.append(f" · **缺**：{', '.join(r['miss'])}")
        if not r.get("long_enough", True):
            md.append(f" · **太短**（要求 ≥{r['min_len']} 字）")
        if not r.get("short_enough", True):
            md.append(f" · **太长**（要求 ≤{r['max_len']} 字，实际 {r['len']}）")
        if r.get("truncated"):
            md.append(" · **回答被截断**")
        md.append("\n\n")
        if r["sources"]:
            md.append("引用：\n")
            for s in r["sources"]:
                md.append(f"- {s}\n")
        md.append(f"\n**答**：\n\n```\n{r['answer'][:3000]}\n```\n")
    (out_dir / f"{stamp}.md").write_text("".join(md), encoding="utf-8")

    print(f"\n通过 {ok}/{len(rows)}")
    print(f"结果: eval/results/{stamp}.json  /  {stamp}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
