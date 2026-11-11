# 本地开发：假 RAG 后端 + 异步 BFF

不依赖 Docker、不依赖 RAGFlow，先把整条链路跑通：

```
浏览器 → 异步 BFF（8020） → 假 RAG 后端（9390）
```

## 它演示了什么

- **端到端异步**：`async def` 端点 + `httpx.AsyncClient` + 异步生成器，SSE 不占线程池。
- **适配层**：BFF 只依赖 `retrieval` / `chat/stream` 两个接口；以后换 RAGFlow 只改后端地址。
- **引用**：检索结果作为 `sources` 事件推给前端。
- **限流**：全局并发信号量，超过返回 429；可在 `MAX_CONCURRENT` 调整。
- **连接池**：共享 `AsyncClient`，不每次新建连接。
- **可观测**：`/api/health`、`/api/stats`。

## 运行

```bash
# 冒烟测试后退出
python tools/local_dev/run_local.py

# 保持运行，浏览器打开 http://127.0.0.1:8020
python tools/local_dev/run_local.py --keep
```

示例问题：

- 部署需要哪些安装包？
- 测试环境数据库地址是什么？
- 节点启动失败怎么排查？
- 上线前要检查什么？

## 目录

```
tools/local_dev/
  demo_docs/        合成演示文档（不含客户资料）
  mock_rag.py       假 RAG 后端（关键词检索 + 模板生成）
  demo_bff.py       异步 BFF + 简单前端
  run_local.py      一键启动 + 冒烟
```

## 接 RAGFlow 时怎么改

1. 部署 RAGFlow，创建 dataset，导入同一批测试文档。
2. 把 BFF 的 `RAG_URL` 指向 RAGFlow（或写一个 `RagflowBackend` 适配器）。
3. 保持 `retrieval` / `chat/stream` 的接口语义不变；前端和权限不动。
4. 用 `tools/loadtest/` 的假模型压测，对比前后 P95。

## 注意

- 这是**演示实现**：检索是关键词打分，生成是模板拼接，不代表最终质量。
- 演示文档使用 TEST-NET 保留地址和虚构账号，不对应任何真实环境。
- 公开到作品集前，再扫一遍客户名、公司名、内部域名、IP、账号和 Key。
