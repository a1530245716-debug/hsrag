# -*- coding: utf-8 -*-
"""智能交易平台 RAG · Web 服务(启动入口)

  python app/server.py                 # 启动,默认 http://127.0.0.1:8000
  python app/server.py --port 9000

- 路由 /api:知识库管理与问答
- 静态 /    :轻量单页前端
"""
import base64, html as _html, os, re, sys, shutil, threading, time, urllib.parse, zipfile
import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))
import indexer, kb, llm, auth, fallback, planner, users, qa_log, cache, imgserve, mailer, system_settings
import module_router
import feedback_store
import eval_store
# 阶段 3：检索内核抽象。业务层只认 backend.retrieve，底下换 BM25/RAGFlow/Mock 都不改这里。
import backend


# ---------------- 持久化日志 ----------------
# Docker 的 json-file 会在容器重建时丢掉旧日志。这里再把 stdout/stderr
# 原样写一份到日志目录（服务器挂载 /opt/rag/logs → /app/logs），按天分文件。
# uvicorn 的启动/访问日志、print 的 [coach]/[llm]/报错都会被一起写到文件。
_LOG_SETUP_DONE = False


def _setup_file_logging():
    global _LOG_SETUP_DONE
    if _LOG_SETUP_DONE:
        return
    _LOG_SETUP_DONE = True
    try:
        base = os.environ.get("RAG_LOG_DIR") or (
            "/app/logs" if Path("/app").is_dir()
            else str(Path(__file__).resolve().parents[1] / "logs"))
        log_dir = Path(base)
        log_dir.mkdir(parents=True, exist_ok=True)
        # 只保留 30 天，避免长期运行把磁盘写满
        cutoff = time.time() - 30 * 86400
        for p in log_dir.glob("app-*.log"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except Exception:
                pass
    except Exception:
        return

    state = {"day": "", "f": None}
    lock = threading.Lock()

    def _file():
        day = time.strftime("%Y%m%d")
        if state["day"] != day or state["f"] is None or state["f"].closed:
            if state["f"]:
                try:
                    state["f"].close()
                except Exception:
                    pass
            state["f"] = open(log_dir / f"app-{day}.log", "a",
                              encoding="utf-8", buffering=1)
            state["day"] = day
            state["f"].write(
                f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"start pid={os.getpid()} =====\n")
            state["f"].flush()
        return state["f"]

    class _Tee:
        def __init__(self, orig):
            self._orig = orig

        def write(self, s):
            try:
                if self._orig is not None:
                    self._orig.write(s)
            except Exception:
                pass
            try:
                with lock:
                    f = _file()
                    f.write(s)
                    f.flush()
            except Exception:
                pass
            return len(s)

        def flush(self):
            try:
                if self._orig is not None:
                    self._orig.flush()
            except Exception:
                pass
            try:
                with lock:
                    _file().flush()
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self._orig, name)

    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr)


_setup_file_logging()

app = FastAPI(title="新人AI导师", version="1.0.0")

# ---------------- 模型 ----------------

class KbCreate(BaseModel):
    name: str = ""
    folder: str = ""

class AskIn(BaseModel):
    question: str
    top_k: int = 0
    model: str = ""      # 可选覆盖配置文件里的模型
    thinking: bool | None = None   # 前端按钮：开/关思考；None=沿用配置
    history: list = []   # 最近几轮对话 [{"q": 问, "a": 答}], 用于多轮上下文(仅存前端内存)
    no_cache: bool = False   # True = 跳过答案缓存, 强制重新检索+生成(演示用)
    fallback_mode: str = ""  # "" 正常 | "web"/"llm" 用户选了兜底来源 | "none" 用户说不用
    answer_len: str = "normal"   # "brief" 简答 | "normal" 标准 | "detail" 详细
    images: list = []        # 用户随问题贴的图 [{"name","data":dataURL}]，只本轮生效、不落盘

class Config(BaseModel):
    llm: dict = {}

class NoteIn(BaseModel):
    """前端「写内容」录入: 纯文字 + 可选粘贴图片, 直接生成一篇 md 进知识库。"""
    title: str = ""
    content: str = ""
    images: list = []        # [{"name": "截图.png", "data": "data:image/png;base64,..."}]
    filename: str = ""       # 编辑已有笔记时传, 覆盖原文件

# ---------------- 鉴权 ----------------

# 不需要登录就能访问: 登录、状态、健康检查, 以及注册(注册自带邀请码校验)
AUTH_FREE = ("/api/login", "/api/auth/status", "/api/health", "/api/register",
             "/api/register/send_code", "/api/register/config", "/api/warmup")

_BOOT_TS = time.time()      # 进程启动时间, 管理员总览里算运行时长
# 启动预热进度：前端用它显示「索引加载中 x/y」，避免用户以为系统卡死
_WARMUP = {"running": False, "ready": False, "total": 0, "done": 0,
           "current": "", "started": 0, "finished": 0, "errors": []}
_WARMUP_LOCK = threading.Lock()


_IMGIDX_LOCK = threading.Lock()
# 答案配图的匹配（含模型看图核对）比较慢，扔到这个池里算，别占着请求线程
_FIG_POOL = ThreadPoolExecutor(max_workers=4)
_IMGIDX_STATE = {}      # kb_id -> {"running":bool, "msg":str, "ts":float}


# ---------------- 问答限流（三层） ----------------
# 为什么要有这个：一次提问会串行跑「改写 → 拆解 → 检索 → 生成」，
# 全程占着一个线程池线程（uvicorn 默认 40 个），跑完要 10~90 秒。
# 没有上限时，50 并发排队能排到 170 秒，连 /api/health 都被拖慢到 2 秒。
#
# 三层：
#   全局   —— 同时进行的问答数（见下面的拐点实测）。
#   每用户 —— 一个人连点几下不该挤掉别人。
#   每 IP  —— 防止共享账号被当成压测工具。
#
# 超限一律立刻返回 429 + Retry-After，**不排队**。用户重试比干等 3 分钟好。
# 依据 docs/ai-fde/03-目标架构与数据权限.md:129-130、09-并发重构方案.md 阶段 1。
#
# 为什么 global_max 是 8 而不是 20（2026-11-15 实测，20 个并发请求扫参）：
#
#   global_max  放行  429   墙钟     说明
#      4         5    15   25.3s   拒绝太多
#      6         7    13   37.2s
#      8         9    11   57.2s   ← 采用：墙钟还在缓升，体验可接受
#     12        13     7   61.5s   墙钟不再改善
#     20        20     0   97.0s   全都进来，但每个人等到近 100 秒
#
# 瓶颈是重排：单次约 2 秒且串行（RERANK_CONCURRENCY=1），
# 20 个并发排队就要 40 秒以上，靠调并发数救不了，只能决定「让几个人进来」。
# 取 8 是拐点：再多放人，墙钟不再变短，只是把等待摊给更多人。
# 被拒的请求立刻拿到 429 + Retry-After，不是干等。
# 服务器扩容后（更多核 / 有 GPU），这个值要重新测。
_ASK_LIMIT_CFG = {
    "global_max": 8,        # 全局同时进行的问答数
    "per_user_max": 2,      # 单用户同时进行的问答数
    "per_ip_per_min": 60,   # 单 IP 每分钟问答次数
    "retry_after": 5,       # 429 里告诉客户端等几秒
    "queue_max": 50,        # 全局满时最多排队多少个请求（0=不排队，直接 429）
    "queue_timeout": 600,   # 排队最长等待秒数
}

# 全局用纯计数器而不是 asyncio.Semaphore：
# 信号量一旦因为客户端断开没释放，计数就永远回不来，只能重启服务。
# 用整数 + 锁，超时回收时能直接把计数改对。
_ask_global_active = 0
_ask_user_active = {}       # user_id -> 正在进行的问答数
_ask_ip_hits = {}           # ip -> [时间戳,...]（只保留 60 秒内的）
_ask_lock = threading.Lock()  # 保护上面三个


def _clip(v, lo, hi, default):
    try:
        n = int(v)
    except Exception:
        return default
    return max(lo, min(hi, n))


def _load_ask_limits():
    """从配置文件读限流参数，读不到就用默认值。

    配置写在 app/config.json 的 "ask_limits" 段，例如：
        "ask_limits": {"global_max": 8, "per_user_max": 2, "per_ip_per_min": 60}
    """
    try:
        cfg = llm.load_config().get("ask_limits") or {}
    except Exception:
        cfg = {}
    _ASK_LIMIT_CFG["global_max"] = _clip(cfg.get("global_max"), 1, 500, 6)
    _ASK_LIMIT_CFG["per_user_max"] = _clip(cfg.get("per_user_max"), 1, 50, 2)
    _ASK_LIMIT_CFG["per_ip_per_min"] = _clip(cfg.get("per_ip_per_min"), 1, 10000, 60)
    _ASK_LIMIT_CFG["retry_after"] = _clip(cfg.get("retry_after"), 1, 300, 5)
    _ASK_LIMIT_CFG["queue_max"] = _clip(cfg.get("queue_max"), 0, 500, 50)
    _ASK_LIMIT_CFG["queue_timeout"] = _clip(cfg.get("queue_timeout"), 30, 3600, 600)
    return dict(_ASK_LIMIT_CFG)


# 模块加载时就应用配置（必须在 _ASK_LIMIT_CFG 定义之后）
_load_ask_limits()


def _ask_global_inc():
    """占一个全局名额。满了返回 False（不排队）。"""
    global _ask_global_active
    with _ask_lock:
        if _ask_global_active >= _ASK_LIMIT_CFG["global_max"]:
            return False, _ask_global_active
        _ask_global_active += 1
        return True, _ask_global_active


def _ask_global_dec():
    global _ask_global_active
    with _ask_lock:
        if _ask_global_active > 0:
            _ask_global_active -= 1
        return _ask_global_active


def _client_ip(request):
    """取客户端 IP。内网部署常走 Nginx，优先 X-Forwarded-For 第一段。"""
    try:
        xff = request.headers.get("x-forwarded-for") or ""
        if xff:
            return xff.split(",")[0].strip() or "unknown"
        return (request.client.host if request.client else "") or "unknown"
    except Exception:
        return "unknown"


def _ask_ip_hit(ip):
    """记一次 IP 访问并判断是否超频。返回 (allowed, 已用次数, 上限)。"""
    now = time.time()
    limit = _ASK_LIMIT_CFG["per_ip_per_min"]
    with _ask_lock:
        arr = _ask_ip_hits.get(ip)
        if arr is None:
            arr = []
            _ask_ip_hits[ip] = arr
        # 滑窗：丢掉 60 秒前的记录
        cut = now - 60
        if arr and arr[0] < cut:
            arr[:] = [t for t in arr if t >= cut]
        if len(arr) >= limit:
            return False, len(arr), limit
        arr.append(now)
        # 顺手清理长期不用的 IP，避免 dict 无限涨
        if len(_ask_ip_hits) > 2048:
            for k in [k for k, v in _ask_ip_hits.items() if not v or v[-1] < cut]:
                _ask_ip_hits.pop(k, None)
    return True, len(arr), limit


def _ask_user_inc(uid):
    """占一个用户名额。成功返回 True。"""
    with _ask_lock:
        n = _ask_user_active.get(uid, 0)
        if n >= _ASK_LIMIT_CFG["per_user_max"]:
            return False, n
        _ask_user_active[uid] = n + 1
        return True, n + 1


def _ask_user_dec(uid):
    with _ask_lock:
        n = _ask_user_active.get(uid, 0)
        if n <= 1:
            _ask_user_active.pop(uid, None)
        else:
            _ask_user_active[uid] = n - 1


# 泄漏兜底：每个占住的名额登记开始时间，超时强制回收。
#
# 为什么必须有：客户端中途断开（关浏览器、网络断、read 到一半取消）时，
# 同步生成器在 yield 处被丢弃，finally 不一定执行，名额就收不回来。
# 实测：用 read(64) 只读一小段就断开，2 个名额一直没还回来。
#
# 那个 600 秒太长了——真出问题用户要等 10 分钟。正常问答最长 90 秒
#（llm.total_timeout），留一倍余量设 180 秒：正常请求永远够用，
# 异常断开两分钟内必定回收。
ASK_SLOT_MAX_HOLD = 180     # 秒
_ask_slots = {}             # slot_id(int) -> (uid, 开始时间)
_next_slot_id = [0]

# 有界排队：全局名额满时，最多 queue_max 个请求进入 FIFO，等名额释放。
# 队列只在内存里；排队请求不占 per_user/global 名额，被准入时才占。
_ask_queue = []             # [{"id","uid","enqueued"}]
_ask_queue_lock = threading.Lock()
_ask_queue_seq = [0]
_ask_avg_seconds = 120.0    # 最近回答耗时的 EWMA，用于估算排队等待


def _slot_register(uid):
    """登记一个名额，返回 id。"""
    with _ask_lock:
        _next_slot_id[0] += 1
        sid = _next_slot_id[0]
        _ask_slots[sid] = (uid, time.time())
        return sid


def _slot_gc():
    """回收超时名额（客户端断开导致的泄漏）。返回回收了几个。

    两个触发点：
      1. 每次新请求进来时顺带跑一次（_acquire_ask_slot）——保证总有请求时能自愈
      2. 后台线程每 30 秒跑一次（_start_slot_gc_thread）——没人提问时也能回收
    """
    global _ask_global_active
    now = time.time()
    with _ask_lock:
        dead = [(s, u, t) for s, (u, t) in _ask_slots.items()
                if now - t > ASK_SLOT_MAX_HOLD]
        for sid, u, started in dead:
            _ask_slots.pop(sid, None)
            n = _ask_user_active.get(u, 0)
            if n <= 1:
                _ask_user_active.pop(u, None)
            else:
                _ask_user_active[u] = n - 1
            if _ask_global_active > 0:
                _ask_global_active -= 1
            print(f"[ask] 回收超时名额#{sid} user={u} 持有{int(now-started)}s",
                  file=sys.stderr)
        _n_dead = len(dead)
    # 队列过期清理放在 ask 锁之外，避免和 _queue_claim 的 queue->ask 锁顺序死锁
    cut = now - float(_ASK_LIMIT_CFG.get("queue_timeout") or 600)
    with _ask_queue_lock:
        _ask_queue[:] = [t for t in _ask_queue if t.get("enqueued", now) >= cut]
    return _n_dead


def _slot_unregister(sid):
    """名额被正常释放时，把它从登记表摘掉。"""
    with _ask_lock:
        _ask_slots.pop(sid, None)


def _make_release(uid, sid):
    """生成幂等的 release：归还 global/user 名额，并更新平均回答耗时。"""
    state = {"done": False}

    def release():
        if state["done"]:
            return
        state["done"] = True
        global _ask_global_active, _ask_avg_seconds
        with _ask_lock:
            rec = _ask_slots.pop(sid, None)
            start = rec[1] if rec else None
            if _ask_global_active > 0:
                _ask_global_active -= 1
            n = _ask_user_active.get(uid, 0)
            if n <= 1:
                _ask_user_active.pop(uid, None)
            else:
                _ask_user_active[uid] = n - 1
            if start:
                dur = max(1.0, time.time() - start)
                _ask_avg_seconds = 0.8 * _ask_avg_seconds + 0.2 * dur
    return release


_slot_gc_started = [False]


def _start_slot_gc_thread():
    """起一个每 30 秒回收一次的后台线程。

    为什么不用 asyncio 定时任务：服务器可能在没有事件循环的地方被导入
    （脚本、测试），线程更稳，而且 _ask_lock 本来就是线程锁。
    """
    if _slot_gc_started[0]:
        return
    _slot_gc_started[0] = True

    def loop():
        while True:
            time.sleep(30)
            try:
                _slot_gc()
            except Exception as e:
                print(f"[ask] 名额回收出错: {type(e).__name__}", file=sys.stderr)

    threading.Thread(target=loop, daemon=True, name="ask-slot-gc").start()


def _too_many(msg, wait=0):
    """统一的 429 响应。带 Retry-After，前端据此提示"等几秒再试"。"""
    wait = wait or _ASK_LIMIT_CFG["retry_after"]
    return JSONResponse({"ok": False, "msg": msg, "retry_after": wait},
                        status_code=429, headers={"Retry-After": str(wait)})


async def _acquire_ask_slot(request):
    """申请一个问答名额，返回释放函数。

    三层依次判断，**任何一层不过都立刻抛 RateLimited**，不排队。
    返回的 release() 必须在问答真正结束后调用。

    为什么不是 async context manager：StreamingResponse 是懒执行的——
    端点函数只负责把生成器交给 FastAPI，真正跑起来是后面的事。如果用
    `async with` 包住 `return StreamingResponse(...)`，出块的瞬间名额就被释放了，
    等于没限流（实测 5 并发全部放行就是这个原因）。
    所以这里拆成 acquire / release 两步，由调用方在生成器里保证成对调用。
    """
    user = _current_user(request)
    uid = (user or {}).get("id") or "anon"
    ip = _client_ip(request)

    # 每次新请求顺带回收超时名额（客户端断开导致的泄漏），不需要额外定时器
    try:
        _slot_gc()
    except Exception:
        pass

    # 第 3 层：每 IP 频率（最先判，最便宜）
    ok_ip, used, limit = _ask_ip_hit(ip)
    if not ok_ip:
        raise RateLimited(f"请求太频繁（每分钟上限 {limit} 次），请稍后再试。")

    # 第 2 层：每用户并发
    ok_u, n_u = _ask_user_inc(uid)
    if not ok_u:
        raise RateLimited(
            f"你还有 {n_u} 个问题正在回答中，等它们结束后再问"
            f"（每人同时 {_ASK_LIMIT_CFG['per_user_max']} 个）。")

    # 第 1 层：全局并发。满了抛 GlobalBusy，由 endpoint 决定是否进排队。
    ok_g, n_g = _ask_global_inc()
    if not ok_g:
        _ask_user_dec(uid)
        raise GlobalBusy(
            f"系统繁忙（同时回答上限 {_ASK_LIMIT_CFG['global_max']} 个），请稍后重试。")

    # 名额到手，登记（客户端断开的兜底回收靠它）
    sid = _slot_register(uid)
    return _make_release(uid, sid)


class RateLimited(Exception):
    """限流拒绝。携带给用户看的提示文案。"""

    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg


class GlobalBusy(RateLimited):
    """全局并发满；可进入有界排队。"""


def _queue_add(request):
    """把一个全局满的请求放进 FIFO。返回 (ticket, 拒绝原因)。"""
    user = _current_user(request)
    uid = (user or {}).get("id") or "anon"
    maxq = int(_ASK_LIMIT_CFG.get("queue_max") or 0)
    if maxq <= 0:
        return None, "系统繁忙，请稍后重试。"
    with _ask_queue_lock:
        if any(t.get("uid") == uid for t in _ask_queue):
            return None, "你已有一个问题在排队，请等它开始或完成后再试。"
        if len(_ask_queue) >= maxq:
            return None, f"排队已满（{maxq} 个），请稍后重试。"
        _ask_queue_seq[0] += 1
        t = {"id": _ask_queue_seq[0], "uid": uid, "enqueued": time.time()}
        _ask_queue.append(t)
        return t, ""


def _queue_position(ticket):
    with _ask_queue_lock:
        for i, t in enumerate(_ask_queue):
            if t.get("id") == ticket.get("id"):
                return i + 1
    return 0


def _queue_eta(pos):
    avg = float(_ask_avg_seconds or 120.0)
    slots = max(1, int(_ASK_LIMIT_CFG.get("global_max") or 1))
    return max(1, int(round(pos * avg / slots)))


def _queue_remove(ticket):
    with _ask_queue_lock:
        _ask_queue[:] = [t for t in _ask_queue if t.get("id") != ticket.get("id")]


def _queue_claim(ticket):
    """轮到队首时占一个名额。返回 release 或 None。"""
    global _ask_global_active
    with _ask_queue_lock:
        if not _ask_queue or _ask_queue[0].get("id") != ticket.get("id"):
            return None
        uid = ticket.get("uid") or "anon"
        with _ask_lock:
            if _ask_global_active >= int(_ASK_LIMIT_CFG["global_max"]):
                return None
            if _ask_user_active.get(uid, 0) >= int(_ASK_LIMIT_CFG["per_user_max"]):
                return None
            _ask_queue.pop(0)
            _ask_global_active += 1
            _ask_user_active[uid] = _ask_user_active.get(uid, 0) + 1
            _next_slot_id[0] += 1
            sid = _next_slot_id[0]
            _ask_slots[sid] = (uid, time.time())
        return _make_release(uid, sid)


def ask_limits_state():
    """给管理员总览/健康检查看的实时状态。"""
    with _ask_queue_lock:
        qlen = len(_ask_queue)
    with _ask_lock:
        return {
            "config": dict(_ASK_LIMIT_CFG),
            "active_users": dict(_ask_user_active),
            "active_total": _ask_global_active,
            "slots_held": len(_ask_slots),
            "queue_len": qlen,
            "avg_answer_seconds": round(_ask_avg_seconds, 1),
            "tracked_ips": len(_ask_ip_hits),
            "global_free": max(0, _ASK_LIMIT_CFG["global_max"] - _ask_global_active),
        }


def _refresh_image_index(kb_id: str):
    """文档重建后，增量补图片索引（新图 OCR + 摘要），再补向量。

    - 增量：`imgindex.build` 会跳过已有摘要的图，所以只在首次或新增文档时耗时。
    - 加锁：同一个库不会同时跑两遍；正在跑时再次触发直接忽略。
    - 失败只打日志：图片索引挂了不能影响问答主流程。
    """
    def _run():
        import time as _t
        st = _IMGIDX_STATE.setdefault(kb_id, {})
        if st.get("running"):
            return
        if not _IMGIDX_LOCK.acquire(blocking=False):
            return
        st["running"] = True
        t0 = _t.time()
        try:
            import imgindex
            # 文档没变就别白跑：scan_images 要解包全部文档算图片哈希，实测 37 秒。
            # 拿 chunks.jsonl 的 mtime+size 当指纹，和上次建图片索引时比。
            if imgindex.docs_unchanged(kb_id):
                st["msg"] = "文档未变化，跳过"
                print(f"[imgindex] {kb_id} 文档未变化，跳过图片索引", file=sys.stderr)
                return
            r = imgindex.build(kb_id, workers=4, progress=lambda m: None)
            new = int(r.get("sum", 0))
            if new:
                imgindex.build_vectors(kb_id, progress=lambda m: None)
            st["msg"] = f"新增摘要 {new} 张，用时 {(_t.time()-t0)/60:.1f} 分钟"
            print(f"[imgindex] {kb_id} 重建后同步完成：{st['msg']}", file=sys.stderr)
        except Exception as e:
            st["msg"] = f"失败: {type(e).__name__} {str(e)[:120]}"
            print(f"[imgindex] {kb_id} 重建后同步失败: {st['msg']}", file=sys.stderr)
        finally:
            st["running"] = False
            st["ts"] = _t.time()
            _IMGIDX_LOCK.release()

    threading.Thread(target=_run, daemon=True).start()


def _figures_enabled() -> bool:
    """答案是否附带配图（图片语义绑定）。

    默认**关闭**：图片索引要先离线建好（`python app/imgindex.py <kb_id>`），
    建好之前打开只会白跑一次向量匹配。
    打开方式二选一：
      - 环境变量 RAG_FIGURES=1
      - config.json 里 "figures": {"enabled": true}
    """
    import os as _os
    if _os.environ.get("RAG_FIGURES") == "1":
        return True
    try:
        return bool((_cfg().get("figures") or {}).get("enabled"))
    except Exception:
        return False


_DIAGRAM_Q_RE = re.compile(
    r"架构|拓扑|网络结构|网络拓扑|部署图|应用图|应用架构|流程图|时序图|"
    r"数据流|组件关系|调用关系|系统结构|系统图")

def _diagram_intent(q: str) -> bool:
    """只有明确问架构/流程图类问题时才展示图。"""
    return bool(_DIAGRAM_Q_RE.search(str(q or "")))


def _append_diagram_sources(sources, kb_id, q):
    """把命中的架构图结构化文本加入本轮上下文，最多 2 张。"""
    if not _diagram_intent(q) or not _figures_enabled():
        return sources
    try:
        import imgindex
        ds = imgindex.search_diagrams(kb_id, q, topk=2)
    except Exception as e:
        print(f"[figures] 架构图上下文失败: {type(e).__name__} {e}", file=sys.stderr)
        return sources
    seen = {(s.get("path"), (s.get("text") or "")[:80]) for s in sources}
    for d in ds:
        key = (d.get("path"), (d.get("text") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "id": "diagram-" + str(d.get("hash") or ""),
            "path": d.get("path") or "",
            "chunk": 0,
            "text": d.get("text") or "",
            "text_full": d.get("text") or "",
            "score": d.get("score") or 0,
            "kind": "架构图",
            "diagram": True,
        })
    return sources


def _diagram_figure_block(kb_id, q):
    if not _diagram_intent(q) or not _figures_enabled():
        return []
    try:
        import imgindex
        return imgindex.diagram_blocks(kb_id, q, topk=2)
    except Exception as e:
        print(f"[figures] 架构图展示失败: {type(e).__name__} {e}", file=sys.stderr)
        return []


def _token_of(request: Request) -> str:
    """取请求里的 token。**请求头优先于 cookie**。

    为什么要请求头优先：cookie 是整个域名共享的，同一浏览器开两个标签页登录不同账号时，
    cookie 只会保留最后一次登录的身份。前端因此把 token 另存到每个标签页独立的
    sessionStorage，并在每个请求上用 X-RAG-Token 带上，服务端必须以它为准，
    否则两个标签页会被同一个 cookie 带着走，又变回互相顶掉。
    """
    return request.headers.get("X-RAG-Token", "") or request.cookies.get(auth.COOKIE_NAME, "")


def _user_of(request: Request):
    """从请求解析当前用户。返回 user dict; 未登录返回 None。

    - 用户表为空 → 未启用多用户, 返回虚拟管理员(全部放行), 保持老用法可用。
    - 旧格式 token → 映射到最早创建的那个用户(升级不踢人)。
    """
    uid = auth.verify_token(_token_of(request))
    if not uid:
        return None
    all_users = users.load_users()
    if not all_users:
        return {"id": "__legacy__", "name": "本机用户", "role": "admin"}
    if uid == "legacy":
        return users._public(all_users[0])
    u = users.get_user(uid)
    return users._public(u) if u else None


_KB_PATH_RE = re.compile(r"^/api/kb/([^/]+)(?:/|$)")

# 别人分享给我的知识库，只能用这些接口（提问、看引用、看原文件）。
# 用白名单是刻意的：以后新增一个接口默认是"不允许"，而不是"默认允许"。
# 这里的列法和实际路由一一对应，改路由时记得回来检查。
_KB_READONLY_PATHS = (
    "/api/kb/{id}",
    "/api/kb/{id}/notes",
    "/api/kb/{id}/note",
    "/api/kb/{id}/file",
    "/api/kb/{id}/file/preview",
    "/api/kb/{id}/file/raw",
    "/api/kb/{id}/file/download",
    "/api/kb/{id}/files",
    "/api/kb/{id}/img",
    "/api/kb/{id}/debug_search",
    "/api/kb/{id}/ask",
    "/api/kb/{id}/ask/stream",
)


