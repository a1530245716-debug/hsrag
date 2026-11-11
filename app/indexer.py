# -*- coding: utf-8 -*-
"""多知识库管理 + 增量索引。

数据模型:
  data_dir/registry.json         持久化的知识库注册表 [{id,name,folder,virtual,...}]
  data_dir/<kb_id>/chunks.jsonl  该知识库的检索块(每行一个 json)
  data_dir/<kb_id>/manifest.json 统计 + 每文件签名(size:mtime, 用于增量跳过)

- 每个知识库对应一个真实文件夹(或虚拟合并),互不影响 → 支持历史切换无需重建。
- 增量索引:按文件 size+mtime 签名,未变文件跳过,只处理新增/修改;删除文件同步移除其块。
- 复用 index_docs.py 的抽取函数(EXTRACTORS)保证与旧版一致。
"""
import json, math, os, sys, threading, time, hashlib
from pathlib import Path

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR.parent))            # 使 `import index_docs` 可用
import index_docs

EXTRACTORS = index_docs.EXTRACTORS
CHUNKS_PER_KB = 100000

# ---------------- 路径/注册表 ----------------

def _cfg():
    import llm
    c = llm.load_config()
    return Path(c.get("data_dir", "app/data"))


def load_registry():
    cfg = _cfg()
    reg_path = cfg / "registry.json"
    if not reg_path.exists():
        return []
    with open(reg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(reg):
    cfg = _cfg()
    cfg.mkdir(parents=True, exist_ok=True)
    with open(cfg / "registry.json", "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


def _new_id():
    return time.strftime("%Y%m%d_%H%M%S") + "_" + hashlib.md5(str(time.time_ns()).encode()).hexdigest()[:6]


def _kb_dir(kb_id):
    return (_cfg() / str(kb_id))


def upload_dir(kb_id):
    """该库的拖拽上传专用目录(与源文件夹一起并入索引)。"""
    d = _kb_dir(kb_id) / "upload"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_kb(kb_id):
    reg = load_registry()
    for kb in reg:
        if kb["id"] == kb_id:
            return kb
    return None


def resolve_kb_file(kb_id, rel):
    """按索引里的 rel 路径定位真实文件(源目录或 upload 目录), 防路径穿越。

    返回 Path(仅当真实存在) 或 None。
    """
    kb = get_kb(kb_id)
    if not kb:
        return None
    rel_clean = str(rel or "").strip().lstrip("/").replace("\\", "/")
    # 直接拒绝: 空 / 纯点 / 含 .. 的路径穿越
    if not rel_clean or rel_clean in (".", "..") or ".." in rel_clean.split("/"):
        return None
    up = upload_dir(kb_id)
    roots = []
    for r in str(kb.get("folder") or "").split("|"):
        if r.strip():
            roots.append(Path(r.strip()))
    roots.append(up)
    for root in roots:
        try:
            root_res = root.resolve()
            p = (root / rel_clean).resolve()
            # 防穿越: 解析后必须仍在根目录内
            if str(p).startswith(str(root_res) + "\\") or str(p).startswith(str(root_res) + "/"):
                if p.is_file():
                    return p
        except Exception:
            continue
    return None


def create_kb(name, folder):
    """新增一个知识库记录。folder 为源文件夹;可传空串 => 纯上传式(拖拽建库)知识库。"""
    reg = load_registry()
    _fold = str(folder or "").strip()
    _name = (name or "").strip()
    if not _name:
        _name = (Path(_fold).name if _fold else f"项目{len(reg) + 1}")
    kb = {
        "id": _new_id(),
        "name": _name,
        "folder": _fold,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated": "",
        "file_count": 0,
        "chunk_count": 0,
        "status": "created",
    }
    reg.insert(0, kb)
    save_registry(reg)
    return kb


def delete_kb(kb_id):
    reg = load_registry()
    reg = [kb for kb in reg if kb["id"] != kb_id]
    save_registry(reg)
    d = _kb_dir(kb_id)
    if d.exists():
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    return True


def rename_kb(kb_id, name):
    """重命名知识库。返回更新后的记录;不存在或名为空返回 None。"""
    name = str(name or "").strip()
    if not name:
        return None
    reg = load_registry()
    for kb in reg:
        if kb["id"] == kb_id:
            kb["name"] = name
            kb["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_registry(reg)
            return kb
    return None


def list_folders(path=None):
    """列出本机文件夹,供前端选择源目录。返回 [{path, dir, name}]。"""
    path = path or "."
    try:
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"ok": False, "msg": f"不是有效目录: {path}", "dirs": []}
    except Exception as e:
        return {"ok": False, "msg": str(e), "dirs": []}
    dirs = []
    try:
        for child in p.iterdir():
            if child.is_dir() and not child.name.startswith((".", "_rag", "__pycache__", "node_modules", ".git")):
                dirs.append({"path": str(child), "dir": child.is_dir(), "name": child.name})
    except Exception as e:
        return {"ok": False, "msg": str(e), "dirs": []}
    dirs.sort(key=lambda x: x["name"].lower())
    return {"ok": True, "current": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs}


# ---------------- 索引 ----------------

# 抽取逻辑版本号。改了 index_docs 里的抽取代码就改这里，否则增量索引会
# 认为文件"没变过"而直接跳过，新抽取的内容一条都进不了 chunks。
_SIG_VER = "v2026-10-30-imgmark2"


def _sig(path: Path):
    """文件签名 = 抽取器版本 + 大小 + 纳秒mtime，并并入「图片描述文件」的签名。

    文档本身没动、但跑完 img_index 生成了新的图片描述时，也必须重新抽取，
    否则描述进不了 chunks（踩过：重建索引 0 秒完成，图片描述一条没进）。
    """
    st = path.stat()
    base = f"{_SIG_VER}:{st.st_size}:{int(st.st_mtime_ns)}"
    try:
        import img_caption
        fp = img_caption.doc_text_path(path)
        if fp.exists():
            fs = fp.stat()
            base += f"|img:{fs.st_size}:{int(fs.st_mtime_ns)}"
    except Exception:
        pass
    return base


def _sig_mb(path: Path):
    st = path.stat()
    return f"mb:{st.st_size}:{int(st.st_mtime)}"


def index_kb(kb, progress=None):
    """增量重建某知识库。返回统计 dict。progress(text)->None 用于进度回显。"""
    d = _kb_dir(kb["id"])
    d.mkdir(parents=True, exist_ok=True)
    chunk_path = d / "chunks.jsonl"
    manifest_path = d / "manifest.json"

    # 读旧签名表
    old_sigs = {}
    all_file_sigs = {}
    if manifest_path.exists():
        try:
            old_sigs = json.loads(manifest_path.read_text(encoding="utf-8")).get("sigs", {})
        except Exception:
            old_sigs = {}

    # 扫描该知识库文件夹(可含多个真实文件夹,以 | 分隔) + 拖拽上传专用目录
    roots = [Path(r) for r in str(kb.get("folder") or "").split("|") if r.strip()]
    up = d / "upload"
    if not roots or up.is_dir():
        roots.append(up)
    scan = {}   # rel -> (path, sig)
    for root in roots:
        if not root.is_dir():
            continue
        for dp, dirs, files in root.walk():
            dirs[:] = [d for d in dirs if d not in index_docs.SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                if any(fn.startswith(s) for s in index_docs.SKIP_FILES):
                    continue
                if fn.endswith(tuple(index_docs.SKIP_EXT)):
                    continue
                p = Path(dp) / fn
                try:
                    sig = _sig(p)
                except Exception:
                    continue
                rel = p.relative_to(root).as_posix()
                key = f"{root.name}:{rel}"
                scan[key] = (p, sig)

    stats = {"files": len(scan), "added": 0, "changed": 0, "unchanged": 0, "removed": 0,
             "failed": 0, "total_chunks": 0}
    new_sigs = {}
    # 发现删除(旧有但新扫描不存在的文件):其旧块不被写入即被移除
    for key, o_sig in old_sigs.items():
        if key not in scan:
            if progress: progress(f"删除: {key}")
            stats["removed"] += 1

    # 旧块按文件归属路径分组,供复制与替换
    old_block = {}
    if chunk_path.exists():
        with open(chunk_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                old_block.setdefault(r["path"], []).append(line)

    out_lines = []
    # 1) 未变文件:直接保留旧块
    for key, o_sig in old_sigs.items():
        if key in scan and scan[key][1] == o_sig:
            # 在 scan 中仍存在且未变
            new_sigs[key] = o_sig
            stats["unchanged"] += 1
            old_lines = old_block.pop(key.split(":", 1)[1], None)
            # 但旧行 path 存的是 rel,而 key 存 root.name:rel;这里用 rel 作为归属键
            if old_lines:
                out_lines.extend(old_lines)
                stats["total_chunks"] += len(old_lines)

    # 2) 变更/新增文件:重解析
    for key, (p, sig) in scan.items():
        if key in new_sigs:
            continue
        rel = key.split(":", 1)[1]
        ext = p.suffix.lower()
        new_sigs[key] = sig
        if ext not in EXTRACTORS:
            continue
        try:
            text = EXTRACTORS[ext](p)
            if not text or not text.strip():
                stats["failed"] += 1
                continue
            doc_id = hashlib.md5(rel.encode("utf-8")).hexdigest()[:12]
            if old_block.pop(rel, None):
                stats["changed"] += 1
            else:
                stats["added"] += 1
            cs = list(index_docs.chunks(text))
            for i, c in enumerate(cs):
                rec = {"id": f"{doc_id}_{i:04d}", "path": rel, "chunk": i, "text": c}
                out_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
                stats["total_chunks"] += 1
            if progress: progress(f"已索引: {rel} ({len(cs)})")
        except Exception as e:
            stats["failed"] += 1
            if progress: progress(f"[FAIL] {rel}: {e}")

    # 3) 已删除文件:旧块自然不在 out_lines 中(未变会用 pop 保留,变更会替换) —— 需确认删除的块已剔除
    # 原子写: 先写临时文件再替换, 保证索引过程中正在提问的用户不会读到半截 JSON
    tmp_chunk = chunk_path.with_name(chunk_path.name + ".tmp")
    with open(tmp_chunk, "w", encoding="utf-8") as f:
        f.writelines(out_lines)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_chunk, chunk_path)

    manifest = {"sigs": new_sigs, "build_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stats": stats}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    stats["total_chunks"] = len(out_lines)

    # 更新注册表
    kb["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    kb["chunk_count"] = stats["total_chunks"]
    kb["file_count"] = stats["files"]
    kb["status"] = "ready"
    _update_registry(kb)
    # 向量库在后台增量补齐, 不阻塞"索引完成"的返回; 失败也不影响 BM25 检索
    try:
        if stats.get("total_chunks", 0) > 0:
            schedule_vectors(kb["id"])
    except Exception as e:
        print(f"[vectors] 调度失败(索引本身不受影响): {e}", file=sys.stderr)
    return stats


def _update_registry(kb):
    reg = load_registry()
    for i, k in enumerate(reg):
        if k["id"] == kb["id"]:
            reg[i] = kb
            break
    save_registry(reg)


# ---------------- 向量库自动维护 ----------------
# 建索引只产出 chunks.jsonl, 向量库以前要靠手动跑 build_vectors.py,
# 漏跑就会静默退化成纯 BM25。这里在索引结束后后台增量补齐, 每库同时只跑一个;
# 构建期间若又建了索引, 本轮结束后会按最新内容再跑一次。
_VEC_LOCK = threading.Lock()
_VEC_RUNNING = {}
_VEC_PENDING = set()
_REG_LOCK = threading.Lock()


def _auto_vectors_enabled():
    try:
        import llm
        return bool(llm.load_config().get("retrieval", {}).get("auto_vectors", True))
    except Exception:
        return True


def _set_vector_fields(kb_id, **fields):
    with _REG_LOCK:
        reg = load_registry()
        for kb in reg:
            if kb["id"] == kb_id:
                kb.update(fields)
                save_registry(reg)
                return kb
    return None


def schedule_vectors(kb_id):
    """索引完成后在后台增量构建/更新该库向量库。返回是否新起了任务。"""
    if not _auto_vectors_enabled():
        return False
    with _VEC_LOCK:
        if _VEC_RUNNING.get(kb_id):
            _VEC_PENDING.add(kb_id)
            return False
        _VEC_RUNNING[kb_id] = True
    _set_vector_fields(kb_id, vector_status="building")

    def run():
        import embeddings
        while True:
            try:
                st = embeddings.ensure_vectors(kb_id)
                if st.get("ok"):
                    _set_vector_fields(kb_id, vector_status="ready",
                                       vector_count=int(st.get("count", 0)),
                                       vector_ts=time.strftime("%Y-%m-%d %H:%M:%S"))
                    print(f"[vectors] {kb_id} 就绪: {st.get('count')} 条 "
                          f"(复用 {st.get('reused')}, 新算 {st.get('built')})", file=sys.stderr)
                else:
                    _set_vector_fields(kb_id, vector_status="failed")
                    print(f"[vectors] {kb_id} 失败: {st.get('msg')}", file=sys.stderr)
            except Exception as e:
                _set_vector_fields(kb_id, vector_status="failed")
                print(f"[vectors] {kb_id} 异常: {e}", file=sys.stderr)
            with _VEC_LOCK:
                if kb_id in _VEC_PENDING:
                    _VEC_PENDING.discard(kb_id)
                    continue
                _VEC_RUNNING.pop(kb_id, None)
                break

    threading.Thread(target=run, daemon=True, name=f"vec-{kb_id}").start()
    return True
