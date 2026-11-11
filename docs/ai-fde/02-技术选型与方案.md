# 02 · 企业级 RAG 技术选型

> 对应 FDE 流程的**设计**阶段。目标：在“自研 / 平台 / 编排库”之间做出可解释、可回退的选择。

## 1. 企业里常见的组合

没有权威的“用得最多”排名；从招聘 JD、开源社区和企业部署看，主流是组合而不是单一框架：

| 层 | 常见选择 | 说明 |
|---|---|---|
| 检索引擎 | Elasticsearch / OpenSearch、Milvus、pgvector、Qdrant | ES 同时做文本和向量，运维熟悉；Milvus 适合大规模向量；pgvector 最轻 |
| RAG 平台 | RAGFlow、Dify、FastGPT、MaxKB、QAnything | 提供解析、索引、检索、引用、管理后台 |
| 编排 | 直接调 SDK、LlamaIndex；LangChain 次之 | 生产主链路更偏向可控的直接调用 |
| 模型服务 | vLLM、SGLang、Xinference、TEI、Infinity | 私有化部署 embedding/rerank/LLM |
| LLM 网关 | LiteLLM、one-api | Key 池、配额、重试、审计、多模型路由 |
| 部署运维 | Docker / K8s、Nginx、Prometheus + Grafana | 内网私有化常见组合 |

## 2. 平台/框架对比

| 维度 | RAGFlow | Dify | FastGPT | LangChain / LlamaIndex |
|---|---|---|---|---|
| 定位 | 深度文档 RAG 引擎 | LLM 应用/工作流平台 | 知识库 + 工作流 | 编排库 |
| 文档解析 | 强（PDF/表格/扫描件/OCR） | 中 | 中 | 取决于自己接的解析器 |
| 混合检索 | 内置（ES/Infinity） | 内置（多种向量库） | 内置（pgvector/Milvus） | 自己拼 |
| 增量索引 | 内置 | 内置 | 内置 | 自己做 |
| 工作流/Agent | 有，但不是主打 | 强 | 强 | LangGraph/LlamaIndex |
| 管理后台 | 有 | 强 | 有 | 无 |
| 私有化 | 强 | 强 | 强 | 取决于自研 |
| 二次开发 | API + 前端可定制 | API + 插件 | API | 完全自主 |
| 运维复杂度 | 较高（ES/MinIO/MySQL/Redis） | 中高（多组件） | 中 | 取决于自研 |
| 学习曲线 | 中高，但贴近企业 RAG | 低到中 | 中 | 高（抽象多） |
| 适合场景 | 文档重、要求引用准确 | 快速搭应用/工作流 | 中文知识库问答 | 需要高度定制 |
| 简历价值 | 企业 RAG 落地、文档解析、私有化 | LLM 应用/Agent/工作流 | 中文 RAG 落地 | 编程能力，但“会用 LangChain”不稀缺 |

> 许可证和商用条款以具体版本为准，推广前让法务/采购确认。

## 3. 决策标准（按本项目权重）

1. **文档解析质量**（权重最高）：中文 PDF、表格、扫描件、图片。
2. **检索质量与引用**：混合检索、重排、引用可追溯。
3. **增量与版本**：新增/更新/删除、回滚。
4. **私有化与安全**：内网、离线、权限、审计。
5. **并发与可扩展**：外部检索引擎、异步 API、可水平扩展。
6. **运维人力**：一个人能否维护。
7. **学习与简历价值**：能否接触企业真实栈。
8. **退出成本**：能否换掉而不重写业务。

## 4. 本项目的选择

**生产主链路：RAGFlow 做检索内核 + FastAPI BFF 保留业务 + 内部 LLM 网关。**

理由：
- 文档重、表格/扫描件多，RAGFlow 的解析和引用更贴合。
- 检索走 Elasticsearch/Infinity，绕开 Python BM25/GIL 和重排排队。
- 增量索引、文档管理、任务队列内置，减少自研。
- 你的差异化能力（权限、答辩、笔记、业务规则、UI）留在 BFF，不被平台绑死。
- 对 FDE 简历：这是“企业级 RAG 落地”，不是“低代码搭聊天机器人”。

**学习沙箱：Dify。**
- 用 1~2 周做工作流/Agent/插件/API 原型，补 LLM 应用编排的广度。
- 不进入生产主链路；避免权限、审计、定制被低代码限制。

**备选：FastGPT。**
- 如果 RAGFlow 资源占用太高、或团队更熟悉 Postgres/pgvector，可评估 FastGPT。
- 适合中文知识库问答 + 工作流，部署比 RAGFlow 轻一些。

**不作为主线：LangChain。**
- 它是编排库，不是并发方案；抽象多、版本变动快。
- 需要 Agent/多步工具调用时，局部用 LangGraph/LlamaIndex，或直接用平台工作流。
- 简历上“会用 LangChain”不稀缺；能讲清 RAG 架构、评测、并发和取舍才稀缺。

## 5. 为什么不是“全自研”

- 自研 BM25/向量/重排/解析/增量，等于重造一个搜索引擎，维护成本高。
- 一个人维护 100 并发 + 增量 + 权限 + 监控，平台化更稳。
- 但完全依赖平台也有风险：权限模型不匹配、API 变更、许可证、定制受限。
- 所以采用 **BFF + 适配层**：业务不直接依赖平台细节，换内核不改 UI/权限。

## 6. 退出策略（很重要）

在 BFF 定义统一接口：

```
search(kb_id, query, user) -> chunks[]
answer_stream(kb_id, query, history, user) -> SSE
upsert_document(kb_id, file) / delete_document(...)
```

- 生产实现：RAGFlow adapter。
- 备选实现：Elasticsearch + TEI/Infinity adapter。
- 测试实现：本地假服务。
- 业务层只依赖接口；换平台时改一个 adapter，不动前端和权限。

## 7. 架构决策记录（ADR 模板）

```
# ADR-00X：选择 RAGFlow 作为检索内核

日期：
状态：提议 / 已接受 / 已废弃

## 背景
（业务/数据/并发/运维约束）

## 选项
A. RAGFlow
B. FastGPT
C. 自研 Elasticsearch + BFF
D. LangChain/LlamaIndex

## 决定
选择 A，原因是……

## 后果
正面：……
负面：……
风险与缓解：……
回滚方式：……
```

## 8. 参考链接

- RAGFlow：https://github.com/infiniflow/ragflow
- Dify：https://github.com/langgenius/dify
- FastGPT：https://github.com/labring/FastGPT
- Elasticsearch：https://www.elastic.co/
- Milvus：https://github.com/milvus-io/milvus
- LlamaIndex：https://github.com/run-llama/llama_index

> 链接和版本会变；以部署时官方文档为准，不要凭记忆写 API。
