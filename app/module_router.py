# -*- coding: utf-8 -*-
"""业务模块路由与引用校验。

问题：O32 的「交易所指令 / 开放式基金 / 期货 / 固收」共享“下单、委托、指令”
这类动作词。只靠 BM25 + 向量，很容易把其他模块的培训页也召回；再叠加
“每份文件最多 2 个引用位”的多样性策略，就会变成“每份 PPT 各抓一点”。

这里做三件轻量的事（不依赖新模型）：
  1. 从问题里识别模块（交易所 / 开放式基金 / 期货 / 固收）；
  2. 检索后按模块过滤候选片段，必要时触发“模块未命中”而不是硬答；
  3. 生成后检查引用片段是否属于目标模块，跨模块引用给出警告。

模块词表是受控的：只认业务模块名和少量强别名，不把“下单/委托”这种
通用动作词当成模块，避免把正常问题误过滤。
"""
import re
from collections import OrderedDict

# 模块 -> 强别名（canonical 名本身也算命中）
MODULES = OrderedDict([
    ("交易所", ("交易所", "集中竞价", "竞价交易", "大宗交易")),
    ("开放式基金", ("开放式基金", "开基", "场外基金", "申购", "赎回", "认购")),
    ("期货", ("期货", "开仓", "平仓", "期货合约", "保证金")),
    ("固收", ("固收", "固定收益", "债券", "现券", "回购", "质押式回购")),
])

_CANON = tuple(MODULES)
_ALIASES = {m: tuple(a for a in aliases if a != m) for m, aliases in MODULES.items()}
_MODULE_TAG_RE = re.compile(r"【模块[:：]\s*([^】]+)】")
_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_MANUAL_MARKERS = ("用户手册", "操作手册", "使用手册", "操作说明", "基础用户手册", "manual")

# 片段级判定要比“问题级”严格：只出现“证券期货行业”“沪深交易所”
# 这种行业/机构名称，不代表这段内容在讲该业务模块。
# 必须同时出现业务动作词，才给片段打模块标签。
_ACTIONS = {
    "交易所": ("下单", "委托", "报单", "指令", "竞价", "撮合", "大宗交易"),
    "开放式基金": ("申购", "赎回", "认购", "下单", "委托", "净值"),
    "期货": ("开仓", "平仓", "合约", "保证金", "下单", "委托", "指令"),
    "固收": ("询价", "报价", "回购", "现券", "债券", "下单", "委托"),
}


def all_modules(text):
    """不受控的模块信号（canonical + 别名），只用于**文件名**判断。

    例如“7.交易所债券业务/…”同时包含交易所和债券，属于两个模块；
    文件名不能只按第一个 canonical 名判断，否则固收问题会被误杀。
    """
    q = (text or "").lower()
    out = []
    for m, aliases in MODULES.items():
        if m in q or any(a in q for a in aliases):
            out.append(m)
    return out


def strong_modules(text):
    """片段级的模块识别：模块名 + 业务动作词同时出现才算。

    避免把“证券期货行业交付总部”“沪深交易所/期货交易所”这类行业介绍
    误标成具体的期货/交易所业务页。
    """
    q = (text or "").lower()
    out = []
    for m, aliases in MODULES.items():
        terms = (m,) + aliases
        if not any(t in q for t in terms):
            continue
        if any(a in q for a in _ACTIONS.get(m, ())):
            out.append(m)
    return out

# 这些问题带“下单/委托”但没写模块时，不自动过滤；仍然走正常检索。
# 以后可以在前端加模块选择，把这里升级成主动澄清。
GENERIC_ORDER_WORDS = ("下单", "委托", "报单", "指令", "交易")


def detect_modules(text):
    """从问题里识别业务模块，返回 canonical 名列表。

    - 先认 canonical 名（交易所/开放式基金/期货/固收）；
    - 没有 canonical 名时才看强别名（申购/开仓/债券……）；
    - 多个模块同时出现 → 返回多个（对比类问题，不强行过滤成单模块）。
    """
    q = (text or "").lower()
    if not q:
        return []
    found = [m for m in _CANON if m in q]
    if found:
        return found
    scored = []
    for m, aliases in _ALIASES.items():
        n = sum(1 for a in aliases if a in q)
        if n:
            scored.append((m, n))
    if not scored:
        return []
    scored.sort(key=lambda x: -x[1])
    top = scored[0][1]
    winners = [m for m, n in scored if n == top]
    return winners if len(winners) == 1 else []


