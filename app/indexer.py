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
import json, math, os, queue, re, shutil, sys, threading, time, hashlib, tracemalloc, zipfile
from pathlib import Path

APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR.parent))            # 使 `import index_docs` 可用
import index_docs

EXTRACTORS = index_docs.EXTRACTORS
CHUNKS_PER_KB = 100000
# 分块器版本：改动 chunks()/PPT 分块后 +1，让增量索引知道要重解析旧文件。
CHUNKER_VERSION = 2

# registry.json 的读-改-写必须串行，否则两个用户同时建库/改名/索引完成时
# 会互相覆盖（后写的把前一个新建的库整条丢掉）。RLock 允许嵌套调用。
_REGISTRY_LOCK = threading.RLock()

# ---------------- 路径/注册表 ----------------

def _cfg():
    import llm
    c = llm.load_config()
    return Path(c.get("data_dir", "app/data"))


def load_registry():
    cfg = _cfg()
    reg_path = cfg / "registry.json"
    with _REGISTRY_LOCK:
        if not reg_path.exists():
            return []
        with open(reg_path, "r", encoding="utf-8") as f:
            return json.load(f)


def save_registry(reg):
    cfg = _cfg()
    cfg.mkdir(parents=True, exist_ok=True)
    reg_path = cfg / "registry.json"
    tmp = cfg / "registry.json.tmp"
    with _REGISTRY_LOCK:
        # 原子写：并发读的人要么看到旧文件，要么看到完整新文件，不会读到半截
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, reg_path)


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


def list_kb_files(kb_id, limit=800):
    """列出知识库的源目录 + 上传目录文件（只读，不读内容）。

    用于前端确认“这个库到底放了哪些资料”。图片也列出来；隐藏目录、
    Office 临时文件不列。大库最多先返回 limit 条并标记 truncated。
    """
    kb = get_kb(kb_id)
    if not kb:
        return None
    roots = []
    for raw in str(kb.get("folder") or "").split("|"):
        raw = raw.strip()
        if raw:
            p = Path(raw)
            roots.append((p, "源目录"))
    roots.append((upload_dir(kb_id), "上传"))

    files = []
    total = 0
    for root, kind in roots:
        if not root.is_dir():
            continue
        for dp, dirs, fns in os.walk(root):
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", ".git", "node_modules")
                       and not d.startswith(".")]
            for fn in fns:
                if fn.startswith("~$") or fn.endswith(".tmp"):
                    continue
                p = Path(dp) / fn
                try:
                    st = p.stat()
                except Exception:
                    continue
                rel = p.relative_to(root).as_posix()
                total += 1
                if len(files) < limit:
                    files.append({
                        "name": fn,
                        "path": rel,
                        "root": root.name,
                        "source": kind,
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                    })
    files.sort(key=lambda x: (-x["mtime"], x["path"]))
    return {
        "ok": True,
        "files": files,
        "total": total,
        "truncated": total > len(files),
        "roots": [{"name": r.name, "kind": k, "exists": r.is_dir()}
                  for r, k in roots],
    }


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
    with _REGISTRY_LOCK:
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
    with _REGISTRY_LOCK:
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
    with _REGISTRY_LOCK:
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
_SIG_VER = "v2026-11-13-imgtext-chunks"


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


# ---------------- zip 自动解压（文件夹/上传目录里的 zip） ----------------
ZIP_MAX_FILES = 20000
ZIP_MAX_TOTAL = 2 * 1024 * 1024 * 1024      # 解压后总量
ZIP_MAX_ENTRY = 500 * 1024 * 1024           # 单文件
ZIP_MAX_DEPTH = 3                           # 嵌套层数
ZIP_MAX_RATIO = 150                         # 压缩比上限


def _zip_name(info):
    """没带 UTF-8 标记的 zip 会被 zipfile 按 cp437 解成乱码，尝试按 GBK 还原。"""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            plain = name.encode("cp437").decode("gbk")
            if plain and plain != name:
                name = plain
        except Exception:
            pass
    return name


