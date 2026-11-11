# -*- coding: utf-8 -*-
"""查询规划(Query Planning): 让"概括性强/描述模糊"的问题先被拆解再检索。

链路:  原问题 --[规则判定]--> 需要拆解? --[LLM]--> 1-4 个子查询
       多路 hybrid_search --> merge_results(合并去重) --> 统一 rerank --> 生成回答

设计要点:
  1. 规则先行: 短问题/单子句不拆解, 避免给每个问题都加一次 LLM 调用(延迟敏感)。
  2. 原问题保底: 返回的 queries 第一项永远是原问题, 拆解失败/拆偏也不会更差。
  3. 可开关: config.json -> retrieval.enable_planning (默认 True)。
"""
import json
import re
import sys

_CONJ = ("和", "与", "以及", "分别", "同时", "区别", "差别", "对比", "比较",
         "有什么不同", "各自", "还有", "并且", "或者", "怎么操作", "如何操作",
         "联动", "同步", "配合", "关系到", "能否批量", "批量处理")
_SPLIT_RE = re.compile(r"[，,。；;？?、\n]+")

# 拆解用的 LLM 短超时: 网关慢/挂起时快速放弃, 直接用原问题检索(不拖慢问答)
PLAN_TIMEOUT = float(__import__("os").environ.get("RAG_PLAN_TIMEOUT", "15"))

_SYS = (
    "你是检索查询规划助手。把用户问题拆解为 1-4 个独立、可单独检索的中文子查询。\n"
    "要求:\n"
    "1. 每个子查询都要带上关键术语(模块名/业务名词/动作), 不要用'它、这个、上述'等代词;\n"
    "2. 子查询之间不要重复, 各自覆盖问题的一个方面;\n"
    "3. 如果原问题本身已经足够具体、不需要拆解, 就只输出含原问题的单元素数组;\n"
    "4. 只输出 JSON 数组, 不要任何解释文字。\n"
    "5. 如果问题是「这个项目是做什么的/项目内容/项目介绍/项目概况」这类整体认知型问题, "
    "**必须**额外产出一个查询平台由哪些子系统模块组成的子查询(如\"智能交易平台 系统架构 模块组成\"), "
    "因为新接手的人首先要搞清楚这套系统由 OTRADE、OORDER、HIX 这些哪些部分拼起来, "
    "只查功能清单和项目进度是不够的;\n"
    "6. 如果问题同时问「进展/进度」和「内容/是什么」, 要把这两件事拆成不同子查询。\n"
    "示例输入: demo和oorder有什么区别\n"
    "示例输出: [\"demo 系统功能\", \"oorder 系统功能\", \"demo 与 oorder 的区别\"]\n"
    "示例输入: 我刚接手这个项目, 它目前进展如何、这个项目是什么\n"
    "示例输出: [\"项目当前进展 里程碑 状态\", \"项目功能范围 建设内容\", "
    "\"智能交易平台 系统架构 模块组成\"]"
)


def enabled(cfg=None):
    """是否启用查询规划(默认启用)。"""
    try:
        import llm
        c = cfg or llm.load_config()
        return bool(c.get("retrieval", {}).get("enable_planning", True))
    except Exception:
        return True


def need_split(q):
    """规则判断问题是否需要拆解: 多子句 / 含并列语义 / 超长。"""
    q = (q or "").strip()
    if len(q) < 10:
        # "这个项目是干嘛的" 这样的问句很短，但需要架构/功能/进展多路检索才答得全
        return _needs_arch_sub(q)
    clauses = [c for c in _SPLIT_RE.split(q) if len(c.strip()) >= 6]
    if len(clauses) >= 2:
        return True
    if any(k in q for k in _CONJ):
        return True
    return len(q) > 60


# 「这个项目是做什么的 / 项目内容 / 项目介绍」这类整体认知型问题，
# 光查功能清单和进度是答不全的 —— 新接手的人首先要弄清系统由哪些模块拼起来。
_PROJECT_INTRO = re.compile(
    r"(项目|平台|系统|这套)[^。？?]{0,8}(内容|介绍|概况|是什么|做什么|干什么|构成|组成)")
_ARCH_HINT = re.compile(r"(架构|模块|组成|子系统|由哪些|包含哪些|部件)")
_ARCH_SUB = "智能交易平台 系统架构 模块组成"


def _needs_arch_sub(q):
    return bool(_PROJECT_INTRO.search(q or ""))


