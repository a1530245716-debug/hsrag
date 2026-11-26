# Docker 部署手册（业务手册 O32 单库）

> 目标：长期用 Docker 交付当前应用；镜像只含代码/依赖，**知识库/模型/配置全部挂载**。
> 本机（开发）只负责构建和数据打包；服务器只装一次 Docker，之后换镜像 tag 发版。

## 1. 资产清单

在能上网的构建机（本机 Windows 用代理）准备：

| 资产 | 说明 |
|---|---|
| `deploy/assets/docker-20.10.24.tgz` | Docker 静态包（RHEL7 最后支持版本） |
| `deploy/assets/docker-compose` | Compose v2 二进制 |
| `deploy/assets/base-python312.tar` | `python:3.12-slim` 镜像（crane pull 转 docker load） |
| `deploy/wheels/` | Python 3.12 离线 wheels（manylinux2014/2_28） |
| `dist/o32-bundle/` | 业务手册单库数据 + 模型（见 `prepare_o32_bundle.py`） |
| `dist/rag-src.tar.gz` | 代码源码（不含 data/models/demo/docs） |
| `/opt/rag/config.json` | 配置（含 LLM Key；600；不打进镜像） |

`prepare_o32_bundle.py` 只复制 `20261108_174939_69c8b5`（业务手册 O32/资管培训），
并过滤 `registry.json`/`users.json`；不会带其他知识库。

## 2. 服务器目录

```
/opt/rag/
  src/             # 代码 + deploy/
  data/            # bundle/data -> /app/app/data
  models/          # bundle/models -> /app/models
  docs/            # bundle/docs -> /opt/rag/docs (ro)
  logs/            # -> /app/logs (rw)，按天 app-YYYYMMDD.log
  config.json      # -> /app/app/config.json (ro)
  assets/
  compose.yml
```

端口：应用 `8000`；如需 Nginx，宿主机 `8080/8443`；**不使用 8123**，80 保持 learn-center 不动。

## 3. 一次性安装 Docker（服务器）

```bash
tar xzf assets/docker-20.10.24.tgz -C /tmp
cp /tmp/docker/* /usr/local/bin/
cp deploy/docker.service /etc/systemd/system/docker.service
systemctl daemon-reload
systemctl enable --now docker
docker version

docker load -i assets/base-python312.tar
```

说明：

- `docker.service` 使用 `--storage-driver=overlay2 --iptables=false --bridge=none`；
  容器统一用 `network_mode: host`，不新增 docker bridge/iptables，避免影响现有服务；
- 内核 overlay 模块已确认存在；如未加载，`modprobe overlay`；
- Docker 20.10 是 RHEL7 最后支持版本，EOL；长期生产建议新 VM/OS（见 10 号文档）。

## 4. 构建/更新应用镜像（在服务器上，无需外网）

```bash
cd /opt/rag/src
docker build -f deploy/Dockerfile -t rag-bff:20261116-<gitsha> .
docker image ls | grep rag-bff
```

- 构建上下文来自 `dist/rag-src.tar.gz`，`deploy/wheels` 已在源码包内；
- 构建过程不需要 PyPI；base image 已 load；
- 验证镜像内**没有数据**：
  `docker run --rm rag-bff:<tag> ls app | grep -x data || echo "no data in image"`

## 5. 首次数据/配置

```bash
mkdir -p /opt/rag/{data,models,docs}
tar xzf assets/o32-bundle.tar.gz -C /opt/rag   # 或分别解压 data/models/docs
cp /opt/rag/src/deploy/docker-compose.yml /opt/rag/compose.yml
# config.json 由人放入（含 LLM Key，权限 600）
chown -R 1000:1000 /opt/rag/data /opt/rag/models /opt/rag/docs
chmod 600 /opt/rag/config.json
```

## 6. 启动与验收

```bash
cd /opt/rag
printf 'RAG_TAG=20261116-<gitsha>\n' > .env
# 服务器无外网：切换 tag 后必须加 --pull never，否则 compose 会尝试拉取同名镜像并失败
docker-compose -f compose.yml up -d --pull never
docker-compose -f compose.yml ps
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/warmup
```

> 这台服务器只装了独立的 `docker-compose`（v2.24.6），没有 `docker compose`
> 插件；命令一律用 `docker-compose`。版本钉在 `/opt/rag/.env` 的 `RAG_TAG`。

从开发机跑 eval（脚本支持 `RAG_EVAL_BASE`）：

```bash
RAG_EVAL_BASE=http://10.20.154.26:8000 \
RAG_EVAL_USER=admin RAG_EVAL_PASS=<admin密码> \
python eval/run_eval.py
```

