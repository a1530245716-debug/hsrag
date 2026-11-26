# -*- coding: utf-8 -*-
"""模块对抗评测：检查“引用来源是否属于问题限定的业务模块”。

用法：
    python eval/run_module_eval.py
    RAG_EVAL_BASE=http://10.20.154.26:8000 RAG_EVAL_USER=admin RAG_EVAL_PASS=... \
        python eval/run_module_eval.py

和 run_eval.py 的区别：
  - 不看 must_include_any 关键词，只看 sources 的模块归属；
  - 目标模块之外的来源算“错误引用”，用来验证模块路由/过滤是否生效；
  - 结果写到 eval/results_module/，方便改检索前后对比。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "app"))

import module_router  # noqa: E402

BASE = os.environ.get("RAG_EVAL_BASE", "http://127.0.0.1:8000").strip()
USER_NAME = os.environ.get("RAG_EVAL_USER", "1000").strip()
USER_PASS = os.environ.get("RAG_EVAL_PASS", "xhy200326").strip()
TOKEN = os.environ.get("RAG_EVAL_TOKEN", "").strip()


def _check(case, answer, sources):
    expected = case.get("expected_modules") or []
    forbidden = case.get("forbidden_modules") or []
    min_precision = float(case.get("min_precision", 0.8))
    total = len(sources or [])
    good = [s for s in (sources or []) if module_router.matches(s, expected)] if expected else []
    # 综合手册同时覆盖多个模块；它命中了目标模块就不算“错误引用”，
    # 否则“开放式基金/期货”题会因为引用了同一本 O3.2 用户手册被误判。
    bad = []
    if forbidden:
        for s in (sources or []):
            if expected and module_router.matches(s, expected):
                continue
            if module_router.matches(s, forbidden):
                bad.append(s)
    precision = (len(good) / total) if total else 0.0
    warned = "引用校验" in (answer or "")
    low = (answer or "").lower()
    missing = []
    for group in case.get("must_include_any") or []:
        if not any(w.lower() in low for w in group):
            missing.append("/".join(group))
    min_len = int(case.get("min_len") or 0)
    content_ok = not missing and len(answer or "") >= min_len
    if expected:
        passed = (total > 0 and precision >= min_precision and not bad and not warned
                  and content_ok)
    else:
        passed = total > 0 and not warned and content_ok
    return {
        "passed": passed,
        "total": total,
        "good": len(good),
        "bad": len(bad),
        "precision": round(precision, 3),
        "warned": warned,
        "content_ok": content_ok,
        "missing": missing,
        "answer_len": len(answer or ""),
        "expected": expected,
        "forbidden": forbidden,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-f", "--file", default=str(HERE / "module_cases.json"))
    ap.add_argument("-n", "--limit", type=int, default=0)
    ap.add_argument("--kb", default="", help="覆盖评测集里的 kb_id")
    args = ap.parse_args()

    import httpx
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    cases = spec["cases"][:args.limit or None]
    kb = args.kb or os.environ.get("RAG_EVAL_KB") or spec["kb_id"]

    out_dir = HERE / "results_module"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = []
    with httpx.Client(base_url=BASE, timeout=600, trust_env=False) as c:
        headers = {}
        if TOKEN:
            headers = {"X-RAG-Token": TOKEN}
        else:
            r = c.post("/api/login", json={"username": USER_NAME, "password": USER_PASS})
            if r.status_code != 200:
                print("登录失败:", r.status_code, r.text[:200])
                return 1
        print(f"模块评测: {spec.get('name')}  知识库: {spec.get('kb_name') or kb}  共 {len(cases)} 条\n")
        for i, case in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {case['id']} {case['question'][:44]}")
            t0 = time.time()
            try:
                r = c.post(f"/api/kb/{kb}/ask", json={"question": case["question"]},
                           headers=headers, timeout=600)
                d = r.json()
            except Exception as e:
                d = {"answer": f"[请求异常] {type(e).__name__}: {e}", "sources": []}
            answer = d.get("answer") or ""
            sources = d.get("sources") or []
            res = _check(case, answer, sources)
            rows.append({
                "id": case["id"], "topic": case.get("topic"),
                "question": case["question"], "seconds": round(time.time() - t0, 1),
                "answer": answer, "sources": [
                    {"path": s.get("path"), "text": (s.get("text_full") or s.get("text") or "")[:80]}
                    for s in sources
                ], **res,
            })
            flag = "PASS" if res["passed"] else "FAIL"
            print(f"      {flag}  来源 {res['good']}/{res['total']} 命中目标模块"
                  f"（精度 {res['precision']:.0%}，错误引用 {res['bad']}）"
                  f"{'，含引用警告' if res['warned'] else ''}"
                  f"{'，缺内容: ' + ','.join(res['missing']) if res['missing'] else ''}")
            if sources:
                for s in sources[:4]:
                    print(f"        - {s.get('path')}")
            time.sleep(1)

    (out_dir / f"{stamp}.json").write_text(
        json.dumps({"spec": spec["name"], "kb": kb, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    ok = sum(1 for r in rows if r["passed"])
    md = [f"# 模块对抗评测 {stamp}\n", f"知识库: {spec.get('kb_name') or kb}\n",
          f"**通过 {ok}/{len(rows)}**\n"]
    for r in rows:
        md.append(f"\n## {r['id']} {r.get('topic')} — {'PASS' if r['passed'] else 'FAIL'}\n")
        md.append(f"**问**：{r['question']}\n\n")
        md.append(f"目标模块 {r['expected']}；来源 {r['good']}/{r['total']}，"
                  f"精度 {r['precision']:.0%}，错误引用 {r['bad']}\n\n")
        for s in r["sources"]:
            md.append(f"- `{s['path']}` {s['text']}\n")
        md.append(f"\n**答**：\n\n```\n{r['answer'][:2500]}\n```\n")
    (out_dir / f"{stamp}.md").write_text("".join(md), encoding="utf-8")

    print(f"\n通过 {ok}/{len(rows)}")
    print(f"结果: eval/results_module/{stamp}.json  /  {stamp}.md")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
