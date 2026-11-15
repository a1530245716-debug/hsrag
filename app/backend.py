# -*- coding: utf-8 -*-
"""检索内核的抽象层（阶段 3）。

## 为什么要这一层

现在检索直接调 `kb.hybrid_search`，它是「BM25 + 向量 + 重排」全在一个进程里跑的本地实现。
`docs/ai-fde/03-目标架构与数据权限.md` 的目标架构是 **RAGFlow 做检索内核 + FastAPI 做 BFF**，
换内核时不应该动业务代码（权限、引用构建、SSE、提示词）。

所以这里定义一个窄接口 `RetrievalBackend`，把「检索」这件事固定下来：
业务层只认接口，底下换本地 BM25、换 RAGFlow、还是换测试用的假实现，都不用改业务。

## 字段口径（重要）

`Chunk` 的字段是**跟 RAGFlow 对齐过的最小集 + 本地实现额外带的调试字段**：

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | 片段唯一标识。RAGFlow 的 chunk id 也能直接用 |
| `path` | ✅ | 来源文件路径。引用列表、图片匹配都靠它 |
| `text` | ✅ | 片段正文。拼进提示词的就是它 |
| `score` | ✅ | 相关性分数，越大越相关。不同后端**量纲可以不同**，业务层只用于排序和展示 |
| `chunk` | ❌ | 片段在文件内的序号（本地实现有，RAGFlow 用不上） |
| `vec_score` | ❌ | 向量相似度（本地调试用） |
| `fused` | ❌ | 融合分（本地调试用） |
| `rerank_score` | ❌ | 重排分（本地调试用） |

字段是 2026-11-15 对照 `kb.hybrid_search` 的实际返回核对的，
新增字段只能追加、不能改语义——否则换后端时业务层拿到的数据会对不上。

## 已知的取舍

- 接口是**同步**的。本地 BM25/重排都是 CPU 密集的同步代码，RAGFlow 的 HTTP 调用也是
  一等公民的同步实现（`httpx.post`）。异步化放在 `server.py` 用 `run_in_threadpool` 包，
  这样后端不需要各自实现异步版本。
- `retrieve` 只负责「给问题 → 给片段」。**生成答案（调 LLM）不在这一层**，
  因为它和 SSE、历史、图片、追问改写的耦合太深，拆出去反而会让两边都变复杂。

---

## 用法

```python
from backend import get_backend, invalidate_cache

be = get_backend(kb_id)                       # 按 feature flag 选
chunks = be.retrieve(kb_id, "怎么部署", 8)     # None 表示无索引
```

配置写在 `app/config.json`：

```json
"backend": {
  "default": "local_bm25",
  "per_kb": { "20261019_121148_5a7a33": "mock" }
}
```
"""
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---------------- 数据结构 ----------------


