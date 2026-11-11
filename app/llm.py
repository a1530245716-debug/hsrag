# -*- coding: utf-8 -*-
"""调用 OpenAI 兼容的大模型接口(local/cloud 通用)。
读取 config.json 的 llm 段;若未配置 api_key 则 llm 层被禁用,
上层可降级为仅返回检索上下文。所有函数不抛网络异常,失败返回 None。
"""
import json, os, sys, time
from pathlib import Path

import httpx

CFG_PATH = Path(__file__).with_name("config.json")

# ---------------- 超时策略 ----------------
# 网关偶发挂起: 不设总时长上限会让用户盯着转圈好几分钟。
# 分三段控制, 任何一段超了都快速失败并走降级(返回检索片段而非干等)。
#   connect: 建连      —— 网关不可达时秒级失败
#   read:    两次收包间隔 —— 主要防"首 token 迟迟不来"
#   total:   整段生成   —— 防"吐一半卡住"
def _f(env, default):
    try:
        return float(os.environ.get(env, default))
    except Exception:
        return float(default)


def _cfg_or_env(cfg_key, env_key, default):
    """优先 config.json 的 llm.<cfg_key>, 其次环境变量, 最后默认值。"""
    try:
        v = load_config().get("llm", {}).get(cfg_key)
        if v not in (None, ""):
            return float(v)
    except Exception:
        pass
    return _f(env_key, default)


def connect_timeout():
    return _cfg_or_env("connect_timeout", "RAG_LLM_CONNECT_TIMEOUT", 10)


def read_timeout():
    return _cfg_or_env("read_timeout", "RAG_LLM_READ_TIMEOUT", 60)


def total_timeout():
    return _cfg_or_env("total_timeout", "RAG_LLM_TOTAL_TIMEOUT", 180)


def default_timeout():
    """非流式请求的默认超时(整次调用)。"""
    return _cfg_or_env("timeout", "RAG_LLM_TIMEOUT", 180)


def _sse_timeout():
    return httpx.Timeout(connect=connect_timeout(), read=read_timeout(),
                         write=15.0, pool=10.0)


def load_config():
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def llm_settings(cfg=None, user_key=""):
    """组装一份可用的 LLM 设置。

    `user_key` 是该用户自己申请的 key（见 userllm.py）。给了就用它，
    没有就回落到 config.json / 环境变量的全局 key。

    为什么要按人分 key：公司网关**每个 key 只有约 2 路并发**，
    所有人挤同一把 key 会互相排队；各用各的就不互相影响。
    """
    cfg = cfg or load_config()
    llm = dict(cfg.get("llm", {}))
    # 环境变量可覆盖 key/base_url/model,便于部署时不动配置文件
    llm["api_key"] = llm.get("api_key") or os.environ.get("RAG_LLM_API_KEY", "")
    llm["base_url"] = os.environ.get("RAG_LLM_BASE_URL") or llm.get("base_url", "")
    llm["model"] = os.environ.get("RAG_LLM_MODEL") or llm.get("model", "")
    # 用户自己的 key 优先级最高（只换 key，base_url/model 仍走全局，避免用户配错）
    uk = (user_key or "").strip()
    if uk:
        llm["api_key"] = uk
        llm["_key_source"] = "user"
    else:
        llm["_key_source"] = "global"
    return llm


def llm_enabled(cfg=None):
    if cfg is None:
        s = llm_settings()
    elif "llm" in cfg:
        s = llm_settings(cfg)
    else:
        s = cfg  # 已是一份设置字典(调用方可能传了 llm_settings() 的结果)
    return bool(s.get("enabled")) and bool(s.get("base_url") and s.get("api_key") and s.get("model"))


def _chat_openai(s, msgs, timeout=None, meta=None):
    url = s["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": s["model"], "messages": msgs,
               "temperature": float(s.get("temperature", 0.3)),
               "max_tokens": int(s.get("max_tokens", 1024)), "stream": False}
    r = httpx.post(url, json=payload,
                   headers={"Authorization": f"Bearer {s['api_key']}",
                            "Content-Type": "application/json"},
                   timeout=float(timeout or default_timeout()))
    data = _parse_json_or_raise(r)
    ch = (data.get("choices") or [{}])[0]
    if meta is not None and ch.get("finish_reason") == "length":
        meta["stop_reason"] = "max_tokens"
    return ch.get("message", {}).get("content") or ""


def _parse_json_or_raise(r, tag=""):
    """把网关响应解析成 JSON。

    内网网关不稳定时会返回空 body 或 HTML 错误页，直接 r.json() 会抛
    "Expecting value: line 1 column 1 (char 0)"，看不出到底发生了什么。
    这里统一转成带状态码和响应片段的异常，便于日志定位。
    """
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        ct = r.headers.get("content-type", "")
        raise RuntimeError(f"网关返回非 JSON({ct or '无类型'}): {r.text[:300]}")


