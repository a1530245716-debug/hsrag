# -*- coding: utf-8 -*-
"""AI 模拟评委：出题、追问、打分。

这是答辩质检的核心。目标不是"聊天机器人"，而是**让新人提前经历一遍答辩现场**：

    新人交阶段总结材料
        ↓
    AI 扮演评委，结合材料和历史评委的真实问法出题
        ↓
    新人作答 → AI 追问（像真评委那样逮住回答里的薄弱处）
        ↓
    答完若干轮 → 按评分标准打分，并指出"哪里讲漏了、历史答辩里别人踩过什么坑"

为什么每一步都要引用历史记录
----------------------------
公司的评委是固定的那几个人，问题风格有惯性。让模型"凭空想问题"会问出不痛不痒的
通用题（"请介绍一下你的工作"），新人练了也没用。把历史答辩里真实问过的问题检索出来
喂给模型，它才能问出**这家公司评委真正关心什么**。

评分一定要有明确标准
--------------------
`criteria.json` 里是领导给的标准。**没配标准就不打分**，只给评语——
让模型自己编一套评分维度，打出来的分不可信，反而害了新人。
"""
import json
import re

import defense
import defense_search

# ---------------- 提示词 ----------------

_ASK_SYSTEM = """你是一场新人答辩会的评委，正在面试一位即将独立承担交付任务的工程师。

你手上有三份材料，出题必须**同时**用上：
- 【新人的答辩材料】：他这次讲的内容。问题要**针对他讲的东西**，不要跑题。
- 【历史答辩参考】：这家公司真评委问过的原题和导师点评。**照着这个风格和关注点问**。
- 【业务手册知识点】：O32/资管业务的标准说法（股债基、回购、风控、清算…）。
  问题要落在这些业务范围内，**用手册里的术语**，这样新人答出来才对得上公司的实际要求。

出题要求：
1. **紧扣材料的某一处具体内容**。他讲了自己做了什么，就挑里面最容易出错、
   或者最容易含糊带过的环节来问。材料写得笼统的地方，就追问具体怎么做的。
   绝对不要问"请介绍一下你的工作"这种空泛问题。
2. **用真评委的口吻**（参照历史答辩参考）：直接、具体、带一点压力，但不要刁难。
   真评委爱问的是"为什么这么配""出问题怎么回滚""这个环节谁校验"这类。
3. **问业务，不只问流程**。能落到手册知识点（如质押券折算率、清算交收时点、
   风控校验层级）就问业务，这样才检验真实水平。
4. **结合 PPT 的具体页面**。材料里标了【第N页】，如果某页有图（流程图/界面截图/架构图），
   可以就那一页问，例如"你第 6 页画的那个流程，第三步为什么要这样"。
   不要笼统地问"你 PPT 讲了什么"。
5. 一次只问一个问题，不要一口气抛出好几个小问题。
6. **控制长度：一句话到两句话，60 字左右，最多不超过 120 字。**
   真评委开口就是一两句，不会写一大段。问之前先在脑子里砍掉多余的解释和背景铺陈。

引用【业务手册知识点】时的规矩：
- **不要编页码或章节号**（你没有准确页码），说"手册里提到…"就行。
- **表名、字段名只能用手册原文里出现过的**。手册里没写的就别说，换个说法。

只输出问题本身，不要任何前缀、编号或解释。"""

_FOLLOW_SYSTEM = """你是一场新人答辩会的评委，刚才问了对方一个问题，现在他回答完了。

根据他的回答，判断下一步：

- 回答里有**明显含糊、回避、或讲错**的地方 → 针对那个点继续追问，像真评委那样
  抓住不放。追问要具体，指出他刚才哪句话没讲清楚。
  如果【业务手册知识点】里给出了标准说法，而他讲的与之不符，就**用手册里的说法去纠正他**。
- 这个点已经问透了 → 换一个新角度，仍然围绕他的答辩材料。
  新角度优先落在【业务手册知识点】覆盖的业务范围里。

追问的分寸：
- **尖锐但有分寸**。可以说"这个点你还没讲到""我追问的是具体哪一处"，
  但**不要**指责对方的动机，比如"你是不是想绕过去""你是不是没听清"这类揣测。
- 对方如果明显答偏了，就**平静地把他拉回来**："先回到刚才那个问题——"。
- **一次只问一个点**。不要在一句话里塞三四个小问，也不要列 a/b/c 选项逼他选。
  真评委一次只问一句，问不出再补。
- **控制长度：一句话，60 字左右，最多不超过 120 字。** 别写一大段。
- **一个问题最多追问 2 次**。如果对方两轮都答不到点子上，说明超出他的水平了，
  换一个新方向重新问，不要一直卡在同一个点上反复逼问。

如果对方把某个概念理解错了（比如把"回滚"理解成"对账"），
**先一句话点明他错在哪**，再问，这样他才学得到东西：
"你说的对账和这里的回滚不是一回事——回滚是把白天已生成的流水撤掉重做。"

引用【业务手册知识点】时的规矩：
- **不要编页码或章节号**（你没有准确页码），说"手册里提到…"就行。
- **表名、字段名只能用手册原文里出现过的**。手册里没写的，就别说，
  用"那张记录持仓的表"这类说法代替。编一个像模像样的表名最坑人——
  新人会照着背下来记错。

参照【历史答辩参考】里真评委的追问方式：直接、具体、带一点压力。

只输出下一步要问的问题本身，不要任何前缀、编号或解释。"""

