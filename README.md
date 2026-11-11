# 本地文档问答 RAG 系统

一个自包含的本地文档 RAG（**BM25 + 语义向量 + Rerank 混合检索** + 大模型生成）系统，从某基金公司智能交易平台项目交接场景迁移而来，现作为独立工具复用。

## 系统简介

**一句话**：把"翻文档找答案"变成"直接提问得答案"，并给出引用依据。

**解决什么痛点**：项目交付资料动辄几百份（需求、设计、接口、测试、部署、周报等），散落各处；新人上手靠翻目录、查知识点靠一层层找文档、项目交接靠口口相传——查找慢、上手难、交接断档。

**怎么解决**：将文档批量抽取、分块、建索引形成知识库；用户用自然语言提问，系统通过"关键词（BM25）+ 语义向量"混合检索相关片段，经重排精排后交给大模型生成带引用的回答。**一个文件夹 = 一个知识库，支持增量更新，随时补充随时问。**

**核心能力**：
- 📥 支持 Word / Excel / PPT / PDF / 文本 / 图片(OCR) 等全格式入库，增量索引、断点续跑
- 🔎 混合检索：字面命中（BM25）+ 语义理解（本地 bge-small-zh 向量），同义表达也能命中（如问"佣金"能命中"货币中介费"文档）
- 🎯 Rerank 重排精排，手册类加权、测试周报类降权，答案更准
- 💬 大模型生成回答（某公司内网网关），逐字流式输出，每条回答带引用来源
- 🔐 访问口令鉴权、外网目录浏览禁用；可选 Tailscale Funnel 安全外网访问
- ⚡ 热态单问约 5 秒出答案；并发限流、启动预热、上传进度/取消等工程化细节完备

## 目录结构

```
project\
├── app\                       # Web 服务（多知识库管理 + 问答）
│   ├── server.py              #   启动入口（鉴权 / 混合检索 / 流式问答 / 启动预热）
│   ├── indexer.py             #   多知识库注册表 + 增量索引
│   ├── kb.py                  #   BM25 检索 + 变体归一/文档加权 + 混合检索
│   ├── embeddings.py          #   本地语义向量化（bge-small-zh, ONNX）
│   ├── rerank.py              #   Rerank 精排（bge-reranker-base int8, 并发限流）
│   ├── auth.py                #   访问口令鉴权（登录 cookie / token）
│   ├── llm.py                 #   大模型调用（OpenAI / Anthropic 兼容协议）
│   ├── config.json            #   服务端口、数据目录、LLM、检索、鉴权配置
│   ├── data\                  #   知识库数据（registry.json + 每库索引 + vectors.npy）
│   └── static\                #   前端页面（含停止/重试、阶段状态提示）
├── build_vectors.py           # 为知识库构建向量库（断点续跑）
├── index\                     # 命令行 (index_docs.py) 构建的检索索引
├── ask.py                     # 交互式问答（命令行）
├── query.py                   # 单次检索（命令行）
├── index_docs.py              # 批量抽取文档并构建索引
├── diagnose.py                # 扫描源文件，报告抽取失败项
├── doc_ole_extract.py         # .doc 纯 Python 文本提取
├── ocr_extract.py             # 扫描型 PDF/图片 OCR 兜底
├── models\                    # 本地模型（embeddings + rerank, ONNX 格式）
├── start_rag.bat              # 一键启动(本机)
├── start_public.bat           # 一键外网暴露(Tailscale Funnel)
├── run_server.bat             # 启动器（使用 rag 环境的 Python）
├── RAGServer_autostart.cmd    # 登录自启(配合启动文件夹快捷方式)
└── README.md
```

## 启动 Web 服务

方式一（推荐）：双击 `start_rag.bat`，服务启动后（后台自动加载知识库索引期间即可开始预热）打开浏览器 `http://127.0.0.1:8000`。

方式二：命令行执行（先激活 conda 环境）

