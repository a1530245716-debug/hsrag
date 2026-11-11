# 03 · 目标架构、数据与权限设计

> 对应 FDE 流程的**设计**阶段。核心原则：商品化能力交给成熟组件，差异化能力留在 BFF，权限后端强制，随时可回退。

## 1. 目标架构

```
用户浏览器
   │ HTTPS 443（内网 DNS + 内部 CA）
   ▼
Nginx / 公司统一网关
   │ 关闭响应缓冲、超时 >= 300s、上传上限 >= 100MB
   ▼
FastAPI BFF（无状态，可多副本）
   ├─ 登录/会话/权限（原有能力迁移到 Postgres + Redis）
   ├─ 知识库管理、分享、模拟答辩、笔记、配图
   ├─ 检索适配层（Adapter）
   │     ├─ 生产：RAGFlow API
   │     ├─ 备选：Elasticsearch + TEI/Infinity
   │     └─ 测试：本地假服务
   ├─ 答案缓存/限流/审计
   └─ SSE 流式代理（async，不占线程）
        │
        ├── RAGFlow
        │     ├─ 文档解析/OCR/切块/任务队列
        │     ├─ Elasticsearch / Infinity：BM25 + 向量
        │     └─ Rerank / Embedding 模型
        ├── 内部 LLM 网关（LiteLLM / one-api）
        ├── PostgreSQL：用户、知识库映射、问答日志、审计
        ├── Redis：缓存、限流、会话
        └── MinIO：文档、图片、导出文件
```

## 2. 组件职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Nginx/网关 | TLS、SSE 不缓冲、超时、限流、来源限制 | 业务权限 |
| FastAPI BFF | 登录、权限、业务规则、SSE 代理、审计 | 文档解析、向量检索 |
| RAGFlow | 解析、切块、索引、检索、重排、引用 | 你的业务权限和用户体系 |
| LLM 网关 | Key 池、配额、重试、熔断、模型路由 | 业务逻辑 |
| PostgreSQL | 结构化数据和审计 | 大文件、向量 |
| Redis | 热缓存、限流计数、会话 | 持久化主数据 |
| MinIO | 文档/图片对象存储 | 检索 |

## 3. BFF 统一接口（Adapter 模式）

业务层只依赖这些接口，不直接依赖 RAGFlow：

```python
class RetrievalBackend:
    def list_datasets(self) -> list[Dataset]: ...
    def upsert_document(self, kb_id: str, path: Path, meta: dict) -> str: ...
    def delete_document(self, kb_id: str, doc_id: str) -> None: ...
    def retrieve(self, kb_id: str, query: str, top_k: int) -> list[Chunk]: ...
    def answer_stream(self, kb_id: str, query: str, history: list, user: dict):
        """返回 SSE 事件流：status / sources / delta / done"""
```

- 生产实现：`RagflowBackend`
- 备选实现：`ElasticBackend`
- 测试实现：`MockBackend`
- 换内核只改一个文件，不动前端、权限和业务。

## 4. 数据模型（建议）

```sql
-- 用户与权限沿用现有语义，迁到数据库
users(id, username, name, role, password_hash, perms, created_at)
kb(id, name, owner_id, status, created_at, updated_at)
kb_dataset_map(kb_id, backend, dataset_id, config_json)
documents(id, kb_id, doc_id, path, version, hash, status, updated_at)
qa_log(id, ts, user_id, kb_id, question, answer_len, sources_json, latency_ms, model)
feedback(qa_id, vote, comment, ts)
audit_log(id, ts, user_id, action, target, detail_json)
```

**关键映射**：一个知识库对应一个 RAGFlow dataset；`kb_dataset_map` 记录后端和 dataset_id。这样换后端或重建库时不动业务表。

## 5. 权限设计

| 角色 | 建库 | 改库 | 提问 | 看引用 | 看原文 | 分享 |
|---|---|---|---|---|---|---|
| 管理员 | 是 | 是 | 是 | 是 | 是 | 是 |
| 库 owner | 是（自己的） | 是 | 是 | 是 | 是 | 是 |
| 被分享用户 | 否 | 否 | 是 | 是 | 是 | 否 |
| 普通用户 | 否 | 否 | 自己的 | 是 | 是 | 否 |

**强制点在 BFF**：
1. 登录后解析用户。
2. 根据 `kb_id` 查 owner/shared_with。
3. 无权则 403，不进入检索。
4. 只向 RAGFlow 查询用户有权的 dataset。
5. 引用和原文下载再次校验，避免猜 URL 越权。

**不要在以下地方做权限**：前端隐藏按钮、RAGFlow 的租户名、URL 参数。

