# Windows 本机安装 Docker（WSL2 + Docker Engine）

> 目标：在本机跑 `tools/local_dev/docker-compose.yml`，验证容器化链路；服务器上同样用 Docker Engine，不用 Docker Desktop。
>
> 前提：Windows 11、虚拟化已开启、**管理员权限**、能访问 Ubuntu/微软/ Docker 软件源（或内网镜像）。

## 为什么推荐 WSL2 + Docker Engine

- Docker Engine 免费，适合企业；Docker Desktop 对较大规模企业可能要求付费许可。
- 与 Linux 服务器环境接近，compose/镜像基本一致。
- WSL2 的 `localhost` 会自动转发到 Windows，浏览器直接访问 `http://localhost:8020`。

## 步骤 1：安装 WSL2 + Ubuntu

打开 **以管理员身份运行的 PowerShell**：

```powershell
wsl --install -d Ubuntu
```

- 安装完成后按提示重启。
- 重启后会自动打开 Ubuntu，设置 Linux 用户名和密码。
- 如果提示已安装但版本不对：`wsl --update`、`wsl --set-default-version 2`。

检查：

```powershell
wsl -l -v
```

应看到 `Ubuntu`，版本为 `2`。

## 步骤 2：限制 WSL 资源（重要）

在 Windows 用户目录创建 `C:\Users\<你的用户名>\.wslconfig`：

```ini
[wsl2]
memory=16GB
processors=8
swap=4GB
localhostForwarding=true
```

- 本机 31GB：给 WSL 16GB，留 15GB 给 Windows + 现有服务。
- 如果要跑 RAGFlow + Elasticsearch，建议先停掉 8000 的生产服务，并把 `memory` 调到 20GB。
- 修改后执行 `wsl --shutdown`，再重新打开 Ubuntu。

## 步骤 3：启用 systemd（让 Docker 自启）

在 Ubuntu 里：

```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```

然后在 Windows PowerShell 执行：

```powershell
wsl --shutdown
```

重新打开 Ubuntu。

## 步骤 4：安装 Docker Engine

在 Ubuntu 里执行：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

重新登录 Ubuntu（或 `newgrp docker`），验证：

```bash
docker run --rm hello-world
docker compose version
```

如果 Docker 没启动：

```bash
sudo systemctl enable --now docker
```

## 步骤 5：跑本地演示

建议把项目复制到 WSL 自己的文件系统，避免 `/mnt/e` 的 IO 慢：

```bash
mkdir -p ~/rag
cp -r "/mnt/e/doc/示例项目/project/rag/tools/local_dev" ~/rag/local_dev
cd ~/rag/local_dev
docker compose up --build
```

Windows 浏览器打开：

```
http://localhost:8020
```

停止：

```bash
docker compose down
```

## 常见问题

| 现象 | 处理 |
|---|---|
| `wsl --install` 报错/无权限 | 确认是管理员 PowerShell；公司策略不允许时改用 Linux VM 或服务器 |
| `wsl -l -v` 没有 Ubuntu | `wsl --install -d Ubuntu`，检查是否重启 |
| Docker 连不上 daemon | `sudo systemctl start docker`；检查 `/etc/wsl.conf` 的 systemd |
| 浏览器打不开 8020 | `docker compose ps` 看端口；WSL2 的 localhost 转发是否开启 |
| 拉镜像很慢/失败 | 配置内网镜像仓库或代理；离线环境用 `docker save/load` |
| 内存不够 | 停掉 8000 生产服务；调小 WSL memory；RAGFlow 放服务器 |

## 替代方案：Docker Desktop

如果公司有 Docker Desktop 许可，管理员 PowerShell 执行：

```powershell
winget install -e --id Docker.DockerDesktop
```

安装后需要重启/登录。注意许可和资源占用；企业环境优先 WSL2 + Docker Engine。

## 安全提醒

- 加入 `docker` 组等价于 root 权限，仅限本机开发。
- 不要把真实密钥写进镜像或 compose；用 `.env`，且 `.env` 不进 Git。
- 演示数据用合成文档；RAGFlow 导入真实文档前先脱敏/审批。