```
conda activate rag
python app\server.py                # 默认 http://127.0.0.1:8000
python app\server.py --port 9000
```

## 多用户（一人一把 key）

**key 即身份**：登录只输 key，不用输用户名，不同 key 进去就是不同的人，只能看到自己的知识库。

| 角色 | 权限 |
|---|---|
| **普通用户** | 只看得到、也只能操作自己的知识库；彼此完全隔离 |
| **管理员** | 可管理用户（增删、重置 key）、查看并操作全部知识库（列表里标注归属） |

- 用户表：`app/data/users.json`；口令用 PBKDF2-HMAC-SHA256（2 万轮）+ 每人独立 salt，不存明文
- 管理员 key 首次初始化时写入 `app/data/admin_key.txt`（可删，删前先记下）
- 管理员在页面「⚙ 更多 → 👥 用户管理」里增删用户、一键重置 key；新建用户后把生成的 key 发给对方即可
- 越权防护在服务端统一拦截：访问不属于自己的知识库直接 403，前端藏起来不代表安全

> 升级说明：首次启动自动把原来的 `auth.password` 迁移成一个普通用户（**现有知识库都归他**），
> 另建一个管理员账号；原口令照常可用，不会影响你现在登录。

服务提供：
- 创建知识库：绑定任意本地文件夹（增量索引），或拖拽上传文件建库；
- **写内容（手写笔记）**：点右上「✍️ 写内容」，直接打文字、**Ctrl+V 粘贴截图**即可存入知识库，不用自己造文件；
  存为 `upload/_notes/*.md`，可再次打开继续编辑（下拉框切换已有笔记）；
- 问答：**BM25 + 向量语义召回 + Rerank 精排**，再由大模型生成回答（未配置 LLM Key 时仅返回检索片段）；
- 访问控制：外网部署默认启用访问口令，目录浏览默认禁用。

> 关于「添加文件」：浏览器的安全策略不允许网页指定文件选择框的初始目录，
> 所以点「添加文件」时只能由你自己导航。为了少走弯路，点击后会先把**当前知识库的源目录**
> 弹出来（可直接复制），照着路径找即可；只写几段文字建议直接用「✍️ 写内容」。

## 环境（Anaconda / conda）

本机已安装 Anaconda（`C:\Users\<用户名>\anaconda3`），本项目使用独立环境 **rag**（Python 3.14）：

```
conda activate rag
python app\server.py
```

- `start_rag.bat` 会自动使用 `rag` 环境的 Python（该环境不存在时回退到 PATH 上的 python）；
- 在新机器上重建环境：安装 Anaconda 后执行 `conda create -n rag python=3.14`，再安装依赖
  `jieba fastapi uvicorn httpx python-docx python-pptx openpyxl xlrd pymupdf olefile pillow numpy onnxruntime tokenizers`；
- 本地模型（`models/`）：`bge-small-zh-onnx`（向量化，~91MB）、`bge-reranker-base-int8-onnx`（重排，~283MB）。模型缺失时检索自动降级为纯 BM25，不影响使用。

## 命令行用法

在项目根目录执行：

```
python ask.py -q "你的问题"          # 单次提问
python ask.py                        # 进入交互模式（/top N、/raw、/exit）
python query.py "你的问题" -k 8      # 直接打印命中片段
python index_docs.py                 # 从当前目录递归抽取文档，重建 index\
python diagnose.py                   # 检查有哪些文件抽取失败
python build_vectors.py <kb_id>      # 为指定知识库构建/增量更新向量库
```

## 配置