**同一 dataset 内权限不同怎么办**：
- 优先按权限拆 dataset（简单、可靠）。
- 若平台支持 metadata filter，用 `owner/group` 元数据过滤，并在 BFF 再校验。
- 不要把不同权限文档混在同一 dataset 里只靠提示词约束。

## 6. 增量与版本

**入库流程**

```
上传 → MinIO 落盘（临时文件 + 原子改名）
     → 写 documents(status=pending)
     → 投递解析任务（队列）
     → RAGFlow 解析/切块/向量化
     → 回调或轮询更新 status=ready/failed
     → 清理旧版本（保留 N 天）
```

**原则**
- 幂等：同一文件哈希重复上传不重复建索引。
- 可重试：解析失败进入重试队列，超过次数标记 failed。
- 版本：`documents.version` 递增；保留上一版索引，支持回滚。
- 删除：先标记删除，确认无引用后再物理删除。
- 增量：只处理新增/变更文档；全量重建作为运维操作，不在请求路径。

## 7. 流式与并发

- BFF 用 `async def` + `httpx.AsyncClient` 代理 RAGFlow 的 SSE。
- 不要用同步生成器（会占线程池）。
- 统一事件协议：`status / sources / delta / done / error`。
- 客户端断开时取消上游请求，避免继续消耗额度。
- 限流：全局活跃问答数、每用户并发数、每 IP 频率。
- 超载返回 429 + `Retry-After`，不要让用户等几分钟。

## 8. 部署拓扑（第一阶段）

```
一台 16C/32G Linux（建议 64G）
  ├─ Nginx
  ├─ BFF（1~2 副本）
  ├─ RAGFlow 及其依赖（ES/Infinity、MySQL、MinIO、Redis）
  ├─ 内部 LLM 网关
  └─ 监控（Prometheus/Grafana 可选）
```

- 先 Docker Compose，不急于 K8s。
- 数据盘用本地 SSD；备份到 NAS。
- 100 人在线不需要微服务集群；先把检索/推理从 Web 进程拆出去。
- 如果 RAGFlow 的 ES 占用较高，把 ES 单独放一台或换 Infinity。

## 9. 迁移路线与回滚

| 阶段 | 动作 | 回滚 |
|---|---|---|
| PoC | 测试机部署 RAGFlow，导入一个演示库 | 不影响生产 |
| 灰度 | 一个真实库走 RAGFlow，feature flag 控制 | 切回旧实现 |
| 并行 | 新旧系统同时回答，人工对比 | 关闭灰度 |
| 全量 | 所有库迁移，旧索引只读保留 N 天 | 恢复旧路由 |
| 退役 | 下线 BM25/向量/重排代码 | 依赖备份 |

- 每次迁移保留 `kb_dataset_map` 和原文档路径。
- 迁移前备份 `registry.json`、`chunks.jsonl`、`vectors.npy`、`users.json`。
- 灰度期间记录新旧的检索命中、回答质量、延迟，作为 Go/No-Go 依据。

## 10. 可观测性

必须能回答：
- 这次请求在哪个阶段慢？检索、重排、排队、LLM 首 token、生成？
- 错误是什么类型？检索空、权限拒绝、重排超时、LLM 429/5xx、客户端断开？
- 当前队列多长？活跃请求多少？线程/连接池是否打满？
- 哪个知识库/文档最常被问？哪些问题没命中？

建议指标：
- `rag_retrieve_latency_ms`、`rag_rerank_latency_ms`、`llm_ttft_ms`、`llm_total_ms`
- `ask_active`、`ask_queue_depth`、`ask_429_total`
- `retrieval_empty_total`、`permission_denied_total`
- `cache_hit_rate`、`index_task_duration_seconds`
- `process_cpu`、`process_rss`、`db_connections`

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| RAGFlow 资源占用高 | 16C/32G 起步；ES 独立或换 Infinity；压测确认 |
| 平台 API 变更 | 固定版本；Adapter 隔离；升级前回归 |
| 权限对不上 | BFF 强制；按权限拆 dataset |
| 增量解析失败 | 队列重试、失败告警、人工重导入 |
| 数据迁移丢失 | 备份 + 校验文档数/哈希 |
| 许可证/合规 | 法务/采购确认；只用内网 |
| 平台锁定 | 统一接口 + 备选 Elastic 实现 |

## 12. 设计评审检查表

```
[ ] 每个组件职责清晰，没有重复实现
[ ] 权限在后端强制，有越权测试
[ ] 增量/删除/版本/回滚都有方案
[ ] 流式是异步的，客户端断开能取消
[ ] 有全局和每用户限流
[ ] 每个阶段有延迟和错误指标
[ ] 单点故障有应对
[ ] 换检索内核不改业务层
[ ] 数据备份和恢复演练过
```
