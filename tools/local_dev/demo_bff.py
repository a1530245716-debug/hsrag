# -*- coding: utf-8 -*-
"""异步 BFF 演示：把检索和流式回答从 Web 线程里拿出去。

- 端到端 async：FastAPI async endpoint + httpx.AsyncClient
- 共享连接池，不每次新建连接
- 全局并发信号量，超载返回 429
- SSE 转发，不占线程池

  RAG_URL=http://127.0.0.1:9390 python tools/local_dev/demo_bff.py --port 8020
"""
import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

HERE = Path(__file__).resolve().parent
RAG_URL = os.environ.get("RAG_URL", "http://127.0.0.1:9390")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "20"))
_sem = asyncio.Semaphore(MAX_CONCURRENT)
_active = 0

INDEX_HTML = """<!doctype html>
<meta charset="utf-8">
<title>FDE RAG Demo</title>
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:860px;margin:40px auto;padding:0 16px;color:#1f2933}
 h1{font-size:22px} .muted{color:#6b7280;font-size:13px}
 input{width:70%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;font-size:15px}
 button{padding:10px 16px;border:0;border-radius:8px;background:#2563eb;color:#fff;font-size:15px;cursor:pointer}
 #sources{margin:14px 0} .chip{display:inline-block;background:#eef2ff;color:#3730a3;padding:3px 8px;border-radius:999px;font-size:12px;margin:2px}
 pre{white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:16px;min-height:180px}
</style>
<h1>企业 RAG · FDE Demo</h1>
<p class="muted">本地演示：假 RAG 后端 + 异步 BFF。检索是关键词打分，生成是模板拼接，仅用于展示链路与并发处理。</p>
<p><input id="q" placeholder="例如：部署需要哪些安装包？" value="部署需要哪些安装包？">
<button onclick="ask()">提问</button></p>
<div id="sources"></div>
<pre id="out"></pre>
<script>
async function ask(){
  const q=document.getElementById('q').value.trim(); if(!q) return;
  const out=document.getElementById('out'); out.textContent='';
  const src=document.getElementById('sources'); src.innerHTML='';
  const resp=await fetch('/api/ask/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
  if(!resp.ok){ out.textContent='HTTP '+resp.status+' '+(await resp.text()); return; }
  const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
  while(true){
    const {value,done}=await reader.read(); if(done) break;
    buf+=dec.decode(value,{stream:true});
    let idx;
    while((idx=buf.indexOf('\\n\\n'))>=0){
      const block=buf.slice(0,idx); buf=buf.slice(idx+2);
      for(const line of block.split('\\n')){
        if(!line.startsWith('data:')) continue;
        let obj; try{ obj=JSON.parse(line.slice(5).trim()); }catch(e){ continue; }
        if(obj.type==='status') out.textContent += '['+obj.text+']\\n';
        else if(obj.type==='sources') src.innerHTML=obj.sources.map(s=>'<span class="chip">'+s.document+'</span>').join('');
        else if(obj.type==='delta'||obj.type==='answer') out.textContent += obj.text;
        else if(obj.type==='error') out.textContent += '\\n[错误] '+obj.text;
      }
    }
  }
}
</script>
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        base_url=RAG_URL,
        timeout=httpx.Timeout(connect=5, read=60, write=10, pool=10),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=50),
    )
    yield
    await app.state.client.aclose()


app = FastAPI(title="fde-demo-bff", lifespan=lifespan)


def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


@app.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@app.get("/api/health")
async def health():
    try:
        r = await app.state.client.get("/health", timeout=2)
        rag = r.json() if r.status_code == 200 else {}
        return {"ok": r.status_code == 200, "rag": rag}
    except Exception as e:
        return JSONResponse({"ok": False, "error": type(e).__name__}, status_code=503)


@app.get("/api/stats")
async def stats():
    return {"active": _active, "max_concurrent": MAX_CONCURRENT}


@app.post("/api/ask/stream")
async def ask_stream(request: Request):
    global _active
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "msg": "问题不能为空"}, status_code=400)
    try:
        await asyncio.wait_for(_sem.acquire(), timeout=5)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "msg": "系统繁忙，请稍后重试"},
                            status_code=429, headers={"Retry-After": "5"})
    _active += 1

    async def gen():
        global _active
        try:
            yield _sse({"type": "status", "text": "正在检索…"})
            r = await app.state.client.post("/api/v1/retrieval",
                                            json={"query": question, "top_k": 5})
            hits = (r.json() or {}).get("data", []) if r.status_code == 200 else []
            srcs = [{"document": h.get("document"), "id": h.get("id"),
                     "score": h.get("score")} for h in hits]
            yield _sse({"type": "sources", "sources": srcs})
            if not hits:
                yield _sse({"type": "answer", "text": "知识库中没有找到相关内容。"})
                yield _sse({"type": "done"})
                return
            yield _sse({"type": "status", "text": "正在生成回答…"})
            async with app.state.client.stream(
                    "POST", "/api/v1/chat/stream",
                    json={"query": question}) as resp:
                if resp.status_code != 200:
                    yield _sse({"type": "error", "text": f"RAG HTTP {resp.status_code}"})
                else:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            obj = json.loads(line[5:].strip())
                        except Exception:
                            continue
                        if obj.get("type") == "done":
                            break
                        yield line + "\n\n"
            yield _sse({"type": "done"})
        except Exception as e:
            yield _sse({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            _active -= 1
            _sem.release()

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8020)
    args = ap.parse_args()
    import uvicorn
    print(f"[bff] http://{args.host}:{args.port} -> {RAG_URL} "
          f"max_concurrent={MAX_CONCURRENT}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