_SCORE_SYSTEM = """你是答辩评委，正在给这位新人的**每一个回答**打分。

规则：**每题按 100 分制打分**。题目数量由本次实际问答决定；
最终报告会按题数折算成 100 分制（例如选 3 题，每题贡献约 33.3 分，总分 100）。

打分尺度（按这个，**既不要手软，也不要苛刻**）：
- 90-100 分：答得准确、完整，讲清了原因和影响，能应对追问
- 70-89 分：主要点答到了，个别环节含糊或有小错
- 50-69 分：**答对了一部分**，但只到表面、漏了关键环节，或只背结论说不出原因
- 30-49 分：大部分跑偏，但有沾边的内容
- 0-29 分：答非所问 / 完全不知道 / 敷衍（"是的""我同意""不清楚"）

**评分分布要求（这条最重要）**：
整场打完，分数必须**拉开档次**，不能挤在 0-20 分里。
正常一个准备不足的新人，大致应该是 30、40、50、60 分这样分布，个别 10-20 分。

逐档对照（严格照这个给）：
- **方向对、说得浅** → 50-69 分。比如问题问"怎么排查"，他答对一个步骤但没讲全。
- **事实正确但没答到问的点** → 30-49 分。
  例如问"费用不一致去哪个菜单查"，他答"清算时点是日终"——这句话本身对，
  他知道东西，只是答偏了，给 30-49 分，**不要给 10 分以下**。
- **背了相关但不对口的定义** → 20-39 分。定义本身没错，但用错了地方。
- **只有一两个关键词沾边** → 10-29 分。
- **0-9 分**只给这几种情况：明说"不知道"、敷衍（"是的""我同意""嗯"）、或确实一个字没答。

**如果你发现自己给了一堆 10-20 分，回头重看一遍**：
这些回答里有没有哪一句其实是正确的？有就给到 30-49 分。

**关于复读**：如果某几题的回答几乎一模一样（同一个人反复说同一段话），
那是同一个答案被问了两次。这种情况下：
- 第一次答这段给的分，后面重复的**按同样的标准给**，不要因为"又重复一遍"就一路扣到 10 分以下。
- 但如果这段答案本来就没答到点子上，也不能因为它说了很多字就给高分。
换句话说：**按内容给分，不按"第几次说"给分**。

判断依据：
1. **以【导师点评参考】的尺度为准**。历史导师认为"这样答不行"的，这里也不能给高分。
2. **不要因为出现了几个关键词就给高分**，要看是否真的讲清楚了。
3. 如果【业务手册知识点】里有标准说法，学员讲的与之矛盾就要扣分。

输出 JSON（不要 markdown 代码块，不要任何额外文字）：
{
  "items": [
    {"no": 1, "score": 分数, "comment": "为什么给这个分，指出缺什么（40 字以内）"}
  ],
  "strengths": ["做得好的点，1-2 条，每条 30 字以内"],
  "gaps": ["讲漏或讲错的点，1-2 条，每条 40 字以内"],
  "misconceptions": ["概念搞混的地方，1-2 条，每条 40 字以内。没有就留空数组"],
  "advice": "一句话建议，50 字以内"
}

**控制篇幅**：comment 40 字以内，gaps 每条 40 字以内，advice 50 字以内。
输出太长会被截断，导致整份评分作废（踩过）。

必须给每一题都打分，不能漏题。

特别注意 **misconceptions**：如果这位新人反复答不到点子上，
往往不是没背下来，而是**某个基础概念理解错了**，导致后面全跑偏。
找出这种根本性误解写进去——这比逐题扣分有用得多。"""


def _fmt_history(hits, limit=5):
    """把检索到的历史记录排成模型能读的参考材料。"""
    if not hits:
        return "（暂无历史答辩记录）"
    out = []
    for i, h in enumerate(hits[:limit], 1):
        part = [f"【历史案例 {i}】{h.get('type', '')} / {h.get('batch', '')} / {h.get('role', '')}"]
        part.append(f"评委问：{h.get('question', '')}")
        ans = (h.get("answer") or "").strip()
        if ans:
            part.append(f"学员答：{ans[:400]}")
        for c in (h.get("comments") or [])[:2]:
            t = (c or {}).get("text", "").strip()
            if t:
                part.append(f"导师点评：{t[:300]}")
        out.append("\n".join(part))
    return "\n\n".join(out)


def _fmt_criteria(crit):
    items = (crit or {}).get("items") or []
    if not items:
        return ""
    lines = []
    for it in items:
        w = it.get("weight")
        head = f"- {it.get('name')}"
        if w not in (None, ""):
            head += f"（权重 {w}）"
        lines.append(head)
        if it.get("desc"):
            lines.append(f"  判定要点：{it['desc']}")
        if it.get("levels"):
            lines.append(f"  档次：{it['levels']}")
    return "\n".join(lines)


def _clean_question(s: str) -> str:
    """模型偶尔会带"问题："前缀或编号，去掉。"""
    t = (s or "").strip()
    t = re.sub(r"^[\d１-９]+[.、)）]\s*", "", t)
    t = re.sub(r"^(问题|追问|提问|Q|Q\d+)[:：]\s*", "", t, flags=re.I)
    return t.strip().strip('"“”').strip()