def _chat_anthropic(s, msgs, timeout=None, meta=None):
    """Anthropic Messages 协议(公司内网代理 /v1/messages 常用)。"""
    url = s["base_url"].rstrip("/") + "/v1/messages"
    payload = {"model": s["model"],
               "max_tokens": int(s.get("max_tokens", 1024)),
               "temperature": float(s.get("temperature", 0.3)),
               "messages": msgs}
    r = httpx.post(url, json=payload,
                   headers={"Authorization": f"Bearer {s['api_key']}",
                            "anthropic-version": s.get("anthropic_version", "2023-06-01"),
                            "Content-Type": "application/json"},
                   timeout=float(timeout or default_timeout()))
    data = _parse_json_or_raise(r)
    if meta is not None and data.get("stop_reason"):
        meta["stop_reason"] = data["stop_reason"]
    if isinstance(data.get("content"), list):
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
    err = data.get("error", {}).get("message", str(data))
    raise RuntimeError(f"{data.get('error')}: {err}")


def chat(messages, cfg=None, system=None, timeout=None, meta=None, user_key=""):
    """messages: [{role, content}...] 或单个消息字符串。返回完整回答文本或 None。

    timeout:  可选超时秒数(默认取 RAG_LLM_TIMEOUT 或 180)。短任务(如查询拆解)应传小值。
    meta:     可选 dict，回填 stop_reason；为 "max_tokens" 说明被输出上限截断。
    user_key: 用户自己的 key，给了就用它（见 userllm.py）。
    """
    s = llm_settings(cfg, user_key=user_key)
    if not llm_enabled(cfg=s):
        return None
    if isinstance(messages, str):
        msgs = [{"role": "user", "content": messages}]
    else:
        msgs = messages
    if system:
        msgs = [{"role": "system", "content": system}] + msgs
    # 本机 conda 的 SSL_CERT_FILE 指向不存在的证书,会令 httpx 初始化崩溃,清掉走系统默认
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)
    # 短任务(查询拆解/标题生成)偶发失败会连带影响主流程,重试两次成本很低。
    # 网关偶尔 200 但 content 为空（公共 Key 排队/思考链吃满额度），
    # 这种不会抛异常，也必须重试，否则上层会误报“大模型没返回内容”。
    last_err = None
    md = meta if meta is not None else {}
    s = dict(s)
    for attempt in range(3):
        try:
            if s.get("protocol", "openai") == "anthropic":
                out = _chat_anthropic(s, msgs, timeout=timeout, meta=md)
            else:
                out = _chat_openai(s, msgs, timeout=timeout, meta=md)
            if out is not None and str(out).strip():
                return out
            last_err = RuntimeError("网关返回空内容")
            # 思考链把输出额度吃光时，下一次给多一点额度
            if md.get("stop_reason") == "max_tokens":
                try:
                    s["max_tokens"] = min(max(int(s.get("max_tokens") or 1024) * 2, 2048), 8192)
                except Exception:
                    pass
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    print(f"[llm] 调用失败(已重试 2 次): {last_err} stop={md.get('stop_reason')}", file=sys.stderr)
    return None


def verify_key(key: str, cfg=None, timeout: int = 25):
    """验证一把 key 能不能用。返回 (ok, 说明)。

    用户在界面上填自己的 key 时先跑这个：填错了当场告诉他，
    而不是等他提问失败才发现。用完立刻丢弃，不写任何地方。
    """
    key = (key or "").strip()
    if not key:
        return False, "key 不能为空"
    s = llm_settings(cfg, user_key=key)
    if not s.get("base_url") or not s.get("model"):
        return False, "系统还没配置 base_url / model，无法验证"
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)
    try:
        if s.get("protocol", "openai") == "anthropic":
            txt = _chat_anthropic(s, [{"role": "user", "content": "回复 OK 两个字"}],
                                  timeout=timeout)
        else:
            txt = _chat_openai(s, [{"role": "user", "content": "回复 OK 两个字"}],
                               timeout=timeout)
    except Exception as e:
        return _explain_err(str(e))
    if txt is None:
        return False, "网关没有返回内容，key 可能无效"
    return True, "key 可用"


def _explain_err(err: str):
    """把网关错误翻成人话。"""
    e = err or ""
    low = e.lower()
    if "401" in e or "403" in e or "unauthorized" in low or "invalid" in low:
        return False, "key 无效或无权限（401/403）"
    if "429" in e or "rate" in low or "too many" in low:
        return False, "调用太频繁（429），稍后重试"
    if "404" in e or "not found" in low:
        return False, "模型名或接口地址不对（404）"
    if "timeout" in low or "timed out" in low:
        return False, "网关响应超时，请稍后重试"
    return False, f"验证失败：{e[:120]}"


