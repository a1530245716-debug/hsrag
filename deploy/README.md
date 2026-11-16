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
docker-compose -f compose.yml up -d
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

## 8. 注意事项

- 镜像**绝不**包含 `app/data`、`models`、`config.json`；`.dockerignore` 已排除；
- 不要用 `latest`；tag 用日期+gitsha，保留 2–3 个；
- 不要在服务器上 `pip install`/改代码；所有变更走新镜像；
- 单容器更新有几秒中断；要零中断可起两个容器 + Nginx 切换，当前规模不需要；
- `figures.enabled=false`（当前决定）：图片知识在 chunks 文本里，不展示图；
- 注册邮箱白名单在 `data/email_domains.txt`，当前只允许 `@hundsun.com`；打包脚本会一并带上；
- OCR 未装（rapidocr/opencv）：业务手册的 OCR 已进 chunks；重新索引扫描件需另加依赖；
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

回滚：把 `/opt/rag/.env` 的 `RAG_TAG` 改回 `20261116-o32`，
`docker-compose -f compose.yml up -d`；数据卷不动。

未做/后续：Nginx 8080/8443 + TLS/ACL、admin 密码轮换、每日备份、
`docker save` 镜像备份、数据卷扩容；RAGFlow 等新 VM。