def _parse_json(s: str):
    """从模型输出里抠出 JSON。模型爱包 ```json 代码块。"""
    if not s:
        return None
    t = s.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # 退一步：截取第一个 { 到最后一个 }
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None


# ---------------- 对外接口 ----------------

# ---------------- 业务手册检索 ----------------
# 手册是 O32/资管培训资料（股债基、回购、风控、清算…），单独建的一个知识库。
# 出题时用它把握"业务范围"和术语，问出来的问题才落在这套体系里。
MANUAL_KB_NAME = "业务手册（O32/资管培训）"
_manual_kb_id_cache = {"id": None, "ts": 0}


def manual_kb_id():
    """找到业务手册知识库的 id。找不到返回空串（那就只用历史答辩出题）。"""
    import time as _t
    if _manual_kb_id_cache["id"] and _t.time() - _manual_kb_id_cache["ts"] < 300:
        return _manual_kb_id_cache["id"]
    kb_id = ""
    try:
        import indexer
        for kb in indexer.load_registry():
            if kb.get("name") == MANUAL_KB_NAME:
                kb_id = kb.get("id") or ""
                break
    except Exception:
        kb_id = ""
    _manual_kb_id_cache.update({"id": kb_id, "ts": _t.time()})
    return kb_id


def pick_manual(query, topk=4, kb_id=""):
    """从知识库里找和材料相关的知识点。失败返回空列表，不影响出题。

    kb_id 为空时用业务手册（默认）；传了别的库就用那个库——
    新人可能想拿自己项目的资料当依据。
    """
    kb_id = kb_id or manual_kb_id()
    if not kb_id or not (query or "").strip():
        return []
    try:
        import kb as kbmod
        hits = kbmod.hybrid_search(kb_id, query[:300], topk=topk) or []
    except Exception as e:
        print(f"[coach] 手册检索失败: {type(e).__name__}", file=__import__("sys").stderr)
        return []
    out = []
    for h in hits[:topk]:
        txt = (h.get("text_full") or h.get("text") or "").strip()
        if not txt:
            continue
        out.append({"path": h.get("path", ""), "text": txt[:600], "score": h.get("score")})
    return out


def _fmt_manual(hits):
    if not hits:
        return "（未检索到相关手册内容）"
    parts = []
    for i, h in enumerate(hits, 1):
        name = (h.get("path") or "").split("\\")[-1].split("/")[-1]
        parts.append(f"【手册 {i}】{name}\n{h.get('text', '')[:500]}")
    return "\n\n".join(parts)


_REVEAL_SYSTEM = """你是答辩辅导老师。现在要给学员讲清楚这道题的正确答案。

你要给出两部分（不要点评学员刚才的回答，省掉这段）：

**1. 标准答案**
把这个问题该怎么答，用 3-5 句话讲清楚。要求：
- 落到具体动作、菜单、表名、字段名上，不要泛泛而谈
- 如果【业务手册原文】里有标准说法，以它为准
- 讲"为什么"：为什么这么做、不这么做会出什么问题

**2. 依据**
指出上面答案出自哪里，**必须具体**：
- 优先引用【业务手册原文】里的内容，写出文件名和那一小段原文
- **绝对不要编造文件名、页码、章节号**。手册原文里没有的，就不要编"依据"
- 依据只写 1-3 条，每条附上能对上的原文片段（20-60 字）

输出 JSON（不要代码块）：
{
  "answer": "标准答案正文",
  "basis": [{"source": "文件名", "quote": "原文片段", "point": "这段说明了什么"}],
  "key_points": ["关键得分点，2-4 条"]
}
"""


_ANALYSIS_SYSTEM = """你是答辩辅导老师。下面是学员整场答辩的全部问答。
请逐题给出诊断：哪里对、哪里错或漏了、有没有概念混淆；最后给总体评价和补强建议。
**不要打分**（分数由评分器单独给）。
输出 JSON（不要代码块）：
{
  "items": [{"no": 1, "diagnosis": "第1题的诊断"}],
  "overall": "整场总体评价与补强建议"
}
"""


def _fmt_manual_cite(hits):
    """手册检索结果排成带文件名的引用，让模型能准确引用。"""
    if not hits:
        return "（未检索到相关手册内容）"
    parts = []
    for i, h in enumerate(hits, 1):
        path = (h.get("path") or "").replace("\\", "/")
        name = path.split("/")[-1]
        parts.append(f"【手册 {i}·{name}】\n{(h.get('text') or '')[:800]}")
    return "\n\n".join(parts)


