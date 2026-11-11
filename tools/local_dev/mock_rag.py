# -*- coding: utf-8 -*-
"""本地假 RAG 后端（不依赖 Docker / RAGFlow）。

模拟 RAGFlow 的核心交互：
  GET  /health
  GET  /api/v1/datasets
  POST /api/v1/retrieval      {"query": "...", "top_k": 5}
  POST /api/v1/chat/stream    {"query": "..."} -> SSE
  GET  /stats

检索是简单的关键词/汉字 bigram 打分，生成是模板拼接，仅用于把 BFF 链路跑通。
以后接 RAGFlow 时，只换 BFF 的后端地址，不改业务逻辑。
"""
import argparse
import asyncio
import json
import re
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

HERE = Path(__file__).resolve().parent
DOCS_DIR = HERE / "demo_docs"

app = FastAPI(title="mock-rag")
_STATS = {"started": 0, "active": 0, "completed": 0}


def _load_chunks():
    chunks = []
    for p in sorted(DOCS_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        parts = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
        buf = ""
        for part in parts:
            if buf and len(buf) + len(part) > 500:
                chunks.append({"id": f"{p.stem}-{len(chunks)}",
                               "document": p.name, "text": buf})
                buf = ""
            buf = (buf + "\n\n" + part).strip() if buf else part
        if buf:
            chunks.append({"id": f"{p.stem}-{len(chunks)}",
                           "document": p.name, "text": buf})
    return chunks


CHUNKS = _load_chunks()


def _tokens(s):
    s = (s or "").lower()
    words = re.findall(r"[a-z0-9_]+", s)
    cjk = re.findall(r"[一-鿿]", s)
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return words + cjk + bigrams


def _retrieve(query, top_k=5):
    qt = Counter(_tokens(query))
    if not qt:
        return []
    scored = []
    for ch in CHUNKS:
        ct = Counter(_tokens(ch["text"]))
        score = sum(min(qt[t], ct[t]) for t in qt)
        if score:
            scored.append((score, ch))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, ch in scored[:max(1, min(top_k, 10))]:
        out.append({"id": ch["id"], "document": ch["document"],
                    "text": ch["text"], "score": round(float(score), 3)})
    return out


def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


@app.get("/health")
async def health():
    return {"ok": True, "documents": len({c["document"] for c in CHUNKS}),
            "chunks": len(CHUNKS)}


@app.get("/stats")
async def stats():
    return dict(_STATS)


@app.get("/api/v1/datasets")
async def datasets():
    return {"data": [{"id": "demo", "name": "FDE Demo KB"}]}


@app.post("/api/v1/retrieval")
async def retrieval(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = str(body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query 不能为空"}, status_code=400)
    top_k = int(body.get("top_k") or 5)
    hits = _retrieve(query, top_k)
    return {"data": [{"id": h["id"], "content": h["text"],
                      "document": h["document"], "score": h["score"]}
                     for h in hits]}


@app.post("/api/v1/chat/stream")
async def chat_stream(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = str(body.get("query") or "").strip()
    hits = _retrieve(query, 3)

    async def gen():
        _STATS["started"] += 1
        _STATS["active"] += 1
        try:
            if not hits:
                yield _sse({"type": "delta", "text": "知识库中没有找到相关内容。"})
                yield _sse({"type": "done", "sources": []})
                return
            answer = "根据演示知识库，整理如下：\n\n"
            for i, h in enumerate(hits, 1):
                answer += f"[{i}] {h['text'][:300]}\n\n"
            answer += "来源：" + "、".join(sorted({h["document"] for h in hits}))
            for i in range(0, len(answer), 4):
                yield _sse({"type": "delta", "text": answer[i:i + 4]})
                await asyncio.sleep(0.015)
            yield _sse({"type": "done", "sources": [
                {"document": h["document"], "id": h["id"], "score": h["score"]}
                for h in hits]})
            _STATS["completed"] += 1
        finally:
            _STATS["active"] -= 1

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9390)
    args = ap.parse_args()
    import uvicorn
    print(f"[mock-rag] http://{args.host}:{args.port} "
          f"chunks={len(CHUNKS)}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