def _zip_safe_extract(zpath: Path, dest: Path, depth=0, budget=None):
    """安全解压；递归处理内部 zip（限层数/总量）。返回解压文件数。"""
    if budget is None:
        budget = {"files": 0, "bytes": 0}
    if depth > ZIP_MAX_DEPTH or not zipfile.is_zipfile(zpath):
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    dest_res = dest.resolve()
    saved = 0
    with zipfile.ZipFile(zpath) as zf:
        infos = zf.infolist()
        total = sum(max(0, i.file_size) for i in infos)
        zsize = zpath.stat().st_size
        if len(infos) > ZIP_MAX_FILES - budget["files"]:
            raise ValueError("压缩包内文件过多")
        if total > ZIP_MAX_TOTAL - budget["bytes"]:
            raise ValueError("解压后体积超过限制")
        if zsize and total > max(200 * 1024 * 1024, zsize * ZIP_MAX_RATIO):
            raise ValueError("压缩比异常")
        nested = []
        for info in infos:
            if info.flag_bits & 0x1:
                raise ValueError("压缩包不能有密码")
            name = _zip_name(info)
            low = name.lower().replace("\\", "/")
            if (not name or low.startswith("__macosx/") or
                    low == ".ds_store" or low.endswith("/.ds_store") or
                    low == "thumbs.db" or low.endswith("/thumbs.db")):
                continue
            parts = [p for p in low.strip("/").split("/") if p]
            if not parts or any(p == ".." or ":" in p for p in parts):
                continue
            target = (dest / Path(*parts)).resolve()
            if not (str(target) == str(dest_res) or
                    str(target).startswith(str(dest_res) + os.sep)):
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000 or info.file_size == 0:
                continue
            if info.file_size > ZIP_MAX_ENTRY:
                continue
            if budget["files"] >= ZIP_MAX_FILES or \
                    budget["bytes"] + info.file_size > ZIP_MAX_TOTAL:
                break
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + f".{os.getpid()}_{time.time_ns()}.tmp")
            try:
                with zf.open(info) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                tmp.replace(target)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            saved += 1
            budget["files"] += 1
            budget["bytes"] += info.file_size
            if target.suffix.lower() == ".zip":
                nested.append(target)
    for nz in nested:
        if depth < ZIP_MAX_DEPTH:
            key = hashlib.md5(str(nz).encode("utf-8")).hexdigest()[:8]
            sub = dest / ".inner" / f"{nz.stem}-{key}"
            try:
                _zip_safe_extract(nz, sub, depth + 1, budget)
            except Exception as e:
                print(f"  [ZIP-NESTED-FAIL] {nz}: {e}", file=sys.stderr)
        try:
            nz.unlink()
        except Exception:
            pass
    return saved


