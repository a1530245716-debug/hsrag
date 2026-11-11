# -*- coding: utf-8 -*-
"""图片索引：把知识库里每张图的「OCR 文字 + 语义摘要」离线建好，供检索与展示使用。

背景
----
`index_docs.py` 的 OCR 只在**文档完全没有文字层**时才触发（整份扫描件）。
正常手册 docx 走的是文本分支，里面夹的截图从来没被读过，只在文末留几个
`[image1.png]` 占位符（每份文档最多 4 个）。结果是：答案只写在截图里的问题，
永远检索不到。

这个模块补上这一环，思路是「先用摘要判断，再决定要不要读全量 OCR」：

1. **全量取图**：`DocImages.all_names()` 拿文档里所有图，不截断。
2. **按内容去重**：同一份手册的多个版本往往图完全相同，全库 4239 张里有 61%
   是重复的，去重后只剩 1640 张，省掉大半 OCR 和模型调用。
3. **OCR**：`ocr_extract.ocr_array()` 抽图里的文字（配置项、IP、报错都要）。
4. **多模态摘要**：让视觉模型看图，给一句话描述 + 判定图有没有有效内容。
   - 纯 logo / 分隔线 / 空白块的摘要会落在 `useful=false`，**不参与检索也不展示**。
   - 有内容的图给出「这图讲什么」，比 OCR 全文短得多，进检索不会带噪音。
5. **落盘**：写到 `<kb_dir>/image_index.json`，**不改 chunks.jsonl、不动向量库**。

检索侧用法（`app/server.py`）
--------------------------
- 摘要文本参与检索计分（短、干净，不怕噪音）。
- 摘要命中度高 → 需要细节时再取该图的 `ocr` 全文。
- `useful=false` 的图直接跳过。

设计约束
--------
- 本模块只**读** `index_docs.py` / `indexer.py` / `imgserve.py` 的公开能力，不改它们。
- 输出文件独立，任何时候都可以删掉重建，不影响现有检索。
- 支持断点续跑：已处理过的图（按内容哈希）直接跳过。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
# 允许 `python -m app.imgindex` 和 `python app/imgindex.py` 两种跑法
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_ROOT.parent))

INDEX_NAME = "image_index.json"
INDEX_VERSION = 1

# ---- 摘要提示词 ----
# 要求模型：先判断图有没有有效内容，再给一句话描述；有文字就带上关键信息。
_SUMMARY_SYSTEM = (
    "你是技术文档配图的分析助手。用户会给你一张来自交付文档的图片。"
    "请只描述你真实看到的内容，不要猜测、不要编造。"
)

_SUMMARY_PROMPT = """分析这张图，按下面的 JSON 格式输出（只输出 JSON，不要任何解释）：

{
  "useful": true/false,
  "kind": "截图|配置|流程图|架构图|表格|示意图|照片|图标|其他",
  "summary": "一句话说清这张图在讲什么（不超过60字）",
  "keywords": ["关键词1", "关键词2", "关键词3"]
}

判断规则：
- useful=false：纯 logo、分隔线、空白块、纯色块、无信息的装饰图、完全糊掉看不清的图。
- useful=true：界面截图、配置文件、报错信息、流程图、架构图、表格、拓扑图、参数说明等**有信息量**的图。
- summary 要说「这张图让人知道什么」，例如「O-order 配置文件中红框标出 FullSubscription 需设为 1」，
  而不是「一张图片」「配置文件的截图」这种没有信息的话。