def stream(messages, cfg=None, system=None, meta=None, retries=3, user_key=""):
    """同 chat 但逐 token 产出文本(generator)。调用前请先判断 llm_enabled()。

    retries: 网关抖动(503/502/空响应/连接失败)时的自动重试次数。
    只在"一个字都还没吐出来"时重试——已经输出过内容的，重试会造成重复的半截回答。
    内网网关实测有相当比例的偶发失败，没有重试的话用户看到的就是"大模型无输出"。
    """
    s = llm_settings(cfg, user_key=user_key)
    if not llm_enabled(cfg=s):
        return
    if isinstance(messages, str):
        msgs = [{"role": "user", "content": messages}]
    else:
        msgs = messages
    if system:
        msgs = [{"role": "system", "content": system}] + msgs
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)

    last_err = None
    for attempt in range(retries + 1):
        got = False
        try:
            if s.get("protocol", "openai") == "anthropic":
                gen = _stream_anthropic(s, msgs, meta)
            else:
                gen = _stream_openai(s, msgs, meta)
            for piece in gen:
                got = True
                yield piece
            if got or (meta is not None and meta.get("timeout")):
                return                       # 正常跑完，或主动超时截断
            last_err = RuntimeError("网关返回空流(没有任何文本)")
        except httpx.ReadTimeout:
            if meta is not None:
                meta["timeout"] = "read"
            last_err = RuntimeError(f"等待首个响应超过 {read_timeout():.0f}s")
        except httpx.ConnectTimeout:
            if meta is not None:
                meta["timeout"] = "connect"
            last_err = RuntimeError(f"连接网关超时({connect_timeout():.0f}s)")
        except Exception as e:
            last_err = e

        if got:
            # 已经吐过内容，不能再重试
            if meta is not None and not meta.get("error") and not meta.get("timeout"):
                meta["error"] = f"{type(last_err).__name__}: {last_err}"
            print(f"[llm] 流式中途失败(已输出, 不重试): {last_err}", file=sys.stderr)
            return

        if attempt < retries:
            # "网关返回空流" 多半是上游瞬时过载，间隔太密会继续撞同一堵墙
            wait = 2.0 * (attempt + 1)
            print(f"[llm] 第 {attempt + 1} 次失败: {last_err}；{wait:.1f}s 后重试", file=sys.stderr)
            time.sleep(wait)

    if meta is not None and not meta.get("error"):
        meta["error"] = f"{type(last_err).__name__}: {last_err}"
    print(f"[llm] 流式最终失败(已重试 {retries} 次): {last_err}", file=sys.stderr)


def _stream_anthropic(s, msgs, meta=None):
    url = s["base_url"].rstrip("/") + "/v1/messages"
    payload = {"model": s["model"], "max_tokens": int(s.get("max_tokens", 1500)),
               "temperature": float(s.get("temperature", 0.3)),
               "messages": msgs, "stream": True}
    headers = {"Authorization": f"Bearer {s['api_key']}",
               "anthropic-version": s.get("anthropic_version", "2023-06-01"),
               "Content-Type": "application/json"}
    limit = total_timeout()
    t0 = time.time()
    with httpx.stream("POST", url, json=payload, headers=headers,
                      timeout=_sse_timeout()) as r:
        if r.status_code >= 400:
            body = r.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {r.status_code}: {body}")
        for line in r.iter_lines():
            if time.time() - t0 > limit:
                if meta is not None:
                    meta["timeout"] = "total"
                print(f"[llm] 流式生成超过 {limit:.0f}s 上限, 提前结束", file=sys.stderr)
                return
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if obj.get("type") == "content_block_delta":
                d = obj.get("delta", {})
                if d.get("type") == "text_delta" and d.get("text"):
                    yield d.get("text")
            elif obj.get("type") == "message_delta":
                # 记录停止原因：stop_reason=max_tokens 说明额度用尽、回答被硬截断
                sr = (obj.get("delta") or {}).get("stop_reason")
                if sr and meta is not None:
                    meta["stop_reason"] = sr
            elif obj.get("type") == "error":
                # 网关在流中间报错(常见于额度/额度用完/上游 5xx)
                msg = (obj.get("error") or {}).get("message") or json.dumps(obj, ensure_ascii=False)
                raise RuntimeError(f"流中错误: {msg[:200]}")


def _stream_openai(s, msgs, meta=None):
    url = s["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": s["model"], "messages": msgs,
               "temperature": float(s.get("temperature", 0.3)),
               "max_tokens": int(s.get("max_tokens", 1500)), "stream": True}
    headers = {"Authorization": f"Bearer {s['api_key']}", "Content-Type": "application/json"}
    limit = total_timeout()
    t0 = time.time()
    with httpx.stream("POST", url, json=payload, headers=headers,
                      timeout=_sse_timeout()) as r:
        if r.status_code >= 400:
            body = r.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"HTTP {r.status_code}: {body}")
        for line in r.iter_lines():
            if time.time() - t0 > limit:
                if meta is not None:
                    meta["timeout"] = "total"
                print(f"[llm] 流式生成超过 {limit:.0f}s 上限, 提前结束", file=sys.stderr)
                return
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:
                continue
            delta = obj.get("choices", [{}])[0].get("delta", {})
            if delta.get("content"):
                yield delta["content"]


if __name__ == "__main__":
    print("enabled:", llm_enabled())
    print("settings:", llm_settings())
