# -*- coding: utf-8 -*-
"""方案 X 验证：让模型**看原图**判断图文是否对应。用 8 条人工标注样本。"""
import sys
import time

sys.path.insert(0, "app")
import imgindex  # noqa: E402

KB = "20261019_121148_5a7a33"
idx = imgindex.load_index(KB)
by_sum = {v.get("summary", ""): v for v in idx["images"].values()}


def pick(frag):
    for s, v in by_sum.items():
        if frag in s:
            return v
    return {"summary": frag, "kind": "?", "ocr": "", "docs": []}


CASES = [
    # (段落, 图片摘要片段, 期望保留?, 说明)
    ("1. 进入天鉴运维控制台，依次点击【应用服务】→【发布物管理】→【发布物】→【添加】，即可上传需要升级的发布物。",
     "发布物管理页弹出「发布物添加」弹窗", True, "讲上传入口，图就是上传弹窗"),
    ("**1. 添加应用服务器** 打开天鉴->计算机资源管理->服务器，点击「添加」，新增服务器并设置相关参数后保存",
     "用红框和序号标出进入「计算资源管理-服务器」并点击「+添加」", True, "讲添加服务器路径"),
    ("**2. 部署组件时添加/选择服务器** 点击【服务器】参数的右侧加号，高可用模式服务器选两台；负载模式下需要选 2 个节点，一个节点编号是 0、另外一个是 1",
     "红框标注节点序号主机配0、备机配1", True, "讲节点编号0/1"),
    ("**1. 进入配置入口** 在【应用服务】界面左侧点击「计算资源管理」->「数据库服务器」，随后在右侧的页面点击上方的添加",
     "计算资源管理-服务器页面，红框标出左侧「服务器」菜单和「+添加」", True, "讲点菜单再添加"),
    ("**1. 进入配置入口** 在【应用服务】界面左侧点击「计算资源管理」->「数据库服务器」，随后在右侧的页面点击上方的添加",
     "新增 Oracle 数据库配置页，填写地址 10.188.130.92", False, "段落只讲点菜单，图在填参数"),
    ("1. **DAIR_OTRADE 服务**：安装目录填 `/home/llm/dair_demo`，miniforge 路径为 `/home/llm/miniforge3`。",
     "miniforge路径 输入框填写为 /home/agent", False, "路径值不同"),
    ("**3. 部署前服务器上的用户与 hosts 配置** hix 和 Oorder 部署前需要在应用服务器 /etc/cron.allow 中添加对应的用户",
     "hix的mq配置界面，红框标出连接串需填写IP:服务端口", False, "讲 hosts，图是 MQ"),
    ("**四、环境依赖** demo 客户端运行于 Windows 机器，需提前安装 .NET 6 运行环境；后台应用服务器需安装 JDK 1.8 及以上",
     "服务组件配置页展示 ZooKeeper-demo 的地址、端口 2181", False, "讲 .NET/JDK，图是 ZK"),
]

print(f"方案 X：模型看原图判定，共 {len(CASES)} 条样本\n")
pairs = [(p, pick(f)) for p, f, _, _ in CASES]
for (_, frag, _, _), (_, im) in zip(CASES, pairs):
    print(f"  {frag[:38]:40} 图 {im.get('docs',[{}])[0].get('name','?') if im.get('docs') else '缺失'}")

t0 = time.time()
res = imgindex.judge_many(pairs, workers=3, kb_id=KB)
dt = time.time() - t0

ok = 0
keep = sum(1 for r in res if r)
print()
for (para, frag, want, why), got in zip(CASES, res):
    mark = "正确" if got == want else "错误"
    if got == want:
        ok += 1
    print(f"[{mark}] 期望={'保留' if want else '剔除'} 实际={'保留' if got else '剔除'}")
    print(f"       段落: {para[:54]}")
    print(f"       图  : {frag[:54]}")
    print(f"       理由: {why}\n")

print(f"准确率 {ok}/{len(CASES)}   保留 {keep} 条 / 剔除 {len(res)-keep} 条")
print(f"耗时 {dt:.1f}s（{dt/len(CASES):.1f}s/条，并发3）")
