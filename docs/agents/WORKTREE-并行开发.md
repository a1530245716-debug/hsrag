# 多 Agent 并行开发指南

> 本文件说明如何用 git worktree 让多个 agent 同时开发互不干扰。
> **归属**：共用，谁改了都要登记 CHANGELOG。

---

## 一、当前目录布局

```
E:\doc\恒生电子交付工程师学习\project\
├── rag\          ← 主目录，main 分支，生产服务跑在 8000
├── rag-agent\    ← RAG-Agent 工作区，分支 agent/phase1-limit，端口 8011
└── rag-claude\   ← Claude Code 工作区，分支 claude/rerank-tune，端口 8012
```

三个目录共享同一份 `.git`，但文件完全隔离。

```powershell
# 查看所有 worktree
git worktree list
```

---

## 二、每个 worktree 的约定

| 目录 | 分支 | 端口 | 谁用 |
|---|---|---|---|
| `rag\` | `main` | **8000**（生产，别拿来做测试） | 人 |
| `rag-agent\` | `agent/phase1-limit` | **8011** | RAG-Agent |
| `rag-claude\` | `claude/rerank-tune` | **8012** | Claude Code |

**端口依据**：`AGENTS.md` 第七节「端口分工」——测试一律用 8010/8020 以上，不要拿 8000 做测试。

---

## 三、启动自己的开发服务

每个 worktree 根目录下有一个 `run_dev.ps1`：

```powershell
cd E:\doc\恒生电子交付工程师学习\project\rag-agent
.\run_dev.ps1          # 起在 8011
```

```powershell
cd E:\doc\恒生电子交付工程师学习\project\rag-claude
.\run_dev.ps1          # 起在 8012
```

想换端口直接调：

```powershell
& $py app\server.py --port 8013
```

---

## 四、共享的资源

以下目录**没有**各自复制，而是通过 Windows Junction 指向主目录：

| 路径 | 指向 | 说明 |
|---|---|---|
| `app\data` | `rag\app\data` | 知识库、用户、答辩记录、日志（975 MB） |
| `models` | `rag\models` | ONNX 模型（373 MB） |
| `index` | `rag\index` | 索引文件（65 MB） |

**含义**：三个目录看到的是**同一份数据**。

```powershell
# 查看 Junction
Get-Item E:\doc\恒生电子交付工程师学习\project\rag-agent\models | Select-Object LinkType,Target
```

### 危险操作

- ❌ **不要在任何一个 worktree 里删 `app\data`** —— 会连主目录的数据一起删
- ❌ **不要在 worktree 里重建索引** —— 会影响另外两边的服务
- ❌ **不要同时跑两个评测** —— `app\data\qa_log.jsonl` 会写乱
- ✅ 只读操作（提问、检索、看文件）随便跑，互不影响

---

## 五、`app\config.json` 是单独复制的

`app\config.json` 被 `.gitignore` 忽略，worktree 里没有。已手动复制到两个 worktree。

**注意**：主目录改了 `config.json`，两个 worktree 不会同步。

需要同步时：

```powershell
# 从主目录推到 worktree
Copy-Item "E:\...\rag\app\config.json" "E:\...\rag-agent\app\config.json" -Force
Copy-Item "E:\...\rag\app\config.json" "E:\...\rag-claude\app\config.json" -Force
```

---

## 六、开发流程

```
1. 在自己 worktree 里改代码
2. .\run_dev.ps1 起服务（8011 或 8012）
3. 跑验证：eval\run_eval.py / tools\loadtest
4. git add -A && git commit
5. 通知人合并
```

### 提交规范

```powershell
git add -A
git commit -m "feat: 阶段1 三层限流

- server.py 加全局/每用户/每IP 限流
- /api/health 改 async
- 超限返回 429 + Retry-After"
```

---

## 七、合并回 main

由人在主目录操作：

```powershell
# 1. 先确保主目录干净
cd E:\doc\恒生电子交付工程师学习\project\rag
git status

# 2. 合并
git merge agent/phase1-limit

# 3. 有冲突就解决，然后
git add -A && git commit

# 4. 跑回归
& $py -u eval\run_eval.py
```

### 冲突处理原则

- **只改自己负责的文件**，冲突概率低
- 两边都要改同一个文件时：**先合一个，另一个基于新 main 改**
- 冲突了不要硬合，先问人

---

## 八、清理 worktree

工作完成后：

```powershell
# 回到主目录
cd E:\doc\恒生电子交付工程师学习\project\rag

# 删除 worktree（不会删分支）
git worktree remove ..\rag-agent
git worktree remove ..\rag-claude

# 删除分支（确认已合并）
git branch -d agent/phase1-limit
git branch -d claude/rerank-tune
```

**注意**：`git worktree remove` 会因为 Junction 报错，先删 Junction：

```powershell
cmd /c rmdir "E:\...\rag-agent\app\data"
cmd /c rmdir "E:\...\rag-agent\models"
cmd /c rmdir "E:\...\rag-agent\index"
git worktree remove ..\rag-agent --force
```

---

## 九、常见问题

### Q：worktree 里提示找不到 config.json

`.gitignore` 忽略了它，复制一份过去：

```powershell
Copy-Item "..\rag\app\config.json" "app\config.json"
```

### Q：知识库列表是空的

检查 Junction 是否还在：

```powershell
Test-Path app\data\registry.json
Get-Item app\data | Select-Object LinkType,Target
```

### Q：服务起不来，报端口占用

换个端口：

```powershell
& $py app\server.py --port 8013
```

查看谁占了端口：

```powershell
Get-NetTCPConnection -LocalPort 8011 -State Listen | Select-Object OwningProcess
```

### Q：两个 agent 改了同一个文件

说明分工没谈好。**停下来，先在 `docs/agents/INBOX.md` 协商**，不要硬合并。

---

## 十、为什么不直接复制目录

复制目录会：

- 多占 1.4 GB（data + models + index 各一份）
- 数据不同步，测试结果对不上
- 改完要手动比对合并

worktree + Junction 只隔离**代码**，共享**数据**，这才是对的。

---

**最后更新**：2026-11-12
