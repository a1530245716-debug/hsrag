# -*- coding: utf-8 -*-
"""第 0 步可行性验证：模型能不能像真评委那样出题和打分。

用仿真数据（结构按方案设计），跑一遍完整流程，人工看质量。
测完清空，不留垃圾数据。
"""
import sys

sys.path.insert(0, "app")
import defense  # noqa: E402
import defense_coach  # noqa: E402
import defense_search  # noqa: E402

HISTORY = [
    {"type": "应届生", "batch": "2025春招", "role": "交付工程师",
     "question": "O-order 部署的关键步骤是什么，最容易出问题的是哪一步",
     "answer": "先装数据库和中间件，然后配置参数，最后启动。容易出问题的是参数配置。",
     "comments": [{"by": "导师", "text": "他只说了'参数配置'，但没讲清具体要校验哪些参数、"
                                        "校验不过怎么回滚。这一块是现场最常翻车的地方，必须展开讲。"}],
     "tags": ["部署", "O-order"]},
    {"type": "应届生", "batch": "2025春招", "role": "交付工程师",
     "question": "客户现场 RabbitMQ 消息堆积，你怎么排查",
     "answer": "看队列长度，看看消费者是不是挂了，重启消费者。",
     "comments": [{"by": "导师", "text": "思路不完整。要先判断是生产端暴增还是消费端变慢，"
                                        "两者的处置完全不同；直接重启可能丢消息。"}],
     "tags": ["中间件", "RabbitMQ", "排障"]},
    {"type": "社招", "batch": "2025-07社招", "role": "交付工程师",
     "question": "股债基三类产品在清算流程上有什么区别",
     "answer": "股票 T+1，债券 T+0，基金 T+1。",
     "comments": [{"by": "导师", "text": "只背了时效，没讲清算环节的差异（交收、对账、"
                                        "差错处理），也没提各品种的风险点。"}],
     "tags": ["业务知识", "清算"]},
    {"type": "社招", "batch": "2025-07社招", "role": "交付工程师",
     "question": "回购业务里质押券管理要注意什么",
     "answer": "注意折算率和到期时间。",
     "comments": [{"by": "导师", "text": "漏了质押券替换和提前购回的处理，这是客户问得最多的两个场景。"}],
     "tags": ["业务知识", "回购"]},
    {"type": "应届生", "batch": "2025春招", "role": "交付工程师",
     "question": "风控系统里限价指令怎么校验的，讲一下链路",
     "answer": "按价格上下限做校验，超了就拒绝。",
     "comments": [{"by": "导师", "text": "太表面。要讲清校验发生在哪一层、"
                                        "和多账户/多市场规则怎么叠加、拒绝之后的业务流程。"}],
     "tags": ["业务知识", "风控"]},
]

MATERIAL = """我在某基金公司项目组实习了三个月，主要做了三件事：

1. 参与了交易系统升级的部署工作。按照部署手册完成了数据库、中间件和应用的安装配置，
   期间遇到过一次参数不匹配导致服务起不来的问题，排查后解决。

2. 负责日常运维值班，处理客户反馈的问题单。三个月共处理 40 多个问题单，
   主要集中在账号权限、报表数据不一致这两类。

3. 编写了一份《常见问题处理手册》，把重复出现的问题整理成了文档。

下阶段计划：希望能独立负责一个小模块的上线支持。"""

CRIT = [
    {"name": "准确性", "weight": 40,
     "desc": "术语、流程、产品规则是否说对，有无关键错误",
     "levels": "优秀(90-100分)：完全准确；良好(75-89分)：个别不准；合格(60-74分)：有错但不致命；不合格(60分以下)：关键错误"},
    {"name": "完整性", "weight": 30,
     "desc": "是否覆盖该问题的关键环节，有没有明显遗漏",
     "levels": "优秀(90-100分)：覆盖全面；合格(60-74分)：漏了次要环节；不合格(60分以下)：漏关键步骤"},
    {"name": "业务理解", "weight": 30,
     "desc": "能否讲清'为什么'，而不只是'是什么'",
     "levels": "优秀(90-100分)：能讲清原理和影响；合格(60-74分)：知道怎么做但说不清原因；不合格(60分以下)：只会背结论"},
]

print("=" * 70)
print("准备数据")
bak = defense.load_all()
defense.add_records(HISTORY)
defense.save_criteria(CRIT, source="仿真测试用")
defense_search.invalidate()
print(f"  历史记录 {len(defense.load_all())} 条，评分标准 {len(defense.load_criteria()['items'])} 项")

print("\n" + "=" * 70)
print("第 1 问（不答，先看问题质量）")
q1 = defense_coach.next_question(MATERIAL, [], timeout=90)
print(f"  >>> {q1}")

if not q1:
    print("  生成失败，终止")
    raise SystemExit(1)

print("\n" + "=" * 70)
print("模拟学员回答后，追问")
A1 = "我部署的时候是照着手册做的，先装数据库再配中间件。遇到过参数不匹配，改了就好了。"
q2 = defense_coach.next_question(MATERIAL, [{"q": q1, "a": A1}], user_answer=A1, timeout=90)
print(f"  学员答：{A1}")
print(f"  >>> 追问：{q2}")

A2 = "那次是数据库连接数配少了，服务起来就连不上库，我把连接数调大就正常了。"
print(f"\n  学员答：{A2}")

print("\n" + "=" * 70)
print("打分")
H = [{"q": q1, "a": A1}]
if q2:
    H.append({"q": q2, "a": A2})
res = defense_coach.score(MATERIAL, H, timeout=120)
if res.get("ok"):
    print(f"  总分: {res['total']}  等级: {res['grade']}")
    for s in res["scores"]:
        print(f"    {s['name']}: {s['score']}  {s['comment'][:70]}")
    print(f"  优点: {res['strengths']}")
    print(f"  不足: {res['gaps']}")
    print(f"  建议: {res['advice']}")
    print(f"  参照的导师点评条数: {res['refs_used']}")
else:
    print(f"  失败: {res}")

print("\n" + "=" * 70)
print("还原数据")
for p in (defense.records_path(), defense.criteria_path(), defense.imports_path()):
    p.unlink(missing_ok=True)
if bak:
    defense.add_records(bak, replace=True)
print(f"  还原后 {len(defense.load_all())} 条")