def reveal(question, user_answer=None, material="", ppt=None, history=None,
           cfg=None, user_key="", timeout=150, kb_id=""):
    """给学员看参考答案 + 依据 + 对照点评。

    kb_id: 依据从哪个知识库找。空串=业务手册（默认）。
    返回 {"ok": True, "answer":..., "basis":[{source,quote,point,verified}], ...}
    """
    import llm

    # 用问题去知识库里找依据——这是"依据"的主要来源，必须给真文件名
    manual = pick_manual(question, topk=4, kb_id=kb_id)
    manual_text = _fmt_manual_cite(manual)
    manual_files = []
    for h in manual:
        p = (h.get("path") or "").replace("\\", "/")
        if p and p not in manual_files:
            manual_files.append(p)

    # 历史答辩里有没有类似的问题——别人的答法和导师点评是很强的参照
    hits = defense_search.search(question, topk=3)
    if hits and hits[0].get("_score", 0) < 1.5:
        hits = []
    ref_text = _fmt_history(hits, limit=3)

    parts = ["【评委的问题】", question or "", "",
             "【业务手册原文（依据来源）】", manual_text, "",
             "【历史答辩里的类似情形】", ref_text, "",
             "请按上面的要求，给出标准答案、依据、关键得分点。"]
    ans = llm.chat([{"role": "user", "content": "\n".join(parts)}],
                   cfg=cfg, system=_REVEAL_SYSTEM, timeout=timeout, user_key=user_key)
    data = _parse_json(ans or "")
    if not isinstance(data, dict):
        return {"ok": False, "msg": "生成参考答案失败，请重试",
                "raw": (ans or "")[:300]}

    basis = []
    for b in (data.get("basis") or []):
        if not isinstance(b, dict):
            continue
        src = str(b.get("source") or "").strip()
        quote = str(b.get("quote") or "").strip()
        if not src and not quote:
            continue
        basis.append({"source": src, "quote": quote[:400],
                      "point": str(b.get("point") or "").strip()})

    # 校验依据是不是编的：文件名要真在检索结果里，或原文片段真出现过
    real_names = [(p.split("/")[-1] or "").lower() for p in manual_files if p]
    for b in basis:
        blob = (b["source"] + " " + b["quote"]).lower()
        ok = any(n and (n in blob or blob in n) for n in real_names)
        if not ok and b["quote"]:
            head = b["quote"][:12]
            ok = any(head in (h.get("text") or "") for h in manual)
        b["verified"] = bool(ok)

    return {
        "ok": True,
        "answer": str(data.get("answer") or "").strip(),
        "basis": basis[:4],
        "key_points": [str(x) for x in (data.get("key_points") or [])][:5],
        "manual_files": manual_files[:4],
        "refs": [{"question": h.get("question", ""),
                  "answer": (h.get("answer") or "")[:400],
                  "comments": [(c or {}).get("text", "") for c in (h.get("comments") or [])][:2]}
                 for h in hits[:3]],
    }


def analyze(material, history, cfg=None, user_key="", timeout=180, kb_id=""):
    """整场答辩结束后，复用全部问答生成逐题解析 + 总体评价。

    单题「看参考答案」不再点评学员回答；诊断统一放到这里，只调用一次模型。
    """
    import llm
    if not history:
        return {"ok": False, "msg": "还没有问答记录，无法生成解析"}

    qa = []
    for i, h in enumerate(history, 1):
        qa.append(f"第{i}题：{h.get('q', '')}\n学员答：{(h.get('a') or '（未回答）')[:1500]}")
    probe = " ".join(h.get("q", "") for h in history)[:500]
    manual = pick_manual(probe, topk=4, kb_id=kb_id)
    manual_text = _fmt_manual_cite(manual)
    refs = defense_search.search(probe, topk=3) if probe.strip() else []
    ref_text = _fmt_history(refs, limit=3)

    parts = [
        "【答辩材料】", (material or "")[:2000], "",
        "【全部问答】", "\n\n".join(qa), "",
        "【业务手册原文】", manual_text, "",
        "【历史答辩参考】", ref_text, "",
        "请逐题给出诊断，并给总体评价和补强建议。",
    ]
    ans = llm.chat([{"role": "user", "content": "\n".join(parts)}],
                   cfg=cfg, system=_ANALYSIS_SYSTEM, timeout=timeout, user_key=user_key)
    data = _parse_json(ans or "")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"ok": False, "msg": "生成解析报告失败，请重试", "raw": (ans or "")[:300]}

    by_no = {}
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("no") or 0)
        except Exception:
            continue
        if 1 <= n <= len(history):
            by_no[n] = str(it.get("diagnosis") or "").strip()

    items = [{"no": i, "question": h.get("q", ""),
              "diagnosis": by_no.get(i, "")}
             for i, h in enumerate(history, 1)]
    return {"ok": True, "items": items,
            "overall": str(data.get("overall") or "").strip()}


def pick_reference(material, question="", rtype=None, topk=5):
    """挑历史答辩记录做参考：优先按材料内容检索，检索不到就给几条随机的。"""
    q = (question or material or "")[:600]
    hits = []
    if q.strip():
        hits = defense_search.search(q, topk=topk, rtype=rtype)
    if not hits:
        hits = defense_search.sample(topk, rtype=rtype)
    return hits


# ---------------- PPT 结构化材料 ----------------

def material_from_ppt(ppt_meta):
    """把 defense_ppt.parse_pptx 的结果变成给模型看的材料。

    关键是**标出页码和有无图片**：模型知道"第 7 页有张架构图"，
    才会问出"你第 7 页那个流程为什么这么画"这种针对性问题。
    """
    if not ppt_meta:
        return ""
    slides = ppt_meta.get("slides") or []
    if not slides:
        return (ppt_meta.get("text") or "")[:8000]
    out = [f"（答辩 PPT：{ppt_meta.get('name', '')}，共 {len(slides)} 页）"]
    for s in slides:
        head = f"【第{s['no']}页】{s.get('title') or ''}".rstrip()
        out.append(head)
        t = (s.get("text") or "").strip()
        if t:
            out.append(t[:800])
        if s.get("images"):
            out.append(f"（本页含 {len(s['images'])} 张图：可能是流程图/界面截图/架构图）")
    txt = "\n".join(out)
    return txt[:12000]