`app/config.json`：
- `server`：监听地址与端口；
- `data_dir`：知识库数据目录（相对项目根目录，默认 `app/data`）；
- `auth`：访问口令（`password`，也可用环境变量 `RAG_AUTH_PASSWORD` 覆盖）、`allow_fs_browse`（是否允许目录浏览，外网建议 false）、`token_ttl`（登录有效期秒，默认 7 天）；
- `llm`：大模型配置。也可用环境变量覆盖：`RAG_LLM_API_KEY`、`RAG_LLM_BASE_URL`、`RAG_LLM_MODEL`、`RAG_LLM_TIMEOUT`；
- `retrieval`：检索配置。
  - `top_k` / `hybrid_top_k`：最终返回片段数 / 混合召回的候选数（默认 8 / 15）；
  - `enable_vector` / `enable_rerank`：向量检索与重排开关（false 则降级为纯 BM25）；
  - `embedding`：向量模型（`bge-small-zh-onnx`，在 `models/` 目录；`provider` 默认 cpu，将来可切 `DmlExecutionProvider` 等）；
  - `normalize` / `authority` / `suppress`：同义词归一（如 O-Trade→demo）与文档类型加权规则（手册类加权、测试/周报类降权）；
  - 向量库文件：`app/data/<kb_id>/vectors.npy`（与 chunks.jsonl 行序对应），用 `python build_vectors.py <kb_id>` 构建（支持断点续跑）。

## 检索链路（混合检索）

```
提问
 ├─ ⓪ 查询规划（Query Planning，见下节；模糊/复合问题先拆成多个子查询）
 ├─ ① jieba 分词 + 变体归一（O-Trade/O-ORDER/HIX 等写法统一）
 ├─ ② BM25 召回（文档类型加权：手册×1.8 / 测试周报×0.4）
 ├─ ③ 本地向量（bge-small-zh, 512 维）语义召回
 ├─ ④ 融合 top hybrid_top_k（默认 15）
 ├─ ⑤ Rerank（bge-reranker-base int8）精排 → top_k（默认 8）
 └─ ⑥ 交给大模型生成（流式返回 + 引用来源）
```

- 模型缺省/推理失败/排队超时 → 自动降级：跳过向量或重排环节，仍可用 BM25；
- 并发控制：Rerank 同时最多 2 路推理，超出排队（120s 超时兜底），避免多人同时提问互相拖慢。

## 运维：备份 / 健康检查 / 评测

三件"平时无感、出事救命"的事，都有现成脚本。

### 备份（必须做）

```bash
python backup.py --light     # 轻量：用户表/配置/手写笔记，约 0.1MB，0.3 秒
python backup.py             # 全量：再加客户上传的原始文档，约 700MB
python backup.py --list      # 看现有备份
```

**只备份不可再生的数据**（用户表、知识库注册表、上传文件、手写笔记、manifest），跳过 `chunks.jsonl` 等可重建的索引。保留最近 7 份，自动清理。

> 建议：`--light` 每天跑（秒级），全量每周跑（原始文档丢了要麻烦客户重传）。

### 健康检查

```bash
python healthcheck.py                      # 查一次
python healthcheck.py --install            # 注册计划任务（需管理员）
```

检查 5 项：服务端口 / `/api/health` / **外网 Funnel 入口** / 磁盘剩余 / 最近备份是否过期。
外部入口这项最关键——服务进程活着但 Funnel 挂了，客户看到的是打不开，而系统内部毫无感知。

告警渠道：`health.log` + Windows 事件日志 + 可选 webhook（`config.json → health.webhook`）。

### 一键装定时任务

右键「以管理员身份运行」`install_tasks.bat`，会注册三个任务：

| 任务 | 频率 | 作用 |
|---|---|---|
| RAG-HealthCheck | 每 10 分钟 | 健康检查，异常告警 |
| RAG-Backup-Light | 每天 12:00 | 轻量备份 |
| RAG-Backup-Full | 每周日 03:00 | 全量备份 |

### 检索质量评测

调参前先建基线，否则改好改坏全靠感觉。

```bash
python eval/run.py                       # 跑评测集（默认走服务 API，热态 3 秒）
python eval/run.py --save baseline.json  # 存基线
python eval/run.py --compare baseline.json   # 改动后对比，看是变好还是变坏
python eval/run.py --verbose             # 看每题命中了哪些片段
```

