# -*- coding: utf-8 -*-
"""知识库未命中时的兜底回答: 联网搜索(可选) / 大模型自身知识。

- source_type: "web"  => 联网搜索得到参考链接, 回答基于搜索结果
- source_type: "llm"  => 未联网/搜索失败, 直接由大模型自身知识回答(会诚实标注)

配置(config.json → fallback):
  {
    "fallback": {
      "enabled": true,
      "min_hits": 1,            // 知识库命中数低于此值触发兜底
      "web_search": {
        "enabled": true,        // 是否尝试联网搜索
        "provider": "duckduckgo",  // duckduckgo | bing(需 api_key)
        "api_key": "",          // bing 时填
        "max_results": 5
      }
    }
  }
"""
import sys, time
from urllib.parse import quote

APP_DIR = __import__("pathlib").Path(__file__).parent
sys.path.insert(0, str(APP_DIR))


def load_fb_cfg():
    import llm
    cfg = llm.load_config()
    fb = cfg.get("fallback", {})
    ws = fb.get("web_search", {})
    return {"enabled": bool(fb.get("enabled", True)),
            # true = 知识库没命中时先问用户"要不要联网/用通用知识"，不自动跑
            "ask_first": bool(fb.get("ask_first", True)),
            "min_hits": int(fb.get("min_hits", 1)),
            "web_enabled": bool(ws.get("enabled", True)),
            "provider": ws.get("provider", "duckduckgo"),
            "api_key": ws.get("api_key", ""),
            "max_results": int(ws.get("max_results", 5))}


def should_fallback(results):
    """results 为知识库检索结果列表; 命中数低于阈值则兜底。"""
    fbc = load_fb_cfg()
    if not fbc["enabled"]:
        return False
    n = len(results) if results else 0
    return n < fbc["min_hits"]


def ask_first(cfg=None):
    """知识库没命中时, 是不是先问用户再决定走哪条兜底路。

    默认 True：不再自作主张联网或拿模型自己的知识顶上——那些内容不可信，
    交付场景里"答得对吗"比"答得上吗"重要。
    """
    fb = ((cfg or {}).get("fallback") if cfg else None) or {}
    return bool(fb.get("ask_first", True))


def offer_options(cfg=None):
    """给前端的选择项: 哪些兜底路是开着的。"""
    fbc = load_fb_cfg()
    opts = []
    if fbc["web_enabled"]:
        opts.append({"id": "web", "label": "🌐 联网搜一下"})
    opts.append({"id": "llm", "label": "🧠 用大模型自己的知识回答"})
    return opts


# ---------------- 联网搜索 ----------------

_WEB_COOLDOWN_UNTIL = 0.0      # 联网失败冷却截止时间戳(避免每次提问都等超时)
_WEB_TIMEOUT = 4.0             # 搜索超时(秒), 内网不通时快速失败降级
_WEB_COOLDOWN = 60.0           # 失败后 60s 内不再尝试联网