通过标准：`/api/health` 200、warmup ready、eval 6/6、业务问题能返回引用。

## 7. 发版与回滚（核心流程）

**发版**（本地改完 → 构建机/服务器）：

1. `git tag <version>`；构建 `rag-bff:<version>`；
2. `docker save` 推内网 registry，或上传 tar 后 `docker load`；
3. 备份 `/opt/rag/data` 与 `config.json`；
4. 改 `RAG_TAG`，`docker compose up -d`；
5. health/eval，保留上一个 image tag。

**回滚**：把 `RAG_TAG` 改回上一个 tag，`docker compose up -d`；数据卷不动。
若新版本改过数据格式，先从备份恢复 `/opt/rag/data`。

**数据更新**（文档/索引）不重新构建镜像：更新 `/opt/rag/data`（上传/重建索引），
镜像只负责代码；模型更新替换 `/opt/rag/models` 后重启容器。

### 7.1 一键发版脚本（本机 Windows）

凭据放在仓库外的本机文件（`chmod/icacls` 仅当前用户可读，**勿提交**）：

```
~/.rag_prod.env
  RAG_DEPLOY_HOST=10.20.154.26
  RAG_DEPLOY_USER=root
  RAG_DEPLOY_PASS=...
```

打包源码（不含 data/models/config，含 `deploy/wheels`）：

```bash
tar --exclude='app/data' --exclude='app/config.json*' \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='deploy/assets' --exclude='deploy/*.tar' \
    -czf dist/rag-src-<tag>.tar.gz .dockerignore app deploy *.py
```

发版（自动备份 `.env`/config、上传、换 src、build、切 tag、重启、health）：

```bash
set -a; source ~/.rag_prod.env; set +a
python deploy/release_to_server.py --tag <tag> --src dist/rag-src-<tag>.tar.gz
```

脚本不保存密码：不设 `RAG_DEPLOY_PASS` 时交互输入。回滚只需把 `/opt/rag/.env`
的 `RAG_TAG` 改回上一版，再 `docker-compose -f compose.yml up -d --pull never`。

## 8. 注意事项

- 镜像**绝不**包含 `app/data`、`models`、`config.json`；`.dockerignore` 已排除；
- 不要用 `latest`；tag 用日期+gitsha，保留 2–3 个；
- 不要在服务器上 `pip install`/改代码；所有变更走新镜像；
- 单容器更新有几秒中断；要零中断可起两个容器 + Nginx 切换，当前规模不需要；
- `figures.enabled=false`（当前决定）：图片知识在 chunks 文本里，不展示图；
- 注册邮箱白名单在 `data/email_domains.txt`，当前只允许 `@hundsun.com`；打包脚本会一并带上；
- 索引默认 2 个 worker（`indexing.workers`，上限 4）：不同库并行，同一个库仍单任务；
  多用户上传/建库没有全局锁；rerank 并发 1、问答全局 10/单用户 2 是资源限流，超限返回 429；
- 上传支持 zip 批量导入（安全解压、路径穿越/zip bomb 限制）；多文件默认 2–3 路并行；
  `upload.fsync` 默认 false，追求速度；需要强持久化可改为 true；
- 首次注册默认权限/默认共享知识库由管理员「系统参数设置」维护，
  存 `/opt/rag/data/system_settings.json`（运行时数据卷）；
- 业务模块路由：交易所/开放式基金/期货/固收；单模块问题会过滤其他模块片段，
  生成后再做一次跨模块引用校验；PPT 重建后按“一页一块 + 模块/页标题”索引；
  评测脚本 `eval/run_module_eval.py`（模块对抗集 `eval/module_cases.json`）。
- **综合用户手册是跨模块文档**：模块过滤对手册有豁免；模块 + 操作类问题会
  额外做一次“用户手册定向检索”，手册页优先放进引用（最多 4 块），避免 299 页
  手册里的关键小节被培训 PPT / 参数表挤掉。
- **个人 Key 可自定义模型**：`我的 Key` 里的模型文本框随用户存
  `data/user_llm.json`；管理员在全局设置里改模型只影响没自定义模型的用户，
  不会覆盖个人模型。个人 Key 的模型必须在该 Key 支持的列表内（如
  `deepseek-flash` / `deepseek-v4-pro`）。
- 当前问答放行 **global_max=10 / 单用户 2**。10 路完整 RAG 实测 p95 首字 178.6s、
  总耗时 180.9s；**不要把 global_max 直接调到 30**：按固定上游容量外推，
  30 路同时提问约 3 波、p95 约 9 分钟。30 人场景应先加网关通道/个人 Key、
  降低单次回答成本，或做有界排队+等待提示。
