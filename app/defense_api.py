# -*- coding: utf-8 -*-
"""答辩演练的接口（挂到 server.py 里）。

流程（新人视角）：
    1. 上传自己的答辩 PPT          POST /api/defense/upload
    2. 开始演练，AI 出第 1 题       POST /api/defense/start
    3. 逐题回答，AI 追问/换题       POST /api/defense/answer
    4. 结束后看报告                 POST /api/defense/report

为什么要单独一个会话（Session）
------------------------------
演练是有状态的：问到第几题、问过哪些、学员怎么答的，都要记着。
存内存即可——一次演练就十分钟，服务重启就没了也不可惜（用户重来一遍）。
但要做上限，防止内存越用越多。
"""
import base64
import io
import re
import threading
import time
import uuid

# ---------------- 会话 ----------------
_SESS = {}
_LOCK = threading.RLock()
MAX_SESSIONS = 200
SESSION_TTL = 6 * 3600          # 6 小时不动作就清理

QUESTIONS_TOTAL = 10

# 两种模式：
#   practice 练习模式 —— 每题都能立刻看参考答案和依据，答错就学，适合自己练
#   exam     答辩模式 —— 全程不给答案，答完统一出报告，答完才能看每题解析，模拟真评委
MODES = {
    "practice": {"label": "练习模式", "reveal": True,  "default_n": 10,
                 "desc": "每题都能随时看参考答案和依据"},
    "exam":     {"label": "答辩模式", "reveal": False, "default_n": 10,
                 "desc": "全程不给答案，答完统一出报告，模拟真实答辩"},
}
# 可选题目数量。真答辩通常 10 题左右，练手可以少来几道。
ALLOWED_N = (3, 5, 10, 15, 20)


def _gc():
    now = time.time()
    dead = [k for k, v in _SESS.items() if now - v.get("ts", 0) > SESSION_TTL]
    for k in dead:
        _SESS.pop(k, None)
    # 还是太多就丢最老的
    if len(_SESS) > MAX_SESSIONS:
        for k in sorted(_SESS, key=lambda x: _SESS[x].get("ts", 0))[:len(_SESS) - MAX_SESSIONS]:
            _SESS.pop(k, None)


def get_session(sid):
    with _LOCK:
        s = _SESS.get(sid)
        if s:
            s["ts"] = time.time()
        return s


def new_session(uid, ppt_meta, mode="practice", total=None, kb_id=""):
    sid = uuid.uuid4().hex[:16]
    mode = mode if mode in MODES else "practice"
    n = total if total in ALLOWED_N else MODES[mode]["default_n"]
    with _LOCK:
        _gc()
        _SESS[sid] = {
            "id": sid,
            "uid": uid,
            "ppt": ppt_meta,
            "mode": mode,
            "total": n,
            # 用哪个知识库当依据；空串 = 默认的业务手册
            "kb_id": kb_id or "",
            "history": [],          # [{"q":..., "a":...}]
            "asked_pages": [],
            "started": time.strftime("%Y-%m-%d %H:%M"),
            "ts": time.time(),
            "done": False,
        }
    return _SESS[sid]


def drop_session(sid):
    with _LOCK:
        _SESS.pop(sid, None)


# ---------------- 文件解析 ----------------

def parse_upload(filename, raw):
    """把上传的 PPT 解析成结构。返回 (meta, err)。"""
    if not raw:
        return None, "文件是空的"
    if len(raw) > 60 * 1024 * 1024:
        return None, "文件太大了（限 60MB）"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in (".pptx", ".ppt"):
        return None, "只支持 PPT 文件（.pptx）"

    import defense_ppt
    import tempfile
    from pathlib import Path

    # 先落临时文件再解析——python-pptx 要按路径打开
    tmp = Path(tempfile.gettempdir()) / f"defense_{uuid.uuid4().hex[:8]}{ext}"
    try:
        tmp.write_bytes(raw)
        meta = defense_ppt.parse_pptx(tmp, keep=True)
        # 解析时用的是临时文件名，对外一律显示用户上传的原名
        meta["name"] = filename
        # meta.json 也改回去，报告里读到的是原名
        try:
            import json
            p = defense_ppt.store_dir() / meta["id"] / "meta.json"
            if p.exists():
                p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return meta, ""
    except Exception as e:
        return None, f"解析失败：{type(e).__name__}: {e}"
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


