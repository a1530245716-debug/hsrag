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
    ap.add_argument("--global-max", type=int, default=0,
                    help="覆盖 ask_limits.global_max（0=用 config）")
    ap.add_argument("--per-user-max", type=int, default=0,
                    help="覆盖 ask_limits.per_user_max（0=用 config）")
    ap.add_argument("--per-ip-per-min", type=int, default=0,
                    help="覆盖 ask_limits.per_ip_per_min（0=用 config）")
    args = ap.parse_args()

    os.environ["RAG_LLM_BASE_URL"] = args.mock_url
    os.environ["RAG_LLM_MODEL"] = "mock-model"

    import qa_log
    import fallback

    qa_log.record = lambda *a, **k: ""
    qa_log.feedback = lambda *a, **k: ""
    fallback.web_search = lambda query: None

    import server

    overrides = {}
    if args.global_max:
        overrides["global_max"] = args.global_max
    if args.per_user_max:
        overrides["per_user_max"] = args.per_user_max
    if args.per_ip_per_min:
        overrides["per_ip_per_min"] = args.per_ip_per_min
    if overrides:
        server._ASK_LIMIT_CFG.update(overrides)
        print(f"[loadtest] ask_limits override: {overrides}", flush=True)

    server._figures_enabled = lambda: False

    threading.Thread(target=server.preload_kbs, daemon=True).start()

    import uvicorn

    print(f"[loadtest] test server http://{args.host}:{args.port} -> mock {args.mock_url}",
          flush=True)
    uvicorn.run(server.app, host=args.host, port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
