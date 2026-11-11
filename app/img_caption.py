# -*- coding: utf-8 -*-
"""图片理解: 把文档里的图变成可检索的文字。

为什么需要:
    安装手册里大量是截图 —— 界面路径、参数值、命令输出、架构图。
    这些信息**只在图里**。原来的抽取器只取文字层, 2593 张图的内容等于不存在。

做法:
    每张图交给视觉模型, 生成 1~3 句描述(含识别到的关键文字), 作为正文的一段。
    这样:
      - 向量/BM25 能检索到"图里说了什么"
      - 引用仍指向原文档, 用户点开能看到原图

成本控制:
    - 按内容 md5 缓存, 同一张图跨文档只调一次 (多版本手册重复图很多)
    - 跳过过小的图(图标/装饰)
    - 可并发, 可中断续跑
"""
import base64, hashlib, io, json, os, re, sys, threading, time
from pathlib import Path

APP = Path(__file__).parent
_LOCK = threading.Lock()
_CACHE_DIR = None
MIN_SIDE = 120          # 小于这个边长的图视为图标, 跳过
MAX_SIDE = 1600         # 太大的图先缩小, 省 token
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".emf", ".wmf"}

CAPTION_PROMPT = (
    "这是一份交付/实施文档里的插图。请用中文简要说明这张图在讲什么，"
    "并原样抄出图中可见的关键文字（模块名、菜单路径、参数名与取值、命令、报错、流程步骤）。"
    "要求：\n"
    "1. 只描述图中确实存在的内容，不要推测或补充背景知识；\n"
    "2. 如果是界面截图，写出页面标题和关键字段；\n"
    "3. 如果是架构图/流程图，写出节点与连线关系；\n"
    "4. 总长不超过 200 字，用陈述句，不要用'这张图'开头。\n"
    "如果图中没有任何可读信息（纯装饰），只回答：无信息。"
)


def _data_dir() -> Path:
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = APP / "data"
    if not d.is_absolute():
        alt = APP.parent / d
        d = alt if alt.exists() else APP / d
    return d


def cache_dir() -> Path:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = _data_dir() / "_imgcap"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def enabled() -> bool:
    try:
        import llm
        c = llm.load_config()
        v = c.get("index", {}).get("image_caption", True)
        if isinstance(v, str):
            return v.lower() not in ("0", "false", "no", "off")
        return bool(v)
    except Exception:
        return True


# ---------------- 图片预处理 ----------------

def _prep(raw: bytes):
    """解码 → 转 RGB → 限尺寸 → 返回 (png_bytes, key) 或 None(跳过)。

    key 用**原始字节**的 md5: 这样以后调整缩放/编码参数, 已生成的缓存仍然有效。
    (曾经用重编码后的字节做 key, 改一次参数缓存全废)
    """
    try:
        from PIL import Image
        key = hashlib.md5(raw).hexdigest()
        im = Image.open(io.BytesIO(raw))
        im.load()
        w, h = im.size
        if w < MIN_SIDE or h < MIN_SIDE:
            return None
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(w, h) > MAX_SIDE:
            r = MAX_SIDE / max(w, h)
            im = im.resize((max(1, int(w * r)), max(1, int(h * r))))
        buf = io.BytesIO()
        # optimize=True 会慢 3~5 倍, 对识图没有收益
        im.save(buf, format="PNG", compress_level=1)
        return buf.getvalue(), key
    except Exception:
        return None


# ---------------- 调视觉模型 ----------------

