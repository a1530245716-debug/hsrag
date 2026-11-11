# -*- coding: utf-8 -*-
"""端到端并发基线压测。

启动两个子进程：
  1) mock_llm.py     本地假大模型（Anthropic 协议、慢速 SSE）
  2) test_server.py  8010 端口的测试实例（不写 qa_log、不跑配图）

然后按并发级别对 /api/kb/{id}/ask/stream 发压，同时探测 /api/health 和 /api/kb，
观察线程池被流式回答占满后普通接口是否排队。

示例：
  python tools/loadtest/run_loadtest.py --levels 1,5
  python tools/loadtest/run_loadtest.py --levels 10,30 --yes
  python tools/loadtest/run_loadtest.py --levels 50,100 --yes
"""
import argparse
import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def pctl(values, p):
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def fmt(v, nd=0):
    return "-" if v is None else f"{v:.{nd}f}"


def port_busy(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def tail(path, n=30):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return "(无法读取日志)"


def stop_proc(p):
    if not p or p.poll() is not None:
        return
    p.terminate()
    try:
        p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.kill()


def load_questions(path):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    d = json.loads(p.read_text(encoding="utf-8"))
    qs = []
    if isinstance(d.get("rows"), list):
        qs = [r.get("q") for r in d["rows"] if r.get("ok") and r.get("q")]
    if not qs and isinstance(d.get("items"), list):
        qs = [it.get("q") or it.get("question") or it.get("ask") for it in d["items"]]
    if not qs and isinstance(d.get("cases"), list):
        qs = [it.get("question") or it.get("q") for it in d["cases"]]
    return [q for q in qs if q]


def make_token():
    import auth
    import users
    all_users = users.load_users()
    admin = next((u for u in all_users if users.is_admin(u)), all_users[0] if all_users else None)
    if not admin:
        return "", "no-user"
    return auth.make_token(admin["id"]), admin.get("name") or admin.get("id")


def pick_kb(kb_id):
    import indexer
    reg = indexer.load_registry()
    if kb_id:
        for kb in reg:
            if kb.get("id") == kb_id:
                return kb
        raise SystemExit(f"知识库不存在: {kb_id}")
    ready = [k for k in reg if k.get("status") == "ready" and (k.get("chunk_count") or 0) > 0]
    if not ready:
        raise SystemExit("没有可用的知识库")
    return max(ready, key=lambda k: k.get("chunk_count") or 0)


async def wait_mock(url, proc, log_path, timeout=30):
    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=5) as c:
        while time.time() < deadline:
            if proc and proc.poll() is not None:
                raise RuntimeError(f"mock 进程退出:\n{tail(log_path)}")
            try:
                r = await c.get(url + "/health")
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)
    raise TimeoutError("mock 启动超时")


async def wait_warmup(url, proc, log_path, timeout=600):
    deadline = time.time() + timeout
    last = 0.0
    async with httpx.AsyncClient(timeout=5) as c:
        while time.time() < deadline:
            if proc and proc.poll() is not None:
                raise RuntimeError(f"测试服务退出:\n{tail(log_path)}")
            try:
                r = await c.get(url + "/api/warmup")
                if r.status_code == 200:
                    d = r.json()
                    if d.get("ready") and not d.get("running"):
                        return d
                    if time.time() - last > 5:
                        last = time.time()
                        print(f"  预热 {d.get('done')}/{d.get('total')} {d.get('current', '')}",
                              flush=True)
            except Exception:
                pass
            await asyncio.sleep(2)
    raise TimeoutError("预热超时，看 tools/loadtest/test_server.log")