def _pages_asked(history):
    """从已经问过的问题里，找出提到过的 PPT 页码。"""
    pages = []
    for h in (history or []):
        for m in re.finditer(r"第\s*(\d{1,3})\s*页|P(\d{1,3})", str(h.get("q") or "")):
            n = int(m.group(1) or m.group(2))
            if 1 <= n <= 300 and n not in pages:
                pages.append(n)
    return "、".join(f"第{n}页" for n in pages[:8])


def _pages_hint(ppt_meta, topk=3):
    """给提示词用的页码索引，控制长度。"""
    if not ppt_meta:
        return ""
    slides = ppt_meta.get("slides") or []
    if not slides:
        return ""
    lines = []
    for s in slides:
        n = len(s.get("images") or [])
        mark = f"（{n}图）" if n else ""
        lines.append(f"P{s['no']} {s.get('title') or ''}{mark}")
    return "；".join(lines)


MAX_CHASE = 2      # 同一个点最多追问几次，超过就换题

# 判断"是不是在问同一件事"时要忽略的词：疑问词、连接词、泛化动词。
# 这些词在几乎所有问题里都出现，算进重叠率会把不同问题误判成同一个。
_CHASE_STOP = {
    "什么", "哪些", "哪个", "怎么", "如何", "为什么", "是否", "还是", "或者",
    "这个", "那个", "一个", "一下", "具体", "一般", "资料", "材料", "问题",
    "可以", "应该", "需要", "时候", "情况", "地方", "东西", "这样", "那样",
    "回答", "说的", "讲了", "刚才", "现在", "这里", "那里", "你们", "我们",
}


def _chase_count(history):
    """数一下已经就"同一个问题"追问了几次。

    做法：看最后几问是不是在问同一件事——用关键词重叠率粗判。
    重叠够高就认为还在追同一个点。

    为什么要这个：实测如果不限制，学员答不上来时模型会一路复读同一个问题
    （10 题里有 9 题问的是同一件事，换着措辞反复逼）。
    """
    if not history or len(history) < 2:
        return 0

    def toks(s):
        """提取"这件事是什么"的特征：页码 + 核心业务名词。

        只看整句重叠不可靠——同一个点换个说法，词就全变了
        （"为什么不可重复执行" vs "再执行一次会怎样"，重叠率 0，其实是同一个点）。
        所以改成抓两个稳定特征：问的是哪一页、问的是哪个业务对象。
        """
        s = str(s or "")
        feats = set()
        for m in re.finditer(r"第\s*(\d{1,3})\s*页|P(\d{1,3})", s):
            feats.add("page:" + (m.group(1) or m.group(2)))
        try:
            import jieba
            ws = [w for w in jieba.lcut(s) if len(w) >= 2]
        except Exception:
            ws = [s[i:i + 2] for i in range(max(0, len(s) - 1))]
        for w in ws:
            if w in _CHASE_STOP or w.isdigit():
                continue
            feats.add(w)
        return feats

    cur = toks(history[-1].get("q"))
    if not cur:
        return 0

    def core(feats):
        """核心业务词：去掉页码，只留至少 3 字的词，避免"清算""业务""流程"这种到处都有的。"""
        return {w for w in feats if not w.startswith("page:") and len(w) >= 3}

    def pages(feats):
        return {w for w in feats if w.startswith("page:")}

    cur_core, cur_pages = core(cur), pages(cur)

    def same_topic(a, b):
        """两问是不是在说同一件事。"""
        ac, ap = core(a), pages(a)
        hit = ac & cur_core
        if len(hit) >= 2:                  # 两个以上核心词一样
            return True
        if ap & cur_pages and hit:          # 同一页 + 至少一个核心词
            return True
        return False

    # 数一下最近几轮里，有多少轮在说同一件事。
    # **不做严格连续判断**：模型复读时会换措辞，中间偶尔断一次是常态，
    # 一旦 break 就永远数不到 2（实测连问 6 次被判成 0 次）。
    recent = history[:-1][-4:]
    n = 0
    for h in recent:
        prev = toks(h.get("q"))
        if prev and same_topic(prev, cur):
            n += 1
    return n


def _page_of(q):
    m = re.search(r"第\s*(\d{1,3})\s*页", str(q or ""))
    return int(m.group(1)) if m else 0


def _chase_by_topic(history):
    """数"同一个点已经被问了几轮"。用于决定要不要强制换题。

    判断方式：**按 PPT 页码数**，不猜语义。
    理由：试过用关键词重叠、名词指纹、话题指纹，全都不稳——
    同一页上问不同方面，措辞差得远，指纹就不同，永远数不到 2。
    而"同一页问了 3 次"是明确、可数、一定成立的信号，正是我们要拦的情况。
    没提到页码的问题（比如纯业务知识题）退化成比文本相似度。
    """
    if not history:
        return 0

    cur_page = _page_of(history[-1].get("q"))
    recent = history[:-1][-4:]
    n = 0

    if cur_page:
        n = sum(1 for h in recent if _page_of(h.get("q")) == cur_page)
        if n:
            return n

    # 没页码，或者只是本问没提页码（追问时模型常省略）：
    # 退回到"看最近几轮问的是不是同一件事"。
    # 这里要宽松——宁可误判成"还在追问"（顶多提前换个话题，无害），
    # 也不能漏判（漏判就会连问 9 遍同一件事，实测发生过）。
    cur_toks = _chase_tokens(history[-1].get("q"))
    if not cur_toks:
        return 0
    for h in recent:
        prev = _chase_tokens(h.get("q"))
        if not prev:
            continue
        overlap = len(cur_toks & prev) / max(1, min(len(cur_toks), len(prev)))
        if overlap >= 0.34:
            n += 1
    return n