def parse_images(images):
    """前端传的贴图（data URL）转成给模型的块。用于"看图补充说明"。"""
    blocks = []
    for im in (images or [])[:3]:
        try:
            data = (im or {}).get("data") or ""
            if not data.startswith("data:image/"):
                continue
            head, _, b64 = data.partition(",")
            mime = head[5:].split(";")[0].lower()
            if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
                continue
            if len(b64) * 0.75 > 4 * 1024 * 1024:
                continue
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": mime, "data": b64}})
        except Exception:
            continue
    return blocks


# ---------------- 出题 ----------------

def make_question(sess, user_answer=None, images=None,
                  cfg=None, user_key="", timeout=120):
    """按当前会话状态出下一题。返回 (question, err)。"""
    import defense_coach

    ppt = sess.get("ppt") or {}
    material = defense_coach.material_from_ppt(ppt)
    if not material.strip():
        return "", "PPT 里没有提取到内容，换一份试试"

    history = sess.get("history") or []

    # 第一题优先挑有图的页——评委开场常常指着张图问
    focus = None
    if not history:
        withimg = [s for s in (ppt.get("slides") or []) if s.get("images")]
        if withimg:
            # 跳过封面页，从中间挑一页有图的
            pick = withimg[min(1, len(withimg) - 1)]
            focus = pick.get("no")
    elif sess.get("asked_pages"):
        # 追问时优先盯着刚刚那道题涉及的页，而不是 PPT 前几页的图
        focus = sess["asked_pages"][-1]

    q = defense_coach.next_question(
        material, history, user_answer=user_answer, cfg=cfg,
        user_key=user_key, timeout=timeout, ppt=ppt, focus_page=focus,
        kb_id=sess.get("kb_id") or "")

    if not q:
        return "", ("生成问题失败：大模型这次没有返回内容"
                    "（可能是模型思考超时或网关波动）。请重试；"
                    "多次出现可以在右上角填写自己的 Key。")

    # 记下问过的页，报告里统计覆盖面
    m = re.search(r"第\s*(\d{1,3})\s*页", q)
    if m:
        pno = int(m.group(1))
        if pno not in sess["asked_pages"]:
            sess["asked_pages"].append(pno)

    # 题目已生成，后台预生成参考答案；用户点「看参考答案」时大概率已经好了
    prefetch_explain(sess, len(history), q, cfg=cfg, user_key=user_key,
                     timeout=max(180, timeout))
    return q, ""


def total_of(sess):
    return int(sess.get("total") or QUESTIONS_TOTAL)


def add_answer(sess, question, answer):
    sess["history"].append({"q": question, "a": answer or ""})
    sess["ts"] = time.time()
    if len(sess["history"]) >= total_of(sess):
        sess["done"] = True


# ---------------- 报告 ----------------

def prefetch_explain(sess, index, question, cfg=None, user_key="", timeout=180):
    """题目一出来就在后台预生成参考答案，用户点「看参考答案」时直接复用。

    同一题只跑一次；如果用户点得比生成快，explain() 会等这次任务，不重复调模型。
    """
    key = str(index)
    with _LOCK:
        if key in (sess.get("explain") or {}):
            return
        jobs = sess.setdefault("explain_jobs", {})
        if key in jobs:
            return
        ev = threading.Event()
        jobs[key] = ev

    def run():
        try:
            import defense_coach
            ppt = sess.get("ppt") or {}
            r = defense_coach.reveal(
                question, None,
                material=defense_coach.material_from_ppt(ppt),
                ppt=ppt, cfg=cfg, user_key=user_key, timeout=timeout,
                kb_id=sess.get("kb_id") or "")
            if r.get("ok"):
                with _LOCK:
                    sess.setdefault("explain", {})[key] = r
        except Exception as e:
            print(f"[defense] 预生成参考答案失败: {type(e).__name__}",
                  file=__import__("sys").stderr)
        finally:
            with _LOCK:
                (sess.get("explain_jobs") or {}).pop(key, None)
            ev.set()

    threading.Thread(target=run, daemon=True, name=f"def-explain-{key}").start()