async def ask_one(client, base, kb_id, question, idx):
    t0 = time.perf_counter()
    ttft_ms = None
    chars = 0
    done = False
    offer = False
    status = 0
    err = ""
    answer_head = ""
    try:
        payload = {"question": question, "no_cache": True, "history": [],
                   "answer_len": "normal"}
        async with client.stream("POST", f"{base}/api/kb/{kb_id}/ask/stream",
                                 json=payload) as r:
            status = r.status_code
            if status != 200:
                body = await r.aread()
                err = f"HTTP {status}: {body[:160]!r}"
            else:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    typ = obj.get("type")
                    if typ == "delta":
                        txt = obj.get("text") or ""
                        if txt and ttft_ms is None:
                            ttft_ms = (time.perf_counter() - t0) * 1000
                        chars += len(txt)
                    elif typ == "answer":
                        txt = obj.get("text") or ""
                        if txt and not answer_head:
                            answer_head = txt[:120]
                        if txt and ttft_ms is None:
                            ttft_ms = (time.perf_counter() - t0) * 1000
                        chars += len(txt)
                        if txt.startswith("⚠️") or "调用失败" in txt or "超时" in txt:
                            err = txt[:160]
                    elif typ == "offer":
                        offer = True
                        done = True
                        break
                    elif typ == "done":
                        done = True
                        break
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    total_ms = (time.perf_counter() - t0) * 1000
    return {"i": idx, "q": question, "status": status,
            "ttft_ms": ttft_ms, "total_ms": total_ms, "chars": chars,
            "done": done, "offer": offer, "error": err, "answer_head": answer_head}


async def probe_loop(client, base, stop_event, interval, out):
    while not stop_event.is_set():
        for name, path in (("health", "/api/health"), ("kb", "/api/kb")):
            t0 = time.perf_counter()
            rec = {"name": name}
            try:
                r = await client.get(base + path, timeout=10)
                rec["status"] = r.status_code
            except Exception as e:
                rec["error"] = type(e).__name__
            rec["ms"] = (time.perf_counter() - t0) * 1000
            out.append(rec)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def probe_summary(probes, name):
    xs = [p for p in probes if p["name"] == name]
    lat = [p["ms"] for p in xs if not p.get("error")]
    errors = [p for p in xs if p.get("error")]
    return {
        "n": len(xs),
        "p50": round(pctl(lat, 50), 1) if lat else None,
        "p95": round(pctl(lat, 95), 1) if lat else None,
        "max": round(max(lat), 1) if lat else None,
        "errors": len(errors),
        "error_types": sorted({p.get("error") for p in errors if p.get("error")}),
    }


def summarize_level(level, results, wall, probes, mock_stats):
    ok, bad = [], []
    for r in results:
        if r["status"] == 200 and not r["error"] and (r["done"] or r["chars"] > 0):
            ok.append(r)
        else:
            bad.append(r)
    ttft = [r["ttft_ms"] for r in results if r["ttft_ms"] is not None]
    totals = [r["total_ms"] for r in results]
    errs = {}
    for r in bad:
        key = (r["error"] or f"status={r['status']}")[:120]
        errs[key] = errs.get(key, 0) + 1
    return {
        "level": level,
        "ok": len(ok),
        "bad": len(bad),
        "success_rate": round(100.0 * len(ok) / level, 1),
        "wall_s": round(wall, 2),
        "qps": round(level / wall, 2) if wall else None,
        "ttft_ms": {k: (round(pctl(ttft, p), 1) if ttft else None)
                    for k, p in (("p50", 50), ("p95", 95), ("p99", 99))},
        "total_ms": {k: (round(pctl(totals, p), 1) if totals else None)
                     for k, p in (("p50", 50), ("p95", 95), ("p99", 99))},
        "offer": sum(1 for r in results if r["offer"]),
        "not_done": sum(1 for r in results if not r["done"] and r["chars"] == 0),
        "errors": errs,
        "probe": {name: probe_summary(probes, name) for name in ("health", "kb")},
        "mock": mock_stats,
    }


def print_header():
    print("\nlevel  ok/total  err | ttft_p50 ttft_p95  total_p95 |  wall    qps |"
          " health_p95 kb_p95 | health_err kb_err", flush=True)