def is_manual_path(path):
    """用户手册/操作手册是跨模块的综合性文档，不能按“正文出现其他模块”整段丢弃。"""
    p = (path or "").lower()
    return any(m in p for m in _MANUAL_MARKERS)


def terms_for(modules):
    """模块对应的匹配词（canonical + 强别名）。"""
    out = set()
    for m in modules or ():
        if m in MODULES:
            out.add(m)
            out.update(_ALIASES.get(m, ()))
    return out


def tagged_module(record):
    """优先读取 chunk 前缀里的【模块：X】。"""
    text = (record.get("text_full") or record.get("text") or "")
    m = _MODULE_TAG_RE.search(text)
    if not m:
        meta = record.get("metadata") or {}
        v = (meta.get("module") if isinstance(meta, dict) else "") or ""
        if v:
            return v
        return ""
    tag = m.group(1).strip()
    for canon, aliases in MODULES.items():
        if canon in tag or any(a in tag for a in aliases):
            return canon
    return tag


def matches(record, modules):
    """片段是否属于给定模块。modules 为空表示不限制。

    单模块问题从严：路径或正文只要明确属于其他模块，就丢弃。
    典型误召回：期货培训 PPT 里有一页标题是“指令下达”，正文提到
    “交易所指令”和“期货指令”两个页签——按关键词它会命中“交易所”，
    但这一页其实是期货上下文，不能拿来回答交易所问题。
    """
    if not modules:
        return True
    tag = tagged_module(record)
    if tag:
        return tag in modules
    text = (record.get("text_full") or record.get("text") or "")
    path = (record.get("path") or "")
    # 文件名用 canonical 名即可（“14.期货/…”就是期货培训资料）；
    # 正文用强判定，必须有业务动作词，避免行业介绍误伤。
    path_mods = set(all_modules(path))
    text_mods = set(strong_modules(text))
    targets = set(modules)
    # 用户手册/操作手册天然同时覆盖多个模块；只要目标模块出现在页首/正文，
    # 就不因为“还提到了开放式基金/期货”而丢掉整段。
    # O32 基础用户手册就是这样：3.3 交易所指令 同一本手册里也有基金/回购章节。
    if is_manual_path(path):
        terms = {t.lower() for t in terms_for(modules)}
        head = text[:180].lower()
        if any(t in head for t in terms):
            return True
        if text_mods & targets and not (text_mods - targets):
            return True
        return False
    # 文件名明确属于其他模块（如 14.期货/...）→ 不参与单模块回答
    if path_mods and not (path_mods & targets):
        return False
    if len(targets) == 1:
        # 正文同时出现其他模块 → 视为歧义页；但如果文件名已经明确属于
        # 目标模块（如“14.期货/…”），以文件上下文为准，保留该页。
        if (text_mods - targets) and not (path_mods & targets):
            return False
        return bool(text_mods & targets or path_mods & targets)
    # 多模块对比：至少命中一个目标模块；其他非目标模块不额外加分
    return bool(text_mods & targets or path_mods & targets)


def filter_records(records, modules):
    """返回 (保留的片段, 被过滤掉的数量)。"""
    if not modules or not records:
        return records or [], 0
    kept, dropped = [], 0
    for r in records:
        if matches(r, modules):
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def miss_message(modules):
    mods = "、".join(modules or [])
    return (f"知识库里没有找到明确讲「{mods}」的内容（检索到的片段属于其他业务模块）。\n\n"
            "请确认你问的是哪个业务模块，或补充对应的操作手册/培训资料。")


def audit_citations(answer, sources, modules):
    """生成后检查引用片段是否属于目标模块；返回要追加到正文的警告。

    只做“模块一致性”检查，不做语义蕴含判断——目的是挡住最危险的
    “引用来自开放式基金/期货，却拿来回答交易所问题”。
    """
    if not modules or not answer or not sources:
        return ""
    nums = set()
    for m in _CITE_RE.finditer(answer or ""):
        for part in m.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                nums.add(int(part))
    bad = []
    for n in sorted(nums):
        if 1 <= n <= len(sources) and not matches(sources[n - 1], modules):
            bad.append(n)
    if not bad:
        return ""
    mods = "、".join(modules)
    refs = "、".join(f"[{n}]" for n in bad)
    return (f"\n\n---\n⚠️ 引用校验：引用 {refs} 来自其他业务模块，"
            f"可能与「{mods}」无关；请以明确属于该模块的引用为准。")