**当前基线（某基金库，20 题）**：

| 指标 | 值 |
|---|---|
| recall@8 | **90.0%**（18/20） |
| MRR | 0.752 |
| 命中平均排名 | 1.50 |
| 20 题总耗时 | 2.6s（热态） |

已知未命中 2 题，都是真实优化线索：
- `回售行权锁定后 oorder 会自动同步吗` → 疑似知识库真缺口（文档里可能没写这块）
- `JRESCLOUD 是什么版本` → 文件命中但含关键词的片段没排进前 8，属于长文档分块/排序问题

扩充评测集最好的来源是真实的 `data/qa_log.jsonl`——用户实际问过的、答不上来的问题。

## 问答日志与反馈

每次问答都会记一行 `data/qa_log.jsonl`：问题、是否改写、是否拆解、命中数、最高分、是否兜底、耗时、答案摘要。
答案下方有 👍 / 👎，反馈同样落盘。

管理员在「⚙ 更多 → 📊 问答统计」能看到：

| 指标 | 用途 |
|---|---|
| **兜底率** | 知识库没答上的比例 —— 高了说明该补内容 |
| **差评率** | 答得不好的比例 |
| 高频问题 | 可以做成 FAQ 或走缓存 |
| 知识库没答上来的问题清单 | **直接就是补充文档的 TODO 列表** |
| 平均命中分 / P50 延迟 | 检索质量与速度趋势 |

也可以用命令行看：`python -c "import sys;sys.path.insert(0,'app');import qa_log,json;print(json.dumps(qa_log.stats(7),ensure_ascii=False,indent=1))"`

## 答案缓存

首问（无多轮上下文）且知识库命中的答案，按 `知识库 + 规范化问题` 缓存 24 小时：

- 命中时直接秒回，**省一次完整检索 + 一次大模型调用**
- 追问不走缓存（依赖上下文）
- 兜底答案（网搜/模型知识）不缓存（时效性强）
- 该库重建索引后自动清空该库缓存

## 大模型超时策略

网关偶发挂起时，既不让用户无限等，也**不会静默吞掉结果**：

| 阶段 | 默认 | 配置项 | 超时后用户看到 |
|---|---|---|---|
| 建连 | 10s | `llm.connect_timeout` | 网关不可达提示 |
| 首 token 等待 | 60s | `llm.read_timeout` | 「大模型超时：等了 60 秒…」+ 检索片段 + 重试按钮 |
| 整段生成 | 180s | `llm.total_timeout` | 保留已生成内容 + 「⚠️ 回答未完（超过 180 秒上限）」 |
| 非流式单次 | 180s | `llm.timeout` | 降级返回检索片段 |

三种情况都有**明确文案**，不会出现"转半天啥也没有"：

- 首 token 超时 → 提示已等多久、可重试、可换问法
- 生成中途超时 → **保留已生成的部分**，末尾标注 `⚠️ 回答未完`，并显示重试按钮
- 网关报错（5xx / 429 / 401） → 分别提示"网关故障/太频繁/鉴权失败"

等待期间页面会显示已等待秒数，超过 15 秒和 45 秒各给一次提示，随时可以点停止。

## 移动端

窗口宽度 ≤820px 时自动切换：侧栏变抽屉（点 ☰ 打开、选中后自动收起）、顶栏按钮缩小换行、统计卡片两列、弹窗全宽。

## 索引与检索互不阻塞

添加内容后要重建索引，**期间和使用后都不影响提问**：

| 机制 | 作用 |
|---|---|
| 索引线程后台跑 | 上传/保存后立刻返回，界面可继续用 |
| `chunks.jsonl` 原子写 | 先写 `.tmp` 再 `os.replace`，索引过程中查询不会读到半截文件 |
| **索引后台重载 + 旧索引顶用** | 索引完成后，查询**立即用旧索引返回**（毫秒级），新索引在后台加载，完成后自动切换 |
| 手动「重建索引」异步化 | 不再同步等待，只在后台跑并轮询状态 |