def _ddg_search(query, max_results=5, timeout=_WEB_TIMEOUT):
    """DuckDuckGo Instant Answer API: 无需 key, 返回相关链接。"""
    import httpx
    url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_redirect=1"
    r = httpx.get(url, timeout=timeout, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    d = r.json()
    out = []
    for topic in (d.get("RelatedTopics") or [])[:max_results]:
        if "Topics" in topic:  # 分类节点, 取子项
            for sub in (topic.get("Topics") or [])[:1]:
                if sub.get("FirstURL") and sub.get("Text"):
                    out.append({"title": sub.get("Text", "")[:120],
                                "url": sub["FirstURL"], "snippet": sub.get("Text", "")[:200]})
        else:
            if topic.get("FirstURL") and topic.get("Text"):
                out.append({"title": topic.get("Text", "")[:120],
                            "url": topic["FirstURL"], "snippet": topic.get("Text", "")[:200]})
    return out


def _bing_search(query, api_key, max_results=5, timeout=4):
    import httpx
    url = "https://api.bing.microsoft.com/v7.0/search"
    r = httpx.get(url, params={"q": query, "count": max_results},
                  headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    out = []
    for it in (d.get("webPages", {}).get("value", []) or [])[:max_results]:
        out.append({"title": it.get("name", "")[:120],
                    "url": it.get("url", ""),
                    "snippet": it.get("snippet", "")[:200]})
    return out


def web_search(query):
    """返回 [{"title","url","snippet"}...]; 失败返回 None(上层降级为大模型知识)。"""
    global _WEB_COOLDOWN_UNTIL
    fbc = load_fb_cfg()
    if not fbc["web_enabled"]:
        return None
    if time.time() < _WEB_COOLDOWN_UNTIL:
        return None  # 冷却中, 直接降级为大模型知识(不再等待)
    provider = fbc["provider"]
    try:
        if provider == "bing" and fbc["api_key"]:
            return _bing_search(query, fbc["api_key"], fbc["max_results"])
        return _ddg_search(query, fbc["max_results"])
    except Exception as e:
        print(f"[fallback] web_search 失败: {e}", file=sys.stderr)
        _WEB_COOLDOWN_UNTIL = time.time() + _WEB_COOLDOWN
        return None


# ---------------- 兜底回答 ----------------

def _kb_sys_prompt(src_type, refs=None):
    if src_type == "web":
        return ("你是严谨的助手。下面提供从互联网搜索到的参考资料,请依据资料回答用户问题;"
                "资料不足时如实说明。回答末尾用列表列出参考链接。用中文。")
    return ("你是严谨的助手,请凭你的知识回答用户问题。"
            "若问题超出你的知识范围,请明确说明不确定;不要编造。用中文。")


def answer_fallback(question, mode="auto"):
    """知识库未命中时生成兜底回答。

    返回 {"ok", "answer", "source_type": "web"|"llm", "refs": [...] }
    refs 仅 web 时有值(参考链接); llm 时为空列表。
    """
    import llm
    src_type, user_msg, system, refs = prepare_fallback(question, mode=mode)
    answer = llm.chat([{"role": "user", "content": user_msg}], system=system)
    if not answer:
        return {"ok": False, "answer": None, "source_type": src_type, "refs": refs}
    return {"ok": True, "answer": answer, "source_type": src_type, "refs": refs}


def prepare_fallback(question, mode="auto"):
    """准备兜底上下文。返回 (src_type, user_msg, system, refs)。

    mode:
        "auto" 老行为：能联网就联网，联不上用大模型知识
        "web"  用户明确点了"联网搜一下" —— 搜不到就如实说没搜到，再用模型知识兜一下
        "llm"  用户明确点了"用大模型自己的知识" —— 不发起联网
    """
    import llm
    fbc = load_fb_cfg()
    refs = []
    src_type = "llm"
    if mode == "llm":
        hits = None
    else:
        hits = web_search(question) if fbc["web_enabled"] else None
    if mode == "web" and not hits:
        user_msg = (f"{question}\n\n（说明：用户选择了联网搜索，但这次没有取到任何网络结果，"
                    "请明确告诉用户没搜到，然后仅凭你自己的知识谨慎回答，并说明这部分不是知识库内容。）")
        return src_type, user_msg, _kb_sys_prompt(src_type, refs), refs
    if hits:
        refs = hits
        src_type = "web"
        ctx = "\n\n".join(f"[参考{i+1}] {r['title']}\n{r['snippet']}\n链接: {r['url']}"
                          for i, r in enumerate(hits[:5]))
        user_msg = f"【网络参考资料】\n{ctx}\n\n【问题】{question}\n\n请基于以上资料回答,并在结尾附上参考链接列表。"
    else:
        user_msg = question
    system = _kb_sys_prompt(src_type, refs)
    return src_type, user_msg, system, refs