- keywords 写这张图涉及的系统名、配置项名、报错关键字、模块名，便于后续检索命中。
"""


# ---------------- 基础设施 ----------------

def kb_dir(kb_id: str) -> Path:
    import indexer
    return Path(indexer._kb_dir(kb_id))


def index_path(kb_id: str) -> Path:
    return kb_dir(kb_id) / INDEX_NAME


# 索引有 3MB / 1640 条，每次回答都要读会拖慢 3 秒以上（实测 3.6s/次）。
# 按 (mtime, size) 缓存，索引重建后自动失效，不用重启服务。
_INDEX_CACHE = {}


def load_index(kb_id: str) -> dict:
    """读图片索引。返回 {"version":1,"kb_id":...,"images":{hash:{...}}}。没有就返回空壳。

    结果按文件 mtime+size 缓存：同一个索引不会重复解析 3MB JSON。
    注意返回的是**共享对象**，调用方只读、不要就地修改。
    """
    p = index_path(kb_id)
    if not p.exists():
        return {"version": INDEX_VERSION, "kb_id": kb_id, "images": {}}
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _INDEX_CACHE.get(kb_id)
        if hit and hit[0] == key:
            return hit[1]
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d.get("images"), dict):
            d["images"] = {}
        _INDEX_CACHE[kb_id] = (key, d)
        return d
    except Exception as e:
        print(f"[imgindex] 索引读取失败，按空处理: {e}")
        return {"version": INDEX_VERSION, "kb_id": kb_id, "images": {}}


def clear_cache(kb_id: str = ""):
    """手动清缓存（调试用）。不传就全清。"""
    if kb_id:
        _INDEX_CACHE.pop(kb_id, None)
    else:
        _INDEX_CACHE.clear()


def save_index(kb_id: str, data: dict):
    """原子写，避免中断留下半个文件。"""
    p = index_path(kb_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _INDEX_CACHE.pop(kb_id, None)


def _md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def docs_fingerprint(kb_id: str) -> str:
    """chunks.jsonl 的指纹（mtime+size）。文档重建后必然变化。"""
    p = kb_dir(kb_id) / "chunks.jsonl"
    if not p.exists():
        return ""
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except Exception:
        return ""


def docs_unchanged(kb_id: str) -> bool:
    """文档自上次建图片索引以来是否没变。用来跳过无意义的重新扫描。

    `scan_images` 要解包全部文档算图片哈希，1640 张的库空跑一次要 37 秒。
    """
    d = load_index(kb_id)
    fp = docs_fingerprint(kb_id)
    old = d.get("docs_fp") or ""
    # 没有指纹记录（老索引）就认为"变了"，让它跑一次补上
    return bool(fp) and fp == old


def mark_docs_indexed(kb_id: str):
    """把当前文档指纹写进索引，下次 docs_unchanged 就能判断了。"""
    data = load_index(kb_id)
    data["docs_fp"] = docs_fingerprint(kb_id)
    save_index(kb_id, data)


# ---------------- 取图 ----------------

def list_docs(kb_id: str):
    """知识库里所有文档（按 chunks.jsonl 里出现过的 path，保序去重）。"""
    p = kb_dir(kb_id) / "chunks.jsonl"
    out, seen = [], set()
    if not p.exists():
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            rel = r.get("path")
            if rel and rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


# 文档图片清单缓存版本：改了取图逻辑就改这里，旧缓存会自动重扫。
SCAN_VERSION = "v2026-11-09-docimg-cache"


def _doc_sig(full: Path) -> str:
    """单份文档的签名：大小 + 纳秒 mtime。文档没变就不重复解包。"""
    try:
        st = full.stat()
        return f"{SCAN_VERSION}:{st.st_size}:{st.st_mtime_ns}"
    except Exception:
        return ""


def _scan_one_doc(kb_id, rel, full):
    """解包一份文档，返回 (图片清单, 是否完整)。失败返回 (None, False)。"""
    import imgserve
    from docimg import DocImages

    try:
        d = DocImages(full)
        if not d.ok():
            return None, False
        names = d.all_names()
    except Exception:
        return None, False

    out, complete = [], True
    for name in names:
        try:
            data, _ext = imgserve.extract(kb_id, rel, name)
        except Exception:
            data = None
        if not data:
            complete = False       # 有图没取出来：这份文档下次还要重扫
            continue
        out.append({"name": name, "hash": _md5(data), "bytes": len(data)})
    return out, complete


def scan_images(kb_id: str, progress=None, data=None, save=True):
    """遍历全库，产出每张图的统一条目（已按内容哈希去重）。

    返回 list[dict]，每项：
      hash, docs:[{path, name}]（同一张图可能出现在多份文档里）, bytes

    **按文档增量**：每份文档的图片清单（名字/哈希/大小）缓存在
    image_index.json 的 `doc_images` 里，签名不变就直接复用，不再解包。
    只重扫新增/修改/上次没取完整的文档。
    """
    import imgserve

    if data is None:
        data = load_index(kb_id)
    old = data.get("doc_images") if isinstance(data.get("doc_images"), dict) else {}
    new_cache = {}
    by_hash = {}
    docs = list_docs(kb_id)
    reused = rescanned = 0

    for di, rel in enumerate(docs):
        try:
            full = imgserve.doc_full_path(kb_id, rel)
        except Exception:
            full = None
        if not full:
            continue

        sig = _doc_sig(full)
        cached = old.get(rel)
        if (cached and sig and cached.get("sig") == sig
                and cached.get("complete", True)
                and isinstance(cached.get("images"), list)):
            imgs = cached["images"]
            reused += 1
        else:
            imgs, complete = _scan_one_doc(kb_id, rel, full)
            if imgs is None:
                continue
            rescanned += 1
            cached = {"sig": sig, "complete": complete, "images": imgs}
        new_cache[rel] = cached

        for im in cached["images"]:
            h, name = im.get("hash"), im.get("name")
            if not h or not name:
                continue
            it = by_hash.get(h)
            if it:
                it["docs"].append({"path": rel, "name": name})
            else:
                by_hash[h] = {"hash": h, "docs": [{"path": rel, "name": name}],
                              "bytes": int(im.get("bytes") or 0)}

        if progress and (di + 1) % 20 == 0:
            progress(f"扫描文档 {di+1}/{len(docs)}，复用 {reused} 份、重扫 {rescanned} 份，"
                     f"唯一图 {len(by_hash)}")

    data["doc_images"] = new_cache
    data["scan_version"] = SCAN_VERSION
    if save:
        save_index(kb_id, data)
    return list(by_hash.values())


# ---------------- OCR ----------------

def ocr_image(data: bytes) -> str:
    """对单张图做 OCR。失败返回空串，不抛异常。

    这里有两个坑，都是实测出来的：

    1. **通道顺序**：`ocr_extract.ocr_array` 要求 BGR（OpenCV 惯例），
       直接喂 PIL 的 RGB 会让红蓝通道互换，影响识别质量。统一转 BGR。

    2. **过窄的条状图识别不出来**：文档里有很多只截了一行的图
       （如 1271x105 的输入框），RapidOCR 的文本检测模型会整张漏掉，
       返回 0 字。**放大 2~8 倍都没用，补白边才行** —— 上下各补一段白，
       恢复成接近正常宽高比后立刻能识别（实测 0 字 -> 47 字）。
    """
    try:
        import numpy as np
        import ocr_extract
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        if im.mode != "RGB":
            im = im.convert("RGB")

        w, h = im.size
        # 宽高比过于极端（细长条）就补白边，让检测模型有足够上下文
        if h > 0 and w / h >= 5:
            pad = max(60, int(h * 1.2))
            canvas = Image.new("RGB", (w, h + pad * 2), "white")
            canvas.paste(im, (0, pad))
            im = canvas
        elif w > 0 and h / w >= 5:
            pad = max(60, int(w * 1.2))
            canvas = Image.new("RGB", (w + pad * 2, h), "white")
            canvas.paste(im, (pad, 0))
            im = canvas
        # 整体太小的也放大一点
        elif max(w, h) < 700:
            sc = 2.0
            im = im.resize((int(w * sc), int(h * sc)), Image.LANCZOS)

        # RGB -> BGR（引擎要求）
        arr = np.array(im)[:, :, ::-1].copy()
        text = ocr_extract.ocr_array(arr) or ""
        return text.strip()
    except Exception as e:
        print(f"[imgindex] OCR 失败: {e}")
        return ""


# ---------------- 多模态摘要 ----------------

def _to_jpeg_b64(data: bytes, max_w: int = 1400, quality: int = 85) -> str:
    """统一转成体积可控的 JPEG（模型对超大图没额外收益，只是更慢）。"""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    if w > max_w:
        im = im.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _parse_summary(txt: str) -> dict:
    """从模型输出里抠 JSON。模型偶尔会包 ```json 或前后带话。"""
    if not txt:
        return {}
    s = txt.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s[3:]
        s = s.lstrip("json").strip()
        if s.endswith("```"):
            s = s[:-3]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        d = json.loads(s[i:j + 1])
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def summarize_image(data: bytes, timeout: int = 180, retries: int = 3) -> dict:
    """让多模态模型看图，返回 {useful, kind, summary, keywords}；失败返回 {}。

    这里不走 llm.chat：实测网关耗时和输出量无关，主要花在排队上，
    串行 12.7s/张、8 并发 2.3s/张。所以建索引改用外部线程池并发调用，
    这个函数只负责单次请求，并且**限制 max_tokens**（摘要 300 token 足够）。
    """
    try:
        import llm
        b64 = _to_jpeg_b64(data)
        s = llm.llm_settings()
        url = s["base_url"].rstrip("/") + "/v1/messages"
        hdr = {"Authorization": f"Bearer {s['api_key']}",
               "anthropic-version": s.get("anthropic_version", "2023-06-01"),
               "Content-Type": "application/json"}
        # max_tokens 要给足：这个模型会先吐 thinking 再给答案，
        # 卡 300 的话思维链就把额度吃光、答案被截断（实测 200 但 text 为空）。
        payload = {"model": s["model"], "max_tokens": 1200, "temperature": 0.3,
                   "system": _SUMMARY_SYSTEM,
                   "messages": [{"role": "user", "content": [
                       {"type": "image", "source": {"type": "base64",
                                                   "media_type": "image/jpeg", "data": b64}},
                       {"type": "text", "text": _SUMMARY_PROMPT},
                   ]}]}
        last = ""
        for attempt in range(retries + 1):
            try:
                import httpx
                with httpx.Client(timeout=float(timeout)) as c:
                    r = c.post(url, json=payload, headers=hdr)
                if r.status_code != 200:
                    last = f"HTTP {r.status_code} {r.text[:120]}"
                    if attempt < retries:
                        time.sleep(3 * (attempt + 1))
                        continue
                    break
                # 注意：模型可能返回 thinking 块。max_tokens=300 时思维链会把额度吃光，
                # text 块为空 —— 之前就是因此一直"失败"。这里把所有 text 块拼起来，
                # 并把 max_tokens 提高（见 payload）给正式回答留空间。
                j = r.json()
                blocks = j.get("content") or []
                txt = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                d = _parse_summary(txt)
                if not d:
                    last = f"解析不出 JSON（text {len(txt)} 字）"
                    if attempt < retries:
                        time.sleep(2 + attempt * 3)
                        continue
                    return {}
                return {
                    "useful": bool(d.get("useful", True)),
                    "kind": str(d.get("kind") or "")[:16],
                    "summary": str(d.get("summary") or "").strip()[:200],
                    "keywords": [str(k)[:24] for k in (d.get("keywords") or [])
                                 if str(k).strip()][:8],
                }
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:100]}"
                if attempt < retries:
                    time.sleep(2 + attempt * 3)
        print(f"[imgindex] 摘要失败: {last}")
        return {}
    except Exception as e:
        print(f"[imgindex] 摘要异常: {type(e).__name__} {str(e)[:120]}")
        return {}