def _is_repeating(answer, history):
    """学员是不是在复读——把和前面几乎一样的答案又发了一遍。

    真实现场很常见：答不上来的人会重复同一段话。
    这时候继续追问是浪费轮次，应该换个更容易的切入点，
    甚至直接指出"你刚才说的和上一轮一样"。
    """
    a = str(answer or "").strip()
    if not a or not history:
        return False
    for h in history[-4:]:
        prev = str(h.get("a") or "").strip()
        if not prev:
            continue
        # 长度接近 + 字符相似度高 → 认定复读
        if abs(len(a) - len(prev)) > max(8, len(prev) * 0.3):
            continue
        sa, sb = set(a), set(prev)
        if not sa or not sb:
            continue
        sim = len(sa & sb) / max(1, len(sa | sb))
        if sim >= 0.75:
            return True
    return False


def _chase_tokens(s):
    """取一句里的实词，用于判断两问是不是同一件事。"""
    try:
        import jieba
        ws = [w for w in jieba.lcut(str(s or "")) if len(w) >= 2]
    except Exception:
        s = str(s or "")
        ws = [s[i:i + 2] for i in range(max(0, len(s) - 1))]
    return {w for w in ws if w not in _CHASE_STOP and not w.isdigit()}


def _topic_key(q):
    """给一个问题算"话题指纹"。同话题的问题指纹相同，用来数追问次数。

    不靠事后猜词重叠（那样变个措辞就失效），
    而是由调用方在每轮生成问题时算一次、记进 history["topic"]。
    """
    s = str(q or "")
    feats = set()
    for m in re.finditer(r"第\s*(\d{1,3})\s*页", s):
        feats.add("p" + m.group(1))
    # 只留名词/动名词：跳 5 层锁的骨架形态里，噪音全是动词短语和口语词
    # （"先回到""什么样""误操作""会怎样"），业务对象才是名词
    try:
        import jieba.posseg as pseg
        ws = []
        for w, flag in pseg.cut(s):
            if len(w) >= 3 and flag.startswith(("n", "vn", "eng")) and w not in _CHASE_STOP:
                ws.append(w)
    except Exception:
        try:
            import jieba
            ws = [w for w in jieba.lcut(s) if len(w) >= 3 and w not in _CHASE_STOP]
        except Exception:
            ws = []
    # 取最长的 2 个名词当指纹（业务对象名通常最长：日初始化、标准券、清算配对表）
    for w in sorted(set(ws), key=len, reverse=True)[:2]:
        feats.add(w)
    return "|".join(sorted(feats))


def _prepare_image(raw, max_side=1200):
    """读入图片，去透明底、缩放、转 RGB。失败返回 None。"""
    try:
        import io as _io
        from PIL import Image
        im = Image.open(_io.BytesIO(raw))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            sc = max_side / float(max(w, h))
            im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))),
                           Image.LANCZOS)
        return im
    except Exception:
        return None


