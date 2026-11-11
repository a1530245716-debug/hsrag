# -*- coding: utf-8 -*-
"""本地假大模型：兼容 llm.py 使用的 Anthropic Messages 协议。

用途：把上游大模型从压测变量里拿掉，只测本项目的 HTTP/检索/重排/线程模型。
支持流式与非流式；TTFT、每段间隔、错误率都可配。

  python tools/loadtest/mock_llm.py --port 9099 --tokens 200 --token-delay 0.02
"""
import argparse
import asyncio
import json
import random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

CFG = {
    "tokens": 200,
    "token_delay": 0.02,
    "ttft": 0.3,
    "nonstream_delay": 0.05,
    "error_rate": 0.0,
    "empty_rate": 0.0,
    "chunk": "这是压测用的模拟回答片段。",
    "model": "mock-model",
}

app = FastAPI(title="mock-llm")
_STATS = {"started": 0, "active": 0, "completed": 0, "errors": 0}


def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/stats")
async def stats():
    return dict(_STATS)


@app.post("/reset")
async def reset():
    for k in _STATS:
        _STATS[k] = 0
    return dict(_STATS)


@app.post("/v1/messages")
async def messages(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    if CFG["error_rate"] and random.random() < CFG["error_rate"]:
        _STATS["errors"] += 1
        return JSONResponse(
            {"type": "error", "error": {"type": "overloaded_error",
                                        "message": "mock upstream error"}},
            status_code=503,
        )

    if not body.get("stream"):
        _STATS["started"] += 1
        await asyncio.sleep(CFG["nonstream_delay"])
        _STATS["completed"] += 1
        return JSONResponse({
            "id": "msg_mock", "type": "message", "role": "assistant",
            "model": CFG["model"],
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    async def gen():
        _STATS["started"] += 1
        _STATS["active"] += 1
        try:
            yield _sse({"type": "message_start", "message": {
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": CFG["model"], "content": [], "stop_reason": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            }})
            yield _sse({"type": "content_block_start", "index": 0,
                        "content_block": {"type": "text", "text": ""}})
            if not (CFG["empty_rate"] and random.random() < CFG["empty_rate"]):
                await asyncio.sleep(CFG["ttft"])
                for _ in range(CFG["tokens"]):
                    yield _sse({"type": "content_block_delta", "index": 0,
                                "delta": {"type": "text_delta", "text": CFG["chunk"]}})
                    await asyncio.sleep(CFG["token_delay"])
            yield _sse({"type": "content_block_stop", "index": 0})
            yield _sse({"type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": CFG["tokens"]}})
            yield _sse({"type": "message_stop"})
            _STATS["completed"] += 1
        finally:
            _STATS["active"] -= 1

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9099)
    ap.add_argument("--tokens", type=int, default=200)
    ap.add_argument("--token-delay", type=float, default=0.02)
    ap.add_argument("--ttft", type=float, default=0.3)
    ap.add_argument("--nonstream-delay", type=float, default=0.05)
    ap.add_argument("--error-rate", type=float, default=0.0)
    ap.add_argument("--empty-rate", type=float, default=0.0)
    args = ap.parse_args()

    CFG.update({
        "tokens": args.tokens,
        "token_delay": args.token_delay,
        "ttft": args.ttft,
        "nonstream_delay": args.nonstream_delay,
        "error_rate": args.error_rate,
        "empty_rate": args.empty_rate,
    })

    import uvicorn
    print(f"[mock] http://{args.host}:{args.port}  tokens={args.tokens} "
          f"delay={args.token_delay}s ttft={args.ttft}s", flush=True)
    uvicorn.run(app, host=args.host, port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