def print_row(s):
    p = s["probe"]
    print(f"{s['level']:>5}  {s['ok']:>3}/{s['level']:<3} {s['bad']:>3} | "
          f"{fmt(s['ttft_ms']['p50']):>8} {fmt(s['ttft_ms']['p95']):>8} "
          f"{fmt(s['total_ms']['p95']):>10} | "
          f"{s['wall_s']:>6.1f} {fmt(s['qps'], 1):>6} | "
          f"{fmt(p['health']['p95'], 1):>10} {fmt(p['kb']['p95'], 1):>6} | "
          f"{p['health']['errors']:>10} {p['kb']['errors']:>6}", flush=True)


async def main_async(args):
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    levels = [x for x in levels if x > 0]
    if not levels:
        raise SystemExit("--levels 不能为空")

    questions = load_questions(args.questions)
    if not questions:
        raise SystemExit(f"没有从 {args.questions} 读到问题")
    if args.question_limit:
        questions = questions[:args.question_limit]

    kb = pick_kb(args.kb_id)
    kb_id = kb["id"]
    token, uname = (args.token, "cli-token") if args.token else make_token()

    mock_proc = server_proc = None
    mock_log_fh = server_log_fh = None
    mock_log_path = HERE / "mock.log"
    server_log_path = HERE / "test_server.log"
    mock_base = f"http://{args.host}:{args.mock_port}"
    app_base = f"http://{args.host}:{args.app_port}"

    print(f"知识库: {kb.get('name')} ({kb_id}), {kb.get('chunk_count')} 块", flush=True)
    print(f"问题数: {len(questions)}  用户: {uname}", flush=True)
    print(f"并发级别: {levels}", flush=True)
    if max(levels) >= 50 and not args.yes:
        raise SystemExit("并发 >= 50 会明显影响本机正在运行的服务；确认空闲后加 --yes")

    try:
        if not args.skip_start:
            if port_busy(args.host, args.app_port):
                raise SystemExit(f"{args.app_port} 已被占用，先确认没有别的测试实例")
            if port_busy(args.host, args.mock_port):
                raise SystemExit(f"{args.mock_port} 已被占用")

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            mock_log_fh = open(mock_log_path, "w", encoding="utf-8")
            mock_proc = subprocess.Popen(
                [sys.executable, str(HERE / "mock_llm.py"),
                 "--host", args.host, "--port", str(args.mock_port),
                 "--tokens", str(args.mock_tokens),
                 "--token-delay", str(args.mock_token_delay),
                 "--ttft", str(args.mock_ttft),
                 "--error-rate", str(args.mock_error_rate)],
                cwd=str(ROOT), env=env, stdout=mock_log_fh, stderr=subprocess.STDOUT,
            )
            await wait_mock(mock_base, mock_proc, mock_log_path)

            server_log_fh = open(server_log_path, "w", encoding="utf-8")
            server_proc = subprocess.Popen(
                [sys.executable, str(HERE / "test_server.py"),
                 "--host", args.host, "--port", str(args.app_port),
                 "--mock-url", mock_base],
                cwd=str(ROOT), env=env, stdout=server_log_fh, stderr=subprocess.STDOUT,
            )
            print("等待测试实例预热…", flush=True)
            await wait_warmup(app_base, server_proc, server_log_path, args.warmup_timeout)

        headers = {"X-RAG-Token": token} if token else {}
        max_conn = max(20, max(levels) * 2)
        timeout = httpx.Timeout(connect=10, read=args.request_timeout,
                                write=30, pool=args.request_timeout)
        limits = httpx.Limits(max_connections=max_conn,
                              max_keepalive_connections=max(10, max(levels)))
        async with httpx.AsyncClient(base_url=app_base, headers=headers,
                                     timeout=timeout, limits=limits) as client, \
                   httpx.AsyncClient(timeout=timeout,
                                     limits=httpx.Limits(max_connections=4)) as probe_client, \
                   httpx.AsyncClient(timeout=10) as mock_client:
            warm_n = max(1, args.warmup)
            print(f"\n预热请求 {warm_n} 次…", flush=True)
            for i in range(warm_n):
                r = await ask_one(client, app_base, kb_id, questions[i % len(questions)], i)
                print(f"  #{i + 1} ttft={fmt(r['ttft_ms'])}ms total={r['total_ms']:.0f}ms "
                      f"chars={r['chars']} done={r['done']} err={r['error'][:60] or '-'}",
                      flush=True)
                if r["status"] != 200 or r["error"]:
                    print("  预热失败，先看 tools/loadtest/test_server.log", flush=True)
                    raise SystemExit(2)

            report = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "machine": {
                    "cpu_count": os.cpu_count(),
                    "note": "本机同时运行生产实例时，绝对数值会偏悲观",
                },
                "app": app_base,
                "kb": {"id": kb_id, "name": kb.get("name"),
                       "chunk_count": kb.get("chunk_count")},
                "questions": len(questions),
                "mock": {"tokens": args.mock_tokens,
                         "token_delay": args.mock_token_delay,
                         "ttft": args.mock_ttft,
                         "error_rate": args.mock_error_rate},
                "levels": [],
                "raw": {},
            }

            print_header()
            for level in levels:
                probes = []
                stop_event = asyncio.Event()
                probe_task = asyncio.create_task(
                    probe_loop(probe_client, app_base, stop_event,
                               args.probe_interval, probes))
                await asyncio.sleep(0.5)
                try:
                    await mock_client.post(mock_base + "/reset")
                except Exception:
                    pass

                t0 = time.perf_counter()
                results = await asyncio.gather(*[
                    ask_one(client, app_base, kb_id, questions[i % len(questions)], i)
                    for i in range(level)
                ])
                wall = time.perf_counter() - t0
                stop_event.set()
                await probe_task

                try:
                    mock_stats = (await mock_client.get(mock_base + "/stats")).json()
                except Exception:
                    mock_stats = {}
                s = summarize_level(level, results, wall, probes, mock_stats)
                report["levels"].append(s)
                report["raw"][str(level)] = results
                print_row(s)
                if s["errors"]:
                    for msg, n in s["errors"].items():
                        print(f"       err x{n}: {msg}", flush=True)
                if level != levels[-1] and args.pause > 0:
                    await asyncio.sleep(args.pause)

            out = Path(args.out) if args.out else (
                HERE / f"results-{datetime.now():%Y%m%d-%H%M%S}.json")
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print(f"\n结果已写入: {out}", flush=True)

    finally:
        if not args.keep:
            stop_proc(server_proc)
            stop_proc(mock_proc)
        else:
            print(f"保留进程: mock pid={getattr(mock_proc, 'pid', None)} "
                  f"server pid={getattr(server_proc, 'pid', None)}", flush=True)
        for fh in (server_log_fh, mock_log_fh):
            try:
                if fh:
                    fh.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="10,30,50,100")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--app-port", type=int, default=8010)
    ap.add_argument("--mock-port", type=int, default=9099)
    ap.add_argument("--kb-id", default="")
    ap.add_argument("--questions", default=str(ROOT / "eval" / "baseline.json"))
    ap.add_argument("--question-limit", type=int, default=0)
    ap.add_argument("--request-timeout", type=float, default=180)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--warmup-timeout", type=int, default=600)
    ap.add_argument("--probe-interval", type=float, default=1.0)
    ap.add_argument("--pause", type=float, default=10.0)
    ap.add_argument("--mock-tokens", type=int, default=200)
    ap.add_argument("--mock-token-delay", type=float, default=0.02)
    ap.add_argument("--mock-ttft", type=float, default=0.3)
    ap.add_argument("--mock-error-rate", type=float, default=0.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--skip-start", action="store_true")
    ap.add_argument("--token", default="")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
