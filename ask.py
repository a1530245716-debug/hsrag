# -*- coding: utf-8 -*-
"""智能交易平台交接材料 · 交互式问答检索

用法:
  python ask.py                           # 进入交互式问答
  python ask.py -q "上线时间 三批  逆回购"   # 单次提问后退出

特性:
  - 中文分词 + BM25 检索,命中片段按来源文件分组展示
  - 高亮命中关键词,显示得分、所在行、文件名与章节
  - 交互命令: /top N   /n K   /raw   /exit   /help
"""
import os, sys, re, argparse
from pathlib import Path

ROOT = Path(os.environ.get("RAG_ROOT") or os.getcwd())
sys.path.insert(0, str(ROOT))
from query import load_index, tokenize

TOPK = 8


def _hl(text, tokens, n=260):
    """截断 + 关键命中词高亮(用 *包裹)。"""
    needles = [t for t in tokens if len(t) >= 2][:8]
    res = text.strip().replace("\n", " ⏎ ")[:n]
    if needles:
        pat = re.compile("|".join(map(re.escape, sorted(needles, key=len, reverse=True))))
        res = pat.sub(lambda m: "*" + m.group(0) + "*", res)
    return res


def fmt(results, tokens, raw=False):
    by = {}
    for r in results:
        by.setdefault(r["path"], []).append(r)
    out = []
    for path, items in by.items():
        seg = path.split("/")
        name = seg[-1]
        folder = "/".join(seg[:-1])
        hits = len(items)
        top = max(i["score"] for i in items)
        out.append(f"\n━━─ {name}  (命中{ hits }段,最高{top:.0f})")
        out.append(f"    目录: {folder}")
        for r in items[:-1]:
            out.append(f"    · [{r['score']:.0f}] {_hl(r['text'], tokens, 150)}")
        end = items[-1]
        segstrs = [c.strip() for c in end["text"].split("\n") if c.strip()]
        for i, ln in enumerate(segstrs, 1):
            out.append(f"    [{end['score']:.0f}|{i}] {_hl(ln, tokens, 240)}")
        if raw:
            footer = f"    文件: {path} | 片段: {end['id']}"
            out.append(footer)
    return "\n".join(out)


def answer(q):
    bm = load_index()
    toks = tokenize(q)
    results = bm.query(q, topk=args.n)
    if not results:
        return "未检索到相关内容。可换更具体的关键词试试,或分开提问。"
    return fmt(results, toks, raw=args.raw)


ap = argparse.ArgumentParser()
ap.add_argument("-q", "--query", help="单次提问(带则执行后退出)")
ap.add_argument("-k", "--top", type=int, default=8, help="返回片段数")
ap.add_argument("--n", type=int, default=0, help="同 -k")
ap.add_argument("-r", "--raw", action="store_true", help="显示完整片段与文件定位")
args = ap.parse_args()
if args.n == 0:
    args.n = args.top

if args.query:
    print(answer(args.query))
    sys.exit(0)

print("智能交易平台交接材料 · 问答助手")
print("直接输入问题检索;输入 /exit 退出, /top N 调整数量, /raw 切换详细模式")
while True:
    try:
        q = input("\n问 ❯ ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not q:
        continue
    if q == "/exit":
        break
    if q.startswith("/top "):
        args.n = int(q.split()[1]); print(f"已切换 top {args.n}"); continue
    if q == "/help":
        print("命令: /top N  /n K  /raw  /exit"); continue
    print(answer(q))
print("\n再见。")
