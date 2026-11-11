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

## 运行方式一：直接在本机（最快迭代）

```bash
# 冒烟测试后退出
python tools/local_dev/run_local.py

# 保持运行，浏览器打开 http://127.0.0.1:8020
python tools/local_dev/run_local.py --keep
```

适合每天改代码：改完重启即可，不涉及镜像构建，Windows 上最快。

## 运行方式二：Docker（和服务器一致）

```bash
cd tools/local_dev
docker compose up --build
```

打开 `http://127.0.0.1:8020`。`mock-rag` 和 `bff` 分别跑在 9390/8020。

适合：

- 验证 Dockerfile/Compose/健康检查/网络别名（`http://mock-rag:9390`）；
- 上服务器前做一次“容器内能跑”的确认；
- 以后把 `mock-rag` 换成 RAGFlow 的 compose，BFF 的容器配置基本不变。

## 本机用 Docker 还是直接跑？

| 方式 | 优点 | 代价 | 建议 |
|---|---|---|---|
| 直接跑 | 启动快、改代码快、不占 Docker 资源 | 和服务器环境有差异 | 日常开发默认 |
| 本机 Docker | 环境一致、能验证 compose/网络/健康检查 | Windows 上慢一些；Docker Desktop 有许可要求；吃内存 | 每周做一次集成验证 |
| 服务器 Docker | 最终形态 | 需要服务器 | 上线前 |

推荐组合：**平时代码在宿主机跑，基础设施（RAGFlow/ES/Redis/Postgres）用 Docker；每周用 Docker 跑一次完整链路。** 服务器到位后，同一套 compose 换 `.env` 和镜像即可。

### Windows 本机 Docker 的注意点

- Docker Desktop 对较大规模企业可能要求付费许可，先确认公司政策；不允许就用 WSL2 + Docker Engine。
- 项目代码和演示数据放在 WSL2 文件系统里，别从 Windows 挂载大目录，否则 IO 很慢。
- 端口避开生产服务：本机演示用 9390/8020，不要占用 8000。
- 本机只剩 13.5GB 可用内存，RAGFlow + Elasticsearch 可能不够；本地先用假 RAG，RAGFlow 放到服务器或单独 VM。

## 上服务器怎么迁移

1. 服务器装 Docker Engine + Compose v2（Linux，不用 Docker Desktop）。
2. 把仓库/镜像带到服务器（内网镜像仓库或 `docker save/load`）。
3. 复制 `docker-compose.yml`，改环境变量：RAG 地址、并发、端口、密钥。
4. 数据用 named volume 或宿主机目录；不要把 Windows 路径写进配置。
5. 前面加 Nginx：TLS、SSE 不缓冲、超时 300s、上传 100MB。
6. 跑 `tools/ragflow_poc/check_env.py` 和 `poc/acceptance.md`。

迁移时真正会变的只有：

- `RAG_URL`（假 RAG → RAGFlow）；
- `.env`（地址、密钥、并发）；
- 数据卷位置。

代码、接口、前端、权限模型不需要重写。

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
