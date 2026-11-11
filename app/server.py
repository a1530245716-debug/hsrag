# -*- coding: utf-8 -*-
"""智能交易平台 RAG · Web 服务(启动入口)

  python app/server.py                 # 启动,默认 http://127.0.0.1:8000
  python app/server.py --port 9000

- 路由 /api:知识库管理与问答
- 静态 /    :轻量单页前端
"""
import base64, html as _html, os, re, sys, shutil, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))
import indexer, kb, llm, auth, fallback, planner, users, qa_log, cache, imgserve, mailer

app = FastAPI(title="项目智能问答助手", version="1.0.0")

# ---------------- 模型 ----------------

class KbCreate(BaseModel):
    name: str = ""
    folder: str = ""

class AskIn(BaseModel):
    question: str
    top_k: int = 0
    model: str = ""      # 可选覆盖配置文件里的模型
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
                    if not users.can_read_kb(kb_, request.state.user):
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
    if not acfg["enabled"]:
        return {"ok": True, "enabled": False, "authenticated": True,
                "allow_fs_browse": acfg["allow_fs_browse"], "user": None}
    u = _user_of(request)
    return {"ok": True, "enabled": True,
            "authenticated": bool(u), "allow_fs_browse": acfg["allow_fs_browse"],
            "user": u}


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


def _grant_manual_kb(uid):
    """把「业务手册（O32/资管培训）」只读共享给指定用户。"""
    uid = str(uid or "")
    if not uid:
        return
    try:
        import defense_coach
        kid = defense_coach.manual_kb_id()
        if not kid:
            return
        with _DEFAULT_SHARE_LOCK:
            kb_ = indexer.get_kb(kid)
            if not kb_ or kb_.get("owner") == uid:
                return
            ids = users.shared_ids(kb_)
            if uid in ids:
                return
            ids.append(uid)
            users.set_shared_with(kb_, ids)
            indexer._update_registry(kb_)
    except Exception as e:
        print(f"[users] 默认知识库分享失败: {e}", file=sys.stderr)


def _share_default_kb(user):
    """新账号默认只读共享「业务手册（O32/资管培训）」。"""
    if not user or users.is_admin(user):
        return
    _grant_manual_kb(user.get("id"))


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
    try:
        u = users.create_account(email, body.password, name=body.name or email,
                                 role="user", note="自助注册")
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    _share_default_kb(u)
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


@app.post("/api/feedback")
def api_feedback(body: FeedbackIn, request: Request):
    u = _current_user(request)
    ok = qa_log.feedback(body.id, body.vote, u)
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
    if (body.perms or {}).get("defense"):
        _grant_manual_kb(nu["id"])
    return {"ok": True, "user": nu}


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
            _grant_manual_kb(uid)
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
    """返回一份把当前用户 key 合进去的配置副本。

    给那些"只收 cfg、没有 user_key 参数"的模块用（planner / fallback）。
    没配自己的 key 就原样返回全局配置。
    """
    cfg = _cfg()
    uk = _user_llm_key(request)
    if not uk:
        return cfg
    cfg = dict(cfg)
    llmd = dict(cfg.get("llm") or {})
    llmd["api_key"] = uk
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
def kb_list(request: Request):
    """列出知识库。管理员看全部(带归属信息), 普通用户只看自己的。"""
    u = _current_user(request)
    admin = users.is_admin(u)
    reg = indexer.load_registry()
    owner_names = {x["id"]: x.get("name", "") for x in users.load_users()}
    out = []
    for kb_ in reg:
        # 管理员看全部；普通用户看自己的 + 别人分享给他的
        if not admin and not users.can_read_kb(kb_, u):
            continue
        kb_["status"] = STATUS.get(kb_["id"], {}).get("running", False) and "indexing" or kb_.get("status", "")
        kb_["owner_name"] = owner_names.get(kb_.get("owner"), "")
        kb_["mine"] = (kb_.get("owner") == u.get("id"))
        # 不是自己的、又能看到 → 是别人分享的。前端据此显示"只读"标记、隐藏改名/删除
        kb_["shared"] = bool(not admin and not kb_["mine"])
        kb_["readonly"] = bool(kb_["shared"])
        kb_["shared_count"] = len(users.shared_ids(kb_))
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


class KbShareIn(BaseModel):
    users: list = []          # 要分享给哪些用户（id 列表，可多选；空 = 收回全部）