def _contact_sheet(images, max_side=1600):
    """多张图拼成一张，带 1/2/3… 编号。比逐张传给模型快，也不容易漏看。"""
    from PIL import Image, ImageDraw, ImageFont
    n = len(images)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    cell_w, cell_h, pad, label_h = 760, 560, 16, 30
    W = cols * cell_w + (cols + 1) * pad
    H = rows * (cell_h + label_h) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    for i, im in enumerate(images):
        c, r = i % cols, i // cols
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + label_h + pad)
        # 编号圆标
        draw.ellipse([x + 8, y + 4, x + 34, y + 30], fill=(31, 78, 121))
        draw.text((x + 15, y + 7), str(i + 1), fill="white", font=font)
        # 等比放进格子
        w, h = im.size
        sc = min(cell_w / float(w), cell_h / float(h))
        if sc < 1:
            im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))),
                           Image.LANCZOS)
        w, h = im.size
        sheet.paste(im, (x + (cell_w - w) // 2, y + label_h + (cell_h - h) // 2))

    if max(sheet.size) > max_side:
        sc = max_side / float(max(sheet.size))
        sheet = sheet.resize((int(sheet.width * sc), int(sheet.height * sc)),
                             Image.LANCZOS)
    return sheet


def _ppt_image_blocks(ppt_meta, page_no=None, limit=6):
    """把 PPT 某页的图片转成 Anthropic image 块。

    1 张图 → 压缩后直接传；
    多张图 → 拼成一张带编号的联系表再传，模型一次能看全，延迟接近一张图。
    """
    if not ppt_meta:
        return []
    slides = ppt_meta.get("slides") or []
    if not slides:
        return []

    if page_no:
        pick = [s for s in slides if s.get("no") == page_no]
    else:
        pick = [s for s in slides if s.get("images")][:1]
    if not pick:
        return []

    import io as _io
    import base64
    blocks = []
    for s in pick[:1]:
        loaded, seen = [], set()
        for im in (s.get("images") or []):
            fn = im.get("file")
            if not fn:
                continue
            try:
                import defense_ppt
                p = defense_ppt.img_path(ppt_meta.get("id", ""), fn)
                if not p:
                    continue
                raw = p.read_bytes()
                if raw in seen:          # 同页重复图只留一张
                    continue
                seen.add(raw)
                pil = _prepare_image(raw)
                if pil is None:
                    continue
                # 多图时丢掉极小的图标/装饰图（单图不丢）
                if len(s.get("images") or []) > 1 and max(pil.size) < 40:
                    continue
                loaded.append(pil)
            except Exception:
                continue

        if not loaded:
            continue
        if len(loaded) > limit:
            loaded = loaded[:limit]
        if len(loaded) == 1:
            out = loaded[0]
        else:
            out = _contact_sheet(loaded)
        buf = _io.BytesIO()
        out.save(buf, format="JPEG", quality=85)
        raw = buf.getvalue()
        if len(raw) > 4 * 1024 * 1024:      # 理论不会，兜底
            continue
        blocks.append({"type": "image",
                       "source": {"type": "base64", "media_type": "image/jpeg",
                                  "data": base64.b64encode(raw).decode()}})
    return blocks


def next_question(material, history, user_answer=None, rtype=None,
                  cfg=None, user_key="", timeout=90, ppt=None, focus_page=None,
                  kb_id=""):
    """生成下一个问题。

    history: [{"q": 问题, "a": 学员回答}]，本轮对话已发生的往来。
    user_answer 为 None 时是**第一个问题**，否则是**追问**。
    ppt: defense_ppt.parse_pptx 的结果。给了就会把 PPT 页码结构和图片一起喂给模型，
         让它针对具体某一页（包括页面上的图）提问。
    kb_id: 用哪个知识库当依据。空串=业务手册（默认），也可以指到用户自己项目的库。
    返回问题文本；失败返回空串（调用方负责提示）。
    """
    import llm
    refs = pick_reference(material, (history[0]["q"] if history else ""), rtype=rtype)
    ref_text = _fmt_history(refs)
    # 知识库：用材料内容去检索知识点，出题时用来把握业务范围和术语
    manual = pick_manual(material, topk=4, kb_id=kb_id)
    manual_text = _fmt_manual(manual)

    parts = ["【新人的答辩材料】", (material or "（未提供材料）")[:6000], ""]
    if ppt:
        hint = _pages_hint(ppt)
        if hint:
            parts += ["【PPT 页码索引】", hint, ""]
        # 已经问过哪些页：答辩要覆盖知识面，不能 10 题全考同一页
        used = _pages_asked(history)
        if used:
            parts += [f"【已经问过的页】{used}——新问题尽量换别的页，" 
                      f"除非确实需要就这一页继续深挖。", ""]
    parts += ["【历史答辩参考（真评委问过的）】", ref_text, "",
              "【业务手册知识点】", manual_text, ""]

    if user_answer is None:
        parts.append("请根据以上材料，提出第一个问题。")
        system = _ASK_SYSTEM
    else:
        prior = history[-1] if history else {"q": "", "a": ""}
        chase = _chase_by_topic(history)
        repeated = _is_repeating(user_answer, history)
        parts += ["【刚才的问题】", prior.get("q", ""), "",
                  "【对方的回答】", (user_answer or "（没有回答）")[:3000], ""]

        # 同一个点追问到第 3 次就强制换题。
        # 真评委追问两轮对方还答不上来，会认为这就是他的水平上限，换方向继续考，
        # 不会揪着一句话反复逼。不设这个上限，模型会一路复读同一个问题（实测连问 9 遍）。
        if chase >= MAX_CHASE or repeated:
            asked = "；".join(h.get("q", "")[:40] for h in history[-6:])
            why = ("对方刚才的回答和前面几乎一模一样，明显是在复读，"
                   "继续在这个点追问没有意义"
                   if repeated else
                   "这个问题你已经追问两轮了，对方始终答不到点子上，说明超出他的水平")
            parts += [
                f"【重要】{why}。**不要再问这个点，直接换一个新的方向**。",
                "",
                "已经问过的（换题时避开）：",
                asked,
                "",
                "新问题请从材料的其他页、或者业务手册覆盖的其他知识点里出，"
                "不要再纠缠刚才那一个点。",
                "请给出新的问题。",
            ]
            system = _ASK_SYSTEM
        else:
            parts.append("请决定下一句问什么。")
            system = _FOLLOW_SYSTEM

    # 同一页有多张图时，图片会拼成带编号的联系表；告诉模型编号，方便它指定“图 2”
    if ppt and focus_page:
        _s = next((x for x in (ppt.get("slides") or []) if x.get("no") == focus_page), None)
        _n = len((_s or {}).get("images") or [])
        if _n > 1:
            parts += [f"【本页图片】第{focus_page}页有 {_n} 张图，已按编号 1-{_n} "
                      f"拼成一张；提问时可以指明“图几”。", ""]

    content = "\n".join(parts)
    # 单图直接压缩；多图拼成一张联系表，避免多图拖慢和漏看
    blocks = _ppt_image_blocks(ppt, page_no=focus_page, limit=6)
    if blocks:
        content = [{"type": "text", "text": content}] + blocks

    ans = llm.chat([{"role": "user", "content": content}],
                   cfg=cfg, system=system, timeout=timeout, user_key=user_key)
    return _clean_question(ans or "")


# 评分规则（人定的）：10 题，每题 10 分，满分 100
QUESTIONS_TOTAL = 10
PER_QUESTION = 100


def score(material, history, cfg=None, user_key="", timeout=180, kb_id=""):
    """给整场作答逐题打分。模型每题按 0-100 分打，报告按题数折算成 100 分制。

    例如选 3 题：每题贡献约 33.3 分，总分 100。
    history: [{"q": 问题, "a": 学员回答}, ...]
    返回 {"ok": True, "items":[{no,question,score,comment}], "total":..., "grade":...}
    """
    import llm
    if not history:
        return {"ok": False, "reason": "empty", "msg": "还没有问答记录，无法打分。"}

    qa = []
    for i, h in enumerate(history, 1):
        qa.append(f"第{i}题：{h.get('q', '')}\n答：{(h.get('a') or '（未回答）')[:2000]}")
    # 用整场问题去检索导师点评，让模型知道这家公司"什么算答得好"
    probe = " ".join(h.get("q", "") for h in history)[:500]
    refs = defense_search.search(probe, topk=4) if probe.strip() else []
    ref_text = _fmt_history(refs, limit=4)
    # 知识库知识点：判断学员讲的业务对不对
    manual = pick_manual(probe, topk=3, kb_id=kb_id)
    manual_text = _fmt_manual(manual)

    parts = [
        "【新人的答辩材料】", (material or "")[:2500], "",
        "【本次问答】", "\n\n".join(qa), "",
        "【导师点评参考（历史上导师怎么评）】", ref_text, "",
        "【业务手册知识点】", manual_text, "",
        f"请逐题打分（每题 0-{PER_QUESTION} 分），输出 JSON。",
    ]
    # 评分要输出 10 题的 JSON，默认 max_tokens（1024）不够，会被截断导致整份作废。
    # 这里单独给一份放大上限的配置，不影响其他调用。
    try:
        import copy
        base = dict(cfg or llm.load_config())
        base["max_tokens"] = max(int(base.get("max_tokens") or 1024), 3000)
        cfg = base
    except Exception:
        pass

    data, ans = None, ""
    for attempt in range(2):
        body = "\n".join(parts)
        if attempt:
            body += "\n\n注意：上一次输出被截断了。这次务必简短，每条 comment 不超过 40 字。"
        ans = llm.chat([{"role": "user", "content": body}],
                       cfg=cfg, system=_SCORE_SYSTEM, timeout=timeout, user_key=user_key) or ""
        data = _parse_json(ans)
        if isinstance(data, dict):
            break
        print(f"[coach] 评分输出无法解析（第{attempt+1}次），长度 {len(ans)}，重试",
              file=__import__("sys").stderr)

    if not isinstance(data, dict):
        return {"ok": False, "reason": "bad_output",
                "msg": "模型没有返回可解析的评分结果，请重试。",
                "raw": (ans or "")[:300]}

    # 逐题分数：夹在 0-10，漏题的补 0（宁可少给，不能凭空给分）
    got = {}
    for s in (data.get("items") or data.get("scores") or []):
        if not isinstance(s, dict):
            continue
        try:
            no = int(s.get("no") or s.get("index") or 0)
        except Exception:
            no = 0
        try:
            sc = float(s.get("score"))
        except Exception:
            continue
        sc = max(0.0, min(sc, float(PER_QUESTION)))
        if no <= 0:
            no = len(got) + 1
        got[no] = {"no": no,
                   "question": str(s.get("question") or s.get("name") or ""),
                   "score": round(sc, 1),
                   "comment": str(s.get("comment") or "")}

    items = []
    for i, h in enumerate(history, 1):
        it = got.get(i)
        if it:
            if not it["question"]:
                it["question"] = h.get("q", "")
            items.append(it)
        else:
            items.append({"no": i, "question": h.get("q", ""), "score": 0.0,
                          "comment": "（模型未评这一题，按 0 分计）"})

    # 模型每题按 0-100 打，折算成每题贡献（3 题时每题约 33.3，总分始终 100）
    per = 100.0 / len(items) if items else 0.0
    for it in items:
        raw = float(it.get("score") or 0)
        it["raw_score"] = round(raw, 1)
        it["score"] = round(raw * per / 100.0, 1)

    total = round(sum(x["score"] for x in items), 1)
    total = min(total, 100.0)
    full = 100.0
    pct = total / full if full else 0
    grade = ("优秀" if pct >= .9 else "良好" if pct >= .75
             else "合格" if pct >= .6 else "不合格")

    return {
        "ok": True,
        "items": items,
        "total": total,
        "full": full,
        "grade": grade,
        "strengths": [str(x) for x in (data.get("strengths") or [])][:3],
        "gaps": [str(x) for x in (data.get("gaps") or [])][:3],
        "misconceptions": [str(x) for x in (data.get("misconceptions") or [])][:3],
        "advice": str(data.get("advice") or ""),
        "refs_used": len(refs),
        "manual_used": len(manual),
    }
