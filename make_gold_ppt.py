# -*- coding: utf-8 -*-
"""金点子大赛 PPT - 项目智能问答助手(RAG)。16:9 深蓝商务风。"""
import sys
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

OUT = "项目智能问答助手-金点子大赛.pptx"

# ---------- 主题色 ----------
NAVY   = RGBColor(0x0F, 0x2A, 0x4A)   # 深蓝
BLUE   = RGBColor(0x1E, 0x6F, 0xE0)   # 主蓝
LBLUE  = RGBColor(0x4D, 0xA6, 0xFF)   # 亮蓝
CYAN   = RGBColor(0x25, 0xC8, 0xC0)   # 青
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)
GREY   = RGBColor(0x8A, 0x9B, 0xB0)
DARK   = RGBColor(0x22, 0x2E, 0x3D)
LIGHT  = RGBColor(0xEE, 0xF4, 0xFB)
GOLD   = RGBColor(0xF5, 0xB8, 0x41)

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

# ---------- 工具 ----------
def add_rect(slide, x, y, w, h, fill, line=None, shape=MSO_SHAPE.RECTANGLE, shadow=False):
    sp = slide.shapes.add_shape(shape, x, y, w, h)
    sp.fill.solid(); sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line; sp.line.width = Pt(1)
    sp.shadow.inherit = False
    return sp

def add_text(slide, x, y, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
             space_after=6, line_spacing=1.0):
    """runs: list of paragraphs; each paragraph = list of (text, size, color, bold)"""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0; tf.margin_right = 0; tf.margin_top = 0; tf.margin_bottom = 0
    first = True
    for para in runs:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.space_after = Pt(space_after)
        p.line_spacing = line_spacing
        for (t, size, color, bold) in para:
            r = p.add_run(); r.text = t
            r.font.size = Pt(size); r.font.color.rgb = color; r.font.bold = bold
            r.font.name = "Microsoft YaHei"
    return tb

def p1(t, size=18, color=WHITE, bold=True):
    return [(t, size, color, bold)]

def kicker(slide, text, color=CYAN):
    add_text(slide, Inches(0.6), Inches(0.42), Inches(12), Inches(0.4),
             [p1(text, 14, color, True)])

def bottom_bar(slide, idx):
    add_rect(slide, Inches(0.6), Inches(7.02), Inches(12.1), Pt(2), BLUE)
    add_text(slide, Inches(0.6), Inches(7.1), Inches(12), Inches(0.3),
             [[("项目智能问答助手 · 金点子大赛  |  %02d" % idx, 11, GREY, False)]])

def tile(slide, x, y, w, h, title, body_runs, accent=BLUE, tsize=16, bsize=12.5):
    add_rect(slide, x, y, w, h, LIGHT)
    add_rect(slide, x, y, Pt(4), h, accent)
    add_text(slide, x + Inches(0.22), y + Inches(0.16), w - Inches(0.4), Inches(0.4),
             [p1(title, tsize, NAVY, True)])
    add_text(slide, x + Inches(0.22), y + Inches(0.62), w - Inches(0.44), h - Inches(0.7),
             body_runs, space_after=5, line_spacing=1.12)

# =====================================================================
# 1. 封面
# =====================================================================
s = prs.slides.add_slide(BLANK)
add_rect(s, 0, 0, prs.slide_width, prs.slide_height, NAVY)
add_rect(s, 0, Inches(5.9), prs.slide_width, Inches(1.6), RGBColor(0x0A, 0x1E, 0x38))
# 装饰圆环
for (cx, cy, r, col) in [(Inches(11.3), Inches(1.1), Inches(2.6), RGBColor(0x16, 0x3A, 0x66)),
                          (Inches(12.1), Inches(0.6), Inches(1.5), RGBColor(0x1B, 0x45, 0x77))]:
    add_rect(s, cx - r, cy - r, r * 2, r * 2, col, shape=MSO_SHAPE.OVAL)

