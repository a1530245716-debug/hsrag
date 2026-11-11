# -*- coding: utf-8 -*-
"""把两个 Excel 的答辩记录合并导入到答辩库。

两个文件字段几乎一样，只是列名不同：
    资管交付新人能力成长计划.xlsx / 答辩记录
        产品 答辩人 提问人 答辩人.部门 答辩人.直属上级 阶段 评委问题 候选人回答详情 导师点评与补充
    答辩AI小结.xlsx / 问答AI记录
        学员 产品 学员.部门 学员.直属上级 阶段 评委问题 学员回答详情 导师点评与补充

统一成 defense 的记录格式：
    question / answer / comments / tags(产品) / batch(阶段) / role / src_file

用法：
    python tools/import_defense.py            导入（按 id 去重，可重复跑）
    python tools/import_defense.py --stat     只看统计
    python tools/import_defense.py --dry      只预览不写
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import defense  # noqa: E402

SRC = Path(r"C:\Users\user\Desktop\新人问答")

# (文件, 工作表, 列名映射)
SOURCES = [
    (SRC / "资管交付新人能力成长计划.xlsx", "答辩记录", {
        "question": "评委问题",
        "answer": "候选人回答详情",
        "comment": "导师点评与补充",
        "product": "产品",
        "stage": "阶段",
        "who": "答辩人",
        "dept": "答辩人.部门",
        "boss": "答辩人.直属上级",
    }),
    (SRC / "答辩AI小结.xlsx", "问答AI记录", {
        "question": "评委问题",
        "answer": "学员回答详情",
        "comment": "导师点评与补充",
        "product": "产品",
        "stage": "阶段",
        "who": "学员",
        "dept": "学员.部门",
        "boss": "学员.直属上级",
    }),
]


def cell(v):
    return "" if v is None else str(v).strip()


def load_rows(path, sheet, m):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        wb.close()
        print(f"  ! 没有工作表 {sheet}，跳过")
        return []
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        wb.close()
        return []
    head = [cell(c) for c in rows[0]]
    idx = {k: head.index(v) for k, v in m.items() if v in head}

    out = []
    for r in rows[1:]:
        if not any(cell(c) for c in r):
            continue

        def g(key):
            i = idx.get(key)
            return cell(r[i]) if i is not None and i < len(r) else ""

        q = g("question")
        if not q:
            continue
        out.append({
            "question": q,
            "answer": g("answer"),
            "comments": [{"by": "导师", "text": g("comment")}] if g("comment") else [],
            "tags": [g("product")] if g("product") else [],
            "batch": g("stage"),
            "role": "交付工程师",
            "dept": g("dept"),
            "boss": g("boss"),
            "src_file": f"{path.name} / {sheet}",
            "type": "社招",        # 数据里没有应届/社招之分，统一按社招（人已确认不区分）
        })
    wb.close()
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = sys.argv[1:]
    if "--stat" in args:
        s = defense.stats()
        print(f"记录总数: {s['total']}")
        print(f"  按产品(标签): {s['top_tags'][:10]}")
        print(f"  按阶段(批次): {s['batches']}")
        print(f"  有回答 {s['with_answer']} / 有点评 {s['with_comment']}")
        return

    all_recs = []
    print("读取源文件：")
    for path, sheet, m in SOURCES:
        if not path.exists():
            print(f"  ! 文件不存在: {path}")
            continue
        recs = load_rows(path, sheet, m)
        print(f"  {path.name} / {sheet}: {len(recs)} 条")
        all_recs += recs

    print(f"\n合计读出 {len(all_recs)} 条")

    # 内容完全相同的(同问题同回答)会被 make_id 判成同一条，这里先看一眼重复量
    ids = [defense.make_id(defense.normalize(r)) for r in all_recs]
    print(f"  去重后唯一: {len(set(ids))} 条（重复 {len(ids) - len(set(ids))}）")

    if "--dry" in args:
        print("\n预览前 3 条：")
        for r in all_recs[:3]:
            print(f"  [{r['tags']}][{r['batch']}] {r['question'][:50]}")
            print(f"     答: {r['answer'][:60]}")
            print(f"     评: {(r['comments'][0]['text'] if r['comments'] else '')[:60]}")
        return

    res = defense.add_records(all_recs)
    defense.log_import({"file": "Excel 合并导入", **res})
    print(f"\n导入完成: 新增 {res['added']}，跳过重复 {res['skipped']}，现有 {res['total']} 条")

    import defense_search
    defense_search.invalidate()
    s = defense.stats()
    print(f"\n现状：")
    print(f"  按产品: {s['top_tags']}")
    print(f"  按阶段: {s['batches']}")


if __name__ == "__main__":
    main()
