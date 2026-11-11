# -*- coding: utf-8 -*-
"""在独立端口启动一个测试实例，指向本地假大模型。

隔离措施：
- 不写 qa_log（避免污染真实问答日志）
- 关闭答案配图（避免额外视觉模型调用）
- 关闭公网联网搜索
- 只监听 127.0.0.1

  python tools/loadtest/test_server.py --port 8010 --mock-url http://127.0.0.1:9099
"""
import argparse
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
sys.path.insert(0, str(APP))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--mock-url", default="http://127.0.0.1:9099")
    args = ap.parse_args()

    os.environ["RAG_LLM_BASE_URL"] = args.mock_url
    os.environ["RAG_LLM_MODEL"] = "mock-model"

    import qa_log
    import fallback

    qa_log.record = lambda *a, **k: ""
    qa_log.feedback = lambda *a, **k: ""
    fallback.web_search = lambda query: None

    import server

    server._figures_enabled = lambda: False

    threading.Thread(target=server.preload_kbs, daemon=True).start()

    import uvicorn

    print(f"[loadtest] test server http://{args.host}:{args.port} -> mock {args.mock_url}",
          flush=True)
    uvicorn.run(server.app, host=args.host, port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