def _vision_call(png: bytes, cfg=None) -> str:
    """把一张图交给视觉模型, 返回描述文字。兼容 anthropic / openai 两种图片格式。"""
    import httpx
    import llm

    s = llm.llm_settings(cfg)
    if not s.get("base_url"):
        return ""
    b64 = base64.b64encode(png).decode()
    tmo = httpx.Timeout(connect=llm.connect_timeout(), read=25.0,
                        write=30.0, pool=10.0)

    if s.get("protocol", "openai") == "anthropic":
        url = s["base_url"].rstrip("/") + "/v1/messages"
        headers = {"x-api-key": s.get("api_key", ""),
                   "anthropic-version": s.get("anthropic_version", "2023-06-01"),
                   "content-type": "application/json"}
        payload = {
            "model": s.get("model"),
            "max_tokens": 1200,
            # 关掉思考链: 识图不需要推理, 开着会先吐几百字 thinking, 慢一倍多
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png", "data": b64}},
                {"type": "text", "text": CAPTION_PROMPT},
            ]}],
        }
    else:
        url = s["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {s.get('api_key','')}",
                   "content-type": "application/json"}
        payload = {
            "model": s.get("model"), "max_tokens": 400,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": CAPTION_PROMPT},
            ]}],
        }

    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=tmo)
        if r.status_code == 400 and "chat_template_kwargs" in payload:
            payload.pop("chat_template_kwargs", None)      # 老网关不认这个参数
            r = httpx.post(url, headers=headers, json=payload, timeout=tmo)
        if r.status_code != 200:
            print(f"[img_caption] HTTP {r.status_code}: {r.text[:160]}", file=sys.stderr)
            return ""
        d = r.json()
        if s.get("protocol", "openai") == "anthropic":
            parts = d.get("content") or []
            return "".join(p.get("text", "") for p in parts
                           if isinstance(p, dict) and p.get("type") == "text").strip()
        return (d.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"[img_caption] 调用失败: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


def caption(png: bytes, key: str, cfg=None, use_cache=True) -> str:
    """带缓存的描述。缓存按图片内容 md5, 跨文档复用。"""
    cp = cache_dir() / f"{key}.json"
    if use_cache and cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8")).get("desc", "")
        except Exception:
            pass
    desc = _vision_call(png, cfg)
    desc = re.sub(r"\s+\n", "\n", (desc or "").strip())
    if not desc or desc in ("无信息", "无信息。"):
        desc = ""
    try:
        cp.write_text(json.dumps({"desc": desc, "ts": int(time.time())},
                                 ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return desc


# ---------------- 从各类文档里抽图 ----------------

def images_from_office(p: Path):
    """docx/pptx/xlsx: 按 zip 内 media 顺序返回 [(name, bytes)]。"""
    import zipfile
    out = []
    try:
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist()
                     if "/media/" in n and Path(n).suffix.lower() in IMG_EXT]
            names.sort()
            for n in names:
                try:
                    out.append((n, z.read(n)))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def images_from_pdf(p: Path):
    """pdf: 逐页取图, 返回 [(page_label, bytes)]。"""
    try:
        import fitz
    except Exception:
        return []
    out = []
    try:
        d = fitz.open(str(p))
        for i, page in enumerate(d, 1):
            for j, info in enumerate(page.get_images(full=True), 1):
                try:
                    xref = info[0]
                    pix = fitz.Pixmap(d, xref)
                    if pix.n - pix.alpha >= 4:      # CMYK 转 RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    out.append((f"第{i}页图{j}", pix.tobytes("png")))
                except Exception:
                    continue
        d.close()
    except Exception:
        pass
    return out


def describe_images(items, cfg=None, workers=4, progress=None) -> list:
    """并发把 [(label, png, key)] 变成 [(label, desc)]。
    png/key 必须来自 _prep() —— 调用方先做好, 这里不再二次预处理
    (否则重新编码后 md5 会变, 缓存对不上, 等于白跑)。"""
    results = [None] * len(items)

    def work(idx):
        label, png, key = items[idx]
        results[idx] = (label, caption(png, key, cfg))

    todo = list(range(len(items)))
    lock = threading.Lock()

    def runner():
        while True:
            with lock:
                if not todo:
                    return
                i = todo.pop(0)
            try:
                work(i)
            except Exception:
                results[i] = (items[i][0], "")
            if progress:
                try:
                    progress()
                except Exception:
                    pass

    ths = [threading.Thread(target=runner, daemon=True) for _ in range(max(1, workers))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return [(lbl, (r or "")) for lbl, r in zip([i[0] for i in items], results)]


def stats():
    d = cache_dir()
    files = list(d.glob("*.json")) if d.exists() else []
    ok = 0
    for f in files:
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("desc"):
                ok += 1
        except Exception:
            pass
    return {"cached": len(files), "with_text": ok}

# ---------------- 文档级结果(索引时直接读, 不解码图片) ----------------

def doc_text_path(p) -> Path:
    """每个文档的"图片描述汇总文本"落在哪。用路径 md5 做键, 路径不变就稳定。"""
    import hashlib
    d = cache_dir() / "text"
    d.mkdir(parents=True, exist_ok=True)
    k = hashlib.md5(str(p).encode("utf-8")).hexdigest()
    return d / f"{k}.txt"


def save_doc_text(p, blocks) -> None:
    """blocks: [(label, desc)], 写成 [插图 label] desc 的形式。"""
    lines = []
    for label, desc in blocks:
        desc = (desc or "").strip()
        if not desc:
            continue
        lines.append(f"[插图 {label}] {desc}")
    try:
        doc_text_path(p).write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def load_doc_text(p) -> str:
    """索引时调用: 只读文件, 毫秒级。没有就返回空。"""
    try:
        if not enabled():
            return ""
        fp = doc_text_path(p)
        if not fp.exists():
            return ""
        return fp.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
