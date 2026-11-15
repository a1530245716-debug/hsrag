# hsrag_public 同步脱敏清单（待人工确认）

> 目的：内部 `docs/ai-fde` 已经记录到阶段 1-3 完成（2026-11-15），
> 公开作品集 `hsrag_public` 仍停在 2026-11-11 的“优化前”版本。
> 本清单只做同步准备，**不推送**；确认后再在公开仓库开分支更新。

## 1. 两端状态

| | 内部 main | 公开仓库 |
|---|---|---|
| 位置 | `project/rag`，`b13e99a` | `github.com/a1530245716-debug/hsrag_public`，`9055ca2`（2026-11-11） |
| 内容 | fde 00-09 + 实现代码 + 数据 | 脱敏后的 01-08 + 本地 demo，无生产代码/数据 |
| 状态 | 阶段 1-3 完成；eval 6/6；global_max=6 | 只有优化前基线（100 并发 315s）和目标架构 |

## 2. 公开文件 ↔ 内部来源

| 公开文件 | 对应内部 | 是否需要更新 |
|---|---|---|
| `README.md` | 根 README + `docs/ai-fde/README` | **是**：一页结论加“优化后”结果 |
| `docs/case-study.md` | `01-需求与现状评估` | **是**：保留基线，新增“改造后”小节 |
| `docs/technical-notes.md` | `07-技术笔记` | **是**：补倒排/mmap/重排/异步化/多用户压测的实测 |
| `docs/evaluation.md` | `04-PoC实施与评测` | **是**：补 eval 6/6 与多用户压测方法/数据 |
| `docs/architecture.md` | `03-目标架构` | 小改：BFF 侧已有 `RetrievalBackend` 实现 |
| `docs/selection.md` | `02-技术选型` | 否 |
| `docs/deployment.md` | `05-生产部署` | 否（等服务器阶段） |
| `docs/adoption.md` | `06-上线推广` | 否 |
| `docs/fde-playbook.md` | `00-FDE交付方法论` | 否 |
| `docs/learning-roadmap.md` | `08-FDE学习` | 否 |
| `docs/poc/*` | `poc/*` | 否（只做脱敏核对） |
| 无对应 | `09-并发重构方案` | **建议新增** `docs/optimization.md` |

## 3. 敏感信息扫描结果

**公开仓库当前状态：干净。**
- 未发现客户名、内部域名、账号、Key、真实 IP。
- `127.0.0.1` 与 `192.0.2.x / 198.51.100.x` 是本地/文档示例地址（RFC 5737），可保留。
- `nginx-sse.conf` 里 `10.20.0.0/16`、`10.30.0.0/24` 是注释示例，可保留。

**内部来源在同步前需要替换：**

| 文件 | 行 | 内容 | 建议替换 |
|---|---|---|---|
| `01-需求与现状评估.md` | 126 | “鑫元库” | “某基金客户库” |
| `09-并发重构方案.md` | 24 | “鑫元库 56426 块” | “客户库 56,426 块” |
| `09-并发重构方案.md` | 388 | “etf2.0 与鑫元库” | “客户知识库” |
| `09-并发重构方案.md` | 390 | “用户 1000” | “评测账号” |
| `09-并发重构方案.md` | 353/403/410 | “16 账号 / bench” | “16 个测试账号”（不写用户名） |

同步后统一再扫：
`鑫元|光大|星桥|恒生|hundsun|lightcode|tail22c770|ts\.net|\.icu|E:\\doc|
密码|password|api_key|xhy200326|Admin49d07c66|benchPass2026`

## 4. 建议新增 `docs/optimization.md`（脱敏版 09）

只写方法与结果，不写内部路径/账号。核心内容：

1. **问题与基线**：100 并发 p95 315.5s；health p95 2.0s；根因是线程池 + GIL + 重排 2 路 + 无连接池。
2. **阶段 1 止血**：三层限流（全局/每用户/每 IP）+ 429 + `Retry-After`；health async。
   50 并发首字 127.4s → 8.5s（被拒请求立即 429）。
3. **阶段 2 异步化**：`chat_async` / `stream_async` + 共享 `AsyncClient`；SSE 纯异步；
   50 并发放行首字 p50 **15.2s**，30/50 并发基本持平（线程池不再是瓶颈）。
4. **检索侧（Claude Code）**：
   - BM25 内存 CSR 倒排：查询 75–172ms → **1.6–7.0ms**（20–49x），top-k 逐位不变，
     释放 `tf_list` 约 330MB。
   - 向量按查询 mmap：加载 28.8ms → 1.2ms，多线程首载不再重复 8×115MB；
     Windows 上 memmap 会阻塞 `os.replace`（WinError 5），所以按查询打开/关闭 + 写者优先锁。
   - 重排实测：默认线程 + 并发 1 最优（0.30–0.32 job/s）；并发 2 只有 0.28 且单请求翻倍；
     `intra_op_num_threads=2` 更差。**不做静默降级**，过载由准入控制 429 决定。
   - 图片知识文本化：1640 + 1225 张图的 summary/OCR 写成独立 chunk；
     答案可直接引用图中文件用途（实测）。
5. **阶段 3 解耦**：`RetrievalBackend`（`local_bm25` / `mock` / `ragflow` 骨架），
   4 处检索调用改走 `backend.retrieve`；字段契约 `id/path/text/score` 必需。
6. **多用户压测与 global_max=6**（16 账号 / 16 tokens / 20 并发）：

| global | 放行 | 429 | 首字 p95 |
|---|---|---|---|
| 4 | 4/20 | 16 | 16.1s |
| 6 | 6/20 | 14 | 25.9s |
| 8 | 8/20 | 12 | 37.6s |
| 12 | 12/20 | 8 | 53.8s |
| 20 | 20/20 | 0 | 85.8s |

   吞吐从 4 并发起恒定 **0.20 条/秒**；8 只增延迟不增吞吐。最终取 **6**。

7. **验收**：`eval` 6/6（6 条问答）；`/api/health` 空闲 8–11ms；
   建索引峰值 192.5MB / 56,870 块 / 213s（Python 分配下界）。

## 5. 不要同步的内容

- `app/data/`（客户文档、chunks、向量、图片、用户/注册表）
- `app/config.json`（API Key/网关地址/口令）
- `eval/results/*.json`（含真实问题/答案）
- 真实问答日志、截图、备份、`.bak`
- `docs/agents/`（内部协作记录）
- 任何客户名、内网 IP、内部域名、账号、Key

## 6. 推送前检查

- [ ] 新增/修改文件再过一遍敏感正则（见第 3 节）
- [ ] README“At a glance”同时保留 before/after，不夸大
- [ ] 所有数字写清测试条件（16 vCPU、mock LLM 2.3s、63,363 块）
- [ ] 公开仓库 `git status` 只包含预期文件
- [ ] 人工通读新增的 `optimization.md`
- [ ] 用 `git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 push`

## 7. 操作步骤（确认后执行）

1. 在临时目录 clone 公开仓库，新建分支 `docs/post-optimization`；
2. 按第 2、4 节更新 README/technical-notes/evaluation/case-study，新增 optimization.md；
3. 跑第 3 节正则扫描 + `git diff` 人工复核；
4. 确认后推送分支，合并到公开仓库 main；
5. 回写内部 CHANGELOG（公开仓库提交号）。