add_text(s, Inches(0.9), Inches(1.5), Inches(11), Inches(1.0),
         [[("金点子大赛 · 参赛作品", 17, GOLD, True)]])
add_text(s, Inches(0.9), Inches(2.0), Inches(11.5), Inches(2.2),
         [[("让海量项目文档", 46, WHITE, True)],
          [("“开口说话”", 46, LBLUE, True)]], space_after=2)
add_text(s, Inches(0.9), Inches(4.15), Inches(11.6), Inches(0.7),
         [p1("项目智能问答助手 —— 一套开箱即用的本地 RAG 智能问答系统", 20, WHITE, False)])
add_text(s, Inches(0.9), Inches(4.95), Inches(11.6), Inches(0.7),
         [[("One-line  /  ", 15, GREY, False),
           ("把“翻文档找答案”变成“直接提问得答案”，并给出引用依据", 15, GREY, False)]])
add_text(s, Inches(0.9), Inches(6.25), Inches(11), Inches(0.9),
         [[("汇报人：某公司交付工程师  |  项目：项目交接知识库智能问答", 15, WHITE, False)],
          [("Base: 某基金公司智能交易平台交接场景  ·  已实现外网访问", 12, GREY, False)]])

# =====================================================================
# 2. 痛点
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "01 · 我们看到了什么问题")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(1.0),
         [p1("一个交付工程师每天都在面对的“找”字"), True and [(t, 28, NAVY, True) for t in ""] or
          [(t, 28, NAVY, True) for t in ("一个交付工程师每天都在面对的“找”字",)]])
tiles = [
    ("📚 资料又多又散", "项目交付资料动辄几百份：\n需求、设计、接口、测试、部署、周报……\n散落在不同文件夹、不同人手里。", BLUE),
    ("🔍 翻找效率极低", "新人上手一个项目要翻几天文档；\n查一个技术点要一层层打开目录。\n“记不清在哪份文档里”是常态。", CYAN),
    ("🤝 交接严重依赖人", "人员变动时，经验存在个人脑子里；\n“这个人走了，文档就没人讲得清”。\n交接成本高、断档风险大。", GOLD),
]
x0, y0, tw, th, gap = Inches(0.6), Inches(2.1), Inches(3.95), Inches(3.3), Inches(0.12)
for i, (t, body, c) in enumerate(tiles):
    tile(s, x0 + (tw + gap) * i, y0, tw, th, t,
         [[(line, 13, DARK, False)] for line in body.split("\n")], accent=c, tsize=19)
add_rect(s, Inches(0.6), Inches(5.62), Inches(12.13), Inches(1.0), NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text(s, Inches(0.95), Inches(5.82), Inches(11.6), Inches(0.7),
         [[("核心痛点一句话：", 16, GOLD, True),
           ("“文档就在那里，但答案要花大量时间找出来”——这就是我们想解决的问题。", 16, WHITE, True)]])
bottom_bar(s, 2)

# =====================================================================
# 3. 解决方案
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "02 · 我们的解决方案")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(1.0),
         [p1("把文档喂给“会读会答”的检索增强生成系统（RAG）", 28, NAVY, True)])
add_text(s, Inches(0.6), Inches(1.75), Inches(12), Inches(0.6),
         [p1("建设分三步走：文档入库 → 建立智能索引 → 自然语言问答", 15, GREY, False)])

