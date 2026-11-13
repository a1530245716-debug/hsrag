# 给 Claude Code 的协作说明

> **本文是写给 Claude Code 的。如果你不是 Claude Code（比如你是 DeepSeek / RAG-Agent），
> 忽略本文的"你/你的"表述，一律以 `AGENTS.md` 的角色划分为准，并按 CHANGELOG 登记改动。**

**先读 `AGENTS.md`**，那里是完整的多 agent 协作约定。下面是必须马上知道的要点。

> 本机开发 / Docker(WSL2) / 端口 / 公开作品集约定见 `AGENTS.md` 第七节。

## ⚠️ 你的工作目录是 `rag-claude`，不是 `rag`

从 2026-11-12 起用 git worktree 并行开发，三个目录同时存在：

| 目录 | 分支 | 端口 | 谁用 |
|---|---|---|---|
| `project\rag\` | `main` | 8000（生产） | 人 |
| `project\rag-agent\` | `agent/phase1-limit` | 8011 | RAG-Agent |
| **`project\rag-claude\`** | **`claude/rerank-tune`** | **8012** | **你** |

**请在 `E:\doc\恒生电子交付工程师学习\project\rag-claude` 里工作。**

- 启动你的服务：`.\run_dev.ps1`（起在 8012）
- `app\data`、`models`、`index` 是通过 Junction 共享的，看到的是主目录的同一份数据
- **不要删 `app\data`**，会连主目录一起删
- **不要在 worktree 里重建索引**，会影响另外两个服务

详细规则见 `docs\agents\WORKTREE-并行开发.md`。

## 这个项目现在有两个 agent 在改

| 谁 | 管什么 |
|---|---|
| **RAG-Agent**（DeepSeek 会话） | `app/kb.py`、`app/server.py`、`app/planner.py`、`app/llm.py`、`app/static/index.html`、`eval/` |
| **你（Claude Code）** | `app/embeddings.py`、`app/indexer.py`、`build_vectors.py`、数据管道 |

**别改对方归属的文件。** 发现对方文件有 bug，写到 `docs/agents/INBOX.md`。

## 动手前后必做的两件事

1. **动手前**：读 `docs/agents/CHANGELOG.md` 最后 20 行，看看对方在干什么
2. **改完**：往 `docs/agents/CHANGELOG.md` 追加一条（时间 / 文件 / 改了什么 / 为什么）

`app/config.json` 和 `app/data/` 是共用的，动之前先在 `docs/agents/INBOX.md` 留言。

## 绝对不能碰的

- **不要删** `app/data/`、`models/`、`index/`（重建要几十分钟）
- **不要改** `app/data/*/chunks.jsonl` 的字段结构 —— `id` / `path` / `chunk` / `text` 四个字段
  RAG-Agent 的检索逻辑全依赖它。要加字段可以，删改字段先协商。
- **重启服务前**确认没有正在跑的评测

## RAG-Agent 今天动了什么（避免你困惑）

- `app/kb.py`：加了文档版本优先级（同族按时间降权）、文件名命中加权、降权逻辑拆成 `_path_weights`
  返回 `(权威权重, 降权因子)`。**`BM25.add()` 多了一个 `extra_boost` 参数**，`BM25` 多了
  `self.suppress` 和 `self.path_toks` 两个数组。如果你有代码依赖 `kb.BM25`，注意这个变化。
- `app/server.py`：`_multi_search` 改成并行 + `_diversify`（单文件最多占 2 个引用位）
- `app/config.json`：`max_tokens` 1500→3500、`read_timeout` 60→30
- 新增 `eval/`：6 条问答评测集

## 你改的东西我确认过是好的

- `vectors.npy` 两个库都是 `complete=True`，维度 512，行数与 `chunks.jsonl` 严格一致
  （某基金 56999、某资管 2313）
- `chunks.jsonl` 字段没变，我的检索逻辑照常工作
- 实测某资管的向量召回正常（sim 0.73），混合检索融合生效

## 验证命令

```powershell
$py = "$env:USERPROFILE\anaconda3\envs\rag\python.exe"
& $py -m py_compile app/embeddings.py app/indexer.py build_vectors.py
& $py -u eval\run_eval.py          # 涉及检索的改动必须跑，6 条全 PASS 才算没改坏
```

## 有件事要你回答

见 `docs/agents/INBOX.md`，有 4 个问题需要你确认（尤其是 `ensure_vectors` 的触发时机，
会不会在用户提问时同步跑、把服务卡住）。