# ---------------- 建索引主流程 ----------------

def _process_one(args):
    """处理单张图：取字节 -> OCR -> 摘要。在线程池里跑。

    返回 (hash, rec, 是否OCR, 是否摘要成功, 是否无内容)
    """
    kb_id, h, docs, size, need_ocr, need_sum = args
    try:
        import imgserve
        raw, _ext = imgserve.extract(kb_id, docs[0]["path"], docs[0]["name"])
    except Exception:
        raw = None
    if not raw:
        return h, None, False, False, False

    rec = {"hash": h, "docs": docs, "size": size, "ts": int(time.time())}
    did_ocr = did_sum = unuseful = False

    if need_ocr:
        rec["ocr"] = ocr_image(raw)
        did_ocr = True

    if need_sum:
        s = summarize_image(raw)
        if s:
            rec.update(s)
            did_sum = True
            unuseful = not s.get("useful")
        else:
            # 摘要失败时的兜底：OCR 已经读出实质文字，说明这图有内容，
            # 不能因为模型没返回就当成"无内容"丢掉。用 OCR 开头当临时摘要，下次重跑会覆盖。
            txt = (rec.get("ocr") or "").strip()
            if len(txt) >= 30:
                rec["useful"] = True
                rec["kind"] = rec.get("kind") or "截图"
                rec["summary"] = txt.replace("\n", " ")[:80]
                rec["fallback_ocr"] = True
            else:
                rec["summary"] = ""

    return h, rec, did_ocr, did_sum, unuseful