# 三步流程
steps = [
    ("STEP 1", "文档入库", "批量解析 Word / Excel /\nPPT / PDF / 图片(OCR)，\n全格式自动抽文本、分块。", BLUE),
    ("STEP 2", "智能索引", "BM25 关键词 + \n本地向量语义双路建索引，\n同义表达也能命中。", CYAN),
    ("STEP 3", "智能问答", "输入问题 → 检索相关片段\n→ 大模型生成答案\n→ 流式输出 + 引用来源。", GOLD),
]
x0, y0, tw, th, gap = Inches(0.6), Inches(2.55), Inches(3.95), Inches(3.6), Inches(0.12)
for i, (tag, t, body, c) in enumerate(steps):
    x = x0 + (tw + gap) * i
    add_rect(s, x, y0, tw, th, LIGHT)
    add_rect(s, x, y0, tw, Inches(0.12), c)
    add_text(s, x + Inches(0.25), y0 + Inches(0.3), tw - 0.5, Inches(0.4),
             [p1(tag, 13, c, True)])
    add_text(s, x + Inches(0.25), y0 + Inches(0.7), tw - 0.5, Inches(0.6),
             [p1(t, 19, NAVY, True)])
    add_text(s, x + Inches(0.25), y0 + Inches(1.4), tw - 0.5, Inches(2.0),
             [[(line, 12.5, DARK, False)] for line in body.split("\n")], line_spacing=1.15)
# 箭头
for i in range(2):
    ax = x0 + (tw + gap) * (i + 1) - gap/2 - Inches(0.18)
    add_text(s, ax, y0 + Inches(1.5), Inches(0.4), Inches(0.5), [p1("→", 26, BLUE, True)])
bottom_bar(s, 3)

# =====================================================================
# 4. 技术架构
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "03 · 技术架构")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(0.8),
         [p1("检索增强生成：混合检索（BM25 + 语义向量）+ 重排 + 大模型生成", 24, NAVY, True)])

# 三层架构 - 用色块
layers = [
    ("应用层", "Web 前端（流式问答 / 知识库管理）· FastAPI + Uvicorn · 访问口令鉴权 · 外网 Tailscale 隧道", BLUE),
    ("能力层", "文档解析(6+格式/OCR) → 分块 → BM25 + bge-small-zh 语义向量 → bge-reranker 重排 → SSE 流式", CYAN),
    ("数据/模型层", "chunks 索引 + vectors 向量库(本地落盘) · 某公司内网大模型网关(Anthropic/OpenAI 协议)", GOLD),
]
y = Inches(1.95)
for name, desc, c in layers:
    add_rect(s, Inches(0.6), y, Inches(12.13), Inches(1.35), LIGHT)
    add_rect(s, Inches(0.6), y, Inches(2.1), Inches(1.35), NAVY)
    add_text(s, Inches(0.75), y + Inches(0.42), Inches(1.8), Inches(0.5), [p1(name, 17, WHITE, True)])
    add_text(s, Inches(2.95), y + Inches(0.25), Inches(9.6), Inches(0.95),
             [[(seg, 14, DARK, seg.startswith(("Web","文档","chunks")) )] for seg in ("",)] if False else
             [p1(desc, 14, DARK, False)], anchor=MSO_ANCHOR.MIDDLE)
    y += Inches(1.52)

# 底部流
add_text(s, Inches(0.6), Inches(6.62), Inches(12), Inches(0.4),
         [p1("数据流： 提问 → 检索召回(0.1s) → 重排精排 → 拼装上下文 → 大模型生成 → 逐字流式返回", 13.5, GREY, False)])
bottom_bar(s, 4)

# =====================================================================
# 5. 技术亮点
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "04 · 核心技术亮点")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(0.8),
         [p1("不只是“智能问答”，而是工程化的交付工具", 24, NAVY, True)])
cards = [
    ("🔐 本地部署 数据可控", "全部索引、向量、数据落盘本机\n/内网，公司内网大模型网关生成；\n外网访问走加密隧道+口令鉴权。", BLUE),
    ("🎯 混合检索 懂语义", "BM25 保字面召回 + 本地向量模型\n(bge-small-zh) 补语义召回；\n“佣金→货币中介费”也能命中。", CYAN),
    ("⚡ 重排精排 答案更准", "bge-reranker 对候选片段逐条精排，\n手册类加权、测试周报降权；\n每问必带引用来源，可信可溯。", GOLD),
    ("🛡 全流程工程化", "增量索引/断点续跑/上传进度条/\n可取消/并发限流/启动预热；\n安装即用，双击脚本即可上线。", BLUE),
]
pos = [(0.6, 2.0), (6.83, 2.0), (0.6, 4.55), (6.83, 4.55)]
for (t, body, c), (x, y) in zip(cards, pos):
    tile(s, Inches(x), Inches(y), Inches(5.9), Inches(2.3), t,
         [[(line, 12.5, DARK, False)] for line in body.split("\n")], accent=c, tsize=17)