实测（5.7 万块库）：索引刚完成瞬间提问 **0.24s**（改造前会被同步加载卡住 60~120s）。

## 多轮对话（追问改写 + 上下文）

支持**接着上一句继续问**，不用每次把话说完整：

```
你：demo 部署要准备哪些包？
AI：（列出包清单）
你：那它的下载地址呢？        ← 以前这里就答非所问了
AI：（自动理解成"demo 部署包的下载地址"，检索并回答）
```

关键在于**检索也要跟着改写**——光把历史塞进提示词没用，因为检索器拿到"那它的下载地址呢"只能搜"下载地址"这几个字，根本搜不到 demo 的片段。

```
新问题 ──[规则判定]──> 依赖上文？──否──> 原样检索（零额外开销）
                        │
                        是
                        ↓
        [LLM 结合最近 3 轮改写成独立问题]（短超时 15s，失败则退回原问题）
                        ↓
                用改写后的问题 → 检索 → 生成（同时把最近 3 轮对话作为上下文）
```

| 判定条件 | 是否改写 |
|---|---|
| 没有历史（首问） | 否，零开销 |
| 短句（≤12 字） | 是 |
| 含指代/追问词（它、这个、那、还有、再、详细…） | 是 |
| 长句且不含追问词（如"换个话题：…"） | 否，避免旧话题污染 |

- 上下文只存在**浏览器内存**里：刷新页面即清空；切换知识库自动清空（避免串库）
- 发送按钮旁的「🆕 新会话」可手动清空上下文
- 改写发生时，界面会显示「🔗 理解成：xxx」，你能看到系统到底听懂了什么

实测改写效果：

| 你说 | 系统理解成 | 耗时 |
|---|---|---|
| 那它的下载地址呢 | demo 部署包的下载地址是什么？ | 5.2s（首次 12.3s） |
| 再详细说说 | 请详细说明 demo 部署需要准备的包有哪些。 | 1.2s |
| 换个话题：某基金公司的清算时间是什么时候 | 原样（不污染） | 0s |

## 查询规划（Query Planning / Agent 化检索）

用户提问常常**概括性强或描述不准**（如"demo和oorder有什么区别"），单次检索容易只召回其中一侧。
系统先做一次轻量"规划"，再分路检索：

```
原问题 ──[规则判定]──> 需要拆解？──否──> 直接用原问题检索（零额外开销）
                       │
                       是
                       ↓
              [LLM 拆解 1~4 个子查询]（短超时 15s，超时/失败则退回原问题）
                       ↓
        多路 hybrid_search（原问题 + 各子查询）
                       ↓
        合并去重（按片段 id，取最高分）→ 统一排序 → top_k → 大模型生成
```

**关键设计**

| 设计 | 说明 |
|---|---|
| 规则先行 | 短问题/单子句（`demo是什么`、`你好`）不拆解，0 额外延迟 |
| 原问题保底 | 子查询列表第一项永远是原问题，拆偏了也不会比改造前更差 |
| 短超时保护 | 拆解调用独立 15s 超时（`RAG_PLAN_TIMEOUT`），网关慢时快速放弃 |
| 可开关 | `config.json → retrieval.enable_planning`（true/false 热生效，无需重启） |
| 前端可感知 | SSE `plan` 事件会把子查询以标签形式展示（🧩 已拆解的子问题） |

**实测效果**（某基金公司库，BM25 召回对比）

| 问题 | 单查询召回 | 拆解后召回 | 增益 |
|---|---|---|---|
| demo和oorder有什么区别 | 8 片段 / 6 文档 | 12 片段 / 10 文档 | **+4 文档** |
| 正回购排券分户流程怎么操作，需要注意什么 | 8 / 5 | 12 / 7 | **+2 文档** |
| 询价意向确认和分户能否批量处理 | 8 / 4 | 12 / 5 | **+1 文档** |