- 全局满时进入**有界 FIFO 队列**：默认 `queue_max=50`、`queue_timeout=600s`；
  SSE 推送“排队第 N 位/预计约 X 秒”，出队后自动继续原问答流；队列满/超时才
  429。单用户第 3 个问题仍按 `per_user_max=2` 直接 429（防止一人占满队列）。
- OCR 未装（rapidocr/opencv）：业务手册的 OCR 已进 chunks；重新索引扫描件需另加依赖；
- 本服务器无外网，**切换 `RAG_TAG` 后一律用 `docker-compose ... up -d --pull never`**；
  否则 compose 会去 registry 拉同名镜像，拉取失败后容器不会重建（仍跑旧镜像）。
- **本服务器无外网**：实测 DNS 可解析，但 443 全部 `Network is unreachable`；
  已把 `/opt/rag/config.json` 的 `fallback.web_search.enabled` 设为 false，
  前端据此隐藏「🌐 联网搜索回答」，只保留「🧠 大模型本地回答」；
  若日后开放出网或配 Bing API Key，改回 true 并重启即可恢复；
- RAGFlow/阶段 2 不在本机：需要新 VM + Docker 24+ + 32GB 可用内存 + 500GB 磁盘。

## 9. 本次部署记录（2026-11-16）

| 项 | 实际结果 |
|---|---|
| 服务器 | `10.20.154.26`（RHEL 7.5，VMware） |
| Docker | 20.10.24 静态版，overlay2，cgroup v1；docker.service active |
| 基础镜像 | `python:3.12-slim`（Debian，glibc 2.36）；容器内 Python 3.12.16 |
| 应用镜像 | `rag-bff:20261116-o32`，592MB；**镜像内无 data/models/config**（已验证） |
| 数据 | 只含业务手册（O32/资管培训）`20261108_174939_69c8b5`，4,065 块；data 147MB / models 374MB / docs 180MB |
| 容器 | `rag-bff`，healthy，host network，`0.0.0.0:8000` |
| 验证 | `/api/health` 200；admin 登录 200；SJSMXn 问题 23.1s 返回引用；warmup ready |
| 端口 | 8000（未用 8123；80 仍是 learn-center，未受影响） |
| 15:58 更新 | `rag-bff:20261116-a27b725`：索引进度/文件选择/答辩预生成；`/opt/rag/.env` 固定 `RAG_TAG`；容器 healthy |
| 16:12 修复 | `rag-bff:20261116-fc10c8b`：参考答案截断自动重试、手册检索跳过精排、一次只预生成一题 |
| 16:21 日志 | `rag-bff:20261116-809e643`：持久化日志 `/opt/rag/logs`（按天，保留 30 天）；旧日志已导入 |
| 17:02 并发 | `rag-bff:20261116-e9e0f65`：文件选择弹窗加固；registry 加锁/原子写；上传/索引临时文件唯一；索引 2 worker 并行 |
| 10:17 推荐/文件 | `rag-bff:20261117-fb10f7b`：答辩自动推荐知识库（预选可改）、切库清屏、每库文件列表、隐藏块/段计数 |
| 14:30 输入/引用 | `rag-bff:20261117-0d41e2c`：Shift+Enter 换行、引用角标定位高亮、点踩可填原因 |
| 16:02 预览导航 | `rag-bff:20261117-a3cb3d6`：文件预览左侧目录树、当前文件高亮、片段分段定位 |
| 17:40 按库会话 | `rag-bff:20261117-5058d55`：每个知识库保留自己的聊天记录，来回切换恢复 |
| 22:45 DBF | `rag-bff:20261117-dc019f4`：纯 Python 解析 DBF，表结构+行数据+重复表头，支持 memo |
| 23:20 上传 | `rag-bff:20261117-7de5753`：zip 批量导入 + 2–3 路并行上传 + `upload.fsync` 可配 |
| 10:30 系统参数 | `rag-bff:20261118-75c1d79`：注册默认权限/默认共享知识库，管理员可配 |
| 11:05 预览/权限 | `rag-bff:20261118-15ecbd5`：预览改文档标题目录、引用定位修复；知识库/答辩默认权限分开 |
| 13:30 两套库 | `rag-bff:20261118-8c54df4`：问答默认库与答辩默认库分别配置、分别共享 |
| 14:00 参数布局 | `rag-bff:20261118-982a67a`：系统参数改为左侧列表+右侧详情，适配后续增加参数 |
| 15:10 共享拆分 | `rag-bff:20261118-b74bb4d`：知识库分享拆成智能问答/模拟答辩两列 |
| 16:30 架构图 | `rag-bff:20261118-dc0ae0a`：架构/流程/拓扑图结构化，明确问最多展示 2 张 |
| 11-19 直答 | `rag-bff:20261119-llm-direct`：回答旁加「🌐 联网搜索回答 / 🧠 大模型本地回答」；本服务器无外网，关闭 `fallback.web_search`，前端只显示模型直答，web 模式自动降级并标注「联网未启用」 |
| 11-19 模块路由 | `rag-bff:20261119-module-route2`：交易所/开放式基金/期货/固收模块识别与检索过滤；跨模块引用校验；PPT 一页一块 + 模块/页标题前缀；业务手册库重建；模块对抗评测 6/6 |
| 11-19 手册定向 | `rag-bff:20261119-manual-fix2`：模块+操作类问题追加“用户手册定向检索”；综合手册模块豁免、手册最多 4 个引用位；O32 交易所指令问题直接命中 `基金投资管理系统O3.2用户手册V1.7...pdf`，回答 `【指令管理】→【交易所指令】→<个股指令>`；7 个库全部重建为 chunker=2；模块评测 6/6（含内容检查） |
| 11-20 真实网关 | 3 个可用账号各 2 路、共 6 并发（3 个不同知识库，真实 LLM 网关）：**6/6 200，0 个 429**；TTFT p50 **82.1s** / p95 **145.0s**；总耗时 p50 95.9s / p95 146.9s；结束后活跃名额归零。结论：应用侧稳定，真实网关约 2 通道，6 路突发的 p95 首字约 **2.4 分钟** |
| 11-20 申请入口 | `rag-bff:20261120-links`：「我的 Key」申请入口改为 LightCode UIS 控制台 `http://lightcode.hundsun.com/ai_market/API`，并加入公司申请文档链接；全局网关占位地址改为 `http://lightcode-uis.hundsun.com:8080`；手册同步更新 |
| 11-20 个人模型 | `rag-bff:20261120-user-model2`：「我的 Key」增加自由文本模型框；用户模型随 key 存 `user_llm.json`，管理员改全局模型不覆盖；个人 Key + `deepseek-flash` / `deepseek-v4-pro` 验证通过；全局模型 `hs-deepseek-flash-latest` 对该 Key 不可用（会提示检查模型名） |
| 11-20 公共 Key | 全局 Key 切换为用户提供的 LightCode UIS 外部 BYOK（不再记录 Key 本身）；全局模型切到 `deepseek-flash`；直连网关 10 路并发 **10/10 200**，墙钟 0.8s、p95 0.76s；应用层仍 `global_max=6`，10 个人走应用会有 4 个 429；配置备份在 `/opt/rag/backup/20261120-public-key/` |
| 11-20 完整10人 | 临时 `global_max=10` + 10 个临时账号共享业务手册库，真实 RAG/真实网关、10 个不同问题：**10/10 200，0 个 429**；TTFT p50 **127.3s** / p95 **178.6s**，总耗时 p50 134.7s / p95 180.9s，墙钟 181s；9 条完整结束、1 条返回部分内容；来源均 7–8 条。跑完已删除临时账号、恢复分享；按人要求**保留 `global_max=10`** |
| 11-20 有界排队 | `rag-bff:20261120-queue2`：全局满时有界 FIFO（默认 50 / 600s），SSE 推送排队位次和预计等待，出队自动继续。临时 `global_max=1` + 2 个真实用户实测：第一人 79s；第二人收到 39 次“排队第 1 位/预计 120→112s”状态后自动开始，总 106s。已恢复 `global_max=10` |
| 11-20 使用文档 | `rag-bff:20261120-docs`：界面「使用文档 → 用户手册」整体重写，覆盖注册/登录、知识库新建/上传/索引/共享、提问/引用/预览/上下文、模拟答辩、管理员用户管理与系统参数、个人 Key/模型、排队与常见问题；「项目背景与说明」的鉴权描述同步更新 |