def _parse_json_queries(text):
    """从大模型输出中提取 JSON 数组形式的子查询。"""
    if not text:
        return []
    m = re.search(r"\[.*?\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for x in (arr if isinstance(arr, list) else []):
        if isinstance(x, str):
            s = " ".join(x.split()).strip(" -\"'`。.")
            if len(s) >= 4 and s not in out:
                out.append(s)
    return out[:4]


def plan_queries(question, cfg=None):
    """生成检索查询列表。

    返回 {"queries": [...], "split": bool, "reason": str}
    queries[0] 始终是原问题(保底一路)。
    """
    q = (question or "").strip()
    res = {"queries": [q], "split": False, "reason": ""}
    if not q or not enabled(cfg):
        return res
    if not need_split(q):
        res["reason"] = "问题较具体, 无需拆解"
        return res
    try:
        import llm
        if not llm.llm_enabled(cfg=cfg):
            res["reason"] = "未配置大模型, 跳过拆解"
            return res
        ans = llm.chat(
            [{"role": "user", "content": f"用户问题：{q}\n\n请拆解为检索子查询："}],
            cfg=cfg, system=_SYS, timeout=PLAN_TIMEOUT,
        )
        subs = [s for s in _parse_json_queries(ans) if s.strip() != q]
        if not subs:
            res["reason"] = "拆解未产出有效子查询"
            return res
        # 兜底：整体认知型问题若模型没给出架构类子查询，这里补一条，
        # 避免回答只讲"功能建设有哪些"却说不清系统由什么组成
        if _needs_arch_sub(q) and not any(_ARCH_HINT.search(s) for s in subs):
            subs = subs + [_ARCH_SUB]
        res["queries"] = [q] + subs
        res["split"] = True
        res["reason"] = f"拆解为 {len(subs)} 个子查询"
    except Exception as e:
        res["reason"] = f"拆解失败: {e}"
        print(f"[planner] 拆解失败: {e}", file=sys.stderr)
    return res


# ---------------- 追问改写(多轮上下文) ----------------
# 追问特征词: 出现则说明该句依赖上文(指代/省略/继续)
_FOLLOW_RE = re.compile(
    r"(它|他|她|这个|那个|这些|那些|该|此|上面|刚才|前面|还有|再|另外|继续|展开|详细|"
    r"具体|举例|为什么|怎么|如何|呢|吗|那么|然后|接着)")

_REWRITE_SYS = (
    "你在做「追问改写」。把用户**最后一句**改写成一句不依赖上下文、能独立看懂的问题。\n"
    "规则:\n"
    "1. 把代词('它/这个/那个/该')换成上文里的具体名词, 补全被省略的对象;\n"
    "2. 把'再详细说说/展开讲讲/举个例子/还有呢'这类**纯指令**改写成完整的询问, "
    "不要原样保留指令词;\n"
    "3. 不要回答问题、不要解释、不要引号、不要多行, 只输出改写后的一句话;\n"
    "4. 若最后一句已完整, 或明显换了新话题, 就原样输出。\n"
    "示例:\n"
    "  上文: 用户问 demo 部署要准备哪些包\n"
    "  最后一句: 那它的下载地址呢  →  demo 部署包的下载地址是什么\n"
    "  最后一句: 再详细说说        →  demo 部署包的下载地址请再详细说明\n"
    "  最后一句: 举个例子          →  请举例说明 demo 部署包的使用方式"
)


def need_rewrite(q, history=None):
    """判断当前问题是否需要结合上文改写。

    规则: 有历史 + (短句 ≤12 字 或 含指代/追问词) → 需要。
    """
    q = (q or "").strip()
    if not q or not history:
        return False
    if len(q) <= 12:
        return True
    return bool(_FOLLOW_RE.search(q))


def _clean_rewrite(ans, fallback):
    """从模型输出里取第一行有效问句。"""
    if not ans:
        return None
    for line in str(ans).splitlines():
        s = line.strip().strip('"\'“”「」` ')
        s = re.sub(r"^(改写后[:：]?|问题[:：]?|输出[:：]?)\s*", "", s).strip()
        if len(s) >= 4:
            return s[:200]
    return None


def rewrite_query(question, history=None, cfg=None):
    """把依赖上文的追问改写成独立问题。

    返回 {"q": 用于检索/生成的问题, "rewritten": bool, "original": 原问题, "reason": str}
    - 无历史 / 不需要改写 / 改写失败 → q 就是原问题(绝不比不带上下文更差)。
    """
    q = (question or "").strip()
    res = {"q": q, "rewritten": False, "original": q, "reason": ""}
    if not q or not history or not enabled(cfg):
        return res
    if not need_rewrite(q, history):
        res["reason"] = "问题已可独立理解"
        return res
    try:
        import llm
        if not llm.llm_enabled(cfg=cfg):
            res["reason"] = "未配置大模型"
            return res
        hist = ""
        for h in history[-3:]:
            hist += f"用户: {(h.get('q') or '').strip()}\n"
            a = (h.get("a") or "").strip().replace("\n", " ")
            if a:
                hist += f"助手: {a[:300]}\n"
        ans = llm.chat(
            [{"role": "user",
              "content": f"【最近对话】\n{hist}\n【用户最后一句】\n{q}\n\n请输出改写后的一句话："}],
            cfg=cfg, system=_REWRITE_SYS, timeout=PLAN_TIMEOUT,
        )
        newq = _clean_rewrite(ans, q)
        if newq and newq != q:
            res.update(q=newq, rewritten=True, reason="已结合上文改写")
        else:
            res["reason"] = "改写无变化"
    except Exception as e:
        res["reason"] = f"改写失败: {e}"
        print(f"[planner] 追问改写失败: {e}", file=sys.stderr)
    return res


def _score(x):
    """取片段的最佳分数(rerank > fused > score), 允许负分。"""
    for k in ("rerank_score", "fused", "score"):
        v = x.get(k)
        if v is not None:
            return float(v)
    return 0.0


def merge_results(result_lists, topk=8):
    """多路检索结果合并去重, 按分数降序取 topk。

    去重键: 片段 id(有则用), 否则 path+chunk+text 前缀。
    """
    best = {}
    for results in (result_lists or []):
        for r in (results or []):
            rid = r.get("id") or f"{r.get('path','')}#{r.get('chunk','')}#{(r.get('text') or '')[:40]}"
            cur = best.get(rid)
            if cur is None or _score(r) > _score(cur):
                best[rid] = r
    return sorted(best.values(), key=lambda x: -_score(x))[:topk]