def _zip_cache_dir(zpath: Path, unz_root: Path):
    stem = re.sub(r"[^\w一-龥.-]+", "_", zpath.stem)[:40] or "zip"
    key = hashlib.md5(str(zpath.resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return unz_root / f"{stem}-{key}"


def _extract_zip_cached(zpath: Path, unz_root: Path):
    """同一路径/同样大小的 zip 复用上次解压结果；变了就重解。"""
    dest = _zip_cache_dir(zpath, unz_root)
    st = zpath.stat()
    sig = f"{st.st_size}:{int(st.st_mtime_ns)}"
    marker = dest / ".src-signature"
    if marker.exists():
        try:
            if marker.read_text(encoding="utf-8").strip() == sig:
                return dest
        except Exception:
            pass
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        _zip_safe_extract(zpath, dest)
        marker.write_text(sig, encoding="utf-8")
        return dest
    except Exception as e:
        shutil.rmtree(dest, ignore_errors=True)
        print(f"  [ZIP-FAIL] {zpath}: {e}", file=sys.stderr)
        return None


# ---------------- 索引任务队列 ----------------
# index_kb 是同步的; 直接在请求线程里跑会占满 HTTP 线程池, 重建期间其他库的
# 提问也会变慢。这里用「单 worker 后台线程 + 持久化状态文件」:
#   - 入队立即返回, 前端轮询 index_status.json 看进度
#   - 同一库同时只跑一个; 运行中再次请求会合并成本轮后的重跑
#   - 失败重试 2 次, 仍失败标记 failed
#   - 重启后把遗留的 queued/running 标记为 interrupted, 不静默卡住
INDEX_STATUS_FILE = "index_status.json"
INDEX_MAX_ATTEMPTS = 3          # 首次 + 2 次重试
INDEX_RETRY_DELAY = 3.0         # 重试间隔(秒)

_INDEX_Q = queue.Queue()
_INDEX_LOCK = threading.Lock()
_INDEX_STATE = {}               # kb_id -> 状态 dict (内存镜像)
_INDEX_RUNNING = set()
_INDEX_PENDING = set()
_INDEX_WORKERS = []             # 多个独立知识库可以并行索引；同一个库仍只跑一个


def _now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _status_path(kb_id):
    return _kb_dir(kb_id) / INDEX_STATUS_FILE


def _write_status(kb_id, st):
    try:
        p = _status_path(kb_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 入队/worker/前端轮询可能同时写状态；临时名必须唯一
        tmp = p.with_name(
            f"{p.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        print(f"[index] 状态写入失败 {kb_id}: {e}", file=sys.stderr)


def get_index_status(kb_id):
    with _INDEX_LOCK:
        st = _INDEX_STATE.get(kb_id)
        if st:
            return dict(st)
    p = _status_path(kb_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"kb_id": kb_id, "status": "idle", "progress": "", "started": "",
            "finished": "", "error": "", "attempt": 0}


def recover_index_statuses():
    """服务启动时调用: 上次没跑完的任务标记为 interrupted, 避免一直显示 running。"""
    try:
        reg = load_registry()
    except Exception:
        return 0
    n = 0
    for kb in reg:
        kid = kb.get("id")
        if not kid:
            continue
        st = get_index_status(kid)
        if st.get("status") in ("queued", "running"):
            st.update({"status": "interrupted", "finished": _now_str(),
                       "error": st.get("error") or "服务重启，任务中断"})
            with _INDEX_LOCK:
                _INDEX_STATE[kid] = st
            _write_status(kid, st)
            n += 1
    return n


def _index_worker_count():
    """同时索引几个库。默认 2：不同用户建库不会互相排队，但不超过 4 个。"""
    try:
        import llm
        n = int(llm.load_config().get("indexing", {}).get("workers", 2))
    except Exception:
        n = 2
    return max(1, min(4, n))


def _ensure_worker():
    want = _index_worker_count()
    with _INDEX_LOCK:
        _INDEX_WORKERS[:] = [t for t in _INDEX_WORKERS if t.is_alive()]
        if len(_INDEX_WORKERS) >= want:
            return
    # recover 内部会拿 _INDEX_LOCK, 必须在锁外调用(同一把非重入锁, 否则死锁)
    recover_index_statuses()
    with _INDEX_LOCK:
        _INDEX_WORKERS[:] = [t for t in _INDEX_WORKERS if t.is_alive()]
        while len(_INDEX_WORKERS) < want:
            t = threading.Thread(target=_index_worker, daemon=True,
                                 name=f"index-worker-{len(_INDEX_WORKERS) + 1}")
            _INDEX_WORKERS.append(t)
            t.start()


def enqueue_index(kb_id, reason="manual"):
    """把某库的增量重建放进队列。返回 {"ok", "status", "msg", "job"}。"""
    kb = get_kb(kb_id)
    if not kb:
        return {"ok": False, "msg": "知识库不存在"}
    # 先恢复上次遗留状态并确保 worker 在跑, 再入队。
    # 顺序反过来会把刚入队的任务误判成"上次服务遗留"。
    _ensure_worker()
    with _INDEX_LOCK:
        st = _INDEX_STATE.get(kb_id)
        cur = st.get("status") if st else None
        if cur in ("queued", "running"):
            if cur == "running":
                _INDEX_PENDING.add(kb_id)
            return {"ok": True, "status": cur, "msg": "已在队列/索引中", "job": dict(st)}
        st = {"kb_id": kb_id, "status": "queued", "progress": "等待执行",
              "started": "", "queued_at": _now_str(), "finished": "",
              "error": "", "attempt": 0, "max_attempts": INDEX_MAX_ATTEMPTS,
              "stats": None, "peak_mb": None, "elapsed": None, "reason": reason}
        _INDEX_STATE[kb_id] = st
    _write_status(kb_id, st)
    _INDEX_Q.put(kb_id)
    return {"ok": True, "status": "queued", "msg": "已加入索引队列", "job": dict(st)}


def _memory_profile_enabled():
    try:
        import llm
        return bool(llm.load_config().get("indexing", {}).get("memory_profile", True))
    except Exception:
        return True


def _index_worker():
    while True:
        kb_id = _INDEX_Q.get()
        try:
            _run_index_job(kb_id)
        except Exception as e:
            print(f"[index] worker 异常 {kb_id}: {e}", file=sys.stderr)
        finally:
            with _INDEX_LOCK:
                _INDEX_RUNNING.discard(kb_id)
            _INDEX_Q.task_done()
        # 运行期间又来了请求: 按最新内容再跑一轮
        with _INDEX_LOCK:
            again = kb_id in _INDEX_PENDING
            if again:
                _INDEX_PENDING.discard(kb_id)
                st = _INDEX_STATE.get(kb_id) or {"kb_id": kb_id}
                st.update({"status": "queued", "progress": "等待重跑", "queued_at": _now_str()})
                _INDEX_STATE[kb_id] = st
        if again:
            _write_status(kb_id, st)
            _INDEX_Q.put(kb_id)


def _run_index_job(kb_id):
    kb = get_kb(kb_id)
    with _INDEX_LOCK:
        _INDEX_RUNNING.add(kb_id)
        st = _INDEX_STATE.get(kb_id) or {"kb_id": kb_id}
        st.update({"status": "running", "started": _now_str(),
                   "progress": "准备中", "error": ""})
        _INDEX_STATE[kb_id] = st
    if not kb:
        st.update({"status": "failed", "error": "知识库不存在", "finished": _now_str()})
        _write_status(kb_id, st)
        return
    _write_status(kb_id, st)

    last_write = [0.0]

    def progress(msg):
        st["progress"] = str(msg)[:300]
        now = time.time()
        if now - last_write[0] >= 1.0:
            last_write[0] = now
            _write_status(kb_id, st)

    # tracemalloc 是进程级全局，多个 worker 同时开会把彼此的峰值混在一起，
    # 所以只在单 worker 时采样内存；并行索引时 peak_mb 为 None。
    trace = _memory_profile_enabled() and _index_worker_count() == 1
    started_here = False
    if trace and not tracemalloc.is_tracing():
        tracemalloc.start()
        started_here = True
    t0 = time.time()
    stats = None
    err = None
    try:
        for attempt in range(1, INDEX_MAX_ATTEMPTS + 1):
            st["attempt"] = attempt
            _write_status(kb_id, st)
            try:
                stats = index_kb(kb, progress=progress)
                err = None
                break
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                st["error"] = err
                if attempt < INDEX_MAX_ATTEMPTS:
                    st["progress"] = (f"失败, {INDEX_RETRY_DELAY:.0f}s 后重试 "
                                      f"({attempt}/{INDEX_MAX_ATTEMPTS - 1})")
                    _write_status(kb_id, st)
                    time.sleep(INDEX_RETRY_DELAY)
    finally:
        if started_here:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            st["peak_mb"] = round(peak / (1024 * 1024), 1)
        st["elapsed"] = round(time.time() - t0, 1)

    if stats is not None:
        st.update({"status": "done", "progress": "完成", "finished": _now_str(),
                   "stats": stats, "error": ""})
    else:
        st.update({"status": "failed", "progress": "失败", "finished": _now_str(),
                   "error": err or "未知错误"})
    with _INDEX_LOCK:
        _INDEX_STATE[kb_id] = st
    _write_status(kb_id, st)
    print(f"[index] {kb_id} {st['status']} elapsed={st.get('elapsed')}s "
          f"peak={st.get('peak_mb')}MB", file=sys.stderr)


def index_kb(kb, progress=None):
    """增量重建某知识库。返回统计 dict。progress(text)->None 用于进度回显。"""
    d = _kb_dir(kb["id"])
    d.mkdir(parents=True, exist_ok=True)
    chunk_path = d / "chunks.jsonl"
    manifest_path = d / "manifest.json"

    # 读旧签名表
    old_sigs = {}
    all_file_sigs = {}
    old_manifest = {}
    if manifest_path.exists():
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            old_sigs = old_manifest.get("sigs", {})
        except Exception:
            old_manifest, old_sigs = {}, {}
    # 分块规则变了（例如 PPT 从按行切改成按页切），旧块不能继续复用；
    # 清空签名表，让所有文件重解析一次。
    if old_manifest.get("chunker") != CHUNKER_VERSION:
        old_sigs = {}

    # 扫描该知识库文件夹(可含多个真实文件夹,以 | 分隔) + 拖拽上传专用目录
    roots = [Path(r) for r in str(kb.get("folder") or "").split("|") if r.strip()]
    up = d / "upload"
    if not roots or up.is_dir():
        roots.append(up)
    scan = {}   # rel -> (path, sig)
    zip_paths = []
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
                if p.suffix.lower() == ".zip":
                    zip_paths.append(p)
                    continue
                try:
                    sig = _sig(p)
                except Exception:
                    continue
                rel = p.relative_to(root).as_posix()
                key = f"{root.name}:{rel}"
                scan[key] = (p, sig)

    # 文件夹/上传目录里的 zip 自动解压到 upload/.unzipped，再把内部文件并进扫描。
    # 源目录不改动；解压结果带签名，zip 没变就复用。
    if zip_paths:
        up.mkdir(parents=True, exist_ok=True)
        unz_root = up / ".unzipped"
        unz_root.mkdir(parents=True, exist_ok=True)
        used_dirs = set()
        for zp in dict.fromkeys(zip_paths):
            dest = _extract_zip_cached(zp, unz_root)
            if not dest:
                continue
            used_dirs.add(dest.name)
            for dp, dirs, files in os.walk(dest):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for fn in files:
                    if fn.startswith("~$") or fn.startswith(".") or fn.endswith((".tmp", ".zip")):
                        continue
                    if fn.endswith(tuple(index_docs.SKIP_EXT)):
                        continue
                    p = Path(dp) / fn
                    try:
                        sig = _sig(p)
                    except Exception:
                        continue
                    inner = p.relative_to(dest).as_posix()
                    rel = f".unzipped/{dest.name}/{inner}"
                    scan[f"upload:{rel}"] = (p, sig)
        for child in unz_root.iterdir():
            if child.is_dir() and child.name not in used_dirs and not child.name.startswith("."):
                shutil.rmtree(child, ignore_errors=True)

    stats = {"files": len(scan), "added": 0, "changed": 0, "unchanged": 0, "removed": 0,
             "failed": 0, "total_chunks": 0, "zips": len(zip_paths)}
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
            rel = key.split(":", 1)[1]
            old_lines = old_block.pop(rel, None)
            # 旧版跳过的扩展名（典型是 .sql）签名还在，但没有块；
            # 现在已经支持了，不能当“未变”跳过，必须补解析一次。
            if old_lines or scan[key][0].suffix.lower() != ".sql":
                new_sigs[key] = o_sig
                stats["unchanged"] += 1
                if old_lines:
                    out_lines.extend(old_lines)
                    stats["total_chunks"] += len(old_lines)

    # 2) 变更/新增文件:重解析
    todo = [(key, p, sig) for key, (p, sig) in scan.items() if key not in new_sigs]
    if progress:
        progress(f"扫描 {len(scan)} 个文件，未变 {stats['unchanged']}，"
                 f"需处理 {len(todo)}")
    for done, (key, p, sig) in enumerate(todo, 1):
        rel = key.split(":", 1)[1]
        ext = p.suffix.lower()
        new_sigs[key] = sig
        if progress:
            progress(f"解析 {done}/{len(todo)}: {rel}")
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
            # 图片描述单独成块: 塞在正文尾部会被长块稀释, 图片里的知识几乎检索不到。
            # 先从正文里摘掉 sidecar, 再给每条描述生成独立 chunk。
            cap = ""
            try:
                import img_caption
                cap = img_caption.load_doc_text(p)
            except Exception:
                cap = ""
            if cap:
                body = text.rstrip()
                cap_s = cap.strip()
                if body.endswith(cap_s):
                    text = body[: -len(cap_s)].rstrip()
            cs = list(index_docs.chunks_with_meta(text, ext, rel))
            for i, (c, meta) in enumerate(cs):
                rec = {"id": f"{doc_id}_{i:04d}", "path": rel, "chunk": i, "text": c}
                for k, v in (meta or {}).items():
                    if v not in (None, ""):
                        rec[k] = v
                out_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
                stats["total_chunks"] += 1
            if cap:
                for j, line in enumerate(ln for ln in cap.splitlines() if ln.strip()):
                    rec = {"id": f"{doc_id}_img_{j:04d}", "path": rel,
                           "chunk": len(cs) + j, "text": line}
                    out_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
                    stats["total_chunks"] += 1
            if progress:
                progress(f"解析 {done}/{len(todo)}: {rel} → {len(cs)} 块")
        except Exception as e:
            stats["failed"] += 1
            if progress: progress(f"[FAIL] {rel}: {e}")

    # 3) 已删除文件:旧块自然不在 out_lines 中(未变会用 pop 保留,变更会替换) —— 需确认删除的块已剔除
    # 原子写: 先写临时文件再替换, 保证索引过程中正在提问的用户不会读到半截 JSON
    tmp_chunk = chunk_path.with_name(
        f"{chunk_path.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
    with open(tmp_chunk, "w", encoding="utf-8") as f:
        f.writelines(out_lines)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_chunk, chunk_path)

    manifest = {"sigs": new_sigs, "chunker": CHUNKER_VERSION,
                "build_time": time.strftime("%Y-%m-%d %H:%M:%S"),
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
    with _REGISTRY_LOCK:
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


def _auto_vectors_enabled():
    try:
        import llm
        return bool(llm.load_config().get("retrieval", {}).get("auto_vectors", True))
    except Exception:
        return True


def _set_vector_fields(kb_id, **fields):
    with _REGISTRY_LOCK:
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