回滚：把 `/opt/rag/.env` 的 `RAG_TAG` 改回上一版 `20261120-queue2`
（可回 `20261120-user-model2` / `20261120-queue` / `20261120-links`）
（或 `20261119-manual-fix2` / `20261119-module-route2` / `20261119-manual-fix` / `20261119-llm-direct` / `20261118-dc0ae0a` / `20261118-b74bb4d` / `20261118-982a67a` / `20261118-8c54df4` / `20261118-15ecbd5` / `20261118-75c1d79` / `20261117-7de5753` / `20261117-dc019f4` / `20261117-5058d55` / `20261117-a3cb3d6` / `20261117-0d41e2c` / `20261117-fb10f7b` / `20261116-e9e0f65` / `20261116-809e643` / `20261116-fc10c8b` / `20261116-a27b725` / `20261116-o32`），
`docker-compose -f compose.yml up -d --pull never`；数据卷不动。
本次改动前的 config/源码备份在 `/opt/rag/backup/20261119-llm-direct/`、
`/opt/rag/backup/20261119-module-route2/`、`/opt/rag/backup/20261119-manual-fix2/`、
`/opt/rag/backup/20261120-links/`、`/opt/rag/backup/20261120-user-model2/`、
`/opt/rag/backup/20261120-queue2/`。