def explain(sess, index, cfg=None, user_key="", timeout=150):
    """给第 index 题（从 0 数）出参考答案 + 依据。

    学员答错了、或者想看看标准答案，点「看参考答案」调这个。
    结果缓存在会话里，同一题点第二次不再重新生成。
    """
    import defense_coach

    history = sess.get("history") or []
    if index < 0 or index >= len(history):
        return {"ok": False, "msg": "找不到这道题"}

    # 两种模式都能看答案，区别只在"什么时候能看"：
    #   practice 练习模式 —— 当前这题答完就能看
    #   exam     答辩模式 —— 全部答完才统一看，模拟真评委不给提示
    # 注意：答辩模式的限制只针对"这一题答完之后"。还没答的题谁也不能看，
    # 否则等于提前拿到答案。所以 index 必须小于已答题数（上面已校验）。
    mode = sess.get("mode") or "practice"
    if mode == "exam" and not sess.get("done"):
        left = total_of(sess) - len(history)
        return {"ok": False, "locked": True, "wait_done": True,
                "msg": f"答辩模式：答完 {total_of(sess)} 题后统一看解析，还剩 {max(0, left)} 题"}

    cache = sess.setdefault("explain", {})
    key = str(index)
    if key in cache:
        return cache[key]

    # 题目出现时已在后台预生成；如果还没跑完，等这次任务，不再重复调模型
    job = (sess.get("explain_jobs") or {}).get(key)
    if job:
        job.wait(timeout=180)
        with _LOCK:
            if key in cache:
                return cache[key]

    item = history[index]
    ppt = sess.get("ppt") or {}
    r = defense_coach.reveal(
        item.get("q", ""), item.get("a", ""),
        material=defense_coach.material_from_ppt(ppt),
        ppt=ppt, cfg=cfg, user_key=user_key, timeout=timeout,
        kb_id=sess.get("kb_id") or "")
    if r.get("ok"):
        cache[key] = r
    return r


def make_report(sess, cfg=None, user_key="", timeout=180):
    """生成演练报告：逐题打分 + 总体评价。"""
    import defense_coach

    ppt = sess.get("ppt") or {}
    material = defense_coach.material_from_ppt(ppt)
    history = sess.get("history") or []
    if not history:
        return {"ok": False, "msg": "还没有答题记录"}

    r = defense_coach.score(material, history, cfg=cfg, user_key=user_key,
                            timeout=timeout, kb_id=sess.get("kb_id") or "")
    if not r.get("ok"):
        return r

    r["ppt_name"] = ppt.get("name", "")
    r["pages"] = ppt.get("pages", 0)
    r["mode"] = sess.get("mode") or "practice"
    r["mode_label"] = MODES.get(r["mode"], {}).get("label", "练习模式")
    r["asked_pages"] = sorted(sess.get("asked_pages") or [])
    r["started"] = sess.get("started", "")
    r["duration"] = int(time.time() - (sess.get("ts") or time.time())) if sess.get("ts") else 0
    return r


def make_analysis(sess, cfg=None, user_key="", timeout=180):
    """整场答完后生成逐题解析；同一场只生成一次，结果缓存在会话里。"""
    if sess.get("analysis"):
        return sess["analysis"]
    import defense_coach
    ppt = sess.get("ppt") or {}
    r = defense_coach.analyze(
        defense_coach.material_from_ppt(ppt),
        sess.get("history") or [],
        cfg=cfg, user_key=user_key, timeout=timeout,
        kb_id=sess.get("kb_id") or "")
    if r.get("ok"):
        sess["analysis"] = r
    return r