def _kb_readonly_path(path):
    """这个 /api/kb/<id>/... 是不是只读操作（可以给被分享的人用）。"""
    m = re.match(r"^/api/kb/([^/]+)(/.*)?$", path)
    if not m:
        return False
    tail = m.group(2) or ""
    full = "/api/kb/{id}" + tail
    # 去掉结尾多余的斜杠再比
    return full.rstrip("/") in _KB_READONLY_PATHS


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in AUTH_FREE:
        acfg = auth.load_auth_cfg()
        if acfg["enabled"]:
            u = _user_of(request)
            if not u:
                return JSONResponse({"ok": False, "msg": "未登录或登录已过期"}, status_code=401)
            request.state.user = u
        else:
            request.state.user = {"id": "__local__", "name": "本机用户", "role": "admin"}
        if path.startswith("/api/fs/") and not acfg["allow_fs_browse"]:
            return JSONResponse({"ok": False, "msg": "目录浏览已禁用，请直接填写完整路径"}, status_code=403)
        # 知识库归属校验: 非管理员只能操作自己的库(统一在此拦截, 避免逐个接口遗漏)
        m = _KB_PATH_RE.match(path)
        if m:
            kb_ = indexer.get_kb(m.group(1))
            if kb_ and not users.resolve_owner_of_kb(kb_, request.state.user):
                # 例外：别人分享给他的库，"只读类"接口放行。
                # 这里用白名单而不是黑名单——漏写一个读接口最多是"用不了"，
                # 漏写一个写接口就是越权，方向不能搞反。
                if _kb_readonly_path(path):
                    # 只读路径对问答/答辩两种共享都放行；具体入口由业务接口再区分
                    if not (users.can_read_kb(kb_, request.state.user) or
                            users.can_read_kb(kb_, request.state.user, "defense")):
                        return JSONResponse(
                            {"ok": False, "msg": "无权访问该知识库（它属于其他用户）"},
                            status_code=403)
                else:
                    return JSONResponse(
                        {"ok": False, "msg": "这是别人分享给你的知识库，只能提问，不能修改"},
                        status_code=403)
    return await call_next(request)


def _current_user(request: Request):
    return getattr(request.state, "user", None) or _user_of(request) or \
        {"id": "__local__", "name": "本机用户", "role": "admin"}


class LoginIn(BaseModel):
    username: str = ""       # 账号(新登录方式)
    password: str = ""       # 密码; 不填账号时兼容旧的"访问 key"登录


class RegisterIn(BaseModel):
    username: str = ""
    password: str = ""
    name: str = ""
    email: str = ""           # 邮箱即账号
    code: str = ""            # 可选邀请码(服务端开了要求才校验)


@app.get("/api/auth/status")
def auth_status(request: Request):
    acfg = auth.load_auth_cfg()
    # 联网搜索不可用的部署（内网服务器）直接告诉前端，别再摆一个必然失败的按钮
    try:
        web_ok = bool(fallback.load_fb_cfg().get("web_enabled"))
    except Exception:
        web_ok = False
    if not acfg["enabled"]:
        return {"ok": True, "enabled": False, "authenticated": True,
                "allow_fs_browse": acfg["allow_fs_browse"], "user": None,
                "fallback_web": web_ok}
    u = _user_of(request)
    return {"ok": True, "enabled": True,
            "authenticated": bool(u), "allow_fs_browse": acfg["allow_fs_browse"],
            "user": u, "fallback_web": web_ok}


@app.get("/api/me")
def me(request: Request):
    u = _user_of(request) or {}
    return {"ok": True, "user": u, "is_admin": users.is_admin(u),
            "user_count": len(users.load_users())}


def _register_code_path():
    from pathlib import Path
    return Path(__file__).parent / "data" / "register_code.txt"


def _register_code(create=True):
    """注册邀请码。没生成过就随机造一个, 存 app/data/register_code.txt。

    公网可达的站点不能开放自助注册 —— 知识库里有客户账号密码,
    所以注册要一个邀请码, 由管理员在「用户管理」里看和换。
    """
    p = _register_code_path()
    try:
        if p.exists():
            code = p.read_text(encoding="utf-8").strip()
            if code:
                return code
        if not create:
            return ""
        import secrets
        code = secrets.token_hex(3)          # 6 位
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        return code
    except Exception as e:
        print(f"[register] 邀请码读写失败: {e}", file=sys.stderr)
        return ""


@app.get("/api/register/config")
def register_config():
    """注册页要显示什么，由这里决定（不需要登录）。

    前端打开注册表单时先问一次：要不要邮箱验证码？允许哪些域名？要不要邀请码？
    这样用户不会先点了"获取验证码"才发现系统根本没开邮件。
    """
    return {
        "ok": True,
        "mode": "email" if mailer.configured() else "whitelist",
        "domains": _allow_domains(),
        "require_code": bool(_register_code_path().with_name("register_require_code").exists()),
    }


@app.get("/api/register/code")
def register_code_get(request: Request):
    """管理员查看/重置注册邀请码。"""
    u, err = _require_admin(request)
    if err:
        return err
    return {"ok": True, "code": _register_code()}


@app.post("/api/register/code")
def register_code_reset(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    import secrets
    p = _register_code_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    code = secrets.token_hex(3)
    p.write_text(code, encoding="utf-8")
    return {"ok": True, "code": code}


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _allow_domains_path():
    from pathlib import Path
    return Path(__file__).parent / "data" / "email_domains.txt"


def _allow_domains():
    """允许注册的邮箱域名。没配过就给默认值。"""
    p = _allow_domains_path()
    try:
        if p.exists():
            ds = [x.strip().lower() for x in p.read_text(encoding="utf-8").splitlines()]
            ds = [x for x in ds if x and not x.startswith("#")]
            if ds:
                return ds
    except Exception:
        pass
    return ["examplecorp.com"]


def _email_ok(email):
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        return False, "邮箱格式不对"
    dom = e.rsplit("@", 1)[1]
    allow = _allow_domains()
    if dom not in allow:
        return False, f"只允许 {'、'.join('@' + d for d in allow)} 的邮箱注册"
    return True, e


class SendCodeIn(BaseModel):
    email: str = ""


@app.post("/api/register/send_code")
def register_send_code(body: SendCodeIn):
    """发注册验证码。没配 SMTP 时返回 mode=whitelist，前端据此隐藏验证码输入。"""
    email = (body.email or "").strip().lower()
    ok, msg = _email_ok(email)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg}, status_code=400)
    if not mailer.configured():
        return {"ok": True, "mode": "whitelist",
                "msg": "本系统未开启邮件验证，直接设密码注册即可"}
    ok2, msg2, _code = mailer.issue(email)
    if not ok2:
        return JSONResponse({"ok": False, "msg": msg2}, status_code=502)
    return {"ok": True, "mode": "email", "msg": "验证码已发到邮箱，10 分钟内有效"}


