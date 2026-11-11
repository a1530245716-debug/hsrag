# 多 Agent 协作约定

这个项目由**两个 AI agent 加一个人**共同维护。同时改同一个文件会互相覆盖，而且不会报错——
所以下面这些是硬约定，任何一方动手前都要先看。

- **RAG-Agent**（DeepSeek 会话侧）：检索链路、提示词、API、前端
- **Claude Code**（Claude 会话侧）：索引构建、向量库、数据管道
- **人**：最终决策，两边都听他的

## 一、文件归属

| 路径 | 归属 | 说明 |
|---|---|---|
| `app/kb.py` | **RAG-Agent** | 检索核心：BM25、版本权重、文件名加权、融合排序 |
| `app/server.py` | **RAG-Agent** | FastAPI 路由、SSE、提示词、引用构建 |
| `app/planner.py` | **RAG-Agent** | 查询拆解与追问改写 |
| `app/llm.py` | **RAG-Agent** | 网关调用、重试、超时 |
| `app/static/index.html` | **RAG-Agent** | 前端单页 |
| `app/embeddings.py` | **Claude Code** | 向量编码与存取 |
| `app/indexer.py` | **Claude Code** | 建索引 |
| `build_vectors.py` | **Claude Code** | 向量补建/排障脚本 |
| `app/config.json` | **共用** | 改前必须在 INBOX 留言 |
| `app/data/` | **共用（数据）** | 重建索引/向量前确认对方没在跑评测 |
| `eval/` | **RAG-Agent** | 问答评测集 |
| `models/`、`index/` | **共用** | 见下方禁令 |

不在表里的新文件，谁建谁负责，建完登记到 CHANGELOG。

## 二、四条硬规矩

1. **动手前**先读 `docs/agents/CHANGELOG.md` 的最后 20 行，确认对方没有正在改你要动的文件。
2. **改完登记**：在 `docs/agents/CHANGELOG.md` 追加一条（时间 / 文件 / 改了什么 / 为什么）。
3. **不跨归属改**。发现对方文件有 bug，写到 `docs/agents/INBOX.md`，不要直接改。
   跨归属改动只允许一种情况：对方明确在 INBOX 里交办给你。
4. **共用文件**（`config.json`、`app/data/`）动手前先在 INBOX 留言说明要做什么、大概多久。

## 三、禁令

- **绝不删除** `app/data/`、`models/`、`index/`。这些是几十分钟才能重建的东西。
- 不要改 `app/data/*/chunks.jsonl` 的字段结构（`id` / `path` / `chunk` / `text`），
  两边都依赖它，加字段可以，删改字段要先协商。
- 不要在没跑评测的情况下宣布"修好了"。评测命令见 `eval/README.md`。
- 重启服务（`app/server.py`）前，确认没有正在跑的评测或索引任务。

## 四、当前状态快照（2026-10-30 11:15）

### RAG-Agent 今天改的

| 文件 | 改了什么 |
|---|---|
| `app/kb.py` | 新增文档版本优先级（同族按修改时间/版本号降权 1.0/0.82/0.68）；文件名命中加权；把"降权"从 `path_boost` 拆成 `_path_weights` 返回二元组，查询时按问题内容决定是否生效（问"清单/台账"时不降权） |
| `app/server.py` | `_ASK_SYSTEM` 允许直接给出内部账号密码；降级文案不再糊 8 段原文；`_multi_search` 改并行 + `_diversify` 单文件最多占 2 个引用位；补 `stop_reason=max_tokens` 截断提示 |
| `app/planner.py` | 「问项目内容」强制补一条"系统架构 模块组成"子查询；短问句也能触发拆解 |
| `app/llm.py` | `_parse_json_or_raise` 让网关错误可读；流式失败自动重试 2 次；捕获 `stop_reason` |
| `app/static/index.html` | 失败/降级都显示「↻ 重试」；长路径单行省略+悬停看全；按钮不折行；清空上文有反馈；用户手册重写成使用者视角 |
| `app/config.json` | `max_tokens` 1500→3500；`read_timeout` 60→30 |
| `eval/` | 新建：6 条核心问答评测集 + 跑测脚本 |

### Claude Code 今天改的（根据文件时间戳和内容推断，请本人核实）

| 文件 | 改了什么 |
|---|---|
| `app/embeddings.py` | 新增 `ensure_vectors`（增量、断点续跑）、`save_vectors` 原子写、`complete`/`hashes` 字段 |
| `app/indexer.py` | 新增 `schedule_vectors`，建索引后自动触发向量构建 |
| `build_vectors.py` | 新增：手动补建向量脚本 |

### 数据状态（2026-10-30 13:45 更新，两个库都已按新版抽取器重建）

| 知识库 | chunk 数 | 向量 | 含图片标记的块 | 状态 |
|---|---|---|---|---|
| 某基金公司 `20261019_121148_5a7a33` | 56416 | (56416, 512) | 434 块 / 1039 个标记（占正文 0.0%） | `complete=True` |
| 某资管公司 `20261026_104215_3a4b92` | 2313 | (2313, 512) | 16 块 / 57 个标记 | 待重建（人要删了重传） |

两个库的 chunks 行数与向量行数、`vectors_meta.count` 三者严格相等。
EMF/WMF 已全部转成 PNG 缓存（某基金 118 张 + 某资管 35 张）。

两个库的 `chunks.jsonl` 字段都是 `['id','path','chunk','text']`，
`vectors_meta.json` 字段都是 `count/dim/model/provider/ts/ids/complete/hashes`。

## 五、协作流程

```
人提出需求
   ↓
接到需求的一方判断涉及哪些文件
   ↓
读 CHANGELOG 最后 20 行，确认没有冲突
   ↓
改代码 → 编译检查 → 重启服务 → 跑评测（如果涉及检索/提示词）
   ↓
在 CHANGELOG 追加一条
   ↓
如果是共用文件或需要对方配合，在 INBOX 留言
```

## 六、验证命令

```powershell
$py = "$env:USERPROFILE\anaconda3\envs\rag\python.exe"

# 编译检查
& $py -m py_compile app/kb.py app/server.py app/planner.py app/llm.py app/embeddings.py app/indexer.py

# 跑评测（服务要在跑）
& $py -u eval\run_eval.py

# 预热文档矢量图（EMF/WMF → PNG，用户点开引用时才不用等 1.6 秒）
& $py -u warm_images.py --stat    # 先看还有多少没转
& $py -u warm_images.py           # 全转（4 线程并行）
& $py -u warm_images.py <kb_id>   # 只转某个库

# 服务健康
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing

# 重启服务
$c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($c) { Stop-Process -Id $c.OwningProcess -Force; Start-Sleep 3 }
Start-Process $py -ArgumentList "app\server.py" -WorkingDirectory . -WindowStyle Hidden `
  -RedirectStandardOutput "server.out.log" -RedirectStandardError "server.err.log"
```

注意：冷启动要加载两个库的 BM25（约 1 分钟），首次查询会慢，别误判成卡死。