bottom_bar(s, 5)

# =====================================================================
# 6. 实测数据
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "05 · 实测效果")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(0.8),
         [p1("用真实交付数据说话（某基金公司智能交易平台交接材料 · 5.7 万检索块）", 22, NAVY, True)])

# 指标卡
metrics = [
    ("~5 s", "热态单问完整回答\n(检索 0.1s+重排 1s+生成)"),
    ("5.7 万", "单库索引块数，全量可检索"),
    ("8 / 8", "并发提问全部成功\n(8 人同时不报错)"),
    ("0 失败", "批量 20 文件上传索引\n故障自动降级为纯检索"),
]
x0, y0, tw, th, gap = Inches(0.6), Inches(2.0), Inches(2.92), Inches(2.1), Inches(0.15)
for i, (num, desc) in enumerate(metrics):
    x = x0 + (tw + gap) * i
    add_rect(s, x, y0, tw, th, NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_text(s, x, y0 + Inches(0.25), tw, Inches(0.8), [p1(num, 30, GOLD, True)], align=PP_ALIGN.CENTER)
    add_text(s, x + Inches(0.15), y0 + Inches(1.15), tw - 0.3, Inches(0.9),
             [[(line, 11.5, WHITE, False)] for line in desc.split("\n")], align=PP_ALIGN.CENTER, space_after=3)

# 对比表
add_rect(s, Inches(0.6), Inches(4.5), Inches(12.13), Inches(2.3), LIGHT)
add_text(s, Inches(0.85), Inches(4.7), Inches(11.5), Inches(0.5),
         [p1("效率对比：同一个问题，以前 vs 现在", 17, NAVY, True)])
rows = [
    ("场景：新人上手陌生项目资料", "翻目录、逐份打开文档，约 1~2 天", "问一句“demo 是什么”，5 秒出答案", BLUE),
    ("场景：查一个具体技术点/配置", "凭记忆找件、层层翻找，10 分钟~1 小时", "直接提问+引用到具体文档片段，<10 秒", CYAN),
    ("场景：项目交接/人员变动", "依赖口口相传，覆盖不全、易断档", "交接包建库后可持续问答，知识留痕", GOLD),
]
yy = Inches(5.25)
for sc, old, new, c in rows:
    add_rect(s, Inches(0.85), yy, Inches(3.9), Inches(0.42), NAVY)
    add_text(s, Inches(0.95), yy + Inches(0.07), Inches(3.7), Inches(0.35), [p1(sc, 12, WHITE, True)])
    add_rect(s, Inches(4.85), yy, Inches(3.6), Inches(0.42), RGBColor(0xE8, 0xE3, 0xE5))
    add_text(s, Inches(4.95), yy + Inches(0.07), Inches(3.4), Inches(0.35), [p1("旧方式： " + old, 11.5, GREY, False)])
    add_rect(s, Inches(8.55), yy, Inches(4.0), Inches(0.42), RGBColor(0xD9, 0xEE, 0xFF))
    add_text(s, Inches(8.65), yy + Inches(0.07), Inches(3.8), Inches(0.35), [p1("本方案： " + new, 11.5, BLUE, True)])
    yy += Inches(0.5)
bottom_bar(s, 6)

# =====================================================================
# 7. 价值与收益
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "06 · 业务价值")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(0.8),
         [p1("金点子：一套工具，三类受益", 24, NAVY, True)])