@app.get("/api/register/smtp")
def register_smtp_get(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    c = mailer.load_cfg() or {}
    return {"ok": True, "configured": mailer.configured(),
            "host": c.get("host", ""), "port": c.get("port", 465),
            "user": c.get("user", ""), "from": c.get("from", ""),
            "tls": c.get("tls", True), "has_password": bool(c.get("password"))}


@app.post("/api/register/smtp")
def register_smtp_set(request: Request, host: str = "", port: int = 465,
                      user: str = "", password: str = "", from_: str = "",
                      tls: bool = True):
    u, err = _require_admin(request)
    if err:
        return err
    if not host or not user:
        return JSONResponse({"ok": False, "msg": "host 和 user 必填"}, status_code=400)
    old = mailer.load_cfg() or {}
    d = {"host": host.strip(), "port": int(port or 465), "user": user.strip(),
         "password": password or old.get("password", ""),
         "from": (from_ or user).strip(), "tls": bool(tls)}
    mailer.save_cfg(d)
    return {"ok": True, "configured": mailer.configured(),
            "msg": "已保存。注册将改用邮箱验证码"}


@app.get("/api/register/domains")
def register_domains_get(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    p = _allow_domains_path()
    return {"ok": True, "domains": _allow_domains(),
            "require_code": bool(p.with_name("register_require_code").exists())}


@app.post("/api/register/domains")
def register_domains_set(request: Request, domains: str = ""):
    """设置允许注册的邮箱域名, 逗号分隔; 传空则不限制(不建议)。"""
    u, err = _require_admin(request)
    if err:
        return err
    ds = [d.strip().lower().lstrip("@") for d in (domains or "").replace("；", ",").replace("，", ",").split(",")]
    ds = [d for d in ds if d]
    p = _allow_domains_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(ds) + "\n", encoding="utf-8")
    return {"ok": True, "domains": ds or ["（不限制）"]}


_DEFAULT_SHARE_LOCK = threading.Lock()


def _grant_kb(uid, kid, mode="qa"):
    """把指定知识库只读共享给用户（mode=qa 问答 / defense 答辩）。"""
    uid = str(uid or "")
    kid = str(kid or "")
    if not uid or not kid:
        return
    try:
        with _DEFAULT_SHARE_LOCK:
            kb_ = indexer.get_kb(kid)
            if not kb_ or kb_.get("owner") == uid:
                return
            ids = users.shared_defense_ids(kb_) if mode == "defense" else users.shared_ids(kb_)
            if uid in ids:
                return
            ids.append(uid)
            users.set_shared_with(kb_, ids, mode=mode)
            indexer._update_registry(kb_)
    except Exception as e:
        print(f"[users] 默认知识库分享失败({kid}/{mode}): {e}", file=sys.stderr)


def _grant_manual_kb(uid):
    """把「业务手册（O32/资管培训）」只读共享给指定用户。"""
    try:
        import defense_coach
        _grant_kb(uid, defense_coach.manual_kb_id())
    except Exception as e:
        print(f"[users] 默认手册分享失败: {e}", file=sys.stderr)


def _share_default_kb(user, kb_ids=None):
    """新账号默认只读共享哪些库；未配置时沿用业务手册。

    默认共享库只服务“知识库”入口；模拟答辩权限是独立开关，
    不应该因为给了知识库就顺带开通答辩，反之亦然。
    """
    if not user or users.is_admin(user):
        return
    if not users.perms_of(user).get("kb"):
        return
    if kb_ids is None:
        kb_ids = system_settings.registration_defaults().get("kb_ids")
    if kb_ids is None:
        try:
            import defense_coach
            kid = defense_coach.manual_kb_id()
            kb_ids = [kid] if kid else []
        except Exception:
            kb_ids = []
    for kid in kb_ids:
        _grant_kb(user.get("id"), kid)


def _share_defense_kbs(user, kb_ids=None):
    """模拟答辩的默认依据库，和问答知识库分别配置、分别共享。"""
    if not user or users.is_admin(user):
        return
    if not users.perms_of(user).get("defense"):
        return
    if kb_ids is None:
        kb_ids = system_settings.registration_defaults().get("defense_kb_ids")
    if kb_ids is None:
        try:
            import defense_coach
            kid = defense_coach.manual_kb_id()
            kb_ids = [kid] if kid else []
        except Exception:
            kb_ids = []
    for kid in kb_ids:
        _grant_kb(user.get("id"), kid, mode="defense")


@app.post("/api/register")
def register(body: RegisterIn, response: Response):
    """自助注册: 邮箱就是账号, 只允许指定域名。注册即登录。"""
    acfg = auth.load_auth_cfg()
    email = (body.email or body.username or "").strip().lower()
    ok, msg = _email_ok(email)
    if not ok:
        return JSONResponse({"ok": False, "msg": msg}, status_code=400)
    if len(body.password or "") < 6:
        return JSONResponse({"ok": False, "msg": "密码至少 6 位"}, status_code=400)
    # 邀请码是可选第二道门: 只有管理员显式打开时才要
    if _register_code_path().with_name("register_require_code").exists():
        want = _register_code()
        if want and (body.code or "").strip() != want:
            return JSONResponse({"ok": False, "msg": "邀请码不对"}, status_code=403)
    # 配了发件服务器就必须过验证码；没配就是"邮箱白名单"模式，只校验域名
    if mailer.configured():
        okc, msgc = mailer.verify(email, body.code)
        if not okc:
            return JSONResponse({"ok": False, "msg": msgc}, status_code=401)
    defs = system_settings.registration_defaults()
    try:
        u = users.create_account(email, body.password, name=body.name or email,
                                 role="user", note="自助注册",
                                 perms=defs.get("perms"))
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    _share_default_kb(u, defs.get("kb_ids"))
    _share_defense_kbs(u, defs.get("defense_kb_ids"))
    tk = auth.make_token(u["id"], acfg["ttl"])
    response.set_cookie(value=tk, **auth.cookie_kwargs(acfg["ttl"]))
    # token 也要回给前端（和 /api/login 一致）：前端把它存 sessionStorage，
    # 只回 cookie 的话刷新页面就掉线，新用户会以为注册没成功。
    return {"ok": True, "authenticated": True, "user": u, "token": tk,
            "msg": "注册成功，已登录"}


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    acfg = auth.load_auth_cfg()
    if not acfg["enabled"]:
        return {"ok": True, "enabled": False, "authenticated": True}
    uname = (body.username or "").strip()
    key = (body.password or "").strip()
    if not key:
        return JSONResponse({"ok": False, "msg": "请输入账号和密码"}, status_code=401)

    # 有账号 → 账号 + 密码; 只有密码 → 兼容旧的"访问 key"登录
    u = users.verify_password(uname, key) if uname else users.verify_key(key)
    if u:
        tk = auth.make_token(u["id"], acfg["ttl"])
        response.set_cookie(value=tk, **auth.cookie_kwargs(acfg["ttl"]))
        # token 一并回给前端, 让每个标签页存进 sessionStorage 独立使用(多账号同浏览器)
        return {"ok": True, "authenticated": True, "user": u, "token": tk}

    # 用户表为空时回退到旧单口令(视为管理员), 并顺手初始化多用户
    if not users.load_users() and auth.check_password(key, acfg):
        try:
            users.ensure_bootstrap(legacy_password="")
        except Exception as e:
            print(f"[users] 初始化失败: {e}")
        tk = auth.make_token("legacy", acfg["ttl"])
        response.set_cookie(value=tk, **auth.cookie_kwargs(acfg["ttl"]))
        return {"ok": True, "authenticated": True, "token": tk,
                "user": {"id": "legacy", "name": "本机用户", "role": "admin"}}

    # 登录失败: 顺手清掉浏览器里可能还留着的上一个账号的 token。
    # 不清的话, 用户以为"我换成低权限账号了", 实际页面仍在用旧 token(可能是管理员)读别人的库。
    resp = JSONResponse({"ok": False, "msg": "账号或密码不对"}, status_code=401)
    resp.delete_cookie(key=auth.COOKIE_NAME, path="/", httponly=True, samesite="lax")
    return resp


# ---------------- API: 问答日志 / 反馈 ----------------

class FeedbackIn(BaseModel):
    id: str
    vote: int          # 1 = 有用, -1 = 没用
    comment: str = ""  # 👎 时用户说明原因，作为改进线索
    # 👍 时前端一并带上完整问答，作为评测集候选；-1 不需要，字段可空
    question: str = ""
    answer: str = ""
    sources: list = []
    kb: str = ""
    kb_name: str = ""


@app.post("/api/feedback")
def api_feedback(body: FeedbackIn, request: Request):
    u = _current_user(request)
    ok = qa_log.feedback(body.id, body.vote, u, comment=body.comment)
    # 点赞 = 用户认可这条回答，留作评测候选；不是自动收录，管理员还要人工核对
    if ok and body.vote == 1 and body.question and body.answer:
        try:
            eval_store.add_candidate(
                body.id, body.question, body.answer, body.sources,
                kb=body.kb, kb_name=body.kb_name, user=u)
        except Exception as e:
            print(f"[eval_store] 点赞候选保存失败: {type(e).__name__} {e}", file=sys.stderr)
    return {"ok": ok}


# ---- 管理员：用户点赞的评测候选 ----
class EvalStatusIn(BaseModel):
    status: str = "included"      # pending / included / ignored


@app.get("/api/eval/candidates")
def api_eval_candidates(request: Request, status: str = "", limit: int = 300):
    u, err = _require_admin(request)
    if err:
        return err
    items = eval_store.list_all(limit=limit, status=status or None)
    return {"ok": True, "items": items, "summary": eval_store.summary()}


@app.post("/api/eval/candidates/{cid}/status")
def api_eval_status(cid: str, body: EvalStatusIn, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    try:
        ok = eval_store.set_status(cid, body.status, u)
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    return {"ok": ok}


@app.get("/api/eval/candidates/export")
def api_eval_export(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    return {"ok": True, **eval_store.export_cases()}


# ---- 用户反馈 / 提需求（自由文本，不是答案的 👍/👎）----
class SuggestionIn(BaseModel):
    kind: str = "improve"       # content / improve / feature / other
    content: str = ""
    contact: str = ""           # 选填，方便回访
    source: str = "qa"          # qa / defense
    page: str = ""              # 当前知识库 id 或 defense


@app.post("/api/suggestion")
def api_suggestion(body: SuggestionIn, request: Request):
    """任何登录用户都可以提交反馈/需求。"""
    u = _current_user(request)
    try:
        rec = feedback_store.add(
            u, kind=body.kind, content=body.content, contact=body.contact,
            source=body.source, page=body.page)
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    except Exception as e:
        print(f"[suggestion] 保存失败: {type(e).__name__} {e}", file=sys.stderr)
        return JSONResponse({"ok": False, "msg": "保存失败，请稍后再试"}, status_code=500)
    return {"ok": True, "id": rec["id"], "msg": "已收到，谢谢你的反馈！"}


@app.get("/api/suggestions")
def api_suggestions(request: Request, days: int = 90, limit: int = 300,
                    unhandled: bool = False):
    """管理员查看反馈列表。"""
    u, err = _require_admin(request)
    if err:
        return err
    items = feedback_store.list_all(limit=limit, days=days, only_unhandled=unhandled)
    return {"ok": True, "items": items,
            "summary": feedback_store.summary(days=days),
            "kinds": feedback_store.KINDS, "sources": feedback_store.SOURCES}


@app.post("/api/suggestions/{fid}/handled")
def api_suggestion_handled(fid: str, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    ok = feedback_store.mark_handled(fid, u)
    return {"ok": ok}


@app.get("/api/qa/stats")
def api_qa_stats(request: Request, days: int = 7):
    """问答统计(管理员看全量, 普通用户看自己的)。"""
    u = _current_user(request)
    st = qa_log.stats(days=days)
    if not users.is_admin(u):
        # 普通用户只看自己那部分: 用全量统计但过滤问题列表
        mine = [r for r in qa_log.load(days=days, limit=500) if r.get("uid") == u.get("id")]
        st = dict(st)
        st["asks"] = len(mine)
        st["top_questions"] = _counter([(r.get("q") or "").strip() for r in mine if r.get("q")], 10)
        st["fallback_questions"] = [r.get("q") for r in mine if r.get("fallback")][:10]
        st["worst"] = [r for r in mine if r.get("vote") == -1][:10]
    return {"ok": True, "stats": st}


def _counter(items, n):
    from collections import Counter
    return Counter(items).most_common(n)


@app.post("/api/cache/clear")
def api_cache_clear(request: Request, kb_id: str = ""):
    """清答案缓存(管理员)。演示前清一下, 保证每个问题都是现算的。"""
    u = _current_user(request)
    if not users.is_admin(u):
        return JSONResponse({"ok": False, "msg": "需要管理员权限"}, status_code=403)
    n = cache.clear(kb_id or None)
    return {"ok": True, "cleared": n, "stats": cache.stats()}


@app.get("/api/cache/stats")
def api_cache_stats(request: Request):
    return {"ok": True, "stats": cache.stats()}


@app.get("/api/qa/logs")
def api_qa_logs(request: Request, days: int = 7, limit: int = 200):
    u = _current_user(request)
    rows = qa_log.load(days=days, limit=limit)
    if not users.is_admin(u):
        rows = [r for r in rows if r.get("uid") == u.get("id")]
    return {"ok": True, "logs": rows}


# ---------------- API: 用户管理(仅管理员) ----------------

class UserIn(BaseModel):
    name: str = ""
    role: str = "user"
    key: str = ""                # 兼容旧接口(等于密码)
    username: str = ""
    password: str = ""
    note: str = ""
    perms: dict = {}             # {"kb": bool, "defense": bool}

class UserPatch(BaseModel):
    name: str = ""
    role: str = ""
    note: str = ""
    username: str = ""           # 改账号名
    new_key: bool = False        # True=重置为随机新密码
    password: str = ""           # 指定新密码(留空则随机)
    perms: dict = None           # 管理员改入口权限


class SysSettingsIn(BaseModel):
    kb: bool = True              # 首次注册默认知识库（问答）权限
    defense: bool = False        # 首次注册默认模拟答辩权限
    kb_ids: list = []            # 问答默认共享知识库
    defense_kb_ids: list = []    # 模拟答辩默认共享知识库


class NoticeIn(BaseModel):
    kb: dict = {}                # {"enabled","title","body"}
    defense: dict = {}           # {"enabled","title","body"}


def _require_admin(request: Request):
    u = _user_of(request)
    if not u:
        return None, JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    if not users.is_admin(u):
        return None, JSONResponse({"ok": False, "msg": "仅管理员可操作"}, status_code=403)
    return u, None


def _require_defense(request: Request):
    """模拟答辩入口权限。管理员始终可用。"""
    u = _user_of(request)
    if not u:
        return None, JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    if not (users.is_admin(u) or users.perms_of(u).get("defense")):
        return None, JSONResponse({"ok": False, "msg": "未开通模拟答辩权限，请联系管理员"},
                                  status_code=403)
    return u, None


def _require_kb_perm(request: Request):
    """知识库入口权限（提问/使用）。管理员始终可用。"""
    u = _user_of(request)
    if not u:
        return None, JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    if not (users.is_admin(u) or users.perms_of(u).get("kb")):
        return None, JSONResponse({"ok": False, "msg": "未开通知识库权限，请联系管理员"},
                                  status_code=403)
    return u, None


@app.get("/api/users")
def users_list(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    lst = [users._public(x) for x in users.load_users()]
    reg = indexer.load_registry()
    for x in lst:
        x["kb_count"] = sum(1 for kb in reg if kb.get("owner") == x["id"])
    return {"ok": True, "users": lst}


@app.post("/api/users")
def users_create(body: UserIn, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    key = (body.key or body.password or "").strip() or None
    if key and len(key) < 2:
        return JSONResponse({"ok": False, "msg": "密码至少 2 位"}, status_code=400)
    try:
        nu = users.create_user(name=body.name, role=body.role, key=key, note=body.note,
                               username=body.username, perms=body.perms)
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    _share_default_kb(nu)
    _share_defense_kbs(nu)
    return {"ok": True, "user": nu}


@app.get("/api/admin/settings")
def admin_settings_get(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    s = system_settings.load()
    reg = s["registration"]
    manual = ""
    try:
        import defense_coach
        manual = defense_coach.manual_kb_id() or ""
    except Exception:
        pass
    effective = reg["kb_ids"] if reg["kb_ids"] is not None else ([manual] if manual else [])
    effective_def = (reg["defense_kb_ids"] if reg.get("defense_kb_ids") is not None
                     else ([manual] if manual else []))
    return {"ok": True, "registration": reg, "manual_kb_id": manual,
            "effective_kb_ids": effective,
            "effective_defense_kb_ids": effective_def}


@app.post("/api/admin/settings")
def admin_settings_set(body: SysSettingsIn, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    def _clean(raw):
        ids = []
        for x in (raw or []):
            kid = str(x or "").strip()
            if kid and kid not in ids and indexer.get_kb(kid):
                ids.append(kid)
        return ids
    out = system_settings.save({
        "registration": {
            "perms": {"kb": bool(body.kb), "defense": bool(body.defense)},
            "kb_ids": _clean(body.kb_ids),
            "defense_kb_ids": _clean(body.defense_kb_ids),
        }
    })
    return {"ok": True, **out}


@app.get("/api/manual")
def api_manual(request: Request):
    """内置使用手册（Markdown）。放 app/static/manual.md，登录后可读。"""
    p = APP_DIR / "static" / "manual.md"
    try:
        return {"ok": True, "markdown": p.read_text(encoding="utf-8")}
    except Exception as e:
        print(f"[manual] 读取失败: {type(e).__name__}", file=sys.stderr)
        return {"ok": False, "msg": "使用手册暂不可用"}


@app.get("/api/notice")
def notice_get(request: Request):
    """进入知识库 / 模拟答辩时展示的提示文案（登录用户都可读）。"""
    return {"ok": True, "notice": system_settings.notice_defaults()}


@app.post("/api/notice")
def notice_set(body: NoticeIn, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    out = system_settings.save_notice({"kb": body.kb, "defense": body.defense})
    return {"ok": True, "notice": out["notice"]}


@app.patch("/api/users/{uid}")
def users_patch(uid: str, body: UserPatch, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    target = users.get_user(uid)
    if not target:
        return JSONResponse({"ok": False, "msg": "用户不存在"}, status_code=404)
    if body.new_key or body.password:
        out, plain = users.set_password(uid, body.password or None)
        return {"ok": True, "user": out, "password": plain}
    if body.username:
        try:
            out = users.set_username(uid, body.username)
        except ValueError as e:
            return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
        return {"ok": True, "user": out}
    if body.perms is not None:
        out = users.set_perms(uid, body.perms)
        if out and (out.get("perms") or {}).get("defense"):
            _share_defense_kbs(out)
        return {"ok": True, "user": out}
    out = users.update_user(uid, name=body.name or None,
                            role=body.role or None,
                            note=body.note if body.note != "" else None)
    return {"ok": True, "user": out}


@app.delete("/api/users/{uid}")
def users_delete(uid: str, request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    try:
        ok = users.delete_user(uid)
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    if not ok:
        return JSONResponse({"ok": False, "msg": "用户不存在"}, status_code=404)
    return {"ok": True}


# ---------------- API: 管理员总览 ----------------

@app.get("/api/admin/overview")
def admin_overview(request: Request):
    """管理员一屏看全：系统健康、用户与在线、知识库、今日用量、缓存、OCR。"""
    u, err = _require_admin(request)
    if err:
        return err

    reg = indexer.load_registry()
    all_users = users.load_users()

    # 知识库总览
    kbs = []
    for k in reg:
        kid = k.get("id") or ""
        st = STATUS.get(kid) or {}
        try:
            kst = kb.state(kid)
        except Exception:
            kst = {}
        owner = next((x for x in all_users if x["id"] == k.get("owner")), None)
        kbs.append({
            "id": kid,
            "name": k.get("name") or "",
            "owner": (owner or {}).get("name") or "（无主）",
            "folder": k.get("folder") or "",
            "files": k.get("file_count") or (k.get("stats") or {}).get("files"),
            "chunks": k.get("chunk_count") or (k.get("stats") or {}).get("total_chunks"),
            "vectors": k.get("vector_count"),
            "vector_status": k.get("vector_status") or "",
            "indexing": bool(st.get("running")),
            "index_error": st.get("err") or "",
            "stale": bool(kst.get("stale") or kst.get("loading")),
        })
    kbs.sort(key=lambda x: -(x["chunks"] or 0))

    # 今日用量
    today = time.strftime("%Y-%m-%d")
    n_today, ms_sum, fb, errs = 0, 0, 0, 0
    users_today = {}
    for r in qa_log._iter(days=1):
        ts = r.get("ts")
        try:
            d = time.strftime("%Y-%m-%d", time.localtime(float(ts)))
        except Exception:
            continue
        if d != today:
            continue
        n_today += 1
        try:
            ms_sum += int(float(r.get("ms") or 0))
        except Exception:
            pass
        if str(r.get("fallback")).lower() == "true":
            fb += 1
        if (r.get("source_type") or "") in ("error", "timeout", "none"):
            errs += 1
        name = r.get("user") or "（未知）"
        users_today[name] = users_today.get(name, 0) + 1

    try:
        st7 = qa_log.stats(days=7)
    except Exception:
        st7 = {}

    # 缓存
    try:
        cstat = cache.stats()
    except Exception:
        cstat = {}

    # OCR 是否可用
    ocr_ok = True
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        ocr_ok = False

    return {
        "ok": True,
        "ts": int(time.time()),
        "system": {
            "uptime_s": int(time.time() - _BOOT_TS),
            "llm_enabled": llm.llm_enabled(cfg=_cfg()),
            "model": (llm.llm_settings(_cfg()) or {}).get("model") or "",
            "ocr": ocr_ok,
            "fallback_ask_first": bool(fallback.ask_first(_cfg())),
            "total_timeout": (llm.llm_settings(_cfg()) or {}).get("total_timeout"),
        },
        "accounts": {
            "total": len(all_users),
            "admin": sum(1 for x in all_users if x.get("role") == "admin"),
            "demo_like": sum(1 for x in all_users if "演示" in (x.get("name") or "")),
        },
        "today": {
            "asks": n_today,
            "avg_seconds": round(ms_sum / n_today / 1000, 1) if n_today else 0,
            "fallback": fb,
            "errors": errs,
            "active_users": len(users_today),
            "by_user": sorted(users_today.items(), key=lambda x: -x[1])[:10],
        },
        "week": st7,
        "kbs": kbs,
        "cache": cstat,
    }


@app.post("/api/logout")
def logout(response: Response):
    """退出登录。

    多标签页场景下**不能删 cookie** —— cookie 是整个浏览器共享的，删掉会把
    其他标签页里登录的另一个账号也一起踢下线。本标签页登出只需要前端清掉
    自己 sessionStorage 里的 token（前端 purgeLocalAuth 已处理）。
    token 本身是无状态 HMAC，服务端无需登记失效。
    """
    return {"ok": True}

# ---------------- 状态 ----------------

STATUS = {}          # kb_id -> {running, threads}
INDEX_LOCK = threading.Lock()


def _cfg():
    return llm.load_config()


def _cfg_with_user_key(request):
    """返回一份把当前用户 key（及自定义模型）合进去的配置副本。

    给那些"只收 cfg、没有 user_key 参数"的模块用（planner / fallback）。
    没配自己的 key 就原样返回全局配置；用户自定义模型不会被全局模型覆盖。
    """
    cfg = _cfg()
    uk = _user_llm_key(request)
    if not uk:
        return cfg
    cfg = dict(cfg)
    llmd = dict(cfg.get("llm") or {})
    llmd["api_key"] = uk
    um = _user_llm_model(request)
    if um:
        llmd["model"] = um
    cfg["llm"] = llmd
    return cfg


def _apply_thinking_choice(cfg, choice):
    """按前端按钮覆盖 llm.thinking；choice=None 表示沿用配置文件。"""
    if choice is None:
        return cfg
    cfg = dict(cfg)
    llmd = dict(cfg.get("llm") or {})
    if choice:
        budget = int(llmd.get("thinking_budget", 2048) or 2048)
        llmd["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        llmd["thinking"] = {"type": "disabled"}
    cfg["llm"] = llmd
    return cfg


def _user_llm_key(request):
    """取当前登录用户自己的大模型 key；没配就返回空串（调用方回落全局 key）。

    任何异常都返回空串：拿不到用户的 key 是小事，不能因此让整个问答挂掉。
    """
    try:
        u = _user_of(request)
        if not u:
            return ""
        import userllm
        return userllm.get_key(u.get("id", ""))
    except Exception as e:
        print(f"[userllm] 取用户 key 失败: {type(e).__name__}", file=sys.stderr)
        return ""


def _user_llm_model(request):
    """取当前用户自定义的模型名；没配返回空串，调用方沿用全局模型。"""
    try:
        u = _user_of(request)
        if not u:
            return ""
        import userllm
        return userllm.get_model(u.get("id", ""))
    except Exception:
        return ""


# ---------------- API: 文件/目录 ----------------

@app.get("/api/fs/list")
def fs_list(path: str = ""):
    return indexer.list_folders(path)


@app.get("/api/fs/drives")
def fs_drives():
    out = []
    for d in [f"{chr(c)}:/" for c in range(ord("A"), ord("Z") + 1)]:
        if Path(d).exists():
            out.append({"path": d, "name": d})
    return {"done": True, "drives": out}


# ---------------- API: 知识库 ----------------

@app.get("/api/kb")
def kb_list(request: Request, mode: str = "qa"):
    """列出知识库。mode=qa 看问答共享；mode=defense 看答辩共享。"""
    u = _current_user(request)
    admin = users.is_admin(u)
    read_mode = "defense" if mode == "defense" else "qa"
    reg = indexer.load_registry()
    owner_names = {x["id"]: x.get("name", "") for x in users.load_users()}
    out = []
    for kb_ in reg:
        # 管理员全部可读；普通用户可读自己的 + 对应入口分享的。
        # 没权限的也返回（前端置灰、不可点），让人知道有这个库、找管理员开通。
        can_read = bool(admin or users.can_read_kb(kb_, u, read_mode))
        # 异步索引队列的真实状态在 index_status.json（indexer.get_index_status），
        # 旧的 STATUS 只覆盖 sync=True 的排障路径。不合并的话前端轮询 /api/kb
        # 永远看不到 queued/running，上传后进度条会立刻消失。
        kid = kb_.get("id") or ""
        try:
            ist = indexer.get_index_status(kid) if kid else {}
        except Exception:
            ist = {}
        istatus = str(ist.get("status") or "")
        if STATUS.get(kid, {}).get("running") and istatus not in (
                "queued", "running", "done", "failed"):
            istatus = "running"
            ist = {"status": "running", "progress": "同步索引中"}
        if istatus in ("queued", "running"):
            kb_["status"] = "indexing"
        else:
            kb_["status"] = kb_.get("status", "")
        kb_["index_status"] = istatus
        kb_["index_progress"] = str(ist.get("progress") or "")
        kb_["index_error"] = str(ist.get("error") or "")
        kb_["index_attempt"] = ist.get("attempt") or 0
        kb_["can_read"] = can_read
        if not can_read:
            kb_["owner_name"] = ""
            kb_["mine"] = False
            kb_["shared"] = False
            kb_["readonly"] = True
        else:
            kb_["owner_name"] = owner_names.get(kb_.get("owner"), "")
            kb_["mine"] = (kb_.get("owner") == u.get("id"))
            # 不是自己的、又能看到 → 是别人分享的。前端据此显示"只读"标记、隐藏改名/删除
            kb_["shared"] = bool(not admin and not kb_["mine"])
            kb_["readonly"] = bool(kb_["shared"])
        qa_ids = set(users.shared_ids(kb_))
        def_ids = set(users.shared_defense_ids(kb_))
        kb_["shared_count_qa"] = len(qa_ids)
        kb_["shared_count_defense"] = len(def_ids)
        kb_["shared_count"] = len(qa_ids | def_ids)
        out.append(kb_)
    return {"kbs": out, "is_admin": admin}


@app.post("/api/kb")
async def kb_create(request: Request):
    """创建知识库。兼容 JSON 与 multipart(拖拽建库用 multipart 时也能解析)。"""
    try:
        data = await request.json()
    except Exception:
        form = await request.form()
        data = {k: (v if isinstance(v, str) else "") for k, v in form.items()}
    folder = str(data.get("folder", "") or "").strip()
    # 可空: 空表示"拖拽上传式"知识库(先建库,再用文件上传填充)
    if folder and not Path(folder).is_dir():
        return JSONResponse({"ok": False, "msg": f"文件夹不存在: {folder}"}, status_code=400)
    u = _current_user(request)
    kb_ = indexer.create_kb(str(data.get("name", "") or "").strip(), folder)
    if kb_ and u.get("id"):
        kb_["owner"] = u["id"]
        indexer._update_registry(kb_)
    return {"ok": True, "kb": kb_}


@app.delete("/api/kb/{kb_id}")
def kb_delete(kb_id: str):
    indexer.delete_kb(kb_id)
    STATUS.pop(kb_id, None)
    return {"ok": True}


class KbOwnerIn(BaseModel):
    owner: str = ""          # 目标用户的 id; 空串 = 交给当前管理员自己


@app.post("/api/kb/{kb_id}/owner")
def kb_set_owner(kb_id: str, body: KbOwnerIn, request: Request):
    """管理员把知识库转给指定用户，或收回名下。"""
    u, err = _require_admin(request)
    if err:
        return err
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    target = (body.owner or "").strip()
    if target:
        tu = users.get_user(target)
        if not tu:
            return JSONResponse({"ok": False, "msg": "目标用户不存在"}, status_code=400)
    else:
        tu = u
        target = u["id"]
    kb_["owner"] = target
    kb_["owner_name"] = tu.get("name") or ""
    indexer._update_registry(kb_)
    print(f"[kb] 归属变更 {kb_id} -> {target} ({tu.get('name')}) by {u.get('name')}")
    return {"ok": True, "kb": kb_}


@app.get("/api/kb/{kb_id}")
def kb_detail(kb_id: str):
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    return {"ok": True, "kb": kb_}


@app.get("/api/kb/{kb_id}/files")
def kb_files(kb_id: str, request: Request):
    """文件清单：源目录 + 上传目录，只列名字/大小，不读内容。"""
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    u = _current_user(request)
    if not (users.can_read_kb(kb_, u) or users.can_read_kb(kb_, u, "defense")):
        return JSONResponse({"ok": False, "msg": "无权查看该知识库"}, status_code=403)
    r = indexer.list_kb_files(kb_id)
    return r or {"ok": False, "msg": "知识库不存在"}


class KbShareIn(BaseModel):
    users: list = []          # 兼容旧接口：只设置问答共享
    users_qa: list = None     # 智能问答共享
    users_defense: list = None  # 模拟答辩共享


@app.post("/api/kb/{kb_id}/share")
def kb_set_share(kb_id: str, body: KbShareIn, request: Request):
    """管理员把知识库分别分享给问答/答辩用户（只读，不能改）。"""
    u, err = _require_admin(request)
    if err:
        return err
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)

    owner = kb_.get("owner")
    all_users = {x["id"]: x for x in users.load_users()}
    skipped = []

    def clean(raw):
        valid = []
        for uid in (raw or []):
            uid = str(uid or "").strip()
            if not uid:
                continue
            if uid not in all_users or uid == owner:
                skipped.append(uid)
                continue
            if uid not in valid:
                valid.append(uid)
        return valid

    legacy = body.users_qa is None and body.users_defense is None
    qa = clean(body.users if legacy else body.users_qa)
    users.set_shared_with(kb_, qa, mode="qa")
    if not legacy:
        users.set_shared_with(kb_, clean(body.users_defense), mode="defense")
    indexer._update_registry(kb_)
    qa_names = "、".join(all_users[x].get("name", x) for x in users.shared_ids(kb_)) or "（无）"
    def_names = "、".join(all_users[x].get("name", x) for x in users.shared_defense_ids(kb_)) or "（无）"
    print(f"[kb] 分享 {kb_id} 问答={qa_names} 答辩={def_names} by {u.get('name')}")
    return {"ok": True,
            "shared_with": users.shared_ids(kb_),
            "shared_with_defense": users.shared_defense_ids(kb_),
            "shared_names": qa_names,
            "shared_defense_names": def_names,
            "skipped": skipped, "kb": kb_}


@app.get("/api/kb/{kb_id}/share")
def kb_get_share(kb_id: str, request: Request):
    """看这个库分享给了谁。管理员可看任意库；普通人只能看自己的。"""
    u = _current_user(request)
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    if not (users.can_read_kb(kb_, u) or users.can_read_kb(kb_, u, "defense")):
        return JSONResponse({"ok": False, "msg": "无权访问"}, status_code=403)
    qa_ids = users.shared_ids(kb_)
    def_ids = users.shared_defense_ids(kb_)
    names = {x["id"]: x.get("name", "") for x in users.load_users()}
    return {"ok": True,
            "shared_with": qa_ids,
            "shared_with_defense": def_ids,
            "shared": [{"id": i, "name": names.get(i, i)} for i in qa_ids],
            "shared_defense": [{"id": i, "name": names.get(i, i)} for i in def_ids],
            "can_edit": users.resolve_owner_of_kb(kb_, u)}


class KbRename(BaseModel):
    name: str = ""


@app.post("/api/kb/{kb_id}/rename")
def kb_rename(kb_id: str, body: KbRename):
    if not kb_id:
        return {"ok": False, "msg": "缺少知识库 id"}
    name = (body.name or "").strip()
    if not name:
        return JSONResponse({"ok": False, "msg": "名称不能为空"}, status_code=400)
    if name != name.strip() or len(name) > 60:
        return JSONResponse({"ok": False, "msg": "名称需为 1-60 个字符"}, status_code=400)
    kb_ = indexer.rename_kb(kb_id, name)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    return {"ok": True, "kb": kb_}


@app.post("/api/kb/{kb_id}/index")
def kb_index(kb_id: str, sync: bool = False):
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)

    # sync=True：调用方要求同步拿结果（脚本/排障用），保持直接跑不变。
    # 异步分支交给 indexer 的任务队列（Claude Code 实现）：
    #   - 单 worker，同库只跑一个，运行中再次请求会在本轮后自动重跑
    #   - 状态落盘 app/data/<kb_id>/index_status.json，重启后能标 interrupted
    #   - 失败自动重试 2 次（共 3 次），仍失败标 failed
    # 之前这里是「起个 threading.Thread 直接跑 index_kb」，没有队列、没有
    # 持久化状态、没有重试，服务重启就丢进度。
    if not sync:
        return indexer.enqueue_index(kb_id, reason="manual")

    st = STATUS.setdefault(kb_id, {"running": False, "err": ""})
    if st["running"]:
        return {"ok": True, "status": "indexing", "msg": "已在索引中"}
    st["running"] = True

    def run():
        try:
            stats = indexer.index_kb(kb_, progress=lambda m: None)
            st["last_stats"] = stats
            try:
                cache.clear(kb_id)      # 内容变了, 旧答案可能过时
            except Exception:
                pass
            # 重建完成后后台预加载 BM25 缓存,使随后的首次提问免于长加载
            def _warm():
                try:
                    kb.load_kb_indexes(kb_id)
                except Exception:
                    pass
                # 图片索引跟着文档一起更新：只处理新出现的图，已有的跳过（增量）。
                # 不跑的话新文档的图不会出现在答案里；跑的话首次提问要等它算完，
                # 所以放后台线程，且加锁避免重复起任务。
                if _figures_enabled():
                    _refresh_image_index(kb_id)
            threading.Thread(target=_warm, daemon=True).start()
        except Exception as e:
            st["err"] = str(e)
        finally:
            st["running"] = False

    # 走到这里只可能是 sync=True（异步分支上面已经 return 了）
    run()
    return {"ok": True, "stats": st.get("last_stats"), "status": "done"}


@app.get("/api/kb/{kb_id}/index/status")
def kb_index_status(kb_id: str):
    """索引任务进度（前端轮询）。

    返回 index_status.json 里那一份：status / progress / started / finished /
    error / attempt / stats / peak_mb / elapsed。
    状态取值：idle / queued / running / done / failed / interrupted。
    """
    if not indexer.get_kb(kb_id):
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    try:
        job = indexer.get_index_status(kb_id)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"读取索引状态失败: {e}"},
                            status_code=500)
    return {"ok": True, "job": job}


def _upload_fsync_enabled():
    """每文件 fsync 很慢，大量小文件时可在 config 里关掉。
    关掉后断电可能丢最近上传，但正常进程内读取不受影响。"""
    try:
        import llm
        return bool(llm.load_config().get("upload", {}).get("fsync", False))
    except Exception:
        return False


@app.post("/api/kb/{kb_id}/upload")
async def kb_upload(kb_id: str, request: Request, index: bool = False):
    """拖拽/批量上传文件到该库的 upload 目录。multipart: 多个同名 files 字段。
    文件名可含相对子路径(拖文件夹时前端按目录结构提交),从而保留原结构。"""
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    up = indexer.upload_dir(kb_id)
    do_fsync = _upload_fsync_enabled()
    form = await request.form()
    saved, skipped = 0, []
    for f in form.getlist("files"):
        rel = (f.filename or "").replace("\\", "/").strip()
        if not rel or rel in (".", "/"):
            skipped.append(rel or "(空名)")
            continue
        dest = (up / rel).resolve()
        # 防止路径穿越到上传目录外
        if not str(dest).startswith(str(up.resolve())):
            skipped.append(rel); continue
        # 先写临时文件再原子改名, 避免传输中断留下半截文件被当成正常文档索引。
        # 临时名必须每请求唯一：两个用户同时传同名文件时，固定 .tmp 会互相覆盖。
        tmp = dest.with_name(
            f"{dest.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with open(tmp, "wb") as fh:
                while True:
                    buf = f.file.read(1 << 16)
                    if not buf:
                        break
                    fh.write(buf)
                    written += len(buf)
                fh.flush()
                if do_fsync:
                    os.fsync(fh.fileno())
            if written == 0:
                tmp.unlink(missing_ok=True)
                skipped.append(f"{rel}(空文件)")
                continue
            tmp.replace(dest)
            saved += 1
        except Exception as e:
            skipped.append(f"{rel}({e})")
            try:
                if tmp.exists(): tmp.unlink()
            except Exception:
                pass
    if index and saved:
        threading.Thread(target=lambda: (indexer.index_kb(kb_, progress=lambda m: None))).start()
    return {"ok": True, "saved": saved, "skipped": skipped}


def _zip_entry_name(info):
    """zip 文件名：没带 UTF-8 标记时，zipfile 会按 cp437 解成乱码；
    再 cp437→bytes→GB18030 还原中文包名。"""
    name = info.filename or ""
    if not (info.flag_bits & 0x800):
        try:
            name = name.encode("cp437").decode("gb18030")
        except Exception:
            pass
    return name.replace("\\", "/").lstrip("/")


@app.post("/api/kb/{kb_id}/upload_zip")
async def kb_upload_zip(kb_id: str, file: UploadFile = File(...)):
    """上传 zip，解压到该库 upload 目录；带路径穿越、zip bomb、文件数限制。"""
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    up = indexer.upload_dir(kb_id)
    up_res = up.resolve()
    max_zip = 500 * 1024 * 1024
    max_unzip = 2 * 1024 * 1024 * 1024
    max_files = 20000
    max_entry = 500 * 1024 * 1024
    tmp_zip = up.parent / f".zipupload-{os.getpid()}-{time.time_ns()}.zip"
    try:
        size = 0
        with open(tmp_zip, "wb") as dst:
            while True:
                buf = await file.read(1 << 20)
                if not buf:
                    break
                size += len(buf)
                if size > max_zip:
                    return JSONResponse(
                        {"ok": False, "msg": "压缩包太大（限 500MB）"}, status_code=413)
                dst.write(buf)
        if not zipfile.is_zipfile(tmp_zip):
            return JSONResponse({"ok": False, "msg": "不是有效的 zip 文件"}, status_code=400)
        do_fsync = _upload_fsync_enabled()
        saved = 0
        saved_bytes = 0
        skipped = []
        with zipfile.ZipFile(tmp_zip) as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                return JSONResponse(
                    {"ok": False, "msg": f"压缩包内文件太多（限 {max_files} 个）"},
                    status_code=413)
            total_unzip = sum(max(0, i.file_size) for i in infos)
            if total_unzip > max_unzip or (
                    size > 0 and total_unzip > max(200 * 1024 * 1024, size * 100)):
                return JSONResponse(
                    {"ok": False, "msg": "解压后体积异常（压缩比过高），已拒绝"},
                    status_code=413)
            for info in infos:
                if info.flag_bits & 0x1:
                    return JSONResponse(
                        {"ok": False, "msg": "压缩包不能有密码，请去掉密码后重传"},
                        status_code=400)
                name = _zip_entry_name(info)
                low = name.lower()
                if (not name or low.startswith("__macosx/") or
                        low == ".ds_store" or low.endswith("/.ds_store") or
                        low == "thumbs.db" or low.endswith("/thumbs.db")):
                    continue
                rel = name.strip("/")
                parts = [p for p in rel.split("/") if p]
                if not parts or any(p == ".." for p in parts):
                    skipped.append(name)
                    continue
                dest = (up / "/".join(parts)).resolve()
                if not (str(dest) == str(up_res) or
                        str(dest).startswith(str(up_res) + os.sep)):
                    skipped.append(name)
                    continue
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                # 符号链接不落盘
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    skipped.append(f"{name}(符号链接)")
                    continue
                if info.file_size > max_entry:
                    skipped.append(f"{name}(单文件过大)")
                    continue
                if info.file_size == 0:
                    skipped.append(f"{name}(空文件)")
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(
                    f"{dest.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
                try:
                    with zf.open(info) as src, open(tmp, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                        dst.flush()
                        if do_fsync:
                            os.fsync(dst.fileno())
                    tmp.replace(dest)
                    saved += 1
                    saved_bytes += info.file_size
                except Exception as e:
                    skipped.append(f"{name}({e})")
                    try:
                        if tmp.exists():
                            tmp.unlink()
                    except Exception:
                        pass
        return {"ok": True, "saved": saved, "bytes": saved_bytes,
                "skipped": skipped[:50], "skipped_total": len(skipped),
                "entries": len(infos)}
    except zipfile.BadZipFile:
        return JSONResponse({"ok": False, "msg": "zip 文件损坏"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"解压失败: {e}"}, status_code=500)
    finally:
        try:
            tmp_zip.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------- API: 手写笔记(纯文字/图片直接进库) ----------------

def _notes_dir(kb_id):
    d = indexer.upload_dir(kb_id) / "_notes"
    (d / "images").mkdir(parents=True, exist_ok=True)
    return d


_NOTE_IMG_RE = re.compile(r"^\s*data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", re.S)


@app.get("/api/kb/{kb_id}/notes")
def kb_notes_list(kb_id: str):
    """列出该库里手写的笔记(供前端选择编辑)。"""
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    d = _notes_dir(kb_id)
    items = []
    for p in sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        title = next((ln[2:].strip() for ln in txt.splitlines() if ln.startswith("# ")), p.stem)
        items.append({"filename": p.name, "title": title,
                      "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
                      "chars": len(txt)})
    return {"ok": True, "notes": items}


@app.get("/api/kb/{kb_id}/note")
def kb_note_get(kb_id: str, filename: str):
    """读取一篇笔记(用于再次编辑)。"""
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    d = _notes_dir(kb_id)
    p = (d / Path(filename).name)
    if not p.is_file():
        return JSONResponse({"ok": False, "msg": "笔记不存在"}, status_code=404)
    txt = p.read_text(encoding="utf-8", errors="replace")
    # 拆出标题; 图片行保留在正文里(编辑后保存不会丢图), 另附一份清单供提示
    lines = txt.splitlines()
    title = ""
    imgs, body_lines = [], []
    for ln in lines:
        if not title and ln.startswith("# "):
            title = ln[2:].strip()
            continue
        m = re.match(r"^!\[(.*?)\]\(images/([^)]+)\)\s*$", ln.strip())
        if m:
            imgs.append({"name": m.group(1) or m.group(2), "file": m.group(2)})
        body_lines.append(ln)
    return {"ok": True, "title": title, "content": "\n".join(body_lines).strip(),
            "images": imgs, "filename": p.name}


@app.post("/api/kb/{kb_id}/note")
def kb_note_save(kb_id: str, body: NoteIn):
    """保存一段文字(可含粘贴图片)为 md 文档, 写入 upload/_notes。

    用户不必自己造文件: 前端编辑器里写完点保存即可, 再重建索引就能被检索到。
    """
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    title = (body.title or "").strip()
    content = (body.content or "").strip()
    if not title and not content and not body.images:
        return JSONResponse({"ok": False, "msg": "标题和正文不能都为空"}, status_code=400)

    d = _notes_dir(kb_id)
    img_dir = d / "images"
    stamp = time.strftime("%Y%m%d-%H%M%S")

    # 1) 图片: data URL → 文件
    md_imgs = []
    for i, im in enumerate(body.images or [], 1):
        m = _NOTE_IMG_RE.match(str(im.get("data") or ""))
        if not m:
            continue
        sub = m.group(1).lower()
        ext = {"jpeg": "jpg", "svg+xml": "svg", "x-icon": "ico"}.get(sub, re.sub(r"[^a-z0-9]", "", sub) or "png")
        fn = f"{stamp}-{i}.{ext}"
        try:
            (img_dir / fn).write_bytes(base64.b64decode(m.group(2)))
        except Exception:
            continue
        md_imgs.append(f"![{im.get('name') or fn}](images/{fn})")

    # 2) 组装 md(标题 + 图片 + 正文)
    parts = [f"# {title or '未命名笔记'}", ""]
    if md_imgs:
        parts += md_imgs + [""]
    if content:
        parts.append(content)
    md = "\n".join(parts).rstrip() + "\n"

    # 3) 文件名: 编辑则覆盖原文件, 新建则按时间戳+标题
    if body.filename:
        fn = Path(body.filename).name
        if not fn.endswith(".md"):
            fn += ".md"
    else:
        safe = re.sub(r'[\\/:*?"<>|\s]+', "-", title)[:40].strip("-") or "note"
        fn = f"{stamp}-{safe}.md"
    dest = d / fn
    dest.write_text(md, encoding="utf-8")
    rel = dest.relative_to(indexer.upload_dir(kb_id)).as_posix()
    return {"ok": True, "path": rel, "filename": fn,
            "msg": "已保存到知识库,重建索引后即可被检索"}


# ---------------- API: 问答 ----------------

_CMD_RE = re.compile(
    r"^\s*(?:请)?(?:帮我)?(开启|打开|启用|关掉|关闭|清空|重置|不用)(?:一下)?"
    r"(?:上下文|上文|对话|会话)\s*[，,。:：;；、\s]*")


def _strip_cmd(q):
    """剥掉问题开头的开关类指令，只留下真正的问题。

    实测过的坑：用户把「开启上下文，那demo对应的uat账号呢」整句发过来，
    多出来的「开启上下文」四个字进了 BM25 / 向量检索，把那篇关键笔记挤出前 8，
    于是回答变成"片段中未找到"。
    """
    out = (q or "").strip()
    for _ in range(2):                       # 最多连剥两条（"开启上下文，清空上文，…"）
        m = _CMD_RE.match(out)
        if not m:
            break
        out = out[m.end():].strip()
    return out


def _has_confident_hit(results, min_score=2.0):
    """判断检索结果里是否有可用的高置信命中(BM25 得分过线)。"""
    if not results:
        return False
    for r in results[:6]:
        s = r.get("score", 0) or 0
        if s >= min_score:
            return True
    return False


# ---------------- 按分数分布动态决定给几条 ----------------
#
# 人给的需求（2026-11-26）：
#   「每次都 8 条，感觉不好，能不能改为定为到答案了，那就少点，
#     如果 8 条里面都没有就适当多点，当然不能无限查询，
#     有的时候就是知识库没有这个内容。」
#
# 固定给 8 条的问题：答案本来就在第 1 条时，后面 7 条无关内容会把答案稀释掉，
# 大模型容易东拉西扯。反过来，如果 8 条都不相关，硬塞 8 条只会让它编。
#
# 判据用 rerank_score 而不是 score。2026-11-26 实测（鑫元库，开重排）：
#
#   问题                      rerank_score 序列                     含义
#   otrade 部署需要准备…      4.74 4.69 4.39 4.39 4.39 4.27 3.91 3.57   全正，都相关
#   Hsiar 是做什么的          3.07 2.65 2.39 0.93 0.56 0.52 0.21 -0.07   前 3 强，第 4 条断崖
#   系统上线投产需要准备…     3.75 3.67 3.67 3.67 2.82 2.81 2.75 2.75   平滑下降
#   今天天气怎么样            3.02 2.56 -4.57 -8.11 …                   前 2 弱，后面全负
#   如何做红烧肉              -3.61 -3.61 -4.06 -4.07 …                 全负 = 库里没有
#
# 规律：正数=相关，负数=不相关。而 BM25 的 score 在同一结果里会出现
# [0.0, 100.37, 123.12, 0.0, …] 这种 0 与百位混排，没法判断相关性。
#
# 四档（人已确认第三档拆成「全负就说没找到」）：
#   断层明显（首条远超次条）  → 3 条：答案很明确，多了是噪音
#   分数平滑下降             → 5 条：需要多点上下文
#   全部分数很低             → 3 条：仍给弱片段，由模型判断有没有（别硬判没找到）
#   低分但不全负             → 8 条：尽量凑
#
# 关掉重排的库没有 rerank_score，**回退到固定条数**，不瞎裁（人已确认）。

# ---------------- 决定给几条：只在"冗余"时做减法 ----------------
#
# 人给的需求（2026-11-26）：
#   「每次都 8 条，感觉不好，能不能改为定为到答案了，那就少点，
#     如果 8 条里面都没有就适当多点，当然不能无限查询，
#     有的时候就是知识库没有这个内容。」
#
# 第一版按「重排分数」裁：分数高的留、低的丢。**实测是错的**，因为
# 关键内容片段的重排分往往偏低——「JDK 要求 8u181」这种技术细节，
# 字面不像在回答"部署要注意什么"，但它恰恰是答案。
#
# 2026-11-26 在 6 条真实评测用例上的实测：
#   xy-03 「otrade 部署和升级要注意什么」
#     全库 8 条里**只有 1 条**含 JDK，而且它排在第 5 位。
#     裁到 3 条 = 正好裁掉唯一有用的那条。
#   xy-06 「otrade 升级流程」
#     8 条里 7 条含关键内容；裁到 3 条只剩 3 条，覆盖度直接腰斩。
#
# 评测对比：裁剪关闭 6/6，按分数裁 4/6。**按分数裁会伤答案。**
#
# 所以判据换成「冗余」而不是「分数」：
#   * 同样的内容说两遍没有信息增量 → 可以删
#   * 分数低但内容独有的片段 → 绝不能删
#
# 三档（人已确认）：
#   首条很强 + 后面大量重复    → 3~5 条：删掉的都是重复的文档片段
#   分数平滑 / 命中分散        → 8 条：不动
#   全部分数都很低            → 3 条：给弱片段由模型判断"有没有"
#
# 永远不做的事：因为分数低就丢片段。宁可多给，不可漏掉唯一答案。

# ---------------- 去掉完全重复的检索片段（方案 B，2026-11-26） ----------------
#
# 人给的需求：「每次都 8 条，感觉不好，能不能改为定为到答案了，那就少点，
#   如果 8 条里面都没有就适当多点，当然不能无限查询，
#   有的时候就是知识库没有这个内容。」
#
# 试过两版按「重排分数」裁（高分留低分丢），**都失败**，实测证据：
#
#   xy-03「otrade 部署和升级要注意什么」
#     含 JDK 的片段排在第 **7、8** 位（re rank 3.75），前 6 条是 4.26~5.41。
#     任何按分数的裁剪都保不住它 —— 可它恰恰是答案的关键部分。
#     全库 8 条里只有这 2 条提到 JDK。
#
#   评测对照（每档连跑两轮）：
#     8 条不裁  → 6/6, 6/6   （稳定）
#     按分数裁  → 6/6, 5/6   （不稳定，取决于那几条碰巧有没有重复）
#
# 根因：**"像不像在回答"和"内容是不是答案"是两件事**。重排模型判的是前者；
# 「JDK 版本要求 8u181」这种技术参数，字面确实不像在回答"部署要注意什么"，
# 但它是答案的一部分。按分数裁 = 系统性地优先删掉最专业的那部分。
#
# 所以最终只做一件事：**删掉文本高度重复的片段**。
#   * 同一份文档被切成多块、内容几乎一样 → 删，没有信息损失
#   * 内容各不相同 → 一条都不删，哪怕分数很低
#   * 不设"必须裁到 N 条"的目标，有多少重复就删多少
#
# 结果可能是 8 条、7 条或 5 条，取决于库里文档的重复程度。这是正确的行为：
# 该少的时候少，但绝不能因为"想要少"而丢掉唯一答案。

_TRIM_DEFAULTS = {
    "enabled": True,
    # 两段文字的 5 字滑窗重合度 >= 此值，判为重复，只保留分数高的那条
    "dup_ratio": 0.85,
    # 同一份文档最多保留几条（同一份长文档切成很多块时，内容往往高度相似）
    "same_doc_keep": 3,
    # 这条片段里出现了"前面都没有的技术名词"（JDK / Redis / Zookeeper…），
    # 就绝不删——它可能正是唯一讲这个点的那一段。
    "protect_unique_terms": True,
}


def _trim_cfg(cfg=None):
    """取去重配置，允许在 config.json 的 retrieval.trim 里覆盖。"""
    out = dict(_TRIM_DEFAULTS)
    try:
        sec = (cfg or llm.load_config()).get("retrieval", {}).get("trim") or {}
    except Exception:
        sec = {}
    for k in _TRIM_DEFAULTS:
        if k in sec:
            v = sec[k]
            if k in ("enabled", "protect_unique_terms"):
                out[k] = bool(v)
            else:
                try:
                    out[k] = float(v) if k == "dup_ratio" else int(v)
                except Exception:
                    pass
    return out


def _shingles(text, n=5):
    """把正文切成 n 字滑窗集合，用来算两段文字的重合度。

    取 5 字是因为中文里 5 个连续的词基本能确定"这是在说同一句"，
    再短容易把不同的句子误判成重复。
    """
    t = "".join((text or "").split())
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _overlap(a_set, b_set):
    """a 有多大比例出现在 b 里，0~1。"""
    if not a_set:
        return 0.0
    return len(a_set & b_set) / len(a_set)


_TERM_RE = None


def _tech_terms(text):
    """提取一段文字里的"技术名词"：英文缩写/版本号/产品名。

    JDK、Redis、Zookeeper、RabbitMQ、O32、V1.3、.NET 这类词，
    是判断"这段有没有独有信息"最靠得住的信号——中文描述可以换着说，
    但技术名词该出现还是会出现。
    """
    global _TERM_RE
    if _TERM_RE is None:
        import re as _re
        _TERM_RE = _re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
    return {m.group(0).lower() for m in _TERM_RE.finditer(text or "")}


def _trim_by_scores(results, cfg=None):
    """去掉重复片段。返回 (保留的列表, 决策说明)。

    名字保留 `_trim_by_scores` 是为了不动调用处，但**已经不按分数裁了**，
    只按文本重合度去重。说明会打日志，方便回查删了什么。
    """
    conf = _trim_cfg(cfg)
    n_total = len(results or [])
    if not conf["enabled"] or n_total <= 1:
        return results, {"why": "跳过(未启用或不足 2 条)", "keep": n_total}

    # 记下每条的位置，日志里能看出删掉的是第几条
    kept = []
    kept_sh = []
    doc_count = {}
    dropped = []
    seen_terms = set()      # 已保留片段里出现过的技术名词
    for idx, r in enumerate(results):
        text = r.get("text") or ""
        doc = r.get("path") or ""

        if doc_count.get(doc, 0) >= conf["same_doc_keep"]:
            dropped.append(idx + 1)
            continue

        sh = _shingles(text)

        # 保护：这条带来了前面没有的技术名词，说明它有独有信息，不能删。
        # 2026-11-26 实测 xy-03 就栽在这里——含 JDK 的那条和前面的尽调材料
        # 文本高度重合（同族文档），被误判成重复删掉了，可全库只有它提到 JDK。
        if conf["protect_unique_terms"]:
            new_terms = _tech_terms(text) - seen_terms
            if len(new_terms) >= 1 and idx > 0:
                kept.append(r)
                kept_sh.append(sh)
                doc_count[doc] = doc_count.get(doc, 0) + 1
                seen_terms |= new_terms
                continue

        if any(_overlap(sh, ks) >= conf["dup_ratio"] for ks in kept_sh):
            dropped.append(idx + 1)
            continue

        kept.append(r)
        kept_sh.append(sh)
        doc_count[doc] = doc_count.get(doc, 0) + 1
        seen_terms |= _tech_terms(text)

    if not dropped:
        return kept, {"why": "没有重复片段，原样给出", "keep": len(kept)}

    return kept, {"why": f"删掉 {len(dropped)} 条重复片段", "keep": len(kept),
                  "dropped_at": dropped}


_NOT_FOUND_MARKS = ("未找到相关信息", "未检索到相关内容", "文档中未找到",
                    "未在文档中", "没有找到相关", "未找到与", "文档中未提及")


def _looks_like_not_found(answer):
    """大模型回答是否表示'知识库没答案'。开头 60 字内出现否定标记即可(不限长度)。"""
    if not answer:
        return False
    a = answer.strip().replace("\u3000", " ").replace("\n", " ")
    a = " ".join(a.split())                     # 压缩空白
    return any(m in a[:80] for m in _NOT_FOUND_MARKS) and len(a) < 500


def _diversify(results, topk, max_per_path=2):
    """同一份文件最多占 max_per_path 个引用位。

    检索经常出现"一份部署手册的 4 个块霸占 8 个引用位"的情况，
    模型看到的全是同一份文档，其它同样相关的文档（系统概述、环境信息）挤不进来。
    这里保证引用尽量覆盖不同文件，剩下的席位才按原顺序补齐。
    用户手册例外：操作步骤常跨页/跨小节，允许最多 4 块，避免只拿到目录页。
    """
    out, cnt = [], {}
    for r in results:
        p = r.get("path") or ""
        cap = 4 if module_router.is_manual_path(p) else max_per_path
        if cnt.get(p, 0) >= cap:
            continue
        cnt[p] = cnt.get(p, 0) + 1
        out.append(r)
        if len(out) >= topk:
            return out
    if len(out) < topk:                       # 文件种类不够，按原顺序把剩下的补上
        seen = {id(x) for x in out}
        for r in results:
            if id(r) not in seen:
                out.append(r)
                if len(out) >= topk:
                    break
    return out


_HOWTO_RE = re.compile(r"怎么|如何|步骤|流程|操作|下单|委托|开仓|平仓|申购|赎回|询价|报价")


def _is_howto(q):
    return bool(_HOWTO_RE.search(q or ""))


def _manual_query(q, modules):
    """模块 + 操作类问题追加一条手册定向子查询。"""
    if modules and _is_howto(q):
        return f"{' '.join(modules)} 用户手册 操作说明 步骤 {q}"
    return ""


def _manual_hits(kb_id, q, modules, topk=2, pool=64, rerank_n=16, mode="filtered"):
    """模块 + 操作类问题：额外做一次“用户手册定向检索”。

    综合手册（O3.2 基础用户手册）一页里可能同时出现股票/债券/基金/回购，
    普通检索容易被培训 PPT、系统参数表挤掉；这里用“模块 + 用户手册 +
    操作说明 + 原问题”定向捞手册页，保底放进引用。

    mode:
      full     —— 旧行为：召回 top12 并让 hybrid_search 重排全部候选（约 58 条，30s）
      filtered —— 先按“手册路径 + 模块”过滤，只重排过滤后的前 rerank_n 条（默认）
    """
    if not modules or not q or not _is_howto(q):
        return []
    sub = f"{' '.join(modules)} 用户手册 操作说明 步骤 {q}"

    if mode == "full":
        try:
            rs = backend.retrieve(kb_id, sub, top_k=max(12, topk * 3),
                                  enable_vector=True, enable_rerank=True) or []
        except Exception as e:
            print(f"[manual] 手册定向检索失败: {e}", file=sys.stderr)
            return []
        out = []
        for r in rs:
            if (module_router.is_manual_path(r.get("path") or "")
                    and module_router.matches(r, modules)):
                out.append(r)
                if len(out) >= topk:
                    break
        return out

    # filtered：召回不重排，先过滤手册路径+模块，再只重排少量候选
    try:
        cands = backend.retrieve(kb_id, sub, top_k=max(pool, topk * 3),
                                 enable_vector=True, enable_rerank=False) or []
    except Exception as e:
        print(f"[manual] 手册定向检索失败: {e}", file=sys.stderr)
        return []
    manual, seen = [], set()
    for r in cands:
        rid = r.get("id") or f"{r.get('path')}#{r.get('chunk')}"
        if rid in seen:
            continue
        if (module_router.is_manual_path(r.get("path") or "")
                and module_router.matches(r, modules)):
            seen.add(rid)
            manual.append(r)
    if not manual:
        return []
    pool_n = manual[:max(rerank_n, topk)]
    t0 = time.time()
    try:
        import rerank
        rr = rerank.rerank(q, pool_n, topk=topk * 2) or pool_n
    except Exception as e:
        print(f"[manual] 过滤后重排跳过: {e}", file=sys.stderr)
        rr = pool_n
    print(f"[manual] filtered rerank {len(pool_n)}/{len(manual)} cands "
          f"in {time.time() - t0:.1f}s", file=sys.stderr)
    return rr[:topk]


def _manual_cfg(rc):
    mh = rc.get("manual_hits") or {}
    mode = str(mh.get("mode", "auto") or "auto").lower()
    try:
        pool = int(mh.get("pool", 64) or 64)
    except Exception:
        pool = 64
    try:
        rerank_n = int(mh.get("rerank_n", 16) or 16)
    except Exception:
        rerank_n = 16
    return mode, pool, rerank_n


def _manual_pages(records, modules):
    """从记录里挑出"用户手册路径 + 当前模块"的片段（去重，保持顺序）。"""
    out, seen = [], set()
    for r in records or []:
        rid = r.get("id") or f"{r.get('path')}#{r.get('chunk')}"
        if rid in seen:
            continue
        if (module_router.is_manual_path(r.get("path") or "")
                and module_router.matches(r, modules)):
            seen.add(rid)
            out.append(r)
    return out


def _manual_reserved(kb_id, q, modules, topk, rc, records):
    """手册定向检索的统一入口（流式/非流式共用）。

    auto（默认）：主链路记录里已有 >=2 条手册页 → 跳过；
    否则按 filtered 只召回+过滤+重排前 rerank_n 条；mode=full 保留旧行为。
    """
    mode, pool, rerank_n = _manual_cfg(rc)
    if not modules or mode == "off":
        return []
    manual_topk = min(2, topk)
    in_main = _manual_pages(records, modules)
    if mode in ("auto", "skip_if_main") and len(in_main) >= manual_topk:
        print(f"[manual] skip: main candidates already have "
              f"{len(in_main)} manual pages", file=sys.stderr)
        return []
    return _manual_hits(kb_id, q, modules, topk=manual_topk, pool=pool,
                        rerank_n=rerank_n,
                        mode="full" if mode == "full" else "filtered")


def _merge_manual(picked, manual, topk):
    """手册页优先放在引用前面，按 id 去重。"""
    out, seen = [], set()
    for r in list(manual or []) + list(picked or []):
        rid = r.get("id") or f"{r.get('path')}#{r.get('chunk')}"
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
        if len(out) >= topk:
            break
    return out


def _multi_search(kb_id, queries, topk, rc, modules=None):
    """多路检索(查询拆解后用): 各路只做召回, 合并去重后**统一重排一次**。

    相比"每路各重排一次", 延迟从数十秒降到 2~3 秒。
    modules 非空时，在重排/去重之前先按业务模块过滤，避免其他模块的片段
    被 _diversify 强行塞进引用位。
    返回 None 表示无索引。
    """
    def _one(sub):
        return backend.retrieve(kb_id, sub, top_k=topk,
                                enable_vector=rc.get("enable_vector", True),
                                enable_rerank=False)

    # 多路检索并行：串行时 4 路各 10 秒，光召回就要 40 秒，而它们互不依赖
    if len(queries) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(queries))) as ex:
            lists = list(ex.map(_one, queries))
    else:
        lists = [_one(queries[0])]
    if any(x is None for x in lists):
        return None
    cand = planner.merge_results([x for x in lists if x], topk * 3)
    # 文件名逐字命中的文档（如问题里写着"环境信息"、库里就有《测试环境信息.txt》）。
    # 长问题重排时这类文档几乎必被挤掉，所以先留好位子再重排其余部分。
    try:
        reserved = kb.name_hits(kb_id, queries[0], max_docs=2, per_doc=2) or []
    except Exception as e:
        print(f"[ask] 文件名命中跳过: {e}")
        reserved = []
    if modules:
        # 先按模块过滤，再重排/去重：否则 _diversify 会把其他模块的片段
        # 强行补进引用位，正确模块的片段反而被每文件 2 块的上限挤掉。
        cand, _drop = module_router.filter_records(cand, modules)
        reserved, _drop_r = module_router.filter_records(reserved, modules) if reserved else ([], 0)
        if _drop or _drop_r:
            print(f"[module] {kb_id} 过滤其他模块片段 {_drop + _drop_r} 个（{ '、'.join(modules) }）",
                  file=sys.stderr)
    # 手册定向检索：主链路已经有足够的手册页就跳过（省一次全量重排）；
    # 没有才跑 filtered（先过滤手册路径+模块，只重排少量候选）。
    manual_reserved = _manual_reserved(
        kb_id, queries[0], modules, topk, rc,
        list(cand or []) + list(reserved or []))
    if modules and not cand and not reserved:
        return manual_reserved[:topk]
    # 统一重排: 用原问题作为 query(最终要回答的仍是原问题)
    try:
        import rerank
        # 多留些候选，重排后再做文件去重，否则去重会把好结果挤没
        rr = rerank.rerank(queries[0], cand, topk=topk * 2)
        if rr:
            picked = _with_reserved(_diversify(rr, topk), reserved, topk)
            return _merge_manual(picked, manual_reserved, topk)
    except Exception as e:
        print(f"[ask] 合并重排跳过: {e}")
    picked = _with_reserved(_diversify(cand, topk), reserved, topk)
    return _merge_manual(picked, manual_reserved, topk)


def _with_reserved(picked, reserved, topk, max_reserve=2):
    """把"文件名逐字命中"的片段保底插进结果，最多占 max_reserve 个引用位。"""
    if not reserved or not picked:
        return picked
    have = {p["path"] for p in picked}
    keep = []
    for r in reserved:
        if r["path"] in have:
            continue
        if len(keep) >= max_reserve:
            break
        keep.append(r)
        have.add(r["path"])
    if not keep:
        return picked
    return picked[:max(0, topk - len(keep))] + keep


def _build_sources(results, kb_id=None, brief=200, full=1500):
    """构造引用片段。

    text      : 截断短版(200 字), 供前端引用区展示, 避免刷屏。
    text_full : 完整版(最多 1500 字), **专供大模型上下文**——包名/路径/版本号/代码
                等标识信息不能被截断, 否则回答会出现 "ARCH-AI1.0…" 这种残缺内容。
    images    : 该片段文本里引用到的文档插图, 供前端显示缩略图。
    """
    src = []
    for r in results:
        raw = (r["text"] or "").strip()
        flat = " ".join(raw.split())
        short = flat[:brief] + "…" if len(flat) > brief else flat
        fulltxt = raw if len(raw) <= full else raw[:full] + "…"
        item = {"path": r["path"], "score": r.get("score", r.get("rerank_score", 0)),
                "rerank_score": r.get("rerank_score"),
                "text": short, "text_full": fulltxt}
        if kb_id:
            try:
                refs = imgserve.images_in_text(raw)
                # 这个块自己没引用图片时，退回"该文档里前几张图"——用户点开引用
                # 至少能看到这份材料里的界面截图/架构图，总比什么都没有强。
                imgs = imgserve.doc_images(kb_id, r["path"], refs or None, limit=4)
                if imgs:
                    item["images"] = imgs
            except Exception:
                pass  # 取图失败不能影响正常回答
        src.append(item)
    return src


_TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".ini", ".xml", ".yaml", ".yml", ".py",
             ".sql", ".conf", ".cfg", ".properties"}
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
_MEDIA_EXT = {".pdf", ".mp4", ".mp3", ".wav", ".ogg"}


def _preview_html(kb_id, path, name, content_html, kind, size=None, hl=""):
    """生成预览页 HTML: 顶栏(标题+下载按钮) + 命中片段卡片 + 内容区。

    hl: 检索命中的片段文本(可选), 页面会高亮并滚动定位到该段落。
    """
    enc = urllib.parse.quote(path)
    dwl = f"/api/kb/{urllib.parse.quote(kb_id)}/file/download?path={enc}"
    raw = f"/api/kb/{urllib.parse.quote(kb_id)}/file/raw?path={enc}"
    size_txt = f"{size} 字节" if size else ""
    badge = {"text": "文本", "image": "图片", "media": "媒体/PDF", "office": "文档", "error": "错误"}.get(kind, "")

    # 左侧文件树（目录导航）。用普通字符串拼，避免和 f-string 的花括号打架。
    nav_css = """
  .shell { display:flex; align-items:flex-start; }
  #nav { width:280px; flex:none; position:sticky; top:52px; max-height:calc(100vh - 52px);
         overflow:auto; background:#fff; border-right:1px solid #e3e6ea; padding:10px 8px;
         font-size:12.5px; box-sizing:border-box; }
  #nav .navhead { font-weight:600; color:#334155; padding:4px 6px 8px; position:sticky; top:0;
                  background:#fff; z-index:1; }
  #nav details { margin-left:8px; }
  #nav summary { cursor:pointer; padding:3px 5px; color:#475569; border-radius:5px;
                 white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #nav summary:hover { background:#f1f5f9; }
  #nav a.navlink { display:block; padding:4px 8px; margin:1px 0; color:#334155;
                   text-decoration:none; border-radius:5px; white-space:nowrap;
                   overflow:hidden; text-overflow:ellipsis; }
  #nav a.navlink:hover { background:#eff6ff; }
  #nav a.navlink.active { background:#dbeafe; color:#1d4ed8; font-weight:600; }
  #nav .navempty { color:#94a3b8; padding:10px 6px; line-height:1.6; }
  #navBtn { display:none; background:#374151; border:0; color:#fff; border-radius:6px;
            padding:6px 10px; font-size:13px; cursor:pointer; }
  #navMask { display:none; position:fixed; inset:0; background:rgba(15,23,42,.35); z-index:150; }
  main { flex:1; min-width:0; padding:20px; max-width:1100px; margin:0 auto; }
  @media (max-width:820px) {
    .shell { display:block; }
    #nav { position:fixed; left:0; top:0; width:82vw; max-width:320px; height:100vh;
           max-height:none; z-index:200; transform:translateX(-100%);
           transition:transform .18s ease; border-right:1px solid #e3e6ea; }
    #nav.open { transform:translateX(0); }
    #navMask.show { display:block; }
    #navBtn { display:inline-block; }
    main { padding:14px; }
  }
"""
    nav_script = r"""
<script>
(function(){
  var nav = document.getElementById('nav');
  var mask = document.getElementById('navMask');
  var body = document.getElementById('navBody');
  var count = document.getElementById('navCount');
  function esc(v){ return String(v == null ? '' : v).replace(/&/g,'&amp;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
  function toggle(open){
    nav.classList.toggle('open', open);
    mask.classList.toggle('show', open);
  }
  var btn = document.getElementById('navBtn');
  if (btn) btn.onclick = function(){ toggle(!nav.classList.contains('open')); };
  mask.onclick = function(){ toggle(false); };
  var main = document.querySelector('main');
  if (!main){
    body.innerHTML = '<div class="navempty">没有正文内容。</div>';
    return;
  }
  var entries = [];
  Array.from(main.querySelectorAll('h1,h2,h3,h4,h5,h6')).forEach(function(h){
    var txt = (h.textContent || '').trim().replace(/\s+/g, ' ');
    if (!txt) return;
    if (!h.id) h.id = 'sec-' + (entries.length + 1);
    entries.push({el:h, text:txt, level:parseInt(h.tagName.slice(1), 10)});
  });
  // PPT 有些页没有标题，用“第 N 页”作为目录项
  Array.from(main.querySelectorAll('.slide')).forEach(function(s){
    if (s.querySelector('h1,h2,h3,h4,h5,h6')) return;
    var sno = s.querySelector('.sno');
    var txt = sno ? (sno.textContent || '').trim() : '';
    if (!txt) return;
    if (!s.id) s.id = 'slide-' + (entries.length + 1);
    entries.push({el:s, text:txt, level:6});
  });
  if (!entries.length){
    body.innerHTML = '<div class="navempty">这份文档没有识别到标题。' +
      'PDF/图片，或没有使用标题样式的文本，暂不支持文档目录。</div>';
    count.textContent = '';
    return;
  }
  var minLevel = Math.min.apply(null, entries.map(function(e){ return e.level; }));
  var html = '';
  entries.forEach(function(e, i){
    var indent = Math.max(0, Math.min(5, e.level - minLevel));
    e.el.style.scrollMarginTop = '70px';
    html += '<a class="navlink" data-i="' + i + '" style="padding-left:' +
            (8 + indent * 14) + 'px" href="#' + e.el.id + '" title="' +
            esc(e.text) + '">' + esc(e.text.length > 80 ? e.text.slice(0, 80) + '…' : e.text) +
            '</a>';
  });
  body.innerHTML = html;
  count.textContent = '(' + entries.length + ')';
  function active(a){
    body.querySelectorAll('a.navlink').forEach(function(x){
      x.classList.toggle('active', x === a);
    });
  }
  body.querySelectorAll('a.navlink').forEach(function(a){
    a.addEventListener('click', function(ev){
      ev.preventDefault();
      var entry = entries[+a.dataset.i];
      if (!entry) return;
      active(a);
      entry.el.scrollIntoView({behavior:'smooth', block:'start'});
      if (window.innerWidth <= 820) toggle(false);
    });
  });
  if ('IntersectionObserver' in window){
    var obs = new IntersectionObserver(function(items){
      items.forEach(function(it){
        if (!it.isIntersecting) return;
        var i = entries.findIndex(function(e){ return e.el === it.target; });
        if (i >= 0) active(body.querySelector('a.navlink[data-i="' + i + '"]'));
      });
    }, {rootMargin:'-10% 0px -80% 0px', threshold:0});
    entries.forEach(function(e){ obs.observe(e.el); });
  }
})();
</script>
"""
    hl_block = ""
    if hl:
        hl_json = __import__("json").dumps(hl)
        # 表格行(含 ≥2 个 | 分隔) → 按列竖排更可读; 普通段落 → 原文
        cols = [c.strip() for c in hl.split("|") if c.strip()]
        if len(cols) >= 2:
            hl_esc = "<div class='hitext'>" + "".join(
                f"<div class='hcol'><span class='hmark'>▸</span> {_html.escape(c)}</div>" for c in cols[:20]) + "</div>"
        else:
            hl_esc = "<div class='hitext'>" + _html.escape(hl) + "</div>"
        hl_block = f"""
<div class="hitcard" id="hitCard">
  <div class="hitlabel" id="hitLabel">🎯 检索命中片段（正文中高亮）</div>
  {hl_esc}
</div>
<script>
(function(){{
  try {{
    var needle = {hl_json};
    if (needle && needle.length > 3) {{
      // 表格片段里有 |，正文可能被拆成多个单元格；按 | 和句号切成短片段。
      var pieces = needle.split(/[|\\n\\r]+/)
        .concat(needle.split(/[。！？；;]/))
        .map(function(s){{ return s.trim(); }})
        .filter(function(s){{ return s.length >= 4; }});
      pieces.sort(function(a,b){{ return b.length - a.length; }});
      var root = document.querySelector('main') || document.body;
      var blocks = Array.from(root.querySelectorAll(
        'p,li,td,th,h1,h2,h3,h4,h5,h6,.li,.slide'));
      if (!blocks.length) blocks = [root];

      function markIn(block, frag) {{
        var walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
        var nodes = [], text = '', n;
        while ((n = walker.nextNode())) {{
          nodes.push({{node:n, start:text.length}});
          text += n.nodeValue;
        }}
        var idx = text.indexOf(frag);
        if (idx < 0) return null;
        var end = idx + frag.length;
        function locate(pos) {{
          for (var i = 0; i < nodes.length; i++) {{
            var st = nodes[i].start;
            var en = st + nodes[i].node.nodeValue.length;
            if (pos >= st && pos <= en) return {{node:nodes[i].node, off:pos - st}};
          }}
          return null;
        }}
        var a = locate(idx), b = locate(end);
        if (!a || !b) return null;
        try {{
          var range = document.createRange();
          range.setStart(a.node, a.off);
          range.setEnd(b.node, b.off);
          var mark = document.createElement('mark');
          // extractContents 能处理 Word 拆 run / 跨 <b> 的情况；
          // surroundContents 在部分选中非文本节点时会抛异常。
          mark.appendChild(range.extractContents());
          range.insertNode(mark);
          return mark;
        }} catch(e) {{ return null; }}
      }}

      var found = null, marked = 0;
      for (var p = 0; p < pieces.length && marked < 4; p++) {{
        var frag = pieces[p];
        var tries = [frag];
        if (frag.length > 120) tries.push(frag.slice(0, 120));
        if (frag.length > 60) tries.push(frag.slice(0, 60));
        for (var t = 0; t < tries.length && !found; t++) {{
          var nd = tries[t].trim();
          if (nd.length < 4) continue;
          for (var i = 0; i < blocks.length; i++) {{
            var m = markIn(blocks[i], nd);
            if (m) {{ if (!found) found = m; marked++; break; }}
          }}
        }}
      }}
      var lab = document.getElementById('hitLabel');
      if (found) {{
        var host = found.closest('p,li,td,th,h1,h2,h3,h4,h5,h6,.li,.slide') || found;
        setTimeout(function(){{
          host.scrollIntoView({{block:'center', behavior:'smooth'}});
        }}, 250);
      }} else {{
        if (lab) lab.textContent = '🎯 检索命中片段（正文中未精确匹配，以下是原文）';
        var card = document.getElementById('hitCard');
        if (card) setTimeout(function(){{ card.scrollIntoView({{block:'center', behavior:'smooth'}}); }}, 150);
      }}
    }}
  }} catch(e) {{}}
}})();
</script>"""
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>预览 - {_html.escape(name)}</title>
<style>
  body {{ margin:0; font-family:"Segoe UI","Microsoft YaHei",sans-serif; background:#f5f6f8; }}
  .bar {{ position:sticky; top:0; background:#1f2430; color:#fff; padding:10px 18px;
         display:flex; align-items:center; gap:12px; flex-wrap:wrap; z-index:10; }}
  .bar .nm {{ font-weight:600; font-size:14px; word-break:break-all; }}
  .bar .meta {{ color:#9aa7b8; font-size:12px; margin-left:auto; }}
  .btn {{ display:inline-block; padding:6px 14px; border-radius:6px; background:#2563eb; color:#fff;
         text-decoration:none; font-size:13px; }}
  .btn.ghost {{ background:#374151; }}
  main {{ padding:20px; max-width:1100px; margin:0 auto; }}
  .hitcard {{ background:#fff7e6; border:2px solid #f59e0b; border-radius:10px; padding:12px 14px;
             margin-bottom:16px; }}
  .hitlabel {{ font-weight:600; color:#b45309; font-size:13px; margin-bottom:6px; }}
  .hitext {{ font-size:13px; color:#78350f; line-height:1.6; white-space:pre-wrap; word-break:break-word; }}
  .hcol {{ padding:2px 0; word-break:break-all; }}
  .hmark {{ color:#d97706; margin-right:4px; }}
  mark {{ background:#fde047; padding:1px 2px; border-radius:3px; scroll-margin-top:80px; }}
  pre {{ background:#fff; border:1px solid #e3e6ea; border-radius:8px; padding:16px;
        font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word; max-height:78vh; overflow:auto; }}
  table {{ border-collapse:collapse; background:#fff; width:100%; font-size:12px; }}
  th,td {{ border:1px solid #d6dce4; padding:5px 8px; text-align:left; }}
  th {{ background:#f0f4f9; position:sticky; top:52px; }}
  .wrap {{ overflow:auto; max-height:74vh; border:1px solid #e3e6ea; border-radius:8px; }}
  img {{ max-width:100%; border:1px solid #e3e6ea; border-radius:8px; }}
  iframe {{ width:100%; height:78vh; border:1px solid #e3e6ea; border-radius:8px; background:#fff; }}
  .err {{ color:#b91c1c; background:#fee2e2; padding:14px; border-radius:8px; }}
  /* 结构化预览(按原文档排版渲染) */
  .dp {{ background:#fff; border:1px solid #e3e6ea; border-radius:8px; padding:24px 28px;
        font-size:14px; line-height:1.8; color:#111827; overflow:auto; max-height:78vh; }}
  .dp p {{ margin:9px 0; word-break:break-word; }}
  .dp h2 {{ font-size:19px; margin:20px 0 9px; }}
  .dp h3 {{ font-size:16.5px; margin:18px 0 8px; }}
  .dp h4,.dp h5,.dp h6 {{ font-size:15px; margin:14px 0 6px; }}
  .dp .li {{ margin:3px 0; }}
  .dp img {{ display:block; max-width:100%; max-height:560px; height:auto; margin:12px 0;
            border:1px solid #e3e6ea; border-radius:6px; background:#fff; cursor:zoom-in; }}
  .dp .tw {{ overflow:auto; margin:12px 0; }}
  .dp table {{ border-collapse:collapse; width:auto; min-width:50%; font-size:12.5px; }}
  .dp th,.dp td {{ border:1px solid #d6dce4; padding:6px 10px; vertical-align:top;
                  text-align:left; background:#fff; }}
  .dp th {{ background:#f0f4f9; font-weight:600; white-space:nowrap; }}
  .dp .slide {{ border-top:1px solid #e5e7eb; padding:14px 0 4px; margin-top:8px; }}
  .dp .slide:first-child {{ border-top:0; margin-top:0; }}
  .dp .sno {{ font-size:11px; color:#9ca3af; letter-spacing:.5px; }}
  {nav_css}
</style></head><body>
<div class="bar">
  <button id="navBtn" title="打开文件目录">☰ 目录</button>
  <span>📄</span><span class="nm">{_html.escape(name)}</span>
  <span class="meta">{_html.escape(badge)}{(" · " + size_txt) if size_txt else ""}</span>
  <a class="btn ghost" href="{raw}" target="_blank" rel="noopener">🗎 原始文件</a>
  <a class="btn" href="{dwl}" download>⬇ 下载原文件</a>
</div>
<div class="shell">
  <aside id="nav">
    <div class="navhead">📄 文档目录 <span id="navCount" style="color:#94a3b8;font-weight:400"></span></div>
    <div id="navBody" class="navempty">正在生成目录…</div>
  </aside>
  <main>{hl_block}{content_html}</main>
</div>
<div id="navMask"></div>
<div id="lb"><span id="lbx" title="关闭（Esc 或点空白处）">✕</span><img id="lbimg" alt=""></div>
<style>
  #lb {{ display:none; position:fixed; inset:0; background:rgba(15,23,42,.92); z-index:999;
        align-items:center; justify-content:center; cursor:zoom-out; }}
  #lb.on {{ display:flex; }}
  #lbimg {{ max-width:96vw; max-height:90vh; border:0; border-radius:4px; cursor:default; }}
  #lbx {{ position:fixed; top:10px; right:20px; color:#fff; font-size:30px; line-height:1;
         cursor:pointer; opacity:.85; user-select:none; }}
  #lbx:hover {{ opacity:1; }}
</style>
<script>
(function(){{
  var lb = document.getElementById('lb'), img = document.getElementById('lbimg');
  function close(){{ lb.classList.remove('on'); img.removeAttribute('src'); }}
  function open(src){{ img.src = src; lb.classList.add('on'); }}
  document.addEventListener('click', function(e){{
    var t = e.target;
    if (t && t.tagName === 'IMG' && t.id !== 'lbimg') {{ open(t.src); }}
  }});
  lb.addEventListener('click', function(e){{ if (e.target.id !== 'lbimg') close(); }});
  document.getElementById('lbx').addEventListener('click', close);
  document.addEventListener('keydown', function(e){{ if (e.key === 'Escape') close(); }});
}})();
</script>
{nav_script}
</body></html>"""


def _extract_preview(fp, kb_id="", path=""):
    """抽取文件预览内容。返回 (kind, content_html)。"""
    import index_docs
    ext = fp.suffix.lower()
    name = fp.name
    # Office 走结构化渲染：保留标题层级、表格、段落，并把文档里的图就地放进来。
    # 失败才回退到"纯文本塞 <pre>"，至少不会白屏。
    if ext in (".docx", ".docm", ".dotx", ".pptx", ".pptm", ".potx", ".xlsx", ".xlsm", ".xltx"):
        try:
            import docpreview
            return docpreview.render(fp, kb_id, path or name)
        except Exception as e:
            print(f"[preview] 结构化渲染失败, 回退纯文本: {e}", file=sys.stderr)
    if ext in _TEXT_EXT:
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")[:100_000]
        except Exception:
            raw = fp.read_bytes().decode("utf-8", "replace")[:100_000]
        return "text", f"<pre>{_html.escape(raw)}</pre>"
    if ext in _IMG_EXT:
        data_url = f"data:image/{ext[1:].replace('jpg','jpeg')};base64," + \
                   __import__("base64").b64encode(fp.read_bytes()[:2 * 1024 * 1024]).decode()
        return "image", f"<img src='{data_url}' alt='{_html.escape(name)}'>"
    if ext in _MEDIA_EXT:
        return "media", f"<p>该文件不便于文本预览，请使用右上角「原始文件」或「下载」。</p>"
    if ext in index_docs.EXTRACTORS:
        try:
            txt = index_docs.EXTRACTORS[ext](fp)
        except Exception as e:
            return "error", f"<div class='err'>解析失败: {_html.escape(str(e))}</div>"
        if ext in (".xlsx", ".xls"):
            rows = [ln.split("|") for ln in txt.split("\n") if ln.strip()]
            head = "<table><tr>" + "".join(f"<th>{_html.escape(c[:60])}</th>" for c in (rows[0] if rows else [])) + "</tr>"
            body = "".join("<tr>" + "".join(f"<td>{_html.escape(c[:120])}</td>" for c in row) + "</tr>"
                           for row in rows[1:100])  # 最多 100 行
            return "office", f"<div class='wrap'>{head}{body}</table></div>"
        return "office", f"<pre>{_html.escape(txt[:100_000])}</pre>"
    return "text", f"<pre>{_html.escape(fp.read_bytes()[:50_000].decode('utf-8','replace'))}</pre>"


def _resolve_or_404(kb_id, path):
    if not path:
        return None, JSONResponse({"ok": False, "msg": "缺少 path 参数"}, status_code=400)
    fp = indexer.resolve_kb_file(kb_id, path)
    if fp is None:
        return None, JSONResponse({"ok": False, "msg": "文件不存在或不可访问"}, status_code=404)
    return fp, None


@app.get("/api/kb/{kb_id}/file")
def kb_file(kb_id: str, path: str = ""):
    """兼容旧链接: 跳转到预览页。"""
    return _preview_redirect(kb_id, path)


@app.get("/api/kb/{kb_id}/file/preview")
def kb_file_preview(kb_id: str, path: str = "", hl: str = ""):
    """网页预览原文件(不下载)。顶栏带「下载原文件」与「原始文件」;
    传 hl(=检索命中的片段文本)时, 页面高亮该片段并滚动定位到对应段落。"""
    fp, err = _resolve_or_404(kb_id, path)
    if err:
        return err
    try:
        if fp.suffix.lower() == ".pdf":
            # PDF 内嵌渲染: 通过 raw 接口
            q = urllib.parse.quote(path)
            inner = f"<iframe src='/api/kb/{urllib.parse.quote(kb_id)}/file/raw?path={q}'></iframe>"
            html_ = _preview_html(kb_id, path, fp.name, inner, "media", fp.stat().st_size, hl=hl)
            return Response(html_, media_type="text/html; charset=utf-8")
        kind, content_html = _extract_preview(fp, kb_id, path)
        html_ = _preview_html(kb_id, path, fp.name, content_html, kind, fp.stat().st_size, hl=hl)
        return Response(html_, media_type="text/html; charset=utf-8")
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"预览失败: {e}"}, status_code=500)


def _preview_redirect(kb_id, path):
    if not path:
        return JSONResponse({"ok": False, "msg": "缺少 path 参数"}, status_code=400)
    fp = indexer.resolve_kb_file(kb_id, path)
    if fp is None:
        return JSONResponse({"ok": False, "msg": "文件不存在或不可访问"}, status_code=404)
    return FileResponse(str(fp), headers={"Content-Disposition": "inline"})


@app.get("/api/kb/{kb_id}/file/raw")
def kb_file_raw(kb_id: str, path: str = ""):
    """原始字节(内嵌于 iframe/img 预览)。"""
    fp, err = _resolve_or_404(kb_id, path)
    if err:
        return err
    ext = fp.suffix.lower()
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
             ".bmp": "image/bmp", ".pdf": "application/pdf", ".mp4": "video/mp4",
             ".mp3": "audio/mpeg", ".wav": "audio/wav"}.get(ext)
    return FileResponse(str(fp), media_type=media or "application/octet-stream")


@app.get("/api/kb/{kb_id}/file/download")
def kb_file_download(kb_id: str, path: str = ""):
    """下载原文件(附件)。"""
    fp, err = _resolve_or_404(kb_id, path)
    if err:
        return err
    return FileResponse(str(fp), filename=fp.name,
                        media_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(fp.name)}"})


@app.get("/api/kb/{kb_id}/debug_search")
def kb_debug_search(kb_id: str, q: str = "", top_k: int = 8):
    """调试/排查用: 纯检索,不调大模型,返回 top 命中与归一化信息。"""
    if not q:
        return {"ok": False, "msg": "参数 q 不能为空"}
    results = kb.search(kb_id, q, topk=min(int(top_k), 20))
    if results:
        rs = [{"score": r["score"], "path": r["path"], "text": r["text"][:120]} for r in results]
    else:
        rs = []
    return {"ok": True, "sources": rs,
            "raw_tokens": kb.tokenize(q),
            "config": kb._rc()}


@app.post("/api/kb/{kb_id}/ask")
def kb_ask(kb_id: str, body: AskIn, request: Request):
    _u, err = _require_kb_perm(request)
    if err:
        return err
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    q = _strip_cmd((body.question or "").strip())
    has_img = bool(body.images)
    # 只贴图不写字：给一句默认问题（与非流式口径一致）
    if has_img and not q:
        q = "请看看这几张图，说明这是什么情况、该怎么处理"
    if not q:
        return {"ok": False, "msg": "问题不能为空"}
    # 寒暄 / 泛问: 不检索不调模型, 直接回(与流式口径一致)。带图则跳过这两条短路
    if _is_chitchat(q) and not has_img:
        return {"ok": True, "answer": _chitchat_reply(q),
                "sources": [], "source_type": "kb", "chitchat": True}
    if _is_generic_ask(q) and not has_img:
        kbn = (kb_ or {}).get("name") or "这个知识库"
        return {"ok": True, "clarify": True, "sources": [], "source_type": "kb",
                "answer": (f"「{q}」问的范围有点大——{kbn} 里不同系统、不同环境各有各的账号密码。\n\n"
                           "你要问的是哪一个？可以说得具体些，例如：\n"
                           "· 哪个系统（demo / O32 / oorder / UFT …）\n"
                           "· 哪个环境（测试 / UAT / 生产）\n"
                           "· 要账号、密码，还是数据库连接信息")}

    _t0 = time.time()
    # 带上当前用户自己的 key：planner（改写/拆解）和 fallback 都只吃 cfg，
    # 用同一把 key 才不会出现"主问答用自己的 key、拆解仍挤公共 key"的割裂。
    cfg = _apply_thinking_choice(_cfg_with_user_key(request),
                                 getattr(body, "thinking", None))

    # 用户主动选了「联网搜索 / 大模型本地回答」：不检索知识库、不走缓存
    fb_mode = (body.fallback_mode or "").strip().lower()
    if fb_mode in ("web", "llm"):
        if not llm.llm_enabled(cfg=cfg):
            return {"ok": True, "sources": [], "source_type": "none",
                    "answer": "未配置大模型，暂时不能用「联网搜索回答 / 大模型本地回答」。"}
        q_eff, src_type, user_msg, system, refs, note = _forced_fallback_prepare(
            q, body.history or [], fb_mode, cfg)
        answer = llm.chat([{"role": "user", "content": user_msg}], cfg=cfg,
                          system=system, user_key=_user_llm_key(request)) or ""
        if not answer.strip():
            tip = "回答失败：联网搜索或大模型暂时不可用，请稍后重试。"
            _log_ask(q, kb_id, request, tip, [], "none", True, _t0, "", False, cfg)
            return {"ok": True, "answer": tip, "sources": [], "source_type": "none"}
        full = (note + answer) if note else answer
        label = _forced_fallback_label(src_type, fb_mode, note)
        _log_ask(q, kb_id, request, full, [], src_type, True, _t0,
                 q_eff if q_eff != q else "", False, cfg)
        return {"ok": True, "answer": full, "sources": [], "source_type": src_type,
                "refs": refs, "source_label": label}

    rc = cfg.get("retrieval", {})
    top_k = body.top_k or rc.get("top_k", 8)
    # 多轮: 依赖上文的追问先改写成独立问题(改写后的句子用于检索与回答)
    hist = body.history or []
    rw = planner.rewrite_query(q, hist, cfg=cfg)
    q_eff = rw["q"] if rw["rewritten"] else q
    mods = module_router.detect_modules(q_eff) or module_router.detect_modules(q)
    # 查询规划: 模糊/复合问题 → 拆成多个子查询, 多路检索后合并去重(原问题保底一路)
    plan_ = planner.plan_queries(q_eff, cfg=cfg)
    queries = plan_["queries"]
    if len(queries) > 1:
        results = _multi_search(kb_id, queries, top_k, rc, modules=mods)
    else:
        results = backend.retrieve(
            kb_id, q_eff, top_k=top_k,
            enable_vector=rc.get("enable_vector", True),
            enable_rerank=rc.get("enable_rerank", True),
        )
        if results is not None and mods:
            results, _drop = module_router.filter_records(results, mods)
            if _drop:
                print(f"[module] {kb_id} 过滤其他模块片段 {_drop} 个（{'、'.join(mods)}）",
                      file=sys.stderr)
            _mh = _manual_reserved(kb_id, q_eff, mods, top_k, rc, results)
            if _mh:
                results = _merge_manual(results, _mh, top_k)
    if results is None:
        return {"ok": True, "answer": "该知识库还没有索引,请先点\"重建索引\"。", "sources": []}
    if mods and not results:
        msg = module_router.miss_message(mods)
        _log_ask(q, kb_id, request, msg, [], "none", True, _t0, q_eff, False, cfg)
        return {"ok": True, "answer": msg, "sources": [], "source_type": "none",
                "source_label": "模块未命中", "modules": mods}

    # 知识库未命中 → 兜底(联网搜索 / 大模型自身知识), 并标注来源
    if not results or (fallback.should_fallback(results) and not _has_confident_hit(results)):
        if not llm.llm_enabled(cfg=cfg):
            return {"ok": True, "answer": "未检索到相关内容,换个问法试试。", "sources": [], "source_type": "none"}
        fb_mode = (body.fallback_mode or "").strip().lower()
        # 默认先问用户走哪条兜底路, 不自动联网、不自动拿模型知识顶上
        if fb_mode not in ("web", "llm", "none") and fallback.ask_first(cfg):
            return {"ok": False, "need_choice": True, "sources": [],
                    "msg": f"知识库里没有找到能回答「{q}」的内容。要继续的话得换个来源，你选一个：",
                    "options": fallback.offer_options(cfg) + [{"id": "none", "label": "不用了"}]}
        if fb_mode == "none":
            return {"ok": True, "answer": "没找到。知识库里确实没有相关内容，可以换个问法，或者把资料补进来。",
                    "sources": [], "source_type": "none"}
        fb = fallback.answer_fallback(q_eff, mode=fb_mode or "auto")
        if fb.get("ok") and fb.get("answer"):
            _log_ask(q, kb_id, request, fb["answer"], [], fb["source_type"], True, _t0, "", False, cfg)
            return {"ok": True, "answer": fb["answer"], "sources": [],
                    "source_type": fb["source_type"], "refs": fb.get("refs", []),
                    "msg": "知识库未命中,以下回答来自" + ("网络搜索" if fb["source_type"] == "web" else "大模型知识")}
        _log_ask(q, kb_id, request, "知识库中未检索到相关内容", [], "none", True, _t0, "", False, cfg)
        return {"ok": True, "answer": "知识库中未检索到相关内容,且大模型兜底失败。", "sources": [], "source_type": "none"}


    # 按分数分布决定给几条（2026-11-26 需求）：答案明确就少给，库里没有就说没找到
    results, _trim_info = _trim_by_scores(results, cfg)
    print(f'[trim] 保留 {len(results)} 条 {_trim_info}', file=sys.stderr)
    if not results:
        # 防御：去重后理论上不会全空，但若上游后端返回空列表，
        # 这里给出明确答复比让提示词拼出空上下文好。
        # 注意这是非流式链路(kb_ask)，直接 return JSON，不能 yield SSE。
        _msg = ('知识库里没有找到与这个问题相关的内容。'
                '可以换个问法，或者把相关资料补进来再问。')
        _log_ask(q, kb_id, request, _msg, [], 'none', True, _t0, '', False, cfg)
        return {"ok": True, "answer": _msg, "sources": [], "source_type": "none",
                "source_label": "未找到相关内容", "not_found": True}
    sources = _reorder_for_listing(q, _build_sources(results, kb_id=kb_id))
    sources = _append_diagram_sources(sources, kb_id, q_eff or q)
    lcfg = llm.llm_settings(cfg)

    if not llm.llm_enabled(cfg=lcfg):
        # 无大模型配置:给出检索命中的原文片段
        lines = ["# 未配置大模型,以下为检索命中片段\n"]
        for i, s in enumerate(sources, 1):
            lines.append(f"{i}. **{s['path']}** (得分 {s['score']})\n   {s.get('text_full') or s['text']}")
        return {"ok": True, "answer": "\n\n".join(lines), "sources": sources, "llm": False}

    # 组提示词(用 _build_ask_context: 完整文本 + 标识信息不得截断的约束 + 多轮历史)
    system, msgs = _build_ask_context(sources, q_eff, hist, body.answer_len, body.images, modules=mods)
    _meta = {}
    _ukey = _user_llm_key(request)
    answer = llm.chat(msgs, cfg=cfg, system=system, meta=_meta, user_key=_ukey) or ""
    # 非流式同样自动续写，避免"答到一半就断"
    for _round in range(_MAX_CONTINUE):
        if _meta.get("stop_reason") != "max_tokens" or not answer.strip():
            break
        _meta = {}
        _more = llm.chat(
            list(msgs) + [{"role": "assistant", "content": answer},
                          {"role": "user", "content": _CONTINUE_HINT}],
            cfg=cfg, system=system, meta=_meta, user_key=_ukey) or ""
        if not _more.strip():
            break
        answer = answer.rstrip() + "\n\n" + _more.lstrip()
    answer = answer or None
    if not answer:
        # 不要把 8 个片段全文糊到用户脸上（几千字，读不下去也没重点）。
        # 这里能走到，说明 API Key 是配好的（上游已校验），失败就是网关抖动/过载。
        answer = ("⚠️ 大模型调用失败：已自动重试 3 次仍未拿到结果（网关抖动或过载）。\n\n"
                  "**问题本身是检索到了的** —— 答案就在下方引用里，点开可以直接看原文。\n"
                  "也可以点「↻ 重试」再来一次，这类失败通常是偶发的。")
        _log_ask(q, kb_id, request, "大模型调用失败(非流式,已重试3次)", sources,
                 "error", True, _t0, "", False, cfg)
        return {"ok": True, "answer": answer, "sources": sources, "llm": False,
                "source_type": "kb", "source_label": "知识库（大模型暂时不可用）"}

    # 知识库命中但大模型判定"未找到" → 默认先问用户, 不自动联网/不自动编
    if _looks_like_not_found(answer):
        fb_mode = (body.fallback_mode or "").strip().lower()
        if fb_mode not in ("web", "llm", "none") and fallback.ask_first(cfg):
            return {"ok": False, "need_choice": True, "sources": sources,
                    "answer": answer,
                    "msg": f"知识库里没有找到能回答「{q}」的内容。要继续的话得换个来源，你选一个：",
                    "options": fallback.offer_options(cfg) + [{"id": "none", "label": "不用了"}]}
        if fb_mode == "none":
            return {"ok": True, "answer": "没找到。知识库里确实没有相关内容，可以换个问法，或者把资料补进来。",
                    "sources": [], "source_type": "none"}
        fb = fallback.answer_fallback(q_eff, mode=fb_mode or "auto")
        if fb.get("ok") and fb.get("answer"):
            return {"ok": True, "answer": fb["answer"], "sources": [],
                    "source_type": fb["source_type"], "refs": fb.get("refs", []),
                    "msg": "知识库未命中,以下回答来自" + ("网络搜索" if fb["source_type"] == "web" else "大模型知识")}
        return {"ok": True, "answer": "知识库中未找到该内容，尝试用大模型补充回答时调用失败（大模型网关暂时不可用，可稍后重试）。\n\n" + answer,
                "sources": sources, "source_type": "kb", "source_label": "知识库",
                "msg": "知识库未命中,且大模型兜底暂时失败"}

    warn = module_router.audit_citations(answer, sources, mods)
    if warn:
        answer += warn
    qa_id = _log_ask(q, kb_id, request, answer, sources, "kb", False,
                     _t0, q_eff if rw["rewritten"] else "", plan_["split"], cfg)
    return {"ok": True, "answer": answer, "sources": sources, "llm": True, "qa_id": qa_id,
            "model": lcfg.get("model", ""), "source_type": "kb", "source_label": "知识库",
            "modules": mods,
            "planning": {"split": plan_["split"], "queries": queries,
                         "reason": plan_["reason"]},
            "rewrite": {"q": q_eff, "rewritten": rw["rewritten"], "reason": rw["reason"]}}


def _log_ask(q, kb_id, request, answer, sources, source_type, fallback,
             t_start, rewritten="", split=False, cfg=None):
    """统一记录一条问答(失败不影响主流程)。"""
    try:
        u = _current_user(request)
        model = ""
        try:
            model = (llm.llm_settings(cfg) or {}).get("model", "")
        except Exception:
            pass
        return qa_log.record(q, kb_id, u, answer, sources or [], source_type,
                             bool(fallback), int((time.time() - t_start) * 1000),
                             rewritten, split, model)
    except Exception as e:
        print(f"[qa_log] {e}", file=sys.stderr)
        return ""


def _forced_fallback_prepare(q, hist, mode, cfg):
    """用户主动选了「不查知识库」的来源：准备联网/模型直答的上下文。

    返回 (q_eff, src_type, user_msg, system, refs, note)。
    note 是要在正文前明确告诉用户的降级说明（例如联网未启用/没搜到）。
    """
    q_eff = q
    if hist:
        try:
            rw = planner.rewrite_query(q, hist, cfg=cfg)
            if rw.get("rewritten") and rw.get("q"):
                q_eff = rw["q"]
        except Exception:
            pass
    note = ""
    use = mode
    if mode == "web":
        try:
            web_enabled = bool(fallback.load_fb_cfg().get("web_enabled"))
        except Exception:
            web_enabled = False
        if not web_enabled:
            use = "llm"
            note = "（联网搜索未启用，本次改用大模型自身知识回答。）\n\n"
    src_type, user_msg, system, refs = fallback.prepare_fallback(q_eff, mode=use)
    if mode == "web" and src_type != "web" and not note:
        note = "（本次没有取到联网搜索结果，以下由大模型凭自身知识回答。）\n\n"
    return q_eff, src_type, user_msg, system, refs or [], note


def _forced_fallback_label(src_type, mode, note):
    if src_type == "web":
        return "联网搜索（未查知识库）"
    if mode == "web" and note:
        if "未启用" in note:
            return "大模型知识（联网未启用）"
        return "大模型知识（联网未取到结果）"
    return "大模型知识（未查知识库）"


_ASK_SYSTEM = (
    "你是一个严谨的文档问答助手。仅依据下面提供的文档片段回答用户问题。"
    "若片段不包含答案,请明确说明\"文档中未找到相关信息\",不要编造。"
    "回答时尽量引用片段中的原文依据,用中文。\n"
    "【引用写法】需要在句末标依据时,只写方括号片段号:单个写 [1],多个写 [1,3,5]。"
    "**不要**写成\"(片段1、片段3)\"、\"【片段1】\"或\"来源:xxx.docx\"这类长串;"
    "标注放在句末、紧跟句号前,同一段落不要反复标注,没依据的句子不要标。\n"
    "【重要1】涉及程序包名、文件名、版本号、路径、IP、命令、代码、配置项、金额等标识信息时,"
    "必须**原样完整引用**,一个字都不能省略、不得用省略号代替、不要自行简写或改写。\n"
    "【重要2】清单类问题(如'要准备哪些包/有哪些节点/包含哪些文件/清单')必须**穷尽所有片段的信息**"
    "并合并成一份完整清单,逐条列出并标注来自哪个片段;不得只看其中一个片段就作答,"
    "不同片段列出的内容要合并而非取舍。\n"
    "【重要3】本系统是**公司内部**的实施知识库,使用者与数据都在内网,不存在对外泄露风险。"
    "片段中出现的 IP、端口、账号、密码、密钥、连接串、数据库地址等,都属于正常的实施交付信息。"
    "用户问到时**必须直接原样给出**,不要以'敏感信息''不便提供''请走审批流程''请联系管理员'"
    "之类的理由回避、拒绝或附加免责声明。片段里写着什么就照实答什么。"
)

# ---------------- 闲聊短路 ----------------
# 「你好」「谢谢」这类话跑完整条检索链路要十几秒，还会把知识库里碰巧含这些字
# 的片段搜出来（历史上「你好」答出过一段意图识别记录），属于纯负收益。
_CHITCHAT = {
    "你好", "您好", "hi", "hello", "在吗", "在么", "有人吗",
    "谢谢", "谢谢你", "多谢", "感谢", "thanks", "thank you",
    "好的", "好", "ok", "okay", "嗯", "嗯嗯", "收到", "明白了", "知道了",
    "再见", "拜拜", "bye",
}
_CHITCHAT_RE = re.compile(
    r"^(你好|您好|hi|hello|在吗|在么|有人吗|谢谢|谢谢你|多谢|感谢|thanks|好的|ok|嗯+|收到|再见)[\s。！!~～,.，？?]*$",
    re.I)


def _is_chitchat(q):
    """纯寒暄/应答, 没有任何实际提问内容。"""
    s = (q or "").strip().lower()
    if not s or len(s) > 12:
        return False
    return s in _CHITCHAT or bool(_CHITCHAT_RE.match(s))


def _chitchat_reply(q):
    """寒暄/应答给一句像人说的话，不要所有情况都回「你好」。"""
    s = (q or "").strip().lower()
    if any(k in s for k in ("谢谢", "多谢", "感谢", "thanks", "thank")):
        return "不客气。还有别的想查的随时问。"
    if any(k in s for k in ("再见", "拜拜", "bye")):
        return "好，回头见。"
    if s in ("好的", "好", "ok", "okay", "收到", "明白了", "知道了", "嗯", "嗯嗯"):
        return "好的。还有别的问题吗？"
    return "你好！我是这个知识库的问答助手，你想了解什么？"


# ---------------- 泛问先反问 ----------------
# 「密码」「账号」「IP」两个字就问，检索一定会给出一堆片段，模型照着答就会把
# 各系统各环境的账号一次性铺开。与其答错，不如先问清楚问的是哪个系统的哪个环境。
_GENERIC_ASK = {
    "密码", "口令", "账号", "帐号", "用户名", "账户", "帐号密码", "账号密码", "密码是什么",
    "密码是多少", "账号是什么", "账号是多少", "ip", "地址", "端口", "数据库", "数据库地址",
    "数据库账号", "数据库密码", "连接串", "账号密码是什么", "配置", "参数", "环境信息",
}
_GENERIC_SUFFIX = ("是什么", "是多少", "是啥", "多少", "发我", "给我", "告诉me")


def _is_generic_ask(q):
    """只有「密码」「账号」这种孤立名词, 没说是哪个系统/哪个环境。"""
    s = re.sub(r"[\s?？!！。.、,，~～]", "", (q or "").lower())
    if not s or len(s) > 10:
        return False
    if s in _GENERIC_ASK:
        return True
    for suf in _GENERIC_SUFFIX:
        if s.endswith(suf) and s[: -len(suf)] in _GENERIC_ASK:
            return True
    return False


# ---------------- 答案长度 ----------------
_LEN_HINT = {
    "brief": ("\n【回答长度·必须遵守】用户选了**简答**模式，你要用最少的字给出结论："
              "**总共不超过 3 行、100 字**，只回答被问的那一点。"
              "严禁分小节、严禁用「一、二、三」或「1. 2. 3.」分点罗列、严禁逐层展开、"
              "严禁把片段里的信息全部搬过来。宁可少说，也不要多说。"),
    "normal": "",
    "detail": ("\n【回答长度】用户要的是**详细**：把片段里的相关信息尽量讲全，"
               "分小节、列清单、写清步骤和注意事项，必要的背景和边界条件也带上。"),
}


def _norm_len(v):
    v = (v or "").strip().lower()
    return v if v in _LEN_HINT else "normal"


# 用户贴图时追加到 system 的说明。要点：
# 1) 图是用户现场截图，往往就是问题的核心（报错、配置、界面），不能被文档片段盖过；
# 2) 但不能因此无视文档，文档给的是标准做法，两者要互补；
# 3) 别把图里读不清的东西编出来。
_IMG_HINT = ("\n【关于附图】用户这次贴了图片，通常是现场截图（报错、配置、界面）。"
             "规则：① 先看懂图，再回答；图是判断问题的主要依据。"
             "② 文档片段用来补充标准做法/参数/步骤，不要用文档覆盖用户图里的事实。"
             "③ 图里看不清、认不出的内容要直说看不清，不要猜着编。"
             "④ 如果图里出现账号密码、IP、密钥等敏感信息，按原文给出，不要改写或打码。")


# 清单类问题的识别词 + 具体包名特征(用于把含包清单的片段提前, 避免长上下文里被忽略)
_LIST_Q = ("哪些包", "什么包", "哪些文件", "清单", "有哪些", "包含哪些", "要准备", "需要准备",
           "哪些组件", "哪些节点", "哪些服务", "包列表")
_PKG_MARKS = (".zip", ".jar", ".tar", ".gz", ".war", ".rpm", ".bin", ".iso", ".7z")


def _reorder_for_listing(q, sources):
    """清单类问题: 把含具体包名/文件名的片段提前。

    长上下文里靠后的片段容易被大模型忽略("lost in the middle"), 而清单往往藏在
    配置表/参数表类文档里。此处按"含包名次数"稳定排序, 让它们进入模型重点视野。
    """
    if not any(k in q for k in _LIST_Q):
        return sources
    def _n(s):
        t = (s.get("text_full") or s.get("text") or "").lower()
        return -sum(t.count(x) for x in _PKG_MARKS)
    return sorted(sources, key=_n)


# 单次输出被 max_tokens 截断时，最多自动续写几轮。
# 2 轮 = 最多约 3 倍单次额度；再多就说明问题太宽，该拆了。
_MAX_CONTINUE = 2

_CONTINUE_HINT = ("接着上面最后一句继续写，写完为止。"
                  "不要重复已写过的内容，不要重新开头，不要加总结性开场白。")


_USER_IMG_MAX_BYTES = 12 * 1024 * 1024     # 用户随问题贴的图，总量上限（前端已压缩，这里是硬兜底）
_IMG_MIME_OK = ("image/png", "image/jpeg", "image/gif", "image/webp")


def _user_image_blocks(images):
    """把前端传来的 dataURL 图片转成 Anthropic 原生 image 块。

    - 只接受 dataURL(data:image/xxx;base64,...)，其它形式直接忽略，防止被塞任意 URL。
    - 单张体积不在此处限制（前端已压缩），但要挡住明显异常的请求体。
    - 解析失败的图跳过，不影响正常提问。
    """
    blocks, used = [], 0
    for im in (images or []):
        try:
            data = (im or {}).get("data") or ""
            if not data.startswith("data:image/"):
                continue
            head, _, b64 = data.partition(",")
            if not b64:
                continue
            mime = head[5:].split(";")[0].lower()
            if mime not in _IMG_MIME_OK:
                # 统一按 png 交给模型（Anthropic 也认 jpeg/webp/gif，未知类型就别猜了）
                continue
            # base64 长度 * 0.75 ≈ 原始字节
            n = int(len(b64) * 0.75)
            if used + n > _USER_IMG_MAX_BYTES:
                print(f"[img] 用户附图超过 {_USER_IMG_MAX_BYTES//1024//1024}MB，超出部分已丢弃")
                break
            used += n
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": mime, "data": b64}})
        except Exception:
            continue
    return blocks


def _build_ask_context(sources, q, history=None, answer_len="normal", images=None, modules=None):
    """组装 messages: [最近几轮对话...] + 当前问题(带检索片段 + 用户贴的图)。

    历史只保留最近 3 轮、每条回答截到 800 字, 防止上下文过长拖慢生成;
    历史里的回答可能引用过别的片段, 因此提示词要求以**本次片段**为准。

    用户贴的图**只放在当前这一轮**，历史轮次只带文字（人明确要求）。
    """
    ctx = []
    for i, s in enumerate(sources, 1):
        # 用完整文本(而非 200 字短版)喂模型, 保证包名/清单不被截断
        ctx.append(f"[片段{i}] 来源:{s['path']}\n{s.get('text_full') or s['text']}")
    context = "\n\n".join(ctx[:8])
    user_msg = f"【文档片段】\n{context}\n\n【问题】{q}\n\n请基于以上片段回答。"

    msgs = []
    for h in (history or [])[-3:]:
        hq = (h.get("q") or "").strip()
        ha = (h.get("a") or "").strip()
        if not hq:
            continue
        msgs.append({"role": "user", "content": hq})
        if ha:
            msgs.append({"role": "assistant", "content": ha[:800]})
    img_blocks = _user_image_blocks(images)
    if img_blocks:
        n = len(img_blocks)
        user_msg += (f"\n\n【用户附图】用户还贴了 {n} 张图片，请优先看图回答他的问题："
                     f"图里的信息以图为准，上面检索到的文档片段作为补充；"
                     f"如果图和文档说的不一致，请明确指出冲突在哪里。")
        # 图片块放在文字之前：Anthropic 建议长上下文在后，短指令靠近生成位置更稳
        msgs.append({"role": "user", "content": img_blocks + [{"type": "text", "text": user_msg}]})
        sys_prompt = _ASK_SYSTEM + _LEN_HINT[_norm_len(answer_len)] + _IMG_HINT
    else:
        msgs.append({"role": "user", "content": user_msg})
        sys_prompt = _ASK_SYSTEM + _LEN_HINT[_norm_len(answer_len)]
    if modules:
        sys_prompt += (
            "\n【业务模块约束】本次问题限定的业务模块：" + "、".join(modules) + "。"
            "只使用明确提到该模块的片段；不要把其他模块的步骤拼进来；"
            "如果片段来自多个模块，先说明歧义并询问用户；"
            "没有对应片段时回答未找到，不要用相邻模块补全。")
    return sys_prompt, msgs


async def _queue_stream(ticket, kb_id, body, request, kb_, _json):
    """排队 SSE：先同步位置/预计等待，轮到后接着跑正常问答流。"""
    import asyncio

    def sse(tag, payload):
        return f"data: {_json.dumps({'type': tag, **payload}, ensure_ascii=False)}\n\n"

    t0 = time.time()
    release = None
    try:
        while True:
            pos = _queue_position(ticket)
            if not pos:
                msg = "排队已结束，请重新提问。"
                yield sse("answer", {"text": msg})
                yield sse("done", {"full": msg, "source_type": "none",
                                   "source_label": "排队结束"})
                return
            eta = _queue_eta(pos)
            ahead = max(0, pos - 1)
            status = (f"排队第 1 位，正在等当前回答结束，预计约 {eta} 秒" if ahead == 0
                      else f"排队中：前面还有 {ahead} 人，预计约 {eta} 秒")
            yield sse("queue", {
                "position": pos, "ahead": ahead, "eta": eta,
                "active": _ask_global_active,
                "global_max": _ASK_LIMIT_CFG["global_max"],
                "queue_len": len(_ask_queue),
                "status": status,
            })
            try:
                if await request.is_disconnected():
                    return
            except Exception:
                pass
            if time.time() - t0 > float(_ASK_LIMIT_CFG.get("queue_timeout") or 600):
                _queue_remove(ticket)
                msg = (f"排队等待超过 {_ASK_LIMIT_CFG.get('queue_timeout', 600)} 秒，"
                       "请稍后再试，或让大家错开提问。")
                yield sse("answer", {"text": msg})
                yield sse("done", {"full": msg, "source_type": "none",
                                   "source_label": "排队超时"})
                return
            release = _queue_claim(ticket)
            if release is not None:
                try:
                    resp = await _run_ask_stream(kb_id, body, request, kb_, _json,
                                                 on_finish=release)
                    async for chunk in resp.body_iterator:
                        yield chunk
                finally:
                    try:
                        release()
                    except Exception:
                        pass
                return
            await asyncio.sleep(2)
    finally:
        _queue_remove(ticket)
        if release is not None:
            try:
                release()
            except Exception:
                pass


@app.post("/api/kb/{kb_id}/ask/stream")
async def kb_ask_stream(kb_id: str, body: AskIn, request: Request):
    """流式问答。

    这个端点必须是 async：限流名额用的是 asyncio.Semaphore，同步端点里没有运行中的
    事件循环，拿不到名额也就没法限流。端点本身只做"准入判断"，真正的问答逻辑仍然
    放到线程池里跑（原来的 gen() 是同步生成器，一次占一个线程到回答结束）。
    阶段 2 会把 gen() 本身改成异步，届时这里就不用再进线程池了。
    """
    _u, err = _require_kb_perm(request)
    if err:
        return err
    from fastapi.responses import StreamingResponse
    import json as _json
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)

    # 轻量短路（寒暄/泛问/空问题）先判，不占限流名额——它们不调模型，几毫秒就返回。
    # 只有真的要检索+生成时才去抢名额。
    _q_probe = _strip_cmd((body.question or "").strip())
    _cheap = (not _q_probe and not body.images) or \
             (bool(_q_probe) and not body.images and
              (_is_chitchat(_q_probe) or _is_generic_ask(_q_probe)))
    if _cheap:
        return await _run_ask_stream(kb_id, body, request, kb_, _json)

    try:
        _release = await _acquire_ask_slot(request)
    except GlobalBusy as e:
        ticket, why = _queue_add(request)
        if not ticket:
            return _too_many(why or "系统繁忙，请稍后重试。", wait=10)
        return StreamingResponse(
            _queue_stream(ticket, kb_id, body, request, kb_, _json),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except RateLimited as e:
        return _too_many(e.msg)

    # 名额要一直持有到流结束。StreamingResponse 是懒执行的，所以不能在这里
    # release——那样流还没开始名额就还回去了。
    #
    # 用一个哨兵对象把 release 传进生成器，让 gen() 自己在 finally 里调用：
    # 这样名额的生命周期和同步生成器完全一致，它结束（正常/异常/被 close）
    # 就释放，不依赖 Starlette 外层会不会包对。
    resp = await _run_ask_stream(kb_id, body, request, kb_, _json,
                                 on_finish=_release)
    return resp


async def _run_ask_stream(kb_id, body, request, kb_, _json, on_finish=None):
    """流式问答的真正实现（原 kb_ask_stream 的函数体）。

    仍然是同步生成器 gen()：阶段 1 只做限流，不动内部逻辑。
    阶段 2 会把它改造成 async 生成器 + AsyncClient，彻底不占线程池。

    on_finish: 生成器结束（正常/异常/被关闭）时调用一次。用它在流末尾释放
    限流名额——名额必须在"整条流都发完"之后才归还，放在端点函数里会提前释放。
    """
    from fastapi.responses import StreamingResponse
    q_raw = (body.question or "").strip()
    # 有人会把操作指令直接打进输入框：「开启上下文，那demo对应的uat账号呢」。
    # 这些词不是问题的一部分，混进检索会把相关片段稀释掉，先剥掉。
    q = _strip_cmd(q_raw)
    cmd_only = bool(q_raw) and not q      # 整句只有指令，没有实际问题
    # 只贴图不写字：给一句默认问题，让模型看图说话；否则会被当成空问题拒掉
    if body.images and not q_raw:
        q_raw = q = "请看看这几张图，说明这是什么情况、该怎么处理"
        cmd_only = False
    # 当前用户自己的大模型 key（见 userllm.py）。空串表示没配，用全局 key。
    # 各用各的 key，就不会全挤在公司网关那 2 路通道上互相排队。
    _ukeys = _user_llm_key(request)
    # planner（改写/拆解）和 fallback 只吃 cfg，这里一并带上，避免主问答与拆解用不同 key
    cfg = _apply_thinking_choice(_cfg_with_user_key(request),
                                 getattr(body, "thinking", None))

    def sse(tag, payload):
        return f"data: {_json.dumps({'type': tag, **payload}, ensure_ascii=False)}\n\n"

    has_img = bool(body.images)

    def gen():
        try:
            yield from _gen_body()
        finally:
            # 流结束/异常/被客户端关闭，都在这里归还限流名额。
            # 放在最外层的 finally：无论内层怎么 return/raise 都会走到。
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:
                    pass

    async def agen():
        """异步生成器（阶段 2）。

        SSE 全程走事件循环：LLM 调用用 llm.stream_async（共享 AsyncClient），
        检索走线程池。这样一个回答从头到尾都不占 anyio 线程池的线程，
        40 个线程不会再被流式请求吃光。

        顺带做了客户端断开检测：用户关掉页面后，下面的 finally 会让
        _agen_body 提前退出，httpx 的 async with 随之关闭上游连接，
        不再继续消耗网关额度（依据 03-目标架构:128）。
        """
        try:
            async for chunk in _agen_body():
                # 每条 SSE 都检查一次：便宜（读一个标志位），但能省下整段生成
                try:
                    if await request.is_disconnected():
                        print("[ask] 客户端已断开，停止生成", file=sys.stderr)
                        break
                except Exception:
                    pass          # 拿不到连接状态就继续发，不影响正常回答
                yield chunk
        finally:
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:
                    pass

    def _gen_body():
        _t_start = time.time()
        # 只贴图不写字也算有效提问：让模型看图说明就行，不要报"问题不能为空"
        if not q_raw and not has_img:
            yield sse("answer", {"text": "问题不能为空"}); return
        if cmd_only and not has_img:
            yield sse("answer", {"text": "上下文开关在输入框上方的「💬 上下文」按钮，直接点就行。"
                                         "你是想问什么？"})
            yield sse("done", {"full": "", "source_type": "kb", "source_label": "知识库"})
            return
        # 寒暄/应答: 直接回一句, 不检索不调模型(省十几秒, 也不会搜出奇怪的片段)
        if _is_chitchat(q):
            greet = _chitchat_reply(q)
            yield sse("status", {"text": "寒暄已跳过检索"})
            yield sse("answer", {"text": greet, "chitchat": True})
            yield sse("done", {"full": greet, "source_type": "kb", "source_label": "助手"})
            return
        # 只问「密码」「账号」这种孤立名词: 先反问, 别把整套口令铺给用户
        if _is_generic_ask(q):
            kbn = (kb_ or {}).get("name") or "这个知识库"
            ask = (f"「{q}」问的范围有点大——{kbn} 里不同系统、不同环境各有各的账号密码。\n\n"
                   "你要问的是哪一个？可以说得具体些，例如：\n"
                   "· 哪个系统（demo / O32 / oorder / UFT …）\n"
                   "· 哪个环境（测试 / UAT / 生产）\n"
                   "· 要账号、密码，还是数据库连接信息")
            yield sse("status", {"text": "问题范围太大，先确认一下"})
            yield sse("answer", {"text": ask, "clarify": True})
            yield sse("done", {"full": ask, "source_type": "kb", "source_label": "请补充问题"})
            return
        # 用户主动选了「联网搜索 / 大模型本地回答」：不检索知识库、不走缓存
        fb_mode = (body.fallback_mode or "").strip().lower()
        if fb_mode in ("web", "llm"):
            if not llm.llm_enabled(cfg=cfg):
                tip = "未配置大模型，暂时不能用「联网搜索回答 / 大模型本地回答」。"
                yield sse("status", {"text": "未配置大模型"})
                yield sse("answer", {"text": tip})
                yield sse("done", {"full": tip, "source_type": "none", "source_label": "未配置大模型"})
                return
            q_eff, src_type, user_msg, system, refs, note = _forced_fallback_prepare(
                q, body.history or [], fb_mode, cfg)
            label = _forced_fallback_label(src_type, fb_mode, note)
            yield sse("status", {"text": ("不查知识库，正在联网搜索并组织回答…"
                                          if src_type == "web" else "不查知识库，正在用大模型自身知识回答…")})
            buf = []
            if note:
                buf.append(note)
                yield sse("delta", {"text": note})
            if refs:
                yield sse("refs", {"refs": refs, "status": f"已获取 {len(refs)} 条网络参考"})
            got = False
            for token in llm.stream([{"role": "user", "content": user_msg}], cfg=cfg,
                                    system=system, user_key=_ukeys):
                got = True
                buf.append(token)
                yield sse("delta", {"text": token})
            full = "".join(buf)
            if got and full.strip():
                yield sse("done", {"full": full, "source_type": src_type,
                                   "refs": refs, "source_label": label})
                _log_ask(q, kb_id, request, full, [], src_type, True, _t_start,
                         q_eff if q_eff != q else "", False, cfg)
            else:
                tip = "回答失败：联网搜索或大模型暂时不可用，请稍后重试。"
                yield sse("answer", {"text": tip})
                yield sse("done", {"full": tip, "source_type": "none", "source_label": "回答失败"})
                _log_ask(q, kb_id, request, tip, [], "none", True, _t_start, "", False, cfg)
            return
        rc = cfg.get("retrieval", {})
        topk_ = body.top_k or rc.get("top_k", 8)
        hist = body.history or []
        mods = module_router.detect_modules(q)
        # 索引刚更新时会有一两分钟还在用旧索引, 这个状态要告诉用户,
        # 否则"刚加的文档搜不到"会被当成 bug
        try:
            _st = kb.state(kb_id)
            _stale = bool(_st.get("stale") or _st.get("loading"))
        except Exception:
            _stale = False
        # 首问先看缓存(追问依赖上下文, 不走缓存; 演示模式/换个回答也不走)
        # 带图更不能走缓存: 缓存键只有问题文本, 同一句话配不同图会拿到上一次的答案
        # 模块问题也不走缓存：旧缓存的答案可能把其他模块的内容拼进来了
        if not hist and not body.no_cache and not has_img and not mods:
            hit = cache.get(kb_id, q)
            if hit:
                srcs = _build_sources(hit["sources"], kb_id=kb_id)
                yield sse("sources", {"sources": srcs, "planning": {"split": False, "queries": [q]},
                                      "status": "⚡ 这个问题刚问过，直接给你上次的结果"})
                yield sse("answer", {"text": hit["answer"], "cached": True})
                _log_ask(q, kb_id, request, hit["answer"], srcs, "kb", False, _t_start, "", False, cfg)
                yield sse("done", {"full": hit["answer"], "source_type": "kb",
                                   "source_label": "知识库（缓存）", "cached": True,
                                   "stale": _stale})
                return
        yield sse("status", {"text": "正在理解问题…"})
        # 多轮: 依赖上文的追问 → 先改写成独立问题
        rw = planner.rewrite_query(q, hist, cfg=cfg)
        q_eff = rw["q"] if rw["rewritten"] else q
        mods = module_router.detect_modules(q_eff) or mods
        if rw["rewritten"]:
            yield sse("rewrite", {"q": q_eff, "original": q,
                                  "status": f"已结合上文理解你的问题：{q_eff}"})
        # 查询规划: 拆分模糊/复合问题后再检索
        plan_ = planner.plan_queries(q_eff, cfg=cfg)
        queries = plan_["queries"]
        _mq = _manual_query(q_eff, mods)
        if _mq and _mq not in queries:
            queries = list(queries) + [_mq]
        if mods:
            yield sse("status", {"text": f"已识别业务模块：{'、'.join(mods)}，只使用该模块的片段"})
        if len(queries) > 1:
            yield sse("plan", {"queries": queries,
                               "status": f"已拆解为 {len(queries) - 1} 个子问题，正在多路检索…"})
            results = _multi_search(kb_id, queries, topk_, rc, modules=mods)
        else:
            yield sse("status", {"text": "正在检索知识库…"})
            results = backend.retrieve(
                kb_id, q_eff, top_k=topk_,
                enable_vector=rc.get("enable_vector", True),
                enable_rerank=rc.get("enable_rerank", True),
            )
            if results is not None and mods:
                results, _drop = module_router.filter_records(results, mods)
                if _drop:
                    print(f"[module] {kb_id} 过滤其他模块片段 {_drop} 个（{'、'.join(mods)}）",
                          file=sys.stderr)
        if results is None:
            _log_ask(q, kb_id, request, "该知识库还没有索引", [], "none", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": "该知识库还没有索引,请先点\"重建索引\"。"}); return
        if mods and not results:
            msg = module_router.miss_message(mods)
            _log_ask(q, kb_id, request, msg, [], "none", True, _t_start, q_eff, False, cfg)
            yield sse("answer", {"text": msg, "module_miss": True})
            yield sse("done", {"full": msg, "source_type": "none",
                               "source_label": "模块未命中", "modules": mods})
            return

        # 知识库未命中 → 兜底(联网搜索 / 大模型自身知识), 并显著标注来源
        if not results or (fallback.should_fallback(results) and not _has_confident_hit(results)):
            if not llm.llm_enabled(cfg=cfg):
                _log_ask(q, kb_id, request, "未检索到相关内容", results or [], "none", True, _t_start, "", False, cfg)
                yield sse("answer", {"text": "未检索到相关内容,换个问法试试。"}); return
            fb_mode = (body.fallback_mode or "").strip().lower()
            # 默认不自动联网、不自动拿模型知识顶上，先让用户选
            if fb_mode not in ("web", "llm", "none") and fallback.ask_first(cfg):
                yield sse("offer", {
                    "q": q,
                    "text": f"知识库里没有找到能回答「{q}」的内容。\n\n要继续的话得换个来源，你选一个：",
                    "options": fallback.offer_options(cfg) + [{"id": "none", "label": "不用了"}],
                    "note": "知识库里的答案更可靠；联网搜索和模型自身知识都可能不准，请自行判断。",
                })
                return
            if fb_mode == "none":
                yield sse("offer", {"q": q, "text": "好的，知识库里确实没有相关内容。可以换个问法，或者把资料补进来再问。",
                                    "options": []})
                return
            src_type, user_msg, system, refs = fallback.prepare_fallback(q_eff, mode=fb_mode or "auto")
            label = "网络搜索" if src_type == "web" else "大模型知识"
            yield sse("status", {"text": f"知识库未命中，正在基于{label}回答…"})
            if refs:
                yield sse("refs", {"refs": refs, "status": f"已获取 {len(refs)} 条网络参考"})
            buf, got = [], False
            for token in llm.stream([{"role": "user", "content": user_msg}], cfg=cfg,
                                    system=system, user_key=_ukeys):
                got = True
                buf.append(token)
                yield sse("delta", {"text": token})
            if got:
                yield sse("done", {"full": "".join(buf), "source_type": src_type, "refs": refs,
                                   "source_label": label})
            else:
                yield sse("answer", {"text": "大模型兜底调用失败,请检查网络或稍后重试。"})
            return


        # 按分数分布决定给几条（2026-11-26 需求）：答案明确就少给，库里没有就说没找到
        results, _trim_info = _trim_by_scores(results, cfg)
        print(f'[trim] 保留 {len(results)} 条 {_trim_info}', file=sys.stderr)
        if not results:
            # 防御：去重后理论上不会全空，但若上游后端返回空列表，
            # 这里给出明确答复比让提示词拼出空上下文好。
            _msg = '知识库里没有找到与这个问题相关的内容。可以换个问法，或者把相关资料补进来再问。'
            _log_ask(q_eff, kb_id, request, _msg, [], 'none', True, _t_start, '', False, cfg)
            yield sse('answer', {'text': _msg, 'not_found': True})
            yield sse('done', {'full': _msg, 'source_type': 'none',
                               'source_label': '未找到相关内容'})
            return
        sources = _reorder_for_listing(q_eff, _build_sources(results, kb_id=kb_id))
        sources = _append_diagram_sources(sources, kb_id, q_eff)
        yield sse("sources", {"sources": sources,
                              "planning": {"split": plan_["split"], "queries": queries,
                                           "reason": plan_["reason"]},
                              "rewrite": {"q": q_eff, "rewritten": rw["rewritten"]},
                              "status": f"已找到 {len(sources)} 个相关片段，正在生成回答…"})
        if not llm.llm_enabled(cfg=cfg):
            lines = [f"{i}. **{s['path']}** (得分 {s['score']})\n{s.get('text_full') or s['text']}"
                     for i, s in enumerate(sources, 1)]
            yield sse("answer", {"text": "未配置大模型,以下为检索命中片段:\n\n" + "\n\n".join(lines),
                                 "llm": False}); return
        system, msgs = _build_ask_context(sources, q_eff, hist, body.answer_len, body.images, modules=mods)
        buf = []
        got = False
        meta = {}
        for token in llm.stream(msgs, cfg=cfg, system=system, meta=meta, user_key=_ukeys):
            got = True
            buf.append(token)
            yield sse("delta", {"text": token})

        # 撞上单次输出上限别把半截话丢给用户——自动接着写。
        # 用户的原话是"有回答就答完呀"：答案写到一半停住，比答得长难受得多。
        for _round in range(_MAX_CONTINUE):
            if meta.get("stop_reason") != "max_tokens" or not buf:
                break
            if meta.get("error") or meta.get("timeout"):
                break
            yield sse("status", {"text": "回答较长，正在接着写…"})
            cont = list(msgs) + [
                {"role": "assistant", "content": "".join(buf)},
                {"role": "user", "content":
                    "接着上面最后一句继续写，写完为止。"
                    "不要重复已写过的内容，不要重新开头，不要加总结性开场白。"},
            ]
            meta = {}
            try:
                for token in llm.stream(cont, cfg=cfg, system=system, meta=meta,
                                        user_key=_ukeys):
                    got = True
                    buf.append(token)
                    yield sse("delta", {"text": token})
            except Exception as _e:
                print(f"[ask] 续写失败: {_e}", file=sys.stderr)
                break
        full = "".join(buf) if got else ""
        # 超时/异常: 明确告知, 而不是静默结束
        tmo = meta.get("timeout")
        if tmo:
            limit = llm.read_timeout() if tmo in ("read", "connect") else llm.total_timeout()
            if tmo in ("read", "connect"):
                why = f"等了 {limit:.0f} 秒，大模型网关一直没有开始返回内容"
            else:
                why = f"生成超过 {limit:.0f} 秒上限，已中断"
            if full.strip():
                tail = f"\n\n---\n⚠️ 回答未完（{why}）。以上是已生成的部分，可点下方「↻ 重试」再试一次。"
                yield sse("delta", {"text": tail})
                full += tail
                _log_ask(q, kb_id, request, full, sources, "kb", False, _t_start, q_eff, plan_["split"], cfg)
                yield sse("done", {"full": full, "source_type": "kb",
                                   "source_label": "知识库（超时截断）", "timeout": tmo})
            else:
                _log_ask(q, kb_id, request, "大模型超时", sources, "timeout", True, _t_start, "", False, cfg)
                if _ukeys:
                    tail_tip = "· 若经常这样，可能是你自己的 Key 额度受限，换一把试试"
                else:
                    tail_tip = ("· 若经常这样，多半是公共 Key 在排队——"
                                "点右上角「🔑 我的 Key」填一个自己的 Key 会稳定很多")
                tip = (f"⚠️ 大模型超时：{why}。\n\n"
                       "已检索到的片段见下方引用。可以：\n"
                       "· 点「↻ 重试」再试一次（网关常是偶发慢）\n"
                       "· 换个更具体的问法\n"
                       + tail_tip)
                yield sse("answer", {"text": tip, "timeout": tmo})
            return
        if meta.get("error") and not full.strip():
            err = str(meta["error"])
            if any(c in err for c in ("503", "502", "504")):
                msg = "⚠️ 大模型网关暂时不可用（HTTP 5xx），这是网关侧的问题，通常几分钟后恢复。"
            elif "429" in err:
                if _ukeys:
                    msg = "⚠️ 大模型调用太频繁（429），等十几秒再试。"
                else:
                    msg = ("⚠️ 大模型调用太频繁（429）。你正在用公共 Key，"
                           "大家共用它的并发额度——点右上角「🔑 我的 Key」"
                           "填一个自己的会好很多。")

            elif "401" in err or "403" in err:
                # 自己的 key 出问题就让他自己换，别叫人去找管理员（管理员也帮不上）
                if _ukeys:
                    msg = ("⚠️ 你自己填的大模型 Key 鉴权失败（401/403）。\n"
                           "它可能已过期或权限不足。点右上角「🔑 我的 Key」换一把，"
                           "或改回使用公共 Key。")
                else:
                    msg = "⚠️ 大模型鉴权失败（401/403），api_key 可能过期了，联系管理员。"
            else:
                msg = f"⚠️ 大模型调用失败：{err}"
            _log_ask(q, kb_id, request, err, sources, "error", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": msg + "\n\n已检索到的片段见下方引用，可点「↻ 重试」。"})
            return
        if not got:
            _log_ask(q, kb_id, request, "大模型无输出", sources, "none", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": "大模型没有返回内容（网关可能繁忙）。已检索到的片段见下方引用，可点「↻ 重试」。"})
            return
        # 已经自动续写过 _MAX_CONTINUE 轮还撞上限，才提示（正常情况走不到这里）
        if meta.get("stop_reason") == "max_tokens" and full.strip():
            tail = (f"\n\n---\n⚠️ 回答未完：已连续续写 {_MAX_CONTINUE} 轮，仍达到单次输出上限。\n"
                    "问题涉及的范围太大，建议拆开问——例如只问其中一个模块或一个阶段。")
            yield sse("delta", {"text": tail})
            full += tail
        # 知识库命中但回答"未找到" → 默认先问用户要不要换来源，不自动联网/不自动编
        if _looks_like_not_found(full):
            fb_mode = (body.fallback_mode or "").strip().lower()
            if fb_mode not in ("web", "llm", "none") and fallback.ask_first(cfg):
                yield sse("offer", {
                    "q": q,
                    "text": (f"知识库里没有找到能回答「{q}」的内容。\n\n"
                             "已检索到的片段见下方引用。要继续的话得换个来源，你选一个："),
                    "options": fallback.offer_options(cfg) + [{"id": "none", "label": "不用了"}],
                    "note": "知识库里的答案更可靠；联网搜索和模型自身知识都可能不准，请自行判断。",
                })
                return
            if fb_mode == "none":
                yield sse("offer", {"q": q, "text": "好的，知识库里确实没有相关内容。可以换个问法，或者把资料补进来再问。",
                                    "options": []})
                return
            yield sse("status", {"text": "知识库中未找到相关内容，正在尝试网络/大模型知识…"})
            src_type, fuser, fsystem, frefs = fallback.prepare_fallback(q_eff, mode=fb_mode or "auto")
            if frefs:
                yield sse("refs", {"refs": frefs, "status": f"已获取 {len(frefs)} 条网络参考"})
            label = "网络搜索" if src_type == "web" else "大模型知识"
            fbuf, fgot = [], False
            for token in llm.stream([{"role": "user", "content": fuser}], cfg=cfg,
                                    system=fsystem, user_key=_ukeys):
                fgot = True
                fbuf.append(token)
                yield sse("delta", {"text": token})
            if fgot:
                yield sse("done", {"full": "".join(fbuf), "source_type": src_type,
                                   "refs": frefs, "source_label": label})
            else:
                yield sse("done", {"full": full, "source_type": "kb", "source_label": "知识库"})
            return
        # 引用模块校验：跨模块引用追加一个显著提示，不让“拼盘”看起来像有依据
        _cwarn = module_router.audit_citations(full, sources, mods)
        if _cwarn:
            yield sse("delta", {"text": _cwarn})
            full += _cwarn
        try:
            # 带图的答案不写缓存：缓存键只有问题文本，会把"看图得出的结论"污染给下次纯文字提问
            if not hist and not rw["rewritten"] and not body.no_cache and not has_img and not mods:
                cache.put(kb_id, q, full, results)
            qa_id = qa_log.record(q, kb_id, _current_user(request), full,
                                  sources, "kb", False, int((time.time() - _t_start) * 1000),
                                  q_eff if rw["rewritten"] else "", plan_["split"],
                                  (llm.llm_settings(cfg) or {}).get("model", ""))
            yield sse("qid", {"id": qa_id})
        except Exception as _e:
            print(f"[qa_log] {_e}", file=sys.stderr)

        # 答案配图：把命中文档里的截图按"意思"挂到答案对应段落上。
        # 不走物理位置（PDF 没有段落结构、docx 段落和图也常错位），
        # 而是答案段落 vs 图片摘要做向量匹配。见 imgindex.bind_to_answer。
        # 答案配图：把命中文档里的截图按"意思"挂到答案对应段落上。
        # 不走物理位置（PDF 没有段落结构、docx 段落和图也常错位），
        # 而是答案段落 vs 图片摘要做向量匹配，再让模型看图确认（见 imgindex.bind_to_answer）。
        #
        # 这一步要 10~30 秒（每张候选图都要上传给模型核对），**不能挡在文字流前面**，
        # 所以扔到独立线程里算，文字照常先流完；算完再补推 figures。
        fig_fut = None
        if _figures_enabled() and full and len(full) >= 60 and _diagram_intent(q_eff):
            try:
                _kb, _q = kb_id, q_eff
                fig_fut = _FIG_POOL.submit(_diagram_figure_block, _kb, _q)
            except Exception as _e:
                print(f"[figures] 提交失败 {type(_e).__name__}: {_e}", file=sys.stderr)

        # 先收尾，让用户立刻看到完整答案；配图算完再补推（前端会插进已渲染的消息里）
        yield sse("done", {"full": full, "source_type": "kb",
                           "source_label": "知识库", "stale": _stale})

        if fig_fut is not None:
            try:
                blocks = fig_fut.result(timeout=30)
                if blocks:
                    yield sse("figures", {"blocks": blocks})
                else:
                    print(f"[figures] 未挂图 kb={kb_id} 答案{len(full)}字", file=sys.stderr)
            except Exception as _e:
                print(f"[figures] 取结果超时/失败 {type(_e).__name__}", file=sys.stderr)

    async def _agen_body():
        _t_start = time.time()
        # 只贴图不写字也算有效提问：让模型看图说明就行，不要报"问题不能为空"
        if not q_raw and not has_img:
            yield sse("answer", {"text": "问题不能为空"}); return
        if cmd_only and not has_img:
            yield sse("answer", {"text": "上下文开关在输入框上方的「💬 上下文」按钮，直接点就行。"
                                         "你是想问什么？"})
            yield sse("done", {"full": "", "source_type": "kb", "source_label": "知识库"})
            return
        # 寒暄/应答: 直接回一句, 不检索不调模型(省十几秒, 也不会搜出奇怪的片段)
        if _is_chitchat(q):
            greet = _chitchat_reply(q)
            yield sse("status", {"text": "寒暄已跳过检索"})
            yield sse("answer", {"text": greet, "chitchat": True})
            yield sse("done", {"full": greet, "source_type": "kb", "source_label": "助手"})
            return
        # 只问「密码」「账号」这种孤立名词: 先反问, 别把整套口令铺给用户
        if _is_generic_ask(q):
            kbn = (kb_ or {}).get("name") or "这个知识库"
            ask = (f"「{q}」问的范围有点大——{kbn} 里不同系统、不同环境各有各的账号密码。\n\n"
                   "你要问的是哪一个？可以说得具体些，例如：\n"
                   "· 哪个系统（demo / O32 / oorder / UFT …）\n"
                   "· 哪个环境（测试 / UAT / 生产）\n"
                   "· 要账号、密码，还是数据库连接信息")
            yield sse("status", {"text": "问题范围太大，先确认一下"})
            yield sse("answer", {"text": ask, "clarify": True})
            yield sse("done", {"full": ask, "source_type": "kb", "source_label": "请补充问题"})
            return
        # 用户主动选了「联网搜索 / 大模型本地回答」：不检索知识库、不走缓存
        fb_mode = (body.fallback_mode or "").strip().lower()
        if fb_mode in ("web", "llm"):
            if not llm.llm_enabled(cfg=cfg):
                tip = "未配置大模型，暂时不能用「联网搜索回答 / 大模型本地回答」。"
                yield sse("status", {"text": "未配置大模型"})
                yield sse("answer", {"text": tip})
                yield sse("done", {"full": tip, "source_type": "none", "source_label": "未配置大模型"})
                return
            q_eff, src_type, user_msg, system, refs, note = await run_in_threadpool(
                _forced_fallback_prepare, q, body.history or [], fb_mode, cfg)
            label = _forced_fallback_label(src_type, fb_mode, note)
            yield sse("status", {"text": ("不查知识库，正在联网搜索并组织回答…"
                                          if src_type == "web" else "不查知识库，正在用大模型自身知识回答…")})
            buf = []
            if note:
                buf.append(note)
                yield sse("delta", {"text": note})
            if refs:
                yield sse("refs", {"refs": refs, "status": f"已获取 {len(refs)} 条网络参考"})
            got = False
            async for token in llm.stream_async([{"role": "user", "content": user_msg}], cfg=cfg,
                                                system=system, user_key=_ukeys):
                got = True
                buf.append(token)
                yield sse("delta", {"text": token})
            full = "".join(buf)
            if got and full.strip():
                yield sse("done", {"full": full, "source_type": src_type,
                                   "refs": refs, "source_label": label})
                _log_ask(q, kb_id, request, full, [], src_type, True, _t_start,
                         q_eff if q_eff != q else "", False, cfg)
            else:
                tip = "回答失败：联网搜索或大模型暂时不可用，请稍后重试。"
                yield sse("answer", {"text": tip})
                yield sse("done", {"full": tip, "source_type": "none", "source_label": "回答失败"})
                _log_ask(q, kb_id, request, tip, [], "none", True, _t_start, "", False, cfg)
            return
        rc = cfg.get("retrieval", {})
        topk_ = body.top_k or rc.get("top_k", 8)
        hist = body.history or []
        mods = module_router.detect_modules(q)
        # 索引刚更新时会有一两分钟还在用旧索引, 这个状态要告诉用户,
        # 否则"刚加的文档搜不到"会被当成 bug
        try:
            _st = kb.state(kb_id)
            _stale = bool(_st.get("stale") or _st.get("loading"))
        except Exception:
            _stale = False
        # 首问先看缓存(追问依赖上下文, 不走缓存; 演示模式/换个回答也不走)
        # 带图更不能走缓存: 缓存键只有问题文本, 同一句话配不同图会拿到上一次的答案
        # 模块问题也不走缓存：旧缓存的答案可能把其他模块的内容拼进来了
        if not hist and not body.no_cache and not has_img and not mods:
            hit = cache.get(kb_id, q)
            if hit:
                srcs = _build_sources(hit["sources"], kb_id=kb_id)
                yield sse("sources", {"sources": srcs, "planning": {"split": False, "queries": [q]},
                                      "status": "⚡ 这个问题刚问过，直接给你上次的结果"})
                yield sse("answer", {"text": hit["answer"], "cached": True})
                _log_ask(q, kb_id, request, hit["answer"], srcs, "kb", False, _t_start, "", False, cfg)
                yield sse("done", {"full": hit["answer"], "source_type": "kb",
                                   "source_label": "知识库（缓存）", "cached": True,
                                   "stale": _stale})
                return
        yield sse("status", {"text": "正在理解问题…"})
        # 多轮: 依赖上文的追问 → 先改写成独立问题
        rw = await run_in_threadpool(planner.rewrite_query, q, hist, cfg=cfg)
        q_eff = rw["q"] if rw["rewritten"] else q
        mods = module_router.detect_modules(q_eff) or mods
        if rw["rewritten"]:
            yield sse("rewrite", {"q": q_eff, "original": q,
                                  "status": f"已结合上文理解你的问题：{q_eff}"})
        # 查询规划: 拆分模糊/复合问题后再检索
        plan_ = await run_in_threadpool(planner.plan_queries, q_eff, cfg=cfg)
        queries = plan_["queries"]
        _mq = _manual_query(q_eff, mods)
        if _mq and _mq not in queries:
            queries = list(queries) + [_mq]
        if mods:
            yield sse("status", {"text": f"已识别业务模块：{'、'.join(mods)}，只使用该模块的片段"})
        if len(queries) > 1:
            yield sse("plan", {"queries": queries,
                               "status": f"已拆解为 {len(queries) - 1} 个子问题，正在多路检索…"})
            results = await run_in_threadpool(_multi_search, kb_id, queries, topk_, rc, mods)
        else:
            yield sse("status", {"text": "正在检索知识库…"})
            results = await run_in_threadpool(
                backend.retrieve,
                kb_id, q_eff, top_k=topk_,
                enable_vector=rc.get("enable_vector", True),
                enable_rerank=rc.get("enable_rerank", True),
            )
            if results is not None and mods:
                results, _drop = module_router.filter_records(results, mods)
                if _drop:
                    print(f"[module] {kb_id} 过滤其他模块片段 {_drop} 个（{'、'.join(mods)}）",
                          file=sys.stderr)
        if results is None:
            _log_ask(q, kb_id, request, "该知识库还没有索引", [], "none", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": "该知识库还没有索引,请先点\"重建索引\"。"}); return
        if mods and not results:
            msg = module_router.miss_message(mods)
            _log_ask(q, kb_id, request, msg, [], "none", True, _t_start, q_eff, False, cfg)
            yield sse("answer", {"text": msg, "module_miss": True})
            yield sse("done", {"full": msg, "source_type": "none",
                               "source_label": "模块未命中", "modules": mods})
            return

        # 知识库未命中 → 兜底(联网搜索 / 大模型自身知识), 并显著标注来源
        if not results or (fallback.should_fallback(results) and not _has_confident_hit(results)):
            if not llm.llm_enabled(cfg=cfg):
                _log_ask(q, kb_id, request, "未检索到相关内容", results or [], "none", True, _t_start, "", False, cfg)
                yield sse("answer", {"text": "未检索到相关内容,换个问法试试。"}); return
            fb_mode = (body.fallback_mode or "").strip().lower()
            # 默认不自动联网、不自动拿模型知识顶上，先让用户选
            if fb_mode not in ("web", "llm", "none") and fallback.ask_first(cfg):
                yield sse("offer", {
                    "q": q,
                    "text": f"知识库里没有找到能回答「{q}」的内容。\n\n要继续的话得换个来源，你选一个：",
                    "options": fallback.offer_options(cfg) + [{"id": "none", "label": "不用了"}],
                    "note": "知识库里的答案更可靠；联网搜索和模型自身知识都可能不准，请自行判断。",
                })
                return
            if fb_mode == "none":
                yield sse("offer", {"q": q, "text": "好的，知识库里确实没有相关内容。可以换个问法，或者把资料补进来再问。",
                                    "options": []})
                return
            src_type, user_msg, system, refs = fallback.prepare_fallback(q_eff, mode=fb_mode or "auto")
            label = "网络搜索" if src_type == "web" else "大模型知识"
            yield sse("status", {"text": f"知识库未命中，正在基于{label}回答…"})
            if refs:
                yield sse("refs", {"refs": refs, "status": f"已获取 {len(refs)} 条网络参考"})
            buf, got = [], False
            async for token in llm.stream_async([{"role": "user", "content": user_msg}], cfg=cfg,
                                    system=system, user_key=_ukeys):
                got = True
                buf.append(token)
                yield sse("delta", {"text": token})
            if got:
                yield sse("done", {"full": "".join(buf), "source_type": src_type, "refs": refs,
                                   "source_label": label})
            else:
                yield sse("answer", {"text": "大模型兜底调用失败,请检查网络或稍后重试。"})
            return


        # 按分数分布决定给几条（2026-11-26 需求）：答案明确就少给，库里没有就说没找到
        results, _trim_info = _trim_by_scores(results, cfg)
        print(f'[trim] 保留 {len(results)} 条 {_trim_info}', file=sys.stderr)
        if not results:
            # 防御：去重后理论上不会全空，但若上游后端返回空列表，
            # 这里给出明确答复比让提示词拼出空上下文好。
            _msg = '知识库里没有找到与这个问题相关的内容。可以换个问法，或者把相关资料补进来再问。'
            _log_ask(q_eff, kb_id, request, _msg, [], 'none', True, _t_start, '', False, cfg)
            yield sse('answer', {'text': _msg, 'not_found': True})
            yield sse('done', {'full': _msg, 'source_type': 'none',
                               'source_label': '未找到相关内容'})
            return
        sources = _reorder_for_listing(q_eff, _build_sources(results, kb_id=kb_id))
        sources = _append_diagram_sources(sources, kb_id, q_eff)
        yield sse("sources", {"sources": sources,
                              "planning": {"split": plan_["split"], "queries": queries,
                                           "reason": plan_["reason"]},
                              "rewrite": {"q": q_eff, "rewritten": rw["rewritten"]},
                              "status": f"已找到 {len(sources)} 个相关片段，正在生成回答…"})
        if not llm.llm_enabled(cfg=cfg):
            lines = [f"{i}. **{s['path']}** (得分 {s['score']})\n{s.get('text_full') or s['text']}"
                     for i, s in enumerate(sources, 1)]
            yield sse("answer", {"text": "未配置大模型,以下为检索命中片段:\n\n" + "\n\n".join(lines),
                                 "llm": False}); return
        system, msgs = _build_ask_context(sources, q_eff, hist, body.answer_len, body.images, modules=mods)
        buf = []
        got = False
        meta = {}
        async for ev in llm.stream_events_async(msgs, cfg=cfg, system=system,
                                                meta=meta, user_key=_ukeys):
            _typ = ev.get("type")
            if _typ == "thinking":
                yield sse("thinking", {"text": ev.get("text") or ""})
            elif _typ == "thinking_reset":
                yield sse("thinking", {"reset": True})
            elif _typ == "text":
                got = True
                token = ev.get("text") or ""
                buf.append(token)
                yield sse("delta", {"text": token})

        # 撞上单次输出上限别把半截话丢给用户——自动接着写。
        # 用户的原话是"有回答就答完呀"：答案写到一半停住，比答得长难受得多。
        for _round in range(_MAX_CONTINUE):
            if meta.get("stop_reason") != "max_tokens" or not buf:
                break
            if meta.get("error") or meta.get("timeout"):
                break
            yield sse("status", {"text": "回答较长，正在接着写…"})
            cont = list(msgs) + [
                {"role": "assistant", "content": "".join(buf)},
                {"role": "user", "content":
                    "接着上面最后一句继续写，写完为止。"
                    "不要重复已写过的内容，不要重新开头，不要加总结性开场白。"},
            ]
            meta = {}
            try:
                async for ev in llm.stream_events_async(cont, cfg=cfg, system=system,
                                                        meta=meta, user_key=_ukeys):
                    _typ = ev.get("type")
                    if _typ == "thinking":
                        yield sse("thinking", {"text": ev.get("text") or ""})
                    elif _typ == "thinking_reset":
                        yield sse("thinking", {"reset": True})
                    elif _typ == "text":
                        got = True
                        token = ev.get("text") or ""
                        buf.append(token)
                        yield sse("delta", {"text": token})
            except Exception as _e:
                print(f"[ask] 续写失败: {_e}", file=sys.stderr)
                break
        full = "".join(buf) if got else ""
        # 超时/异常: 明确告知, 而不是静默结束
        tmo = meta.get("timeout")
        if tmo:
            limit = llm.read_timeout() if tmo in ("read", "connect") else llm.total_timeout()
            if tmo in ("read", "connect"):
                why = f"等了 {limit:.0f} 秒，大模型网关一直没有开始返回内容"
            else:
                why = f"生成超过 {limit:.0f} 秒上限，已中断"
            if full.strip():
                tail = f"\n\n---\n⚠️ 回答未完（{why}）。以上是已生成的部分，可点下方「↻ 重试」再试一次。"
                yield sse("delta", {"text": tail})
                full += tail
                _log_ask(q, kb_id, request, full, sources, "kb", False, _t_start, q_eff, plan_["split"], cfg)
                yield sse("done", {"full": full, "source_type": "kb",
                                   "source_label": "知识库（超时截断）", "timeout": tmo})
            else:
                _log_ask(q, kb_id, request, "大模型超时", sources, "timeout", True, _t_start, "", False, cfg)
                if _ukeys:
                    tail_tip = "· 若经常这样，可能是你自己的 Key 额度受限，换一把试试"
                else:
                    tail_tip = ("· 若经常这样，多半是公共 Key 在排队——"
                                "点右上角「🔑 我的 Key」填一个自己的 Key 会稳定很多")
                tip = (f"⚠️ 大模型超时：{why}。\n\n"
                       "已检索到的片段见下方引用。可以：\n"
                       "· 点「↻ 重试」再试一次（网关常是偶发慢）\n"
                       "· 换个更具体的问法\n"
                       + tail_tip)
                yield sse("answer", {"text": tip, "timeout": tmo})
            return
        if meta.get("error") and not full.strip():
            err = str(meta["error"])
            if any(c in err for c in ("503", "502", "504")):
                msg = "⚠️ 大模型网关暂时不可用（HTTP 5xx），这是网关侧的问题，通常几分钟后恢复。"
            elif "429" in err:
                if _ukeys:
                    msg = "⚠️ 大模型调用太频繁（429），等十几秒再试。"
                else:
                    msg = ("⚠️ 大模型调用太频繁（429）。你正在用公共 Key，"
                           "大家共用它的并发额度——点右上角「🔑 我的 Key」"
                           "填一个自己的会好很多。")

            elif "401" in err or "403" in err:
                # 自己的 key 出问题就让他自己换，别叫人去找管理员（管理员也帮不上）
                if _ukeys:
                    msg = ("⚠️ 你自己填的大模型 Key 鉴权失败（401/403）。\n"
                           "它可能已过期或权限不足。点右上角「🔑 我的 Key」换一把，"
                           "或改回使用公共 Key。")
                else:
                    msg = "⚠️ 大模型鉴权失败（401/403），api_key 可能过期了，联系管理员。"
            else:
                msg = f"⚠️ 大模型调用失败：{err}"
            _log_ask(q, kb_id, request, err, sources, "error", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": msg + "\n\n已检索到的片段见下方引用，可点「↻ 重试」。"})
            return
        if not got:
            _log_ask(q, kb_id, request, "大模型无输出", sources, "none", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": "大模型没有返回内容（网关可能繁忙）。已检索到的片段见下方引用，可点「↻ 重试」。"})
            return
        # 已经自动续写过 _MAX_CONTINUE 轮还撞上限，才提示（正常情况走不到这里）
        if meta.get("stop_reason") == "max_tokens" and full.strip():
            tail = (f"\n\n---\n⚠️ 回答未完：已连续续写 {_MAX_CONTINUE} 轮，仍达到单次输出上限。\n"
                    "问题涉及的范围太大，建议拆开问——例如只问其中一个模块或一个阶段。")
            yield sse("delta", {"text": tail})
            full += tail
        # 知识库命中但回答"未找到" → 默认先问用户要不要换来源，不自动联网/不自动编
        if _looks_like_not_found(full):
            fb_mode = (body.fallback_mode or "").strip().lower()
            if fb_mode not in ("web", "llm", "none") and fallback.ask_first(cfg):
                yield sse("offer", {
                    "q": q,
                    "text": (f"知识库里没有找到能回答「{q}」的内容。\n\n"
                             "已检索到的片段见下方引用。要继续的话得换个来源，你选一个："),
                    "options": fallback.offer_options(cfg) + [{"id": "none", "label": "不用了"}],
                    "note": "知识库里的答案更可靠；联网搜索和模型自身知识都可能不准，请自行判断。",
                })
                return
            if fb_mode == "none":
                yield sse("offer", {"q": q, "text": "好的，知识库里确实没有相关内容。可以换个问法，或者把资料补进来再问。",
                                    "options": []})
                return
            yield sse("status", {"text": "知识库中未找到相关内容，正在尝试网络/大模型知识…"})
            src_type, fuser, fsystem, frefs = fallback.prepare_fallback(q_eff, mode=fb_mode or "auto")
            if frefs:
                yield sse("refs", {"refs": frefs, "status": f"已获取 {len(frefs)} 条网络参考"})
            label = "网络搜索" if src_type == "web" else "大模型知识"
            fbuf, fgot = [], False
            async for token in llm.stream_async([{"role": "user", "content": fuser}], cfg=cfg,
                                    system=fsystem, user_key=_ukeys):
                fgot = True
                fbuf.append(token)
                yield sse("delta", {"text": token})
            if fgot:
                yield sse("done", {"full": "".join(fbuf), "source_type": src_type,
                                   "refs": frefs, "source_label": label})
            else:
                yield sse("done", {"full": full, "source_type": "kb", "source_label": "知识库"})
            return
        # 引用模块校验：跨模块引用追加一个显著提示，不让“拼盘”看起来像有依据
        _cwarn = module_router.audit_citations(full, sources, mods)
        if _cwarn:
            yield sse("delta", {"text": _cwarn})
            full += _cwarn
        try:
            # 带图的答案不写缓存：缓存键只有问题文本，会把"看图得出的结论"污染给下次纯文字提问
            if not hist and not rw["rewritten"] and not body.no_cache and not has_img and not mods:
                cache.put(kb_id, q, full, results)
            qa_id = qa_log.record(q, kb_id, _current_user(request), full,
                                  sources, "kb", False, int((time.time() - _t_start) * 1000),
                                  q_eff if rw["rewritten"] else "", plan_["split"],
                                  (llm.llm_settings(cfg) or {}).get("model", ""))
            yield sse("qid", {"id": qa_id})
        except Exception as _e:
            print(f"[qa_log] {_e}", file=sys.stderr)

        # 答案配图：把命中文档里的截图按"意思"挂到答案对应段落上。
        # 不走物理位置（PDF 没有段落结构、docx 段落和图也常错位），
        # 而是答案段落 vs 图片摘要做向量匹配。见 imgindex.bind_to_answer。
        # 答案配图：把命中文档里的截图按"意思"挂到答案对应段落上。
        # 不走物理位置（PDF 没有段落结构、docx 段落和图也常错位），
        # 而是答案段落 vs 图片摘要做向量匹配，再让模型看图确认（见 imgindex.bind_to_answer）。
        #
        # 这一步要 10~30 秒（每张候选图都要上传给模型核对），**不能挡在文字流前面**，
        # 所以扔到独立线程里算，文字照常先流完；算完再补推 figures。
        fig_fut = None
        if _figures_enabled() and full and len(full) >= 60 and _diagram_intent(q_eff):
            try:
                _kb, _q = kb_id, q_eff
                fig_fut = _FIG_POOL.submit(_diagram_figure_block, _kb, _q)
            except Exception as _e:
                print(f"[figures] 提交失败 {type(_e).__name__}: {_e}", file=sys.stderr)

        # 先收尾，让用户立刻看到完整答案；配图算完再补推（前端会插进已渲染的消息里）
        yield sse("done", {"full": full, "source_type": "kb",
                           "source_label": "知识库", "stale": _stale})

        if fig_fut is not None:
            try:
                blocks = fig_fut.result(timeout=30)
                if blocks:
                    yield sse("figures", {"blocks": blocks})
                else:
                    print(f"[figures] 未挂图 kb={kb_id} 答案{len(full)}字", file=sys.stderr)
            except Exception as _e:
                print(f"[figures] 取结果超时/失败 {type(_e).__name__}", file=sys.stderr)

    # 阶段 2：改用异步生成器。SSE 全程在事件循环里跑，不再占 anyio 线程池。
    # 同步版 gen() 保留在上面，需要回滚时把 agen() 改回 gen() 即可。
    return StreamingResponse(agen(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    """健康检查。

    必须是 async def：同步端点会进线程池排队，问答高负载时线程被占满，
    连这个探活接口都要等 2 秒（压测实测 100 并发时 P95=1985ms）。
    这里只读几个内存变量，本来就是毫秒级，不该排在问答后面。
    """
    # 向量一致性：chunks.jsonl 行数与 vectors.npy 行数对不上时，检索会静默出错。
    # 只返回"已经算过的"结果（cached_consistency_report 不碰文件 I/O），
    # 探活路径绝不做磁盘操作，否则又变回慢接口。
    try:
        import embeddings
        consistency = embeddings.cached_consistency_report()
    except Exception:
        consistency = {}
    # 当前生效的检索内核（feature flag 可按库切换，见 app/backend.py）。
    # describe_cached 只读内存快照，不在探活路径上读 registry/config 文件。
    try:
        be_info = backend.describe_cached()
    except Exception:
        be_info = {}
    return {"ok": True, "llm_configured": llm.llm_enabled(),
            "ask": ask_limits_state(),
            "embeddings": consistency,
            "retrieval": be_info,
            "llm": llm.llm_settings() | {"api_key": "***" if llm.llm_settings()["api_key"] else ""}}


@app.get("/api/warmup")
async def warmup_status():
    """启动预热进度（免登录）。前端轮询它显示进度条。"""
    with _WARMUP_LOCK:
        return {"ok": True, **dict(_WARMUP)}


class LlmCfg(BaseModel):
    enabled: bool = True
    protocol: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = None
    max_tokens: int = None


@app.get("/api/my/llm")
def my_llm_get(request: Request):
    """我的大模型 key 状态。给前端显示"当前用公共 key 还是我自己的"。

    **不返回 key 本身**，只返回有没有配、什么时候配的、末 4 位。
    """
    u = _user_of(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    import userllm
    st = userllm.info(u.get("id", ""))
    s = llm.llm_settings()
    return {
        "ok": True,
        "has_own": bool(st.get("has_own")),
        "saved": st.get("saved", ""),
        "tail": st.get("tail", ""),
        "model": st.get("model", ""),
        "global_model": s.get("model", ""),
        "global_configured": llm.llm_enabled(),
        # 界面上要告诉用户"只能填这个网关签发的 key"，地址从配置里来，别在前端写死
        "gateway": s.get("base_url", ""),
        "can_save": True,
    }


class MyKeyIn(BaseModel):
    key: str = ""
    model: str = ""


@app.post("/api/my/llm/verify")
def my_llm_verify(body: MyKeyIn):
    """只验证不保存。前端可以先点「测试」确认 key + 模型好用。"""
    ok, msg = llm.verify_key(body.key, timeout=25, model=body.model)
    return {"ok": ok, "msg": msg}


@app.post("/api/my/llm")
def my_llm_set(body: MyKeyIn, request: Request):
    """保存我自己的 key。**先验证再用**：验证不过不保存，免得每次提问都失败。

    传空字符串 = 清除自己的 key，回落到公共 key。
    """
    u = _user_of(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    import userllm
    key = (body.key or "").strip()
    model = (body.model or "").strip()
    if not key:
        st = userllm.clear_key(u.get("id", ""))
        return {"ok": True, "msg": "已清除，改回使用公共 Key", **st}
    # 验证通过才存（模型为空则用全局模型）
    ok, why = llm.verify_key(key, timeout=25, model=model)
    if not ok:
        return JSONResponse({"ok": False, "msg": why}, status_code=400)
    try:
        st = userllm.set_key(u.get("id", ""), key, model=model)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"保存失败：{type(e).__name__}"},
                            status_code=500)
    return {"ok": True, "msg": "已保存，之后都使用你自己的 Key 和模型", **st}


class MyModelIn(BaseModel):
    model: str = ""


@app.post("/api/my/llm/model")
def my_llm_set_model(body: MyModelIn, request: Request):
    """只更新自定义模型，不用重新粘贴 Key。保存前用已存 Key 验证一次。"""
    u = _user_of(request)
    if not u:
        return JSONResponse({"ok": False, "msg": "未登录"}, status_code=401)
    import userllm
    uid = u.get("id", "")
    key = userllm.get_key(uid)
    if not key:
        return JSONResponse({"ok": False, "msg": "还没有配置自己的 Key"}, status_code=400)
    model = (body.model or "").strip()
    ok, why = llm.verify_key(key, timeout=25, model=model)
    if not ok:
        return JSONResponse({"ok": False, "msg": why}, status_code=400)
    try:
        st = userllm.set_key(uid, key, model=model)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"保存失败：{type(e).__name__}"},
                            status_code=500)
    return {"ok": True, "msg": "模型已更新", **st}


@app.get("/api/config/llm")
def get_llm_config(request: Request):
    u, err = _require_admin(request)
    if err:
        return err
    s = llm.llm_settings()
    return {"ok": True, "config": {
        key: s.get(key) for key in ("enabled", "protocol", "base_url", "model",
                                    "anthropic_version", "temperature", "max_tokens")
    }, "has_key": bool(s.get("api_key")), "configured": llm.llm_enabled()}


@app.post("/api/config/llm")
def set_llm_config(body: LlmCfg, request: Request):
    # 这是**全局**配置：改了所有人（没用自己 key 的人）都受影响，必须限管理员。
    # 之前这个接口没有权限校验，任何登录用户都能把全公司的 key 改掉。
    _u, err = _require_admin(request)
    if err:
        return err
    import json as _json
    path = Path(llm.CFG_PATH)
    cfg = _json.loads(path.read_text(encoding="utf-8"))
    llmd = cfg.setdefault("llm", {})
    llmd["enabled"] = body.enabled
    if body.protocol:                       llmd["protocol"] = body.protocol
    if body.base_url:                       llmd["base_url"] = body.base_url
    if body.model:                          llmd["model"] = body.model
    if body.temperature is not None:        llmd["temperature"] = body.temperature
    if body.max_tokens is not None:         llmd["max_tokens"] = body.max_tokens
    if body.api_key:                        llmd["api_key"] = body.api_key
    elif "api_key" not in llmd:             llmd["api_key"] = ""
    path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "configured": llm.llm_enabled(),
            "model": llmd.get("model", ""), "msg": "已保存并生效"}


# ---------------- 静态前端 ----------------

STATIC = APP_DIR / "static"


@app.get("/api/kb/{kb_id}/img")
def kb_image(kb_id: str, path: str, name: str):
    """按需返回文档内嵌图片。chunk 里的 [image1.png] 就靠这个变成真实图片。"""
    data, ext = imgserve.extract(kb_id, path, name)
    if not data:
        return Response(status_code=404, content=b"")
    return Response(content=data, media_type=imgserve.mime_of(ext),
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/")
def index_page():
    # 前端改版后必须让浏览器重新取，否则用户看到的还是缓存里的旧页面
    return FileResponse(STATIC / "index.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ---------------- 答辩演练 ----------------
# 新人上传自己的答辩 PPT，AI 扮演评委按 10 题问，答完给报告。
# 页签、出题、参考答案、报告的逻辑分别在 defense_page.py / defense_api.py。

async def _run_blocking(fn, *args, **kw):
    """把会阻塞的函数放到线程池里跑。

    答辩演练里出题/评分都要调大模型，一次十几秒。
    直接在 async 路由里调用会把整个事件循环卡住，别人连页面都打不开。
    """
    return await run_in_threadpool(lambda: fn(*args, **kw))


@app.get("/defense")
def defense_page(request: Request):
    u = _user_of(request)
    if u and not users.is_admin(u) and not users.perms_of(u).get("defense"):
        return Response(
            "<!doctype html><meta charset='utf-8'><h3>没有模拟答辩权限</h3>"
            "<p>请联系管理员开通「模拟答辩」权限。</p><p><a href='/'>返回首页</a></p>",
            media_type="text/html; charset=utf-8", status_code=403)
    return FileResponse(STATIC / "defense.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/favicon.ico")
def favicon():
    """浏览器默认会要这个，没有就刷一片 404 噪音。返回一个空图标即可。"""
    ico = STATIC / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico))
    return Response(status_code=204)


@app.post("/api/defense/upload")
async def defense_upload(request: Request, file: UploadFile = File(...)):
    """接收 PPT，解析出文字和图片。只解析不建库——答辩 PPT 是临时的。"""
    _u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    try:
        raw = await file.read()
    except Exception as e:
        return {"ok": False, "msg": f"读取文件失败：{type(e).__name__}"}
    meta, err = defense_api.parse_upload(file.filename or "答辩.pptx", raw)
    if err:
        return {"ok": False, "msg": err}
    # 解析完就后台预生成第一题；用户选模式/题数的时间正好覆盖模型耗时
    try:
        defense_api.prefetch_first_question(
            meta, cfg=_cfg_with_user_key(request), user_key=_user_llm_key(request))
    except Exception as e:
        print(f"[defense] 预生成第一题失败: {type(e).__name__}: {e}", file=sys.stderr)
    # 只回前端需要的字段，slides 里的 url 要能直接访问
    return {
        "ok": True,
        "id": meta["id"],
        "name": meta["name"],
        "pages": meta["pages"],
        "images": meta["images"],
        "chars": meta["chars"],
        "slides": [{"no": s["no"], "title": s["title"],
                    "has_image": s["has_image"], "images": s["images"]}
                   for s in meta.get("slides", [])],
    }


@app.post("/api/defense/recommend_kb")
async def defense_recommend_kb(request: Request):
    """根据 PPT 内容推荐知识库，前端预选但保留用户选择权。"""
    u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    import defense_ppt
    try:
        body = await request.json()
    except Exception:
        body = {}
    ppt_id = str(body.get("ppt_id") or "")
    meta = defense_ppt.load(ppt_id) if ppt_id else None
    if not meta:
        return {"ok": False, "msg": "PPT 解析结果已失效，请重新上传"}
    r = await _run_blocking(
        defense_api.recommend_kb_for_ppt, meta, u,
        cfg=_cfg_with_user_key(request), user_key=_user_llm_key(request))
    return {"ok": True, **r}


@app.get("/api/defense/ppt/{upload_id}/img/{filename}")
def defense_ppt_img(upload_id: str, filename: str):
    """答辩 PPT 里抽出来的图片。"""
    import defense_ppt
    p = defense_ppt.img_path(upload_id, filename)
    if not p:
        return Response(status_code=404, content=b"")
    ext = p.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
    return FileResponse(str(p), media_type=mime,
                        headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/defense/start")
async def defense_start(request: Request):
    """开始一次演练，出第 1 题。"""
    _u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    import defense_ppt
    body = await request.json()
    ppt_id = str(body.get("ppt_id") or "")
    meta = defense_ppt.load(ppt_id)
    if not meta:
        return {"ok": False, "msg": "PPT 解析结果已失效，请重新上传"}

    ukey = _user_llm_key(request)
    mode = str(body.get("mode") or "practice")
    try:
        total = int(body.get("total") or 0) or None
    except Exception:
        total = None
    # 用哪个知识库当依据。空串 = 默认业务手册；也要挡掉用户无权访问的库
    kb_id = str(body.get("kb_id") or "").strip()
    if kb_id:
        import defense_coach
        kb_ = indexer.get_kb(kb_id)
        if not kb_ or not users.can_read_kb(kb_, _current_user(request), "defense"):
            kb_id = defense_coach.manual_kb_id()
    else:
        import defense_coach
        kb_id = defense_coach.manual_kb_id()
    difficulty = str(body.get("difficulty") or "basic")
    sess = defense_api.new_session("anon", meta, mode=mode, total=total,
                                   kb_id=kb_id, difficulty=difficulty)
    q, err = await _run_blocking(defense_api.make_first_question, sess,
                                 cfg=_apply_thinking_choice(
                                     _cfg_with_user_key(request), body.get("thinking")),
                                 user_key=ukey)
    if err:
        defense_api.drop_session(sess["id"])
        return {"ok": False, "msg": err}
    return {"ok": True, "session": sess["id"], "question": q, "no": 1,
            "thinking": str(sess.get("last_thinking") or "")[:4000],
            "total": sess["total"], "mode": sess["mode"],
            "reveal": defense_api.MODES[sess["mode"]]["reveal"],
            "difficulty": sess["difficulty"],
            "difficulty_label": defense_api.DIFFICULTIES[sess["difficulty"]]["label"]}


@app.post("/api/defense/start_keywords")
async def defense_start_keywords(request: Request):
    """不上传 PPT：按学员输入的关键词匹配知识库，像导师一样提问。"""
    u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    try:
        body = await request.json()
    except Exception:
        body = {}
    keywords = str(body.get("keywords") or "").strip()
    if len(keywords) < 2:
        return {"ok": False, "msg": "请输入 2 个字以上的学习关键词"}
    if len(keywords) > 300:
        keywords = keywords[:300]
    mode = str(body.get("mode") or "practice")
    try:
        total = int(body.get("total") or 0) or None
    except Exception:
        total = None
    difficulty = str(body.get("difficulty") or "basic")
    kb_id = str(body.get("kb_id") or "").strip()
    kb_name = ""
    hits = []
    if kb_id:
        kb_ = indexer.get_kb(kb_id)
        if not kb_ or not users.can_read_kb(kb_, u, "defense"):
            return {"ok": False, "msg": "没有这个知识库的答辩权限"}
        kb_name = str(kb_.get("name") or "")
        hits = defense_api.search_kb_keywords(kb_id, keywords, topk=10)
    else:
        m = defense_api.match_kb_for_keywords(keywords, u)
        if not m.get("kb_id"):
            return {"ok": False,
                    "msg": m.get("reason") or "没有匹配到内容，请换一组关键词"}
        kb_id, kb_name, hits = m["kb_id"], m["name"], m["hits"]
    if not hits:
        return {"ok": False,
                "msg": f"「{kb_name or '知识库'}」里没有匹配到这些关键词，请换关键词或换库"}
    material = defense_api.keyword_material(hits, keywords)
    meta = {"id": "", "name": f"关键词练习：{keywords}", "pages": 0,
            "slides": [], "images": 0, "chars": len(material)}
    sess = defense_api.new_session(
        "anon", meta, mode=mode, total=total, kb_id=kb_id,
        difficulty=difficulty, material=material, keywords=keywords,
        source="keywords")
    q, err = await _run_blocking(defense_api.make_first_question, sess,
                                 cfg=_apply_thinking_choice(
                                     _cfg_with_user_key(request), body.get("thinking")),
                                 user_key=_user_llm_key(request))
    if err:
        defense_api.drop_session(sess["id"])
        return {"ok": False, "msg": err}
    return {"ok": True, "session": sess["id"], "question": q, "no": 1,
            "thinking": str(sess.get("last_thinking") or "")[:4000],
            "total": sess["total"], "mode": sess["mode"],
            "reveal": defense_api.MODES[sess["mode"]]["reveal"],
            "difficulty": sess["difficulty"],
            "difficulty_label": defense_api.DIFFICULTIES[sess["difficulty"]]["label"],
            "kb_id": kb_id, "kb_name": kb_name, "matched": len(hits),
            "source": "keywords", "keywords": keywords}


@app.post("/api/defense/answer")
async def defense_answer(request: Request):
    """记下这题的回答，出下一题；问满 10 题就结束。"""
    _u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    body = await request.json()
    sess = defense_api.get_session(str(body.get("session") or ""))
    if not sess:
        return {"ok": False, "msg": "演练会话已过期，请刷新页面重新开始"}

    q = str(body.get("question") or "")
    a = str(body.get("answer") or "")
    defense_api.add_answer(sess, q, a)
    total = defense_api.total_of(sess)
    answered = len(sess["history"]) - 1
    cfg = _apply_thinking_choice(_cfg_with_user_key(request),
                                 body.get("thinking") if isinstance(body, dict) else None)
    ukey = _user_llm_key(request)

    if len(sess["history"]) >= total:
        # 最后一题也后台诊断；用户看报告时逐题解析已基本就绪
        defense_api.prefetch_diagnosis(sess, answered, cfg=cfg, user_key=ukey)
        return {"ok": True, "done": True, "no": len(sess["history"]),
                "total": total, "mode": sess.get("mode", "practice")}

    nq, err = await _run_blocking(defense_api.make_question, sess, user_answer=a,
                                  cfg=cfg, user_key=ukey)
    # 下一题已经返回，再投后台诊断：不占用前台出题的模型并发
    defense_api.prefetch_diagnosis(sess, answered, cfg=cfg, user_key=ukey)
    if err:
        return {"ok": True, "done": True, "no": len(sess["history"]), "total": total}
    return {"ok": True, "done": False, "question": nq,
            "no": len(sess["history"]) + 1, "total": total,
            "thinking": str(sess.get("last_thinking") or "")[:4000],
            "reveal": defense_api.MODES[sess.get("mode") or "practice"]["reveal"]}


@app.post("/api/defense/reveal")
async def defense_reveal(request: Request):
    """某一题的参考答案 + 依据。答错了想看看标准答案走这里。"""
    _u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    body = await request.json()
    sess = defense_api.get_session(str(body.get("session") or ""))
    if not sess:
        return {"ok": False, "msg": "演练会话已过期，请刷新页面重新开始"}
    try:
        idx = int(body.get("index") or 0)
    except Exception:
        idx = 0
    return await _run_blocking(defense_api.explain, sess, idx,
                               cfg=_apply_thinking_choice(
                                   _cfg_with_user_key(request), body.get("thinking")),
                               user_key=_user_llm_key(request))


@app.post("/api/defense/report")
async def defense_report(request: Request):
    """整场演练的评分报告。"""
    _u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    body = await request.json()
    sess = defense_api.get_session(str(body.get("session") or ""))
    if not sess:
        return {"ok": False, "msg": "演练会话已过期，请刷新页面重新开始"}
    return await _run_blocking(defense_api.make_report, sess,
                               cfg=_apply_thinking_choice(
                                   _cfg_with_user_key(request), body.get("thinking")),
                               user_key=_user_llm_key(request))


@app.post("/api/defense/analysis")
async def defense_analysis(request: Request):
    """全部答完后，按需生成逐题解析 + 总体评价（复用整场问答）。"""
    _u, err = _require_defense(request)
    if err:
        return err
    import defense_api
    body = await request.json()
    sess = defense_api.get_session(str(body.get("session") or ""))
    if not sess:
        return {"ok": False, "msg": "演练会话已过期，请刷新页面重新开始"}
    if len(sess.get("history") or []) < defense_api.total_of(sess):
        return {"ok": False, "msg": "还没答完全部题目，答完后再生成解析报告"}
    return await _run_blocking(defense_api.make_analysis, sess,
                               cfg=_apply_thinking_choice(
                                   _cfg_with_user_key(request), body.get("thinking")),
                               user_key=_user_llm_key(request))


@app.get("/api/defense/stat")
def defense_stat():
    """管理/开发用：看答辩库导了多少条、手册库在不在。"""
    import defense
    import defense_coach
    try:
        s = defense.stats()
    except Exception as e:
        return {"ok": False, "msg": f"{type(e).__name__}"}
    return {"ok": True, "records": s.get("total", 0),
            "by_tag": s.get("top_tags", [])[:5],
            "batches": s.get("batches", {}),
            "manual_kb": defense_coach.manual_kb_id(),
            "ready": bool(s.get("total"))}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------- 启动 ----------------

def preload_kbs():
    """启动时后台预热所有知识库的 BM25 索引(首次提问省去 ~60s 加载)。

    业务手册优先加载（答辩演练默认用它），其余按注册表顺序。
    进度写进 _WARMUP，/api/warmup 给前端显示。
    """
    # 先恢复上次遗留的索引任务状态：服务重启时还标着 queued/running 的，
    # 实际已经不会继续跑了，不改成 interrupted 前端会一直转圈。
    try:
        n = indexer.recover_index_statuses()
        if n:
            print(f"[preload] 恢复索引状态: {n} 个任务标记为 interrupted",
                  file=__import__("sys").stderr, flush=True)
    except Exception as e:
        print(f"[preload] 恢复索引状态失败: {type(e).__name__}: {e}",
              file=__import__("sys").stderr, flush=True)

    # health 的 retrieval.resolved 由后台线程维护（探活路径不做文件 I/O）
    try:
        backend.start_refresher()
    except Exception as e:
        print(f"[preload] backend refresher 启动失败: {type(e).__name__}: {e}",
              file=__import__("sys").stderr, flush=True)

    # 启动时把向量一致性缓存算一次，health 的 embeddings 字段从启动起就有值。
    # 放在 preload 线程里，不在探活路径上数行数。
    try:
        import embeddings
        embeddings.consistency_report()
    except Exception as e:
        print(f"[preload] 向量一致性预热失败: {type(e).__name__}: {e}",
              file=__import__("sys").stderr, flush=True)

    try:
        reg = indexer.load_registry()
        reg.sort(key=lambda k: 0 if "业务手册" in (k.get("name") or "") else 1)
        with _WARMUP_LOCK:
            _WARMUP.update({"running": True, "ready": False, "total": len(reg),
                            "done": 0, "current": "", "started": time.time(),
                            "finished": 0, "errors": []})
        for kb_ in reg:
            kid = kb_.get("id")
            if not kid:
                continue
            with _WARMUP_LOCK:
                _WARMUP["current"] = kb_.get("name") or kid
            try:
                kb.load_kb_indexes(kid)
            except Exception as e:
                with _WARMUP_LOCK:
                    _WARMUP["errors"].append(f"{kid}: {type(e).__name__}")
                print(f"[preload] BM25 失败 {kid}: {e}", file=__import__("sys").stderr, flush=True)
            with _WARMUP_LOCK:
                _WARMUP["done"] += 1
        with _WARMUP_LOCK:
            _WARMUP.update({"running": False, "ready": True, "current": "",
                            "finished": time.time()})
        print(f"[preload] 已预热 {len(reg)} 个知识库的 BM25 索引",
              file=__import__("sys").stderr, flush=True)
    except Exception as e:
        with _WARMUP_LOCK:
            _WARMUP.update({"running": False, "ready": True, "current": "",
                            "finished": time.time()})
            _WARMUP["errors"].append(f"preload: {type(e).__name__}")
        print(f"[preload] 预热失败: {e}", file=__import__("sys").stderr, flush=True)


def main():
    import argparse, uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    args, _ = ap.parse_known_args()
    cfg = _cfg()["server"]
    port = args.port or int(cfg.get("port", 8000))
    host = cfg.get("host", "127.0.0.1")
    print(f"新人AI导师 已启动: http://{host}:{port}")
    print(f"LLM 已配置: {llm.llm_enabled()} (model={llm.llm_settings().get('model')})")
    print(f"LLM 未配置 API Key 时也可用(仅返回检索片段)。key 配置: app/config.json 的 llm 段 或 环境变量 RAG_LLM_API_KEY")
    threading.Thread(target=preload_kbs, daemon=True).start()
    # 限流名额的后台回收（客户端异常断开时靠它兜底）
    _start_slot_gc_thread()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
