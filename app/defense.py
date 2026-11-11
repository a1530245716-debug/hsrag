# -*- coding: utf-8 -*-
"""答辩记录数据中心（AI 辅助质检的基础）。

需求背景
--------
公司要求新人答辩前自己练：AI 模仿评委，根据新人提交的阶段总结材料提问，
新人作答后再追问、最后打分。要做得像评委，就得**拿历史答辩现场做参照**——
评委问过什么、学员怎么答的、导师补了什么。

所以这里存的不是文档，是**结构化的问答对**：
    问题 ← 谁问的 / 什么时候 / 哪一届
    回答 ← 学员原话
    点评 ← 导师的评语和补充

为什么单独存、不塞进知识库
--------------------------
1. 问题/回答/点评三者是**有归属关系**的，拆成文本块会丢关联；
2. `chunks.jsonl` 的 `id/path/chunk/text` 四字段是硬约定，不能改；
3. 要按月增量追加，文档形式不好去重和版本管理；
4. 新人练习时检索答辩记录，不应该和"项目资料"混在一起（会把答案里的技术细节
   当成本项目的真相，其实那是别的项目）。

数据文件（都在 data_dir/defense/ 下）
------------------------------------
    records.jsonl    一条答辩记录一行，追加式写入
    imports.json     每次导入的台账（哪天、什么类型、加了多少条、跳过了多少重复）
    criteria.json    评分标准（领导给的 + 从导师点评里提炼、人工确认过的）

记录字段（加字段可以，改字段名要先协商）
--------------------------------------
    id        记录唯一标识，重复导入靠它去重
    type      "应届生" | "社招"
    batch     批次，如 "2026春招"、"2026-07社招"
    role      岗位，如 "交付工程师"
    question  评委的问题
    answer    学员的回答原文
    comments  导师点评与补充，[{"by": "导师", "text": "..."}]
    tags      标签列表，便于按主题筛
    score     该题评分（有就填，没有留 null）
    src_file  来源文件名，便于回溯
    ts        入库时间
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

APP = Path(__file__).parent
_LOCK = threading.RLock()

# 记录类型。就这两种，需求里明确区分了：
#   应届生 = 一次性导入的静态数据
#   社招   = 每月持续追加
TYPES = ("应届生", "社招")

_DIRNAME = "defense"


# ---------------- 路径 ----------------

def _data_dir() -> Path:
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = APP / "data"
    if not d.is_absolute():
        alt = APP.parent / d
        d = alt if alt.exists() else APP / d
    return d


def store_dir() -> Path:
    d = _data_dir() / _DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def records_path() -> Path:
    return store_dir() / "records.jsonl"


def imports_path() -> Path:
    return store_dir() / "imports.json"


def criteria_path() -> Path:
    return store_dir() / "criteria.json"


# ---------------- 记录 id ----------------

def make_id(rec) -> str:
    """按内容算稳定 id。同一条记录重复导入算出的 id 相同，天然去重。"""
    key = "|".join([
        str(rec.get("type", "")),
        str(rec.get("batch", "")),
        str(rec.get("question", "")).strip(),
        str(rec.get("answer", ""))[:200].strip(),
    ])
    return "d_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def normalize(rec) -> dict:
    """把一条外部数据整理成标准记录。缺字段补空，不抛异常。"""
    q = str(rec.get("question") or rec.get("q") or "").strip()
    a = str(rec.get("answer") or rec.get("a") or "").strip()
    t = str(rec.get("type") or "").strip()
    if t not in TYPES:
        # 猜一下：带"社招"字样当社招，否则按应届生
        t = "社招" if "社招" in t else "应届生"
    comments = rec.get("comments") or rec.get("点评") or []
    if isinstance(comments, str):
        comments = [{"by": "导师", "text": comments}] if comments.strip() else []
    else:
        fixed = []
        for c in comments:
            if isinstance(c, str):
                fixed.append({"by": "导师", "text": c})
            elif isinstance(c, dict):
                fixed.append({"by": str(c.get("by") or c.get("人") or "导师"),
                              "text": str(c.get("text") or c.get("内容") or "")})
        comments = fixed
    tags = rec.get("tags") or []
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]
    out = {
        "type": t,
        "batch": str(rec.get("batch") or "").strip(),
        "role": str(rec.get("role") or "").strip(),
        "question": q,
        "answer": a,
        "comments": comments,
        "tags": tags or [],
        "score": rec.get("score"),
        "src_file": str(rec.get("src_file") or "").strip(),
    }
    out["id"] = str(rec.get("id") or make_id(out))
    out["ts"] = int(rec.get("ts") or time.time())
    return out


def valid(rec) -> bool:
    """一条记录至少要有问题；回答可以为空（有些记录只有题目）。"""
    return bool(str(rec.get("question") or "").strip())


# ---------------- 读写 ----------------

def load_all() -> list:
    """读全部记录。文件坏了就返回能读出来的那些，绝不抛异常。"""
    p = records_path()
    if not p.exists():
        return []
    out = []
    with _LOCK:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(r, dict) and valid(r):
                        out.append(r)
        except Exception:
            pass
    return out


def existing_ids() -> set:
    return {r.get("id") for r in load_all() if r.get("id")}


def add_records(recs, replace=False) -> dict:
    """追加一批记录。按 id 去重，返回统计。

    replace=True 时先清空（用于"人给的是全量"的情况）。
    """
    recs = [normalize(r) for r in (recs or []) if valid(r)]
    if not recs:
        return {"added": 0, "skipped": 0, "total": len(load_all()), "replaced": replace}

    with _LOCK:
        old = [] if replace else load_all()
        seen = {r.get("id") for r in old}
        added, skipped = [], 0
        for r in recs:
            if r["id"] in seen:
                skipped += 1
                continue
            seen.add(r["id"])
            added.append(r)

        if added or replace:
            p = records_path()
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in old + added:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(p)

    return {"added": len(added), "skipped": skipped,
            "total": len(old) + len(added), "replaced": replace}


def log_import(summary: dict):
    """记一次导入台账，便于回溯"哪个月加了什么"。"""
    try:
        p = imports_path()
        hist = []
        if p.exists():
            try:
                hist = json.loads(p.read_text(encoding="utf-8")) or []
            except Exception:
                hist = []
        hist.append({"ts": int(time.time()),
                     "when": time.strftime("%Y-%m-%d %H:%M"),
                     **summary})
        hist = hist[-200:]      # 只留最近 200 次
        p.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[defense] 记台账失败: {type(e).__name__}", file=__import__("sys").stderr)


def import_history() -> list:
    p = imports_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []


# ---------------- 统计 ----------------

def stats() -> dict:
    """给管理界面看的概览。"""
    recs = load_all()
    by_type = {}
    batches = {}
    with_answer = 0
    with_comment = 0
    tag_cnt = {}
    for r in recs:
        t = r.get("type") or "未知"
        by_type[t] = by_type.get(t, 0) + 1
        b = r.get("batch") or "（未填批次）"
        batches[b] = batches.get(b, 0) + 1
        if (r.get("answer") or "").strip():
            with_answer += 1
        if r.get("comments"):
            with_comment += 1
        for tg in (r.get("tags") or []):
            tag_cnt[tg] = tag_cnt.get(tg, 0) + 1
    top_tags = sorted(tag_cnt.items(), key=lambda x: -x[1])[:20]
    return {
        "total": len(recs),
        "by_type": by_type,
        "batches": dict(sorted(batches.items(), key=lambda x: -x[1])),
        "with_answer": with_answer,
        "with_comment": with_comment,
        "top_tags": top_tags,
        "imports": import_history()[-10:],
        "ready": len(recs) > 0,
    }


# ---------------- 评分标准 ----------------

def load_criteria() -> dict:
    p = criteria_path()
    if not p.exists():
        return {"items": [], "source": "", "updated": ""}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"items": [], "source": "", "updated": ""}


def save_criteria(items, source="") -> dict:
    """items: [{"name": 维度名, "weight": 权重, "desc": 判定要点, "levels": "各档标准"}]"""
    fixed = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        fixed.append({
            "name": name,
            "weight": it.get("weight"),
            "desc": str(it.get("desc") or "").strip(),
            "levels": str(it.get("levels") or "").strip(),
        })
    d = {"items": fixed, "source": source, "updated": time.strftime("%Y-%m-%d %H:%M")}
    p = criteria_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return d


def criteria_ready() -> bool:
    return bool((load_criteria() or {}).get("items"))


# ---------------- 命令行 ----------------
#   python app/defense.py --stat             看现有数据概况
#   python app/defense.py --import x.json    导入 JSON
#   python app/defense.py --import x.xlsx    导入 Excel（第一行表头）

if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if "--stat" in args or not args:
        s = stats()
        print(f"记录总数: {s['total']}")
        print(f"  按类型: {s['by_type']}")
        print(f"  有回答: {s['with_answer']}   有点评: {s['with_comment']}")
        print(f"  批次: {s['batches']}")
        print(f"  热门标签: {s['top_tags'][:10]}")
        for h in s["imports"]:
            print(f"  导入 {h.get('when')}: +{h.get('added')} 跳过 {h.get('skipped')} 共 {h.get('total')}")
    elif "--import" in args:
        i = args.index("--import")
        path = Path(args[i + 1])
        if not path.exists():
            print(f"文件不存在: {path}")
            sys.exit(1)
        if path.suffix.lower() in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            head = [str(c or "").strip() for c in (rows[0] if rows else [])]
            recs = []
            for row in rows[1:]:
                d = {}
                for k, v in zip(head, row):
                    if k:
                        d[k] = v
                recs.append(d)
        else:
            recs = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(recs, dict):
                recs = recs.get("records") or []
        for r in recs:
            if isinstance(r, dict) and "src_file" not in r:
                r["src_file"] = path.name
        res = add_records(recs, replace="--replace" in args)
        log_import({"file": path.name, **res})
        print(f"导入完成: 新增 {res['added']}，跳过重复 {res['skipped']}，现有 {res['total']} 条")