items = [
    ("实施提效", "项目上手、实施排障、资料查询的时间\n从“天/小时”级降到“秒”级，\n把人力从翻文档中解放出来。", BLUE),
    ("知识沉淀", "项目经验与材料持续沉淀成可问答的知识库，\n人员变动不带走知识，\n交接从“口口相传”变为“有据可依”。", CYAN),
    ("跨项目复用", "同一套系统按项目建库即可复用；\n支持金融行业本地/内网部署的\n安全合规要求，可复制推广。", GOLD),
]
x0, y0, tw, th, gap = Inches(0.6), Inches(2.1), Inches(3.95), Inches(3.5), Inches(0.12)
for i, (t, body, c) in enumerate(items):
    tile(s, x0 + (tw + gap) * i, y0, tw, th, t,
         [[(line, 13, DARK, False)] for line in body.split("\n")], accent=c, tsize=18)
add_rect(s, Inches(0.6), Inches(5.85), Inches(12.13), Inches(0.8), NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
add_text(s, Inches(0.95), Inches(6.02), Inches(11.6), Inches(0.5),
         [[("一句话价值：", 15, GOLD, True),
           ("让组织里的项目知识“开口说话”，用一次提问替代无数次翻找。", 15, WHITE, True)]])
bottom_bar(s, 7)

# =====================================================================
# 8. 未来展望
# =====================================================================
s = prs.slides.add_slide(BLANK)
kicker(s, "07 · 未来展望")
add_text(s, Inches(0.6), Inches(0.95), Inches(12), Inches(0.8),
         [p1("从“单库问答”走向“团队知识中台”", 24, NAVY, True)])
futures = [
    "多轮对话与追问记忆",
    "多知识库联合检索 + 权限分级",
    "接入更多大模型/语义模型，检索更聪明",
    "从文档扩展至 语音/图片/视频资料",
    "一键生成交接报告、新人学习路线",
]
x0, y0, w, h, gap = Inches(0.6), Inches(2.0), Inches(5.9), Inches(1.0), Inches(0.18)
for i, f in enumerate(futures):
    x = x0 + (w + gap) * (i % 2)
    y = y0 + (h + gap) * (i // 2)
    add_rect(s, Inches(x), Inches(y), Inches(w), Inches(h), LIGHT, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_rect(s, Inches(x), Inches(y), Inches(0.14), Inches(h), BLUE)
    add_text(s, Inches(x + 0.3), Inches(y + 0.12), Inches(w - 0.5), Inches(h - 0.2),
             [p1("·  " + f, 15, DARK, True)], anchor=MSO_ANCHOR.MIDDLE)
bottom_bar(s, 8)

# =====================================================================
# 9. 结束页
# =====================================================================
s = prs.slides.add_slide(BLANK)
add_rect(s, 0, 0, prs.slide_width, prs.slide_height, NAVY)
add_rect(s, Inches(11.2), Inches(0.5), Inches(2.6), Inches(2.6), RGBColor(0x16, 0x3A, 0x66), shape=MSO_SHAPE.OVAL)
add_rect(s, Inches(11.9), Inches(1.0), Inches(1.2), Inches(1.2), RGBColor(0x1B, 0x45, 0x77), shape=MSO_SHAPE.OVAL)
add_text(s, Inches(0.9), Inches(2.5), Inches(11), Inches(1.5),
         [[("让每份文档都被问过、被用好", 38, WHITE, True)]], align=PP_ALIGN.CENTER)
add_text(s, Inches(0.9), Inches(4.0), Inches(11), Inches(0.6),
         [p1("谢谢聆听 · 欢迎交流", 24, LBLUE, True)], align=PP_ALIGN.CENTER)
add_text(s, Inches(0.9), Inches(5.0), Inches(11), Inches(0.8),
         [[("项目智能问答助手 —— 让项目知识“开口说话”", 16, GREY, False),
           ("  |  金点子大赛参赛作品", 16, GREY, False)]], align=PP_ALIGN.CENTER)

prs.save(OUT)
print("saved:", OUT, "| slides:", len(prs.slides.__iter__.__self__._sldIdLst))