拆解本身耗时 0.6~5s（视网关响应），且只在需要时发生。

## 性能表现（本机：Ryzen 7 8核16线程 / 31GB 内存 / 5.7 万块知识库）

| 场景 | 耗时常量 |
|---|---|
| 热态单次提问（含检索+重排+LLM 生成） | **约 5 秒**（检索 0.1s + Rerank 1s + LLM 网关生成 3~4s） |
| 冷启动（服务重启后首次提问） | 约 60~120s（后台预热加载 BM25 索引，一次性） |
| 5 路并发提问 | 全部成功，排队串行，单请求 9~18s |

## 鉴权（外网安全）

- 除 `/api/login`、`/api/auth/status`、`/api/health` 与静态资源外，所有 API 需登录（Cookie `rag_token` 或请求头 `X-RAG-Token`）；
- 访问口令：打开页面输入 `app/config.json` → `auth.password`（本部署默认由部署者配置，登录后可用全部功能）；
- `/api/fs/*` 目录浏览默认禁用（防本机目录结构泄露），建库请直接填完整路径；需要时设 `auth.allow_fs_browse=true` 后重启。

## 外网访问（Tailscale Funnel）

1. 安装 Tailscale：`winget install Tailscale.Tailscale`（已装则跳过），并完成登录授权；
2. 双击 `start_public.bat`：自动检查登录→确保服务在跑→`tailscale funnel 8000` 生成公网地址；
3. 公网地址形如 `https://<机器名>.<你的tailnet>.ts.net`，通过 `tailscale funnel status` 查看（Funnel 为后台持久化，重启电脑自动恢复）；
4. 外网用户访问需输入访问口令；
5. 关闭外网：`tailscale funnel off`（本机使用不受影响）。

> LLM 大模型调用发生在服务器端（走公司内网网关），与用户从公网接入无冲突；若内网网关临时不可达，问答自动降级为仅返回检索片段。

## 日常运维

- **自启**：`RAGServer.lnk` 已放入启动文件夹，Windows 登录自动拉起服务（最小化窗口常驻 8000）；
- **手动启动/停止**：双击 `start_rag.bat` 启动；停止在"RAG-Server"窗口按 Ctrl+C，或任务管理器结束对应 python 进程；
- **加文档 / 增量更新**：Web 界面拖拽或「添加文件」，之后会增量建立索引（新文件自动向量化说明见下）；
- **向量库维护**：重建索引后若向量库过期，执行 `python build_vectors.py <kb_id>` 重建向量（断点续跑）；
- **换口令**：改 `app/config.json` → `auth.password` 或设环境变量 `RAG_AUTH_PASSWORD` 后重启。

## 注意事项

- 所有命令行脚本按“当前工作目录 = 项目根目录”定位索引与数据，请在项目根目录运行。
- `index_docs.py` 会递归扫描当前目录（自动跳过 `app/`、`index/`、`__pycache__`、`.git`、`node_modules` 及隐藏目录）；Web 界面的知识库功能可直接绑定任意路径的源文件夹。
- “交接材料”知识库源路径已修正为 `E:\doc\示例项目\客户现场\某基金公司\文档\智能交易平台项目交接材料(1)`，“重建索引”可安全使用（增量更新）。
- 依赖：Python 3.14（`rag` conda 环境内已安装 jieba、fastapi、uvicorn、httpx、python-docx、python-pptx、openpyxl、xlrd、PyMuPDF、olefile、Pillow、numpy、onnxruntime、tokenizers）；OCR 兜底可选 `rapidocr_onnxruntime`。
- 向量模型目录：`models/bge-small-zh-onnx`（embedding）、`models/bge-reranker-base-int8-onnx`（rerank）；缺失时检索自动降级为纯 BM25。