@app.post("/api/kb/{kb_id}/share")
def kb_set_share(kb_id: str, body: KbShareIn, request: Request):
    """管理员把知识库分享给多个人（只读：能提问、能看引用，不能改）。"""
    u, err = _require_admin(request)
    if err:
        return err
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)

    # 只接受真实存在的用户；顺便挡掉"分享给库的主人"这种没意义的项
    valid, skipped = [], []
    owner = kb_.get("owner")
    all_users = {x["id"]: x for x in users.load_users()}
    for uid in (body.users or []):
        uid = str(uid or "").strip()
        if not uid:
            continue
        if uid not in all_users:
            skipped.append(uid)
            continue
        if uid == owner:
            skipped.append(uid)
            continue
        if uid not in valid:
            valid.append(uid)

    users.set_shared_with(kb_, valid)
    indexer._update_registry(kb_)
    names = "、".join(all_users[x].get("name", x) for x in valid) or "（无）"
    print(f"[kb] 分享 {kb_id} -> {names} by {u.get('name')}")
    return {"ok": True, "shared_with": valid, "shared_names": names,
            "skipped": skipped, "kb": kb_}


@app.get("/api/kb/{kb_id}/share")
def kb_get_share(kb_id: str, request: Request):
    """看这个库分享给了谁。管理员可看任意库；普通人只能看自己的。"""
    u = _current_user(request)
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    if not users.can_read_kb(kb_, u):
        return JSONResponse({"ok": False, "msg": "无权访问"}, status_code=403)
    ids = users.shared_ids(kb_)
    names = {x["id"]: x.get("name", "") for x in users.load_users()}
    return {"ok": True, "shared_with": ids,
            "shared": [{"id": i, "name": names.get(i, i)} for i in ids],
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

    if sync:
        run()
        return {"ok": True, "stats": st.get("last_stats"), "status": "done"}
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "status": "started", "msg": "索引已后台启动"}