def build(kb_id: str, limit: int = 0, workers: int = 6,
          do_ocr: bool = True, do_summary: bool = True,
          progress=None, save_every: int = 40):
    """建图片索引，支持断点续跑。

    limit:   只处理前 N 张（试跑用）；0 = 全量
    workers: 并发数。实测串行 12.7s/张、并发 8 约 2.3s/张。
             **默认 6**：比 8 温和一些，给用户正常提问留出网关余量。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    say = progress or (lambda m: print(m))
    data = load_index(kb_id)
    images = data["images"]

    say("扫描文档里的图片…")
    items = scan_images(kb_id, data=data)
    say(f"唯一图片 {len(items)} 张（按内容去重）")
    if limit:
        items = items[:limit]

    # 先挑出真正要干的，已经处理过的直接跳过（断点续跑）
    todo, skip = [], 0
    for it in items:
        h = it["hash"]
        prev = images.get(h) or {}
        need_ocr = do_ocr and not prev.get("ocr")
        need_sum = do_summary and not prev.get("summary")
        if not need_ocr and not need_sum:
            skip += 1
            continue
        todo.append((kb_id, h, it["docs"], it["bytes"], need_ocr, need_sum))
    say(f"本次需处理 {len(todo)} 张，已完成的跳过 {skip} 张")

    stat = {"total": len(items), "skip": skip, "ocr": 0, "sum": 0, "fail": 0,
            "unuseful": 0, "workers": workers}
    t0 = time.time()
    done = 0

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_process_one, a): a for a in todo}
        for fu in as_completed(futs):
            done += 1
            try:
                h, rec, did_ocr, did_sum, unuseful = fu.result()
            except Exception as e:
                stat["fail"] += 1
                print(f"[imgindex] 处理异常: {type(e).__name__} {str(e)[:100]}")
                continue
            if rec is None:
                stat["fail"] += 1
                continue
            old = images.get(h) or {}
            # 合并：保留上一轮已有字段，本轮成功的结果覆盖
            merged = {**old, **{k: v for k, v in rec.items() if v not in (None, "")}}
            merged["docs"] = rec["docs"]
            merged["size"] = rec["size"]
            images[h] = merged
            if did_ocr:
                stat["ocr"] += 1
            if did_sum:
                stat["sum"] += 1
                if unuseful:
                    stat["unuseful"] += 1
            else:
                stat["fail"] += 1

            if done % 10 == 0 or done == len(todo):
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (len(todo) - done) / rate / 60 if rate else 0
                say(f"[{done}/{len(todo)}] 已用 {el/60:.1f} 分钟  "
                    f"{rate:.2f} 张/秒  预计还需 {eta:.0f} 分钟  "
                    f"无内容 {stat['unuseful']} 失败 {stat['fail']}")
            if done % save_every == 0:
                data["images"] = images
                data["updated"] = int(time.time())
                save_index(kb_id, data)

    data["images"] = images
    data["version"] = INDEX_VERSION
    data["kb_id"] = kb_id
    data["updated"] = int(time.time())
    data["total_unique"] = len(images)
    data["docs_fp"] = docs_fingerprint(kb_id)   # 记下这版文档，下次没变就跳过扫描
    save_index(kb_id, data)

    # ---- 补跑：并发下总有几张因为网关抖动失败 ----
    # 串行、低压力地再试一遍，成功率明显更高（实测并发失败的图单独跑一次就过）。
    retry_list = [a for a in todo if "useful" not in (images.get(a[1]) or {})]
    if retry_list:
        say(f"\n补跑 {len(retry_list)} 张并发时失败的图（串行）…")
        fixed = 0
        for n, a in enumerate(retry_list, 1):
            h, rec, did_ocr, did_sum, unuseful = _process_one(
                (a[0], a[1], a[2], a[3], a[4], a[5]))
            if rec and "useful" in rec:
                old = images.get(h) or {}
                images[h] = {**old, **{k: v for k, v in rec.items() if v not in (None, "")}}
                fixed += 1
            if n % 5 == 0:
                say(f"  补跑 {n}/{len(retry_list)}，已修好 {fixed} 张")
            time.sleep(0.5)
        stat["retry_fixed"] = fixed
        data["images"] = images
        data["updated"] = int(time.time())
        save_index(kb_id, data)

    stat["elapsed_min"] = round((time.time() - t0) / 60, 1)
    return stat


def stats(kb_id: str):
    """看当前索引状况。

    注意区分三种状态，别混：
      - 有内容：摘要判定 useful=True
      - 无内容：摘要判定 useful=False（纯 logo 等，不检索不展示）
      - 待处理：摘要失败/还没跑（useful 字段缺失）——这些下次跑会自动重试
    """
    d = load_index(kb_id)
    imgs = d.get("images") or {}
    vals = list(imgs.values())
    return {
        "总唯一图": len(imgs),
        "有内容": sum(1 for v in vals if v.get("useful") is True),
        "无内容(跳过)": sum(1 for v in vals if v.get("useful") is False),
        "待处理(摘要失败)": sum(1 for v in vals if "useful" not in v),
        "有OCR": sum(1 for v in vals if v.get("ocr")),
        "有摘要": sum(1 for v in vals if v.get("summary")),
        "更新时间": d.get("updated"),
    }


# ---------------- 意思绑定：答案段落 ↔ 图片摘要 ----------------
#
# 不做"按文档物理位置绑定"。位置绑定在 PDF 上几乎不可用（图没有段落结构），
# docx 里段落和图也常错位。改成语义匹配：答案拆段落，每段和候选图的摘要算余弦相似度，
# 超过阈值的图挂到那一段后面。PDF/docx 一视同仁。

def _norm(v):
    import numpy as np
    n = float(np.linalg.norm(v)) or 1.0
    return v / n


def split_answer(answer: str):
    """把答案拆成段落（按空行/编号小节），返回 [(原文段落, 纯文本)]。"""
    import re
    text = (answer or "").strip()
    if not text:
        return []
    # 优先按空行分段；没有空行就按「一、二、」或「1. 2.」这类小节切
    chunks = [c for c in re.split(r"\n\s*\n", text) if c.strip()]
    if len(chunks) <= 1:
        chunks = [c for c in re.split(r"\n(?=\s*(?:[一二三四五六七八九十]+[、.]|\d+[、.．]\s))", text)
                  if c.strip()]
    out = []
    for c in chunks:
        plain = re.sub(r"[#*`>\-\|]", " ", c)
        plain = re.sub(r"\s+", " ", plain).strip()
        if len(plain) >= 8:          # 太短的（标题词、标点）不参与匹配
            out.append((c, plain))
    return out


def _image_text(v: dict) -> str:
    """一张图用于匹配的文本：摘要为主，关键词补充。OCR 全文太长，会把相似度稀释掉。"""
    parts = []
    if v.get("summary"):
        parts.append(str(v["summary"]))
    if v.get("keywords"):
        parts.append(" ".join(str(k) for k in v["keywords"]))
    if v.get("kind"):
        parts.append(str(v["kind"]))
    return " ".join(parts).strip()


# ---------------- 位置信号（只对 pptx 可信） ----------------
#
# 实测过：docx 的 `[image1.png] ... [image4.png]` 标记是 index_docs 无脑贴的
# "本文档前 4 张图"，与正文内容无关 —— 138 份含图 docx 里，
# **0 份**存在多种标记组合，91 份所有块标记完全一样。这种位置信息是假的，用了会误导。
#
# pptx 不一样：它按页写标记，18 块里有 17 种不同组合，是**真实的按页绑定**。
# 所以位置加权只对 pptx 生效，且权重给得低（0.25），只做微调，不改变语义主导。

# 位置加分的生效方式：**只在语义分数接近时打破平局**，不做无差别加分。
#
# 实测过直接加 0.25 的问题：一个 pptx 的第 1 块里列了 9 张图，位置信号粒度太粗，
# 结果把语义 0.701 的「智能交易平台物理架构图」挤到第二，
# 换成语义只有 0.564 的「COP系统架构图」（它也在那个块里）排第一 —— 明显不对。
# 所以改成：只有两张图语义分差在 _POS_TIE 以内时，才是位置说了算。
_POS_TIE = 0.06            # 分差小于这个值时，位置命中的优先
_POS_BOOST = _POS_TIE     # 加分恰好够翻转平局，不影响更大的语义差距

# 挂图的相对窗口：每段只收"离本段最高分不远"的图。
# 实测依据（6 条真实问题、168 个段落-图组合）：同一条回答里各段最高分差异很大
#   「O-order 添加服务器」各段最高 0.736/0.751/0.788/0.845/0.742
#   「发布物怎么上传」各段最高 0.644/0.858/0.780/0.614/0.655  ← 第4段天生偏弱
# 固定绝对阈值 0.66 会把 0.614 那段整段误杀；固定 0.42 又让强段落塞满 0.6x 噪音。
_REL_WINDOW = 0.10         # 每段收进"最高分 - 0.10"以内的图
_ABS_FLOOR = 0.60         # 但再弱也不能低于这个分（0.42 太松，实测 0.6x 已明显跑题）


_POS_CACHE = {}


def _pos_index(kb_id: str) -> dict:
    """建立 {文档路径: {图片名: 该文档里引用它的块序号列表}}，只收 pptx。

    docx/xlsx 的标记不可信，直接跳过。
    要扫 5 万多行 chunks.jsonl，所以按 mtime+size 缓存。
    """
    import re
    p = kb_dir(kb_id) / "chunks.jsonl"
    if not p.exists():
        return {}
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
        hit = _POS_CACHE.get(kb_id)
        if hit and hit[0] == key:
            return hit[1]
    except Exception:
        key = None
    pat = re.compile(r"\[((?:插图\s*)?(?:image|media|img)[-_]?\d+\.(?:png|jpe?g|gif|bmp|webp|emf|wmf|tiff?))\]", re.I)
    out = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            rel = r.get("path") or ""
            if not rel.lower().endswith((".pptx", ".ppt")):
                continue
            names = pat.findall(r.get("text") or "")
            if not names:
                continue
            ch = r.get("chunk", 0)
            slot = out.setdefault(rel, {})
            for n in names:
                slot.setdefault(n, []).append(ch)
    if key is not None:
        _POS_CACHE[kb_id] = (key, out)
    return out


def build_vectors(kb_id: str, batch: int = 64, progress=None):
    """把每张图的摘要文本预先编码成向量，存进索引（`vec` 字段）。

    为什么要预计算：候选图每次回答都要重新编码，300 张要 3.5 秒，
    而图片是静态的 —— 算一次存下来，之后每问只编码答案那几段（30ms 级）。
    """
    say = progress or (lambda m: print(m))
    data = load_index(kb_id)
    imgs = data.get("images") or {}
    todo = [(h, v) for h, v in imgs.items()
            if _image_text(v) and not v.get("vec")]
    say(f"待编码 {len(todo)} 张，已有向量 {len(imgs) - len(todo)} 张")
    if not todo:
        return {"encoded": 0, "total": len(imgs)}

    import embeddings
    import numpy as np
    t0 = time.time()
    done = 0
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        vecs = embeddings.embed_texts([_image_text(v) for _, v in part])
        if vecs is None:
            say("向量模型未就绪，中止")
            break
        for (h, _v), row in zip(part, vecs):
            imgs[h]["vec"] = [round(float(x), 5) for x in np.asarray(row)]
        done += len(part)
        if done % 320 == 0 or done == len(todo):
            say(f"  [{done}/{len(todo)}] {time.time()-t0:.0f}s")
        if done % 640 == 0:
            data["images"] = imgs
            save_index(kb_id, data)

    data["images"] = imgs
    data["vec_dim"] = 512
    data["vec_updated"] = int(time.time())
    save_index(kb_id, data)
    return {"encoded": done, "total": len(imgs), "elapsed_s": round(time.time() - t0, 1)}


def candidate_images(kb_id: str, source_paths=None, limit: int = 300):
    """挑出参与匹配的候选图。

    source_paths: 只在本次命中的文档里找图（默认），传 None 表示全库。
    这样既快又准：答案来自哪几个文件，就优先从那几个文件里找图。
    """
    d = load_index(kb_id)
    imgs = d.get("images") or {}
    if not imgs:
        return []
    paths = set(source_paths or [])
    out = []
    for h, v in imgs.items():
        # 只保留判定有用的图（纯 logo/分隔线不参与）
        if v.get("useful") is False:
            continue
        if paths:
            if not any(doc.get("path") in paths for doc in (v.get("docs") or [])):
                continue
        txt = _image_text(v)
        if not txt:
            continue
        out.append((h, v, txt))
        if len(out) >= limit:
            break
    return out


def bind_to_answer(kb_id: str, answer: str, source_paths=None, sources=None,
                   min_sim: float = _ABS_FLOOR, max_per_para: int = 3,
                   max_total: int = 8, pos_weight: float = _POS_BOOST,
                   rel_window: float = _REL_WINDOW,
                   judge: bool = True, judge_workers: int = 8):
    """把图片按语义挂到答案的各个段落上。

    返回 [{"para_index": 段落下标, "para": 该段原文, "images":[{hash, path, name, url, summary, sim}]}]

    阈值说明：0.42 是 bge 系列在"中文短句 vs 中文短句"上的经验值，
    低于它多半是无关图。宁可少挂，也别把不相关的图堆上去。
    """
    try:
        import numpy as np
        import embeddings
    except Exception:
        return []

    paras = split_answer(answer)
    if not paras:
        return []
    cands = candidate_images(kb_id, source_paths)
    if not cands:
        return []

    try:
        pvecs = embeddings.embed_texts([p for _, p in paras])
        if pvecs is None:
            return []

        # 图向量优先取索引里预存好的（`--vectors` 跑过之后就有）。
        # 缺的那部分才实时编码 —— 300 张实时编码要 3.5 秒，是主要开销。
        rows = [None] * len(cands)
        need, need_i = [], []
        for i, (_h, v, t) in enumerate(cands):
            cv = v.get("vec")
            if cv:
                rows[i] = np.asarray(cv, dtype=np.float32)
            else:
                need.append(t)
                need_i.append(i)
        if need:
            mv = embeddings.embed_texts(need)
            if mv is None:
                return []
            for k, i in enumerate(need_i):
                rows[i] = np.asarray(mv[k], dtype=np.float32)
        ivecs = np.vstack(rows)
    except Exception as e:
        print(f"[imgindex] 绑定向量化失败: {e}")
        return []

    # ---- 位置加分（只对 pptx 生效）----
    # 命中块里如果引用了某张图（pptx 按页标记可信），给这张图加一点分。
    # 权重故意给小：语义才是主判据，位置只用来在"几张图分数接近"时做区分。
    pos_hits = set()      # {(图片名, 文档路径)}：本次命中的块引用过的图
    if pos_weight > 0 and sources:
        try:
            pidx = _pos_index(kb_id)
            for s in sources:
                p_ = s.get("path") or ""
                pages = pidx.get(p_) or {}
                if not pages:
                    continue
                # 该块文本里出现了哪些图标记
                txt = s.get("text_full") or s.get("text") or ""
                import re as _re
                for n in _re.findall(r"\[([^\]]+\.(?:png|jpe?g|gif|bmp|webp|emf|wmf|tiff?))\]", txt, _re.I):
                    if n in pages:
                        pos_hits.add((n, p_))
        except Exception as e:
            print(f"[imgindex] 位置信号失败: {e}")

    def pos_score(v):
        """0..1：这张图是否被本次命中的 pptx 块引用过。"""
        if not pos_hits:
            return 0.0
        for doc in (v.get("docs") or []):
            if (doc.get("name"), doc.get("path")) in pos_hits:
                return 1.0
        return 0.0

    # 总额度按段落数自适应：固定 8 张的话，段落一多前面的段就把额度吃光，
    # 后面的段明明有高分图也一张挂不上（实测 9 段时后 6 段全空）。
    # 每段最多 2 张，整体封顶 12 —— 再多就变成图片墙，反而看不清重点。
    cap = min(max(max_total, len(paras) * 2), 12)
    per = min(max_per_para, 2)
    # 判定阶段只送分数最高的前 N 张：每张都要上传给模型看图，耗时和数量成正比
    # （实测 10 张约 12-24 秒）。分数低的本来就过不了阈值，没必要送去判。
    judge_cap = min(max(4, len(paras)), 8)

    used = set()
    out = []
    for pi, ((raw, _plain), pv) in enumerate(zip(paras, pvecs)):
        sims = ivecs @ _norm(pv)
        # 位置只在语义接近时打破平局：把命中位置说明语义够接近。
        if pos_weight > 0 and pos_hits:
            hit_mask = np.array([pos_score(c[1]) > 0 for c in cands])
            # 相邻两张图的语义差距 <= _POS_TIE 时，位置命中者提上来
            for i in np.where(hit_mask)[0]:
                # 找比它高但差距在平局窗口内的图，直接对调分数让它排前面
                for j in range(len(sims)):
                    if j == i or hit_mask[j]:
                        continue
                    if 0 < sims[j] - sims[i] <= _POS_TIE:
                        sims[i] = sims[j] + 1e-6
                        break
        order = np.argsort(-sims)
        # 相对阈值：本段最高分是基准，只收"离它不太远"的图。
        # 一刀切的绝对阈值不行 —— 实测同样一条回答里，强段落最高 0.86、
        # 弱段落只有 0.60（「发布物上传」第4段），固定 0.66 会把弱段落整段误杀，
        # 而固定 0.42 又让强段落塞进一堆 0.6x 的噪音图。
        top = float(sims[order[0]]) if len(order) else 0.0
        cut = max(min_sim, top - rel_window)
        picked = []
        for idx in order:
            if len(picked) >= per or len(used) >= cap:
                break
            s = float(sims[idx])
            if s < cut:
                break
            h, v, _t = cands[int(idx)]
            if h in used:
                continue
            used.add(h)
            doc = (v.get("docs") or [{}])[0]
            picked.append({
                "hash": h,
                "path": doc.get("path", ""),
                "name": doc.get("name", ""),
                "summary": v.get("summary", ""),
                "kind": v.get("kind", ""),
                "sim": round(s, 3),
                "pos": pos_score(v) > 0,
            })
        if picked:
            out.append({"para_index": pi, "para": raw, "images": picked})

    # ---- 二次确认：让模型【看图】判断"这张图真能支撑这段吗" ----
    # 向量相似度分不出"同话题但内容不对"。只用文字摘要判也不行，实测反例：
    #   「miniforge路径填 /home/agent」相似度 0.827（最高），但答案写的是
    #   /home/llm/miniforge3 —— 同一件事、值不同，图就是错的。
    # 把**原图**给模型看，它能直接读出输入框里的值，9 条人工标注样本判定 9/9，
    # 而只看文字摘要只有 6/8。代价实测 0.9 秒/张（并发 3）。
    if judge and out:
        # 只把分数最高的前 judge_cap 张送去判定：每张都要上传给模型看图，
        # 耗时和数量成正比。分数低的在阈值阶段就该被筛掉了。
        flat = [(blk["para_index"], im) for blk in out for im in blk["images"]]
        flat.sort(key=lambda x: -x[1]["sim"])
        chosen = flat[:judge_cap]
        pmap = {b["para_index"]: b["para"] for b in out}
        pairs = [(pmap.get(pi, ""), im) for pi, im in chosen]
        verdicts = judge_many(pairs, workers=judge_workers, kb_id=kb_id)

        keep_set = {}
        for (pi, im), ok in zip(chosen, verdicts):
            if ok:
                keep_set.setdefault(pi, []).append(im)
        kept = []
        for blk in out:
            ims = keep_set.get(blk["para_index"])
            if ims:
                ims.sort(key=lambda x: -x["sim"])
                kept.append({"para_index": blk["para_index"], "para": blk["para"],
                             "images": ims})
        print(f"[imgindex] 二次确认：送审 {len(chosen)} 张（候选 {len(flat)}），"
              f"保留 {len(chosen) - sum(0 if v else 1 for v in verdicts)} 张",
              file=sys.stderr)
        out = kept
    return out


# ---------------- 二次确认：让模型判断"这张图能不能支撑这段内容" ----------------
#
# 为什么需要：向量相似度只能判断"话题像不像"，判断不了"内容对不对"。
# 实测反例（都是高分）：
#   「新增Oracle数据库配置页，地址10.188.130.92」0.796 挂在"点哪个菜单"的段落上 —— 段落没讲填参数
#   「miniforge路径填 /home/agent」0.827 挂在"DAIR_OTRADE 目录填 /home/llm/miniforge3"上
#      —— 同一件事，但**具体值不一样，图就是错的**
#   「hix的mq配置界面」0.749 挂在"在 hosts / cron.allow 里加用户"上 —— 完全不同的事
# 所以加一层模型确认，并且**宁可判否**：拿不准就当不相关，宁可不展示。

_JUDGE_SYSTEM = (
    "你在核对知识库截图与答案段落是否对应，用来决定要不要把截图展示给用户。\n"
    "判断标准：\n"
    "1. 图展示的界面/内容，和这段话描述的是**同一件事**吗？前提是图里有足够信息让读者"
    "看懂这段话（比如讲'点添加按钮'，图里有添加按钮和路径，就算对应）。\n"
    "2. 只在下面这些情况判 no：\n"
    "   - 讲的根本是另一件事（例：段落讲 hosts 文件配置，图是 MQ 连接串）；\n"
    "   - **具体值对不上**（例：段落说路径是 /home/llm/miniforge3，图里是 /home/agent）；\n"
    "   - 图与这段话毫无关系。\n"
    "3. 不要因为'图里没写全段落的所有细节'就判 no —— 图本来只是一张截图。\n"
    "4. 如果图中文字显示'（无文字）'，就只看配图说明来判断，说明足够具体就判 yes。\n"
    "只输出 JSON：{\"ok\": true/false, \"why\": \"10字内理由\"}"
)


def _judge_prompt(para: str, im: dict) -> str:
    ocr = (im.get("ocr") or "").strip()
    ocr_snip = ocr[:500] if ocr else "（无文字）"
    return (
        f"【答案段落】\n{para[:600]}\n\n"
        f"【配图说明】\n{im.get('summary', '')}\n"
        f"【图类型】{im.get('kind', '')}\n"
        f"【图中文字】\n{ocr_snip}\n\n"
        "这张图能支撑这段话吗？只输出 JSON。"
    )


_JUDGE_SYSTEM_VISION = (
    "你在核对知识库截图能不能配在答案某段旁边。用户点开图是为了看懂这段话在讲什么。\n"
    "你会直接看到截图。\n\n"
    "**判断顺序（照这个走）**：\n"
    "第一步：这段话讲的是哪个界面、哪个操作？\n"
    "第二步：截图是那个界面/那个操作吗？\n"
    "第三步：如果截图里有和段落**不一样的值**（路径、IP、端口、软件名、版本号、菜单名），"
    "判 no。\n\n"
    "**判 yes**：截图展示的正是这段话描述的那个界面和操作。\n"
    "例：段落「点击【服务器】参数右侧加号添加部署服务器，高可用选两台」"
    "→ 截图「服务器配置页，两台服务器，节点序号主机0备机1」= yes。\n"
    "例：段落「进入计算机资源管理-服务器，点添加」→ 截图「服务器列表页，红框标出+添加」= yes。\n\n"
    "**判 no**：\n"
    "例：段落「进入数据库服务器菜单点添加」→ 截图是「填地址/端口/服务名的配置表单」= no（这是下一步）。\n"
    "例：段落「miniforge 路径填 /home/llm/miniforge3」→ 截图输入框是「/home/agent」= no（值不同）。\n"
    "例：段落「在 hosts 文件加用户」→ 截图是「MQ 连接串」= no（另一件事）。\n"
    "例：段落「应用服务器」→ 截图是「数据库服务器」页面 = no（界面类型不同）。\n"
    "例：段落讲「数据库配置」→ 截图是「RabbitMQ 配置界面」= no（讲的是数据库，图是消息队列）。\n"
    "例：段落讲「数据库类型选 Oracle」→ 截图是「ZooKeeper 集群配置」= no（软件都不同）。\n\n"
    "**特别提醒**：这段讲的是**哪个东西**（数据库？服务器？发布物？MQ？ZK？），"
    "截图就必须是**那个东西**的界面。只是\"都属于部署配置\"不算对应。\n\n"
    "**不要因为以下原因判 no**：截图里没出现段落提到的某个词；截图比段落信息少；"
    "截图里有段落没提的额外参数。这些都不算错。\n"
    "只输出 JSON：{\"ok\": true/false, \"why\": \"10字内理由\"}"
)


def judge_image(para: str, im: dict, kb_id: str = "", timeout: int = 90) -> bool:
    """让模型**看图**判断这张图能否支撑这段答案。

    为什么不只看摘要：摘要文字判不出"同一件事但具体值不同"。
    实测「miniforge路径填 /home/agent」的摘要与「路径填 /home/llm/miniforge3」
    语义分高达 0.827，只看文字模型也容易含糊；看原图能直接读出输入框里的值。

    任何异常都返回 False —— 宁缺勿滥。
    """
    try:
        import llm
        if not llm.llm_enabled():
            return False
        # 取原图，压成 JPEG 再给模型
        b64 = ""
        doc = (im.get("docs") or [{}])[0]
        try:
            import imgserve
            raw, _ext = imgserve.extract(kb_id, doc.get("path", ""), doc.get("name", ""))
            if raw:
                b64 = _to_jpeg_b64(raw)
        except Exception as e:
            print(f"[imgindex] 判定取图失败: {type(e).__name__}", file=sys.stderr)

        parts = []
        if b64:
            parts.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": b64}})
        parts.append({"type": "text", "text": _judge_prompt(para, im)})

        out = llm.chat([{"role": "user", "content": parts}],
                       system=_JUDGE_SYSTEM_VISION, timeout=timeout) or ""
        import re as _re
        m = _re.search(r"\{.*\}", out, _re.S)
        if not m:
            return False
        d = json.loads(m.group(0))
        return bool(d.get("ok")) is True
    except Exception as e:
        print(f"[imgindex] 二次确认失败（按不相关处理）: {type(e).__name__} {str(e)[:80]}",
              file=sys.stderr)
        return False


def judge_many(pairs, workers: int = 6, timeout: int = 90, kb_id: str = ""):
    """并发确认一堆 (段落, 图) 组合。返回与输入等长的 bool 列表。

    并发数别太高：每张图都要上传，太多会把网关压住。
    """
    from concurrent.futures import ThreadPoolExecutor
    if not pairs:
        return []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(judge_image, p, im, kb_id, timeout) for p, im in pairs]
        return [f.result() for f in futs]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="图片索引：OCR + 多模态摘要")
    ap.add_argument("kb_id")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（试跑）")
    ap.add_argument("--workers", type=int, default=6,
                    help="并发数（默认6；越大越快但越占网关，用户提问会变慢）")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--no-summary", action="store_true")
    ap.add_argument("--stat", action="store_true", help="只看统计")
    ap.add_argument("--vectors", action="store_true",
                    help="预计算图片摘要向量（每问省 3 秒，索引更新后要重跑）")
    ap.add_argument("--bind", metavar="答案文本", help="测试意思绑定：给一段答案，看会挂上哪些图")
    a = ap.parse_args()

    if a.stat:
        print(json.dumps(stats(a.kb_id), ensure_ascii=False, indent=2))
        sys.exit(0)

    if a.vectors:
        print(json.dumps(build_vectors(a.kb_id), ensure_ascii=False))
        sys.exit(0)

    if a.bind:
        r = bind_to_answer(a.kb_id, a.bind)
        if not r:
            print("没有匹配到图（或索引还没建好）")
        for blk in r:
            print(f"\n--- 段落 {blk['para_index']}: {blk['para'][:60]}")
            for im in blk["images"]:
                print(f"    sim={im['sim']:.3f}  [{im['kind']}] {im['summary'][:60]}")
                print(f"               {im['path'][-40:]} / {im['name']}")
        sys.exit(0)

    r = build(a.kb_id, limit=a.limit, workers=a.workers,
              do_ocr=not a.no_ocr, do_summary=not a.no_summary)
    print("\n完成：", json.dumps(r, ensure_ascii=False))
    print("现状：", json.dumps(stats(a.kb_id), ensure_ascii=False))
