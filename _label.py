# -*- coding: utf-8 -*-
"""标注：拿一批真实问答，把每条 (段落,图) 组合连同分数列出来，人工判断对不对。"""
import json
import sys

import httpx

sys.path.insert(0, "app")
import imgindex  # noqa: E402

KB = "20261019_121148_5a7a33"
BASE = "http://127.0.0.1:8000"

QS = [
    "发布物怎么上传",
    "O-order 部署时服务器怎么添加",
    "数据库怎么配置",
    "应用配置里的安装目录填什么",
    "demo 部署前需要准备什么",
    "系统架构是什么样的",
]

c = httpx.Client(timeout=300.0)
hdr = {"X-RAG-Token": c.post(f"{BASE}/api/login",
                             json={"user": "1000", "password": "xhy200326"}).json()["token"]}

out = []
for q in QS:
    ans, srcs = [], None
    with c.stream("POST", f"{BASE}/api/kb/{KB}/ask/stream",
                  json={"question": q, "no_cache": True}, headers=hdr) as s:
        for line in s.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:].strip())
            except Exception:
                continue
            if d.get("type") == "delta":
                ans.append(d.get("text", ""))
            elif d.get("type") == "sources":
                srcs = d.get("sources") or []
    full = "".join(ans)
    paths = {x.get("path") for x in (srcs or []) if x.get("path")}
    # 放宽到 0.45、每段 4 张，好看到更多样本
    blocks = imgindex.bind_to_answer(KB, full, source_paths=paths, sources=srcs,
                                     min_sim=0.45, max_per_para=4, max_total=40)
    print(f"\n{'#'*72}\n# {q}\n{'#'*72}")
    for b in blocks:
        print(f"\n【段落 {b['para_index']}】{b['para'].strip()[:100]}")
        for im in b["images"]:
            out.append({"q": q, "para": b["para"][:80], "sim": im["sim"],
                        "summary": im["summary"], "name": im["name"]})
            print(f"   {im['sim']:.3f}  {im['summary'][:70]}")

print(f"\n\n共 {len(out)} 条候选")
json.dump(out, open("_label.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
c.close()