@dataclass
class Chunk:
    """一个检索命中的片段。字段口径见模块顶部说明。"""

    id: str
    path: str
    text: str
    score: float = 0.0
    # 以下是本地实现的附加字段，其它后端可以不给
    chunk: Optional[int] = None
    vec_score: Optional[float] = None
    fused: Optional[float] = None
    rerank_score: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转成普通 dict，供 server.py 现有逻辑直接使用。

        只输出有值的键——`server.py` 里 `_build_sources` 等函数会判断
        `'rerank_score' in r`，多塞一个 None 会让判断失效。
        """
        d = {"id": self.id, "path": self.path,
             "text": self.text, "score": self.score}
        for k in ("chunk", "vec_score", "fused", "rerank_score"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.extra:
            d.update(self.extra)
        return d

    @classmethod
    def from_raw(cls, r: Dict[str, Any]) -> "Chunk":
        """从 `kb.hybrid_search` 那种 dict 构造。

        认不出来的键丢进 `extra`，这样 kb.py 以后加字段也不会在这里静默丢失。
        """
        known = {"id", "path", "text", "score", "chunk",
                 "vec_score", "fused", "rerank_score"}
        extra = {k: v for k, v in r.items() if k not in known}
        return cls(
            id=str(r.get("id") or ""),
            path=str(r.get("path") or ""),
            text=str(r.get("text") or ""),
            score=float(r.get("score") or 0.0),
            chunk=r.get("chunk"),
            vec_score=r.get("vec_score"),
            fused=r.get("fused"),
            rerank_score=r.get("rerank_score"),
            extra=extra,
        )


# ---------------- 接口 ----------------


class RetrievalBackend:
    """检索内核的窄接口。

    实现方只需要保证：给 (kb_id, query, top_k)，返回按相关性降序的 Chunk 列表；
    知识库没有索引时返回 None（业务层据此提示「请先重建索引」）。
    """

    name = "base"

    def retrieve(self, kb_id: str, query: str, top_k: int = 8,
                 enable_vector: bool = True,
                 enable_rerank: bool = True) -> Optional[List[Chunk]]:
        raise NotImplementedError

    def available(self, kb_id: str) -> bool:
        """该库是否可用（有索引 / 能连通）。业务层不强制调用。"""
        return True

    def describe(self) -> Dict[str, Any]:
        """给 /api/health 和排障用的自述。"""
        return {"backend": self.name}


class LocalBM25Backend(RetrievalBackend):
    """现有实现：进程内 BM25 + 向量 + ONNX 重排。

    只是把 `kb.hybrid_search` 包一层转成 Chunk，**逻辑一行没改**。
    """

    name = "local_bm25"

    def retrieve(self, kb_id, query, top_k=8, enable_vector=True, enable_rerank=True):
        import kb
        raw = kb.hybrid_search(kb_id, query, topk=top_k,
                               enable_vector=enable_vector,
                               enable_rerank=enable_rerank)
        if raw is None:
            return None
        return [Chunk.from_raw(r) for r in raw]

    def available(self, kb_id):
        try:
            import kb
            return kb.get_index(kb_id) is not None if hasattr(kb, "get_index") else True
        except Exception:
            return True

    def describe(self):
        import rerank
        return {"backend": self.name,
                "rerank_concurrency": rerank.RERANK_CONCURRENCY}


class MockBackend(RetrievalBackend):
    """测试用的假实现：不碰索引，固定返回几条合成片段。

    用途：
      1. 验证 feature flag 能真的把某个库切到别的后端（切入后答案会明显不同）
      2. 压测时排除检索成本，只测 SSE/LLM 链路
      3. 演示环境不想暴露真实文档时

    **不要**在真实库里长期挂这个后端——它返回的 `text` 是假的。
    """

    name = "mock"

    def retrieve(self, kb_id, query, top_k=8, enable_vector=True, enable_rerank=True):
        n = max(1, min(int(top_k or 0) or 3, 5))
        out = []
        for i in range(n):
            out.append(Chunk(
                id=f"mock_{kb_id[:6]}_{i}",
                path=f"[测试]模拟文档-{i + 1}.md",
                text=(f"这是 MockBackend 返回的第 {i + 1} 条模拟片段，"
                      f"用于验证检索内核可以替换。原问题：{query}。"
                      "内容不含任何真实资料，仅供联调压测。"),
                score=float(10 - i),
                chunk=i,
            ))
        return out

    def describe(self):
        return {"backend": self.name, "note": "返回假数据，勿用于真实问答"}


# ---------------- feature flag ----------------

_BACKENDS = {
    "local_bm25": LocalBM25Backend,
    "mock": MockBackend,
}
_instances: Dict[str, RetrievalBackend] = {}
_CACHE: Dict[str, Any] = {"sig": None, "default": "local_bm25", "per_kb": {}}


def _cfg():
    """读 config.json 的 backend 段，按 mtime 缓存。

    为什么按 mtime：改配置不用重启服务（运维常这么干），
    但也不能每次请求都读一遍文件（那是 I/O）。
    """
    import llm
    p = llm.CFG_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {"default": "local_bm25", "per_kb": {}}
    if _CACHE.get("sig") == mtime:
        return _CACHE
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("backend") or {}
    except Exception:
        raw = {}
    default = str(raw.get("default") or "local_bm25").strip()
    per_kb = raw.get("per_kb") or {}
    if not isinstance(per_kb, dict):
        per_kb = {}
    _CACHE.update({"sig": mtime, "default": default, "per_kb": per_kb})
    return _CACHE


def backend_name_for(kb_id: str) -> str:
    """这个库该用哪个后端。per_kb 优先于 default。"""
    cfg = _cfg()
    name = cfg["per_kb"].get(kb_id) or cfg["default"]
    if name not in _BACKENDS:
        print(f"[backend] 未知后端 {name!r}，回落到 local_bm25", file=sys.stderr)
        return "local_bm25"
    return name


def get_backend(kb_id: str = "") -> RetrievalBackend:
    """取某个库该用的后端实例。"""
    name = backend_name_for(kb_id) if kb_id else _cfg()["default"]
    if name not in _BACKENDS:
        name = "local_bm25"
    be = _instances.get(name)
    if be is None:
        be = _BACKENDS[name]()
        _instances[name] = be
    return be


def invalidate_cache():
    """配置改完后强制重读（测试用；正常靠 mtime 自动失效）。"""
    _CACHE["sig"] = None


def describe_all(kb_ids=None) -> Dict[str, Any]:
    """给 /api/health 看的全局自述。"""
    cfg = _cfg()
    out = {
        "default": cfg["default"],
        "per_kb": dict(cfg["per_kb"]),
        "available": sorted(_BACKENDS),
    }
    if kb_ids:
        out["resolved"] = {k: backend_name_for(k) for k in kb_ids}
    return out


def retrieve(kb_id, query, top_k=8, enable_vector=True, enable_rerank=True):
    """便捷入口：自动选后端并转成 dict 列表（server.py 现有逻辑直接用）。

    返回 None 表示该库没有索引；返回 [] 表示检索了但没命中。
    """
    be = get_backend(kb_id)
    chunks = be.retrieve(kb_id, query, top_k=top_k,
                         enable_vector=enable_vector,
                         enable_rerank=enable_rerank)
    if chunks is None:
        return None
    return [c.to_dict() for c in chunks]