## 10. 日志

- **持久化日志**：宿主机 `/opt/rag/logs` 挂到容器 `/app/logs`，
  按天写 `app-YYYYMMDD.log`，保留 30 天。包含启动日志、uvicorn 访问日志、
  `print` 的 `[coach]`/`[llm]`/异常堆栈。容器重建不会丢。
- 查看：`tail -f /opt/rag/logs/app-$(date +%Y%m%d).log`；
  或 `grep -E '\\[coach\\]|\\[llm\\]|ERROR' /opt/rag/logs/app-*.log`。
- 同时保留 Docker `json-file` 控制台日志（单文件 20MB、5 个）：
  `docker logs -t rag-bff`。容器 recreate 后这部分会随旧容器删除。
- `[coach]` 行是答辩拆解耗时（检索/模型/输出长度/stop_reason）；
  `[llm]` 行是重试后仍失败；解析失败会打 `parse_failed ... raw=...`。
- `/opt/rag/data/qa_log.jsonl` 只记录主问答，**不记录答辩内容和模型原文**。

未做/后续：Nginx 8080/8443 + TLS/ACL、admin 密码轮换、每日备份、
`docker save` 镜像备份、数据卷扩容；RAGFlow 等新 VM。

### 2026-11-25 答辩难度 / 关键词直练 / SQL+ZIP / 提示弹窗

- 当前 tag：`rag-bff:20261125-def-kw2`（上一版 `20261125-def-kw`，回滚点 `20261120-docs`）。
- 新增：答辩难度（基础/标准/挑战，默认基础，按实施工程师带新人视角出题）；
  关键词直练（不上传 PPT，按关键词自动匹配知识库）；进入知识库性能提示弹窗；
  SQL 只索引文件名；文件夹/上传目录里的 zip 自动解压并索引内部文档。
- 生产冒烟：health 200；关键词「交易所指令 下单 审核 分发」匹配业务手册，
  基础难度第一题为“分发在哪个菜单、分发前维护什么”；PPT 基础难度第一题为
  “装完从哪里确认 5 个版本包都装上了”。
- 注意：SQL/zip 只对新索引/重建生效；存量库需在空闲窗口重建。

- 2026-11-25 补丁：`rag-bff:20261125-def-kw3`，答辩难度按钮下增加三档区别的小字说明；
  回滚点 `20261125-def-kw2`。
- 2026-11-26 修复：`rag-bff:20261126-sql-backfill`，解决 SQL 文件“签名未变但从未生成块”
  导致重建跳过的问题；etm 库已重建，新增 48 条 SQL 文件名块。回滚点
  `20261125-def-kw3`。

### 2026-11-26 反馈 / 提需求入口

- 当前 tag：`rag-bff:20261126-feedback`（回滚点 `20261126-sql-backfill`，容器 healthy）。
- 新增：更多菜单「💬 反馈 / 提需求」与管理员「📝 用户反馈」（待处理/全部/类型筛选、
  标记已处理）；进入知识库原有的「实验推广阶段」提示里直接给反馈按钮；
  答辩页进入时的推广/排队提醒带「去反馈」，顶栏也有反馈入口。
- 后端：`POST /api/suggestion`、`GET /api/suggestions`、
  `POST /api/suggestions/{id}/handled`；数据写 `/opt/rag/data/feedback.jsonl`
  （首次反馈时创建，追加写、20MB 轮转）。
- 备份：`/opt/rag/backup/20261126-feedback-20261126-154203`（.env/config/镜像列表）；
  旧源码：`/opt/rag/src.bak-20261126-154203`。
- 验证：health 200；首页和答辩页都含反馈入口；`/openapi.json` 出现 3 个新接口；
  容器 `Up (healthy)`。发版使用 `deploy/release_to_server.py` 一键完成。