@app.post("/api/kb/{kb_id}/upload")
async def kb_upload(kb_id: str, request: Request, index: bool = False):
    """拖拽/批量上传文件到该库的 upload 目录。multipart: 多个同名 files 字段。
    文件名可含相对子路径(拖文件夹时前端按目录结构提交),从而保留原结构。"""
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
    up = indexer.upload_dir(kb_id)
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
        # 先写临时文件再原子改名, 避免传输中断留下半截文件被当成正常文档索引
        tmp = dest.with_name(dest.name + ".tmp")
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
    """
    out, cnt = [], {}
    for r in results:
        p = r.get("path") or ""
        if cnt.get(p, 0) >= max_per_path:
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


def _multi_search(kb_id, queries, topk, rc):
    """多路检索(查询拆解后用): 各路只做召回, 合并去重后**统一重排一次**。

    相比"每路各重排一次", 延迟从数十秒降到 2~3 秒。
    返回 None 表示无索引。
    """
    def _one(sub):
        return kb.hybrid_search(kb_id, sub, topk=topk,
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
    # 统一重排: 用原问题作为 query(最终要回答的仍是原问题)
    try:
        import rerank
        # 多留些候选，重排后再做文件去重，否则去重会把好结果挤没
        rr = rerank.rerank(queries[0], cand, topk=topk * 2)
        if rr:
            return _with_reserved(_diversify(rr, topk), reserved, topk)
    except Exception as e:
        print(f"[ask] 合并重排跳过: {e}")
    return _with_reserved(_diversify(cand, topk), reserved, topk)


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
  <div class="hitlabel">🎯 已定位到检索命中片段</div>
  {hl_esc}
</div>
<script>
(function(){{
  try {{
    var needle = {hl_json};
    if (needle && needle.length > 3) {{
      // 1) 找内容里能匹配的最长子串(去掉首尾空白)
      var pieces = [needle, needle.slice(0, 60), needle.slice(0, 40), needle.slice(0, 20)];
      var found = null;
      var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      var nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (var p = 0; p < pieces.length && !found; p++) {{
        var nd = pieces[p].trim();
        if (nd.length < 4) continue;
        for (var i = 0; i < nodes.length; i++) {{
          var n = nodes[i];
          var idx = n.nodeValue.indexOf(nd);
          if (idx >= 0) {{
            var span = document.createElement('mark');
            span.textContent = nd;
            var after = n.splitText(idx);
            var rest = after.splitText(nd.length);
            after.parentNode.replaceChild(span, after);
            found = span;
            break;
          }}
        }}
      }}
      var target = found || document.getElementById('hitCard');
      if (target) {{ setTimeout(function(){{ target.scrollIntoView({{block:'center', behavior:'smooth'}}); }}, 80); }}
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
  mark {{ background:#fde047; padding:1px 2px; border-radius:3px; }}
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
</style></head><body>
<div class="bar">
  <span>📄</span><span class="nm">{_html.escape(name)}</span>
  <span class="meta">{_html.escape(badge)}{(" · " + size_txt) if size_txt else ""}</span>
  <a class="btn ghost" href="{raw}" target="_blank" rel="noopener">🗎 原始文件</a>
  <a class="btn" href="{dwl}" download>⬇ 下载原文件</a>
</div>
<main>{hl_block}{content_html}</main>
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
    cfg = _cfg_with_user_key(request)
    rc = cfg.get("retrieval", {})
    top_k = body.top_k or rc.get("top_k", 8)
    # 多轮: 依赖上文的追问先改写成独立问题(改写后的句子用于检索与回答)
    hist = body.history or []
    rw = planner.rewrite_query(q, hist, cfg=cfg)
    q_eff = rw["q"] if rw["rewritten"] else q
    # 查询规划: 模糊/复合问题 → 拆成多个子查询, 多路检索后合并去重(原问题保底一路)
    plan_ = planner.plan_queries(q_eff, cfg=cfg)
    queries = plan_["queries"]
    if len(queries) > 1:
        results = _multi_search(kb_id, queries, top_k, rc)
    else:
        results = kb.hybrid_search(
            kb_id, q_eff, topk=top_k,
            enable_vector=rc.get("enable_vector", True),
            enable_rerank=rc.get("enable_rerank", True),
        )
    if results is None:
        return {"ok": True, "answer": "该知识库还没有索引,请先点\"重建索引\"。", "sources": []}

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

    sources = _reorder_for_listing(q, _build_sources(results, kb_id=kb_id))
    lcfg = llm.llm_settings(cfg)

    if not llm.llm_enabled(cfg=lcfg):
        # 无大模型配置:给出检索命中的原文片段
        lines = ["# 未配置大模型,以下为检索命中片段\n"]
        for i, s in enumerate(sources, 1):
            lines.append(f"{i}. **{s['path']}** (得分 {s['score']})\n   {s.get('text_full') or s['text']}")
        return {"ok": True, "answer": "\n\n".join(lines), "sources": sources, "llm": False}

    # 组提示词(用 _build_ask_context: 完整文本 + 标识信息不得截断的约束 + 多轮历史)
    system, msgs = _build_ask_context(sources, q_eff, hist, body.answer_len, body.images)
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

    qa_id = _log_ask(q, kb_id, request, answer, sources, "kb", False,
                     _t0, q_eff if rw["rewritten"] else "", plan_["split"], cfg)
    return {"ok": True, "answer": answer, "sources": sources, "llm": True, "qa_id": qa_id,
            "model": lcfg.get("model", ""), "source_type": "kb", "source_label": "知识库",
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


def _build_ask_context(sources, q, history=None, answer_len="normal", images=None):
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
    return sys_prompt, msgs


@app.post("/api/kb/{kb_id}/ask/stream")
def kb_ask_stream(kb_id: str, body: AskIn, request: Request):
    _u, err = _require_kb_perm(request)
    if err:
        return err
    from fastapi.responses import StreamingResponse
    import json as _json
    kb_ = indexer.get_kb(kb_id)
    if not kb_:
        return JSONResponse({"ok": False, "msg": "知识库不存在"}, status_code=404)
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
    cfg = _cfg_with_user_key(request)

    def sse(tag, payload):
        return f"data: {_json.dumps({'type': tag, **payload}, ensure_ascii=False)}\n\n"

    has_img = bool(body.images)

    def gen():
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
        rc = cfg.get("retrieval", {})
        topk_ = body.top_k or rc.get("top_k", 8)
        hist = body.history or []
        # 索引刚更新时会有一两分钟还在用旧索引, 这个状态要告诉用户,
        # 否则"刚加的文档搜不到"会被当成 bug
        try:
            _st = kb.state(kb_id)
            _stale = bool(_st.get("stale") or _st.get("loading"))
        except Exception:
            _stale = False
        # 首问先看缓存(追问依赖上下文, 不走缓存; 演示模式/换个回答也不走)
        # 带图更不能走缓存: 缓存键只有问题文本, 同一句话配不同图会拿到上一次的答案
        if not hist and not body.no_cache and not has_img:
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
        if rw["rewritten"]:
            yield sse("rewrite", {"q": q_eff, "original": q,
                                  "status": f"已结合上文理解你的问题：{q_eff}"})
        # 查询规划: 拆分模糊/复合问题后再检索
        plan_ = planner.plan_queries(q_eff, cfg=cfg)
        queries = plan_["queries"]
        if len(queries) > 1:
            yield sse("plan", {"queries": queries,
                               "status": f"已拆解为 {len(queries) - 1} 个子问题，正在多路检索…"})
            results = _multi_search(kb_id, queries, topk_, rc)
        else:
            yield sse("status", {"text": "正在检索知识库…"})
            results = kb.hybrid_search(
                kb_id, q_eff, topk=topk_,
                enable_vector=rc.get("enable_vector", True),
                enable_rerank=rc.get("enable_rerank", True),
            )
        if results is None:
            _log_ask(q, kb_id, request, "该知识库还没有索引", [], "none", True, _t_start, "", False, cfg)
            yield sse("answer", {"text": "该知识库还没有索引,请先点\"重建索引\"。"}); return

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

        sources = _reorder_for_listing(q_eff, _build_sources(results, kb_id=kb_id))
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
        system, msgs = _build_ask_context(sources, q_eff, hist, body.answer_len, body.images)
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
        try:
            # 带图的答案不写缓存：缓存键只有问题文本，会把"看图得出的结论"污染给下次纯文字提问
            if not hist and not rw["rewritten"] and not body.no_cache and not has_img:
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
        if _figures_enabled() and full and len(full) >= 60:
            try:
                import imgindex
                paths = {s.get("path") for s in (sources or []) if s.get("path")}
                _full, _srcs, _paths, _kb = full, list(sources or []), paths, kb_id
                _pool = _FIG_POOL

                def _calc():
                    try:
                        return imgindex.bind_to_answer(_kb, _full, source_paths=_paths,
                                                       sources=_srcs)
                    except Exception as _e:
                        print(f"[figures] 异常 {type(_e).__name__}: {_e}", file=sys.stderr)
                        return []

                fig_fut = _pool.submit(_calc)
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

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": llm.llm_enabled(),
            "llm": llm.llm_settings() | {"api_key": "***" if llm.llm_settings()["api_key"] else ""}}


@app.get("/api/warmup")
def warmup_status():
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
        "global_model": s.get("model", ""),
        "global_configured": llm.llm_enabled(),
        # 界面上要告诉用户"只能填这个网关签发的 key"，地址从配置里来，别在前端写死
        "gateway": s.get("base_url", ""),
        "can_save": True,
    }


class MyKeyIn(BaseModel):
    key: str = ""


@app.post("/api/my/llm/verify")
def my_llm_verify(body: MyKeyIn):
    """只验证不保存。前端可以先点「测试」确认 key 好用。"""
    ok, msg = llm.verify_key(body.key, timeout=25)
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
    if not key:
        st = userllm.clear_key(u.get("id", ""))
        return {"ok": True, "msg": "已清除，改回使用公共 Key", **st}
    # 验证通过才存
    ok, why = llm.verify_key(key, timeout=25)
    if not ok:
        return JSONResponse({"ok": False, "msg": why}, status_code=400)
    try:
        st = userllm.set_key(u.get("id", ""), key)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"保存失败：{type(e).__name__}"},
                            status_code=500)
    return {"ok": True, "msg": "已保存，之后都使用你自己的 Key", **st}


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
        if not kb_ or not users.can_read_kb(kb_, _current_user(request)):
            kb_id = defense_coach.manual_kb_id()
    else:
        import defense_coach
        kb_id = defense_coach.manual_kb_id()
    sess = defense_api.new_session("anon", meta, mode=mode, total=total, kb_id=kb_id)
    q, err = await _run_blocking(defense_api.make_question, sess,
                                 cfg=_cfg_with_user_key(request), user_key=ukey)
    if err:
        defense_api.drop_session(sess["id"])
        return {"ok": False, "msg": err}
    return {"ok": True, "session": sess["id"], "question": q, "no": 1,
            "total": sess["total"], "mode": sess["mode"],
            "reveal": defense_api.MODES[sess["mode"]]["reveal"]}


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

    if len(sess["history"]) >= total:
        return {"ok": True, "done": True, "no": len(sess["history"]),
                "total": total, "mode": sess.get("mode", "practice")}

    nq, err = await _run_blocking(defense_api.make_question, sess, user_answer=a,
                                  cfg=_cfg_with_user_key(request),
                                  user_key=_user_llm_key(request))
    if err:
        return {"ok": True, "done": True, "no": len(sess["history"]), "total": total}
    return {"ok": True, "done": False, "question": nq,
            "no": len(sess["history"]) + 1, "total": total,
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
                               cfg=_cfg_with_user_key(request),
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
                               cfg=_cfg_with_user_key(request),
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
                               cfg=_cfg_with_user_key(request),
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
    print(f"项目智能问答助手 已启动: http://{host}:{port}")
    print(f"LLM 已配置: {llm.llm_enabled()} (model={llm.llm_settings().get('model')})")
    print(f"LLM 未配置 API Key 时也可用(仅返回检索片段)。key 配置: app/config.json 的 llm 段 或 环境变量 RAG_LLM_API_KEY")
    threading.Thread(target=preload_kbs, daemon=True).start()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
