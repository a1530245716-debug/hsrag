# -*- coding: utf-8 -*-
"""按知识库加载 BM25 检索。query.py 的通用化:可指定任意 chunks.jsonl 路径。

检索质量通用机制(建库/查询均受益):
  1. 变体归一: O-Trade/OTRADE/o_trade/HIX 等写法在分词前归一为规范形式,
     避免"同义词因写法不同而检索不到"。规则可在 config.json retrieval.normalize 配置。
  2. 文档类型加权: 手册/部署/运维/实施/操作说明类文档加权, 纯测试报告/验收类降权,
     缓解 Excel 表格等短块"得分虚高、把正文手册挤出 top-k"的问题。
     规则可在 config.json retrieval.path_boost 配置。
"""
import json, math, os, re, threading, difflib
from pathlib import Path
from collections import Counter

try:
    import jieba
    jieba.initialize()
except Exception:
    jieba = None

# ---------------- 归一化 / 加权配置 ----------------

DEFAULT_NORMALIZE = [
    # 大小写已在 tokenize 中统一小写; 此处处理连字符/下划线/空格变体
    (re.compile(r"\bo[\s\-_]*trade\b", re.I), "demo"),
    (re.compile(r"\bo[\s\-_]*order\b", re.I), "oorder"),
    (re.compile(r"\bh[\s\-_]*i[\s\-_]*x\b", re.I), "hix"),
]

DEFAULT_AUTHORITY = [
    # 权威文档类: 命中即提升权重(定义/手册/说明书/部署/运维/实施/概述)
    (("手册", "说明书", "部署", "运维", "实施", "操作指南", "应用手册", "系统概述", "用户操作"), 1.8),
]
DEFAULT_SUPPRESS = [
    # 噪声文档类: 未命中权威类时,命中则降权(测试/验收/周月报/计划/清单/台账等短块)
    (("测试报告", "性能测试", "验收", "回执", "统计表", "周报", "月报", "日报",
      "例会", "计划", "清单", "台账", "排期", "签到", "进度", "会议", "日志查询"), 0.4),
]


def _load_retrieval_cfg():
    """读取 retrieval 段的归一化与加权配置(默认值兜底)。"""
    import llm
    cfg = llm.load_config()
    rc = cfg.get("retrieval", {})
    normalize = []
    for pat, rep in rc.get("normalize", []):
        try:
            normalize.append((re.compile(pat, re.I), rep))
        except Exception:
            pass
    if not normalize:
        normalize = DEFAULT_NORMALIZE
    authority = []
    for match_list, weight in rc.get("authority", []):
        authority.append((tuple(match_list), float(weight)))
    if not authority:
        authority = DEFAULT_AUTHORITY
    suppress = []
    for match_list, weight in rc.get("suppress", []):
        suppress.append((tuple(match_list), float(weight)))
    if not suppress:
        suppress = DEFAULT_SUPPRESS
    return {"normalize": normalize, "authority": authority, "suppress": suppress}


_RC = None        # retrieval 配置缓存


def _rc():
    global _RC
    if _RC is None:
        _RC = _load_retrieval_cfg()
    return _RC


def tokenize(text):
    t = text.lower()
    for pat, rep in _rc()["normalize"]:
        t = pat.sub(rep, t)
    if jieba:
        return [w for w in jieba.cut(t) if len(w.strip()) > 0 and not w.isspace()]
    return [c for c in t if c.strip()]


def _path_weights(path):
    """返回 (权威加成, 降权因子) 二元组。

    只按「文件名」判断(目录路径如"实施交付/周报"不参与,避免误伤):
      权威类(手册/部署/运维/概述/操作说明) → 加成;
      未命中权威类时,噪声类(测试/周报/清单等短块) → 降权。

    降权**单独返回**而不是直接乘进去，因为"清单类文档降权"只适用于
    用户随口提到清单的时候；一旦他明确在问清单/台账/配置表，那份清单
    恰恰就是他要的东西，再压制就等于答"没找到"。
    """
    if not path:
        return 1.0, 1.0
    fname = path.replace("\\", "/").split("/")[-1].lower()
    rc = _rc()
    w = 1.0
    hit_authority = False
    for match_list, weight in rc["authority"]:
        if any(k in fname for k in match_list):
            w *= weight
            hit_authority = True
    suppress = 1.0
    if not hit_authority:
        for match_list, weight in rc["suppress"]:
            if any(k in fname for k in match_list):
                suppress = weight
                break
    return w, suppress


def path_boost(path):
    """按来源文档类型返回权威权重(乘在 BM25 得分上)。"""
    return _path_weights(path)[0]


# 问题里出现这些词，说明用户要找的就是"清单/台账/配置表"本身，
# 此时不能再按噪声文档压制它们
_LIST_ASK = ("清单", "台账", "配置表", "列表", "明细", "表格", "一览表", "对照表")


def _list_exempt(q):
    return any(k in (q or "") for k in _LIST_ASK)


# ---------------- 文档版本优先级 ----------------
# 同一个文档常有多份"只差版本"的副本（V2023 / V2026 / V1.6 / 带日期 / 带(1)）。
# 旧版本里的参数往往已经被新版本替换掉，直接等价参与排序会把过期答案顶上来。
# 做法：把"同一族"的文档按修改时间排序，最新的一份正常计分，旧版本逐级降权；
# 旧版仍保留一定分值，所以**最新版里没有的内容，依然能从旧版检索到**。

_VERSION_PAT = re.compile(
    r"(?:[vV]\s*\d+(?:\.\d+)*)"          # V2023 / v1.6 / V2
    r"|\d{4}[-_./年]?\d{1,2}[-_./月]?\d{1,2}"   # 20260410 / 2026-04-10
    r"|\d{6,8}"                           # 20260410
    r"|[（(]\s*\d+\s*[)）]"               # (1) (2)
    r"|第\s*\d+\s*版|终版|最终版|修订版|草案|old|new|备份|copy"
)

# 同族内按"新→旧"的排名取权重。
# 这里刻意只做"轻度倾向"而不是强压制：用这套系统的是刚接手项目的实施人员，
# 他们问"项目进度/以前怎么弄的"时，旧周报、旧文档同样有参考价值，
# 压太狠会导致"明明库里有、就是查不到"。新版优先靠 1.0 / 0.82 / 0.68 的差距体现。
_VERSION_WEIGHTS = (1.0, 0.82, 0.68)

# 核心名相似度阈值：超过就认为是同一份文档的不同版本
_VERSION_SIM = 0.92

# 一个"版本族"最多允许这么多成员，超过基本是误判（把不相干的文档凑一起了）
_VERSION_MAX_FAMILY = 15

# 带这些标记的文件才参与版本排序；没有版本标记的一律视为独立文档
_VERSION_MARK = re.compile(
    r"[vV]\s*\d+(?:\.\d+)*"              # V2023 / v1.6
    r"|20\d{2}[-_./年]?\d{1,2}[-_./月]?\d{1,2}"   # 2026-04-10 / 20260410
    r"|20\d{6}"                           # 20260410
    r"|第\s*\d+\s*版|终版|最终版|修订版"
)


def _has_version_mark(name):
    return bool(_VERSION_MARK.search(name))


def _core_name(path):
    """把文件名里与版本相关的内容抹掉，留下"这是哪份文档"的特征。"""
    name = os.path.splitext(os.path.basename(path))[0]
    name = _VERSION_PAT.sub("", name)
    name = re.sub(r"[\s_\-—－()（）【】\[\].,，。]+", "", name)
    return name.lower()


def _ver_num(name):
    """从文件名里提取可比较的版本号。

    纯上传型知识库(没有绑定源目录)拿不到文件修改时间，只能靠名字判断新旧：
      - V2026 / V2023 / v1.6  → 2026.0 / 2023.0 / 1.06
      - 20260410              → 20260410.0
    取不到就返回 0，同族里排在最后（视为最旧）。
    """
    m = re.search(r"[vV]\s*(\d+(?:\.\d+)*)", name)
    if m:
        try:
            parts = m.group(1).split(".")
            v = float(parts[0])
            if len(parts) > 1:
                v += float("0." + parts[1])
            return v
        except (ValueError, IndexError):
            return 0.0
    m = re.search(r"(20\d{6})", name)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _doc_sort_key(p, folder):
    """排序用：(文件修改时间, 文件名版本号)。有 mtime 用 mtime，没有就靠版本号。"""
    mt = 0.0
    if folder:
        try:
            mt = os.path.getmtime(os.path.join(folder, p.replace("/", os.sep)))
        except OSError:
            mt = 0.0
    return (mt, _ver_num(os.path.basename(p)))


def _version_boosts(paths, folder):
    """返回 {path: 版本权重}。单版本文档一律 1.0，不受影响。

    同族判定：核心名完全相同，或相似度超过 _VERSION_SIM。
    族内按 (修改时间, 版本号) 降序排，排名决定权重。
    """
    if not paths or not folder:
        return {}
    groups = {}
    for p in paths:
        groups.setdefault(_core_name(p), []).append(p)

    # 核心名相近的合并成一族（只处理多成员组，单文件不参与，成本可控）
    keys = [k for k in groups if k]
    merged = {}          # 核心名 -> 归属的代表名
    for i, a in enumerate(keys):
        if a in merged:
            continue
        merged[a] = a
        for b in keys[i + 1:]:
            if b in merged:
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() >= _VERSION_SIM:
                merged[b] = a
                groups[a].extend(groups.pop(b))

    out = {}
    for rep, members in groups.items():
        if len(members) < 2:
            out[members[0]] = 1.0
            continue
        ranked = sorted(members, key=lambda p: _doc_sort_key(p, folder), reverse=True)
        for rank, p in enumerate(ranked):
            out[p] = _VERSION_WEIGHTS[rank] if rank < len(_VERSION_WEIGHTS) else _VERSION_WEIGHTS[-1]
    return out


class BM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = []        # (id, path, chunk, text)
        self.tf_list = []     # 预计算的每块词频,避免每次 query 重建 Counter
        self.df = Counter()
        self.lens = []
        self.boosts = []      # 每块的权威权重(乘在得分上)
        self.suppress = []    # 每块的降权因子(查询时按问题内容决定是否生效)
        self.path_toks = []   # 每块所属文件名的分词(用于"文件名命中"加权)
        self.N = 0
        self.avgdl = 0.0

    def add(self, doc_id, path, chunk, text, extra_boost=1.0):
        toks = tokenize(text)
        tf = Counter(toks)
        self.docs.append((doc_id, path, chunk, text))
        self.tf_list.append(tf)
        self.lens.append(len(toks))
        boost, sup = _path_weights(path)
        self.boosts.append(boost * extra_boost)   # 权威权重 × 版本权重
        self.suppress.append(sup)
        # 同一文件有很多块，文件名分词结果缓存起来，别每块重算
        pt = _PATH_TOK_CACHE.get(path)
        if pt is None:
            pt = set(tokenize(path.replace("/", " ").replace("\\", " ")))
            _PATH_TOK_CACHE[path] = pt
        self.path_toks.append(pt)
        for w in tf:
            self.df[w] += 1

    def build(self):
        self.N = len(self.tf_list)
        if self.N:
            self.avgdl = sum(self.lens) / self.N

    def query(self, q, topk=8):
        qcnt = Counter(tokenize(q))
        if self.N == 0:
            return []
        b, k1, N, avgdl = self.b, self.k1, self.N, self.avgdl
        df = self.df
        scores = []
        tfs = self.tf_list
        lens = self.lens
        boosts = self.boosts
        sup = self.suppress
        ptx = self.path_toks
        # 用户明确在问"清单/台账/配置表"时，不再压制这类文档：
        # 他要的就是那份清单，压制等于答"没找到"
        exempt = _list_exempt(q)
        # 文件名逐字命中（见 _path_phrase_hits 上方说明）
        name_hit = _path_phrase_hits(q, {d[1] for d in self.docs})
        for i in range(N):
            tf = tfs[i]
            s = 0.0
            dl = lens[i]
            for w, qw in qcnt.items():
                f = tf.get(w, 0)
                if not f:
                    continue
                idf = math.log((N - df[w] + 0.5) / (df[w] + 0.5) + 1)
                denom = f + k1 * (1 - b + b * dl / avgdl)
                s += idf * (f * (k1 + 1)) / (denom or 1)
            if s:
                w = boosts[i]
                if not exempt:
                    w *= sup[i]
                # 文件名命中加权
                pt = ptx[i]
                if pt:
                    ov = qcnt.keys() & pt
                    if ov:
                        ratio = len(ov) / len(qcnt)
                        if ratio >= _PATH_HIT_MIN:
                            w *= 1.0 + (_PATH_HIT_GAIN - 1.0) * ratio
                # 文件名逐字命中（"环境信息"直接出现在文件名里）
                if name_hit:
                    hit = name_hit.get(self.docs[i][1])
                    if hit:
                        w *= min(3.0, 1.0 + 0.4 * (hit[0] - _NAME_PHRASE_MIN + 1)
                                 + 0.1 * min(hit[1], 5))
                scores.append((s * w, i))
        scores.sort(key=lambda x: -x[0])
        out = []
        for score, i in scores[:topk]:
            if score <= 0:
                break
            id_, path, chunk, text = self.docs[i]
            out.append({"score": round(score, 3), "id": id_, "path": path,
                        "chunk": chunk, "text": text})
        return out

    def name_hits(self, q, max_docs=2, per_doc=2):
        """文件名逐字命中问题短语的文档，单独摘出它最相关的几块。

        与 query() 的分工：query() 只是"加权后一起排序"，长问题里这些文档仍可能被
        更强的文档挤到 15 名开外；这里把它们单独拎出来，交给上层保留固定引用位，
        避免"库里明明有、答案却说没找到"。
        """
        hits = _path_phrase_hits(q, {d[1] for d in self.docs})
        if not hits:
            return []
        qtoks = [t for t in set(tokenize(q)) if t]
        out = []
        ranked = sorted(hits.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
        for path, _hit in ranked[:max_docs]:
            scored = []
            for i in range(self.N):
                if self.docs[i][1] != path:
                    continue
                text = self.docs[i][3]
                cnt = sum(text.count(t) for t in qtoks)
                # 《XXX环境信息.txt》这种文件，值钱的是带 IP / 账号密码的那几段，
                # 而不是恰好重复了问题里几个词的说明段，所以先按"有没有实料"排
                solid = (1 if _IP.search(text) else 0) + \
                        (1 if ("账号" in text or "密码" in text or "用户名" in text) else 0)
                scored.append((solid, cnt, -len(text), i))
            scored.sort(reverse=True)
            for solid, cnt, _, i in scored[:per_doc]:
                if not solid and cnt <= 0:
                    continue
                id_, p, ch, text = self.docs[i]
                # 分数只用于排序占位，不参与 BM25 比较
                out.append({"score": float(cnt) * 10.0, "id": id_, "path": p,
                            "chunk": ch, "text": text})
        return out


_PATH_TOK_CACHE = {}   # 文件名 -> 分词集合(建索引时复用)

# 文件名命中加权：问题里的词有多大比例出现在文件名里
# 用户问"上线投产配置表清单有哪些内容"，恰好有份文件就叫《上线投产配置表清单》，
# 这是极强的相关性信号，但正文词频统计是看不出来的（表格行里并不重复"清单"二字）
_PATH_HIT_MIN = 0.5      # 至少一半的查询词命中文件名才加权
_PATH_HIT_GAIN = 4.0     # 命中比例最大时的加权倍数（文件名几乎一致时）

# ---- 文件名"逐字命中"加权（补上面那个比例门槛够不着的情况）----
# 长问题里"环境信息""配置表清单"这类关键短语会被大量通用词稀释，
# "一半查询词命中文件名"永远不可能成立。实际踩到的：问
# 「客户的环境信息是什么，我需要知道测试环境和生产环境，o32，demo，oorder的环境信息」
# 而库里就有《测试环境信息.txt》（IP/账号密码全在里面），却一条都召不回来。
# 这里改成按**字面最长命中短语长度**单独加权，4 字起步，避开"环境"这种泛词。
_NAME_PHRASE_MIN = 4
_NAME_PHRASE_MAXLEN = 14
_BASENORM_CACHE = {}
_PHRASE_CACHE = {}
_CJK = re.compile(r"[\u4e00-\u9fff]")
_IP = re.compile(r"\b(?:10|11|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


def _basenorm(path):
    """文件名去扩展名、去分隔符、小写，用于逐字比对。"""
    b = _BASENORM_CACHE.get(path)
    if b is None:
        name = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        b = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", stem.lower())
        _BASENORM_CACHE[path] = b
    return b


def _q_phrases(q, min_len=_NAME_PHRASE_MIN, max_len=_NAME_PHRASE_MAXLEN):
    """把问题切成 4~14 字的连续短语（长的一边先产出，命中长短语优先）。"""
    key = (q, min_len, max_len)
    hit = _PHRASE_CACHE.get(key)
    if hit is not None:
        return hit
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (q or "").lower())
    out, seen = [], set()
    L = len(s)
    for n in range(min(max_len, L), min_len - 1, -1):
        for i in range(0, L - n + 1):
            p = s[i:i + n]
            if p not in seen:
                seen.add(p)
                out.append(p)
    if len(_PHRASE_CACHE) > 64:
        _PHRASE_CACHE.clear()
    _PHRASE_CACHE[key] = out
    return out


def _path_phrase_hits(q, paths):
    """{path: 命中短语的最大长度}，只保留 ≥4 字的命中。"""
    phrases = _q_phrases(q)
    if not phrases:
        return {}
    out = {}
    for p in paths:
        bn = _basenorm(p)
        if not bn:
            continue
        best, n = 0, 0
        for ph in phrases:
            # 必须含中文：逗号去掉后会有 "demooorder" 这种跨词拼接，
            # 它会在《demo&oorder&hix系统日常检查.docx》上误命中
            if not _CJK.search(ph):
                continue
            if ph in bn:
                n += 1
                if len(ph) > best:
                    best = len(ph)
                if best >= _NAME_PHRASE_MAXLEN:
                    break
        if best >= _NAME_PHRASE_MIN:
            # (最长命中短语长度, 命中的不同短语数) —— 后者用来区分
            # "碰巧含'生产环境'" 和 "真的就叫'测试环境信息'"
            out[p] = (best, n)
    return out


_CACHE = {}      # kb_id -> {"mtime": float, "bm": BM25}
_LOADING = {}    # kb_id -> 正在后台加载的目标 mtime
_CACHE_LOCK = threading.Lock()
# 每个库一把建索引锁：启动预热和用户首次提问同时到达时，只建一次，后者等结果
_BUILD_LOCKS = {}
_BUILD_LOCKS_LOCK = threading.Lock()


def _build_lock(kb_id):
    with _BUILD_LOCKS_LOCK:
        return _BUILD_LOCKS.setdefault(kb_id, threading.Lock())


def invalidate(kb_id):
    with _CACHE_LOCK:
        _CACHE.pop(kb_id, None)


def _build_bm(chunk_path, folder=None):
    # 第一遍只收 path，用来算版本权重（多版本同族按修改时间降权）
    paths = set()
    with open(chunk_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    paths.add(json.loads(line)["path"])
                except Exception:
                    continue
    vboost = _version_boosts(paths, folder)

    ca = BM25()
    with open(chunk_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            ca.add(r["id"], r["path"], r["chunk"], r["text"],
                   extra_boost=vboost.get(r["path"], 1.0))
    ca.build()
    return ca


def _reload_in_background(kb_id, chunk_path, mtime, folder=None):
    """后台重建 BM25, 完成后原子替换缓存。

    索引刚重建完时, 查询立刻返回**旧索引**结果(毫秒级), 同时后台加载新索引;
    加载完成后下一次查询自动用新的。用户提问不会被 1~2 分钟的加载卡住。
    """
    with _CACHE_LOCK:
        if _LOADING.get(kb_id) == mtime:
            return                      # 同一版本已在加载, 不重复开线程
        _LOADING[kb_id] = mtime

    def run():
        try:
            ca = _build_bm(chunk_path, folder)
            with _CACHE_LOCK:
                cur = _CACHE.get(kb_id)
                if cur is None or cur["mtime"] <= mtime:      # 防止旧版本覆盖新版本
                    _CACHE[kb_id] = {"mtime": mtime, "bm": ca}
                _LOADING.pop(kb_id, None)
        except Exception as e:
            print(f"[kb] 后台加载索引失败 {kb_id}: {e}")
            with _CACHE_LOCK:
                _LOADING.pop(kb_id, None)

    threading.Thread(target=run, daemon=True, name=f"bm-reload-{kb_id}").start()


def load_kb_indexes(kb_id):
    """按知识库取 BM25(内存缓存)。

    策略:
      - 缓存版本与文件一致     → 直接返回(最快路径)
      - 有旧缓存但文件已更新   → **立即返回旧缓存**, 后台线程加载新版本(不阻塞提问)
      - 完全没有缓存(首次/刚启动) → 只能同步加载
    """
    import indexer
    kb = indexer.get_kb(kb_id)
    if not kb:
        return None
    chunk_path = indexer._kb_dir(kb_id) / "chunks.jsonl"
    if not chunk_path.exists():
        return None
    try:
        mtime = chunk_path.stat().st_mtime
    except OSError:
        return None

    with _CACHE_LOCK:
        hit = _CACHE.get(kb_id)
    folder = kb.get("folder")
    if hit and hit["mtime"] == mtime:
        return hit["bm"]
    if hit:
        _reload_in_background(kb_id, chunk_path, mtime, folder)   # 旧索引先顶着用
        return hit["bm"]

    # 首次/刚启动：同一库只允许一个线程真正构建，预加载和用户提问共享结果
    with _build_lock(kb_id):
        with _CACHE_LOCK:
            hit = _CACHE.get(kb_id)
        if hit and hit["mtime"] == mtime:
            return hit["bm"]
        ca = _build_bm(chunk_path, folder)
        with _CACHE_LOCK:
            _CACHE[kb_id] = {"mtime": mtime, "bm": ca}
        return ca


def search(kb_id, q, topk=8):
    ca = load_kb_indexes(kb_id)
    if ca is None or ca.N == 0:
        return None
    return ca.query(q, topk=topk)


def state(kb_id):
    """索引是否落后于磁盘。

    "stale" 表示 chunks.jsonl 已经变了，但内存里还是旧索引（后台正在重载，
    5.6 万块要 100 秒左右）。这时候提问会**用旧数据回答**——用户刚加完笔记就问，
    就会得到"库里明明有却搜不到"的错觉，所以要把这个状态传出去。
    """
    try:
        import indexer
        cp = indexer._kb_dir(kb_id) / "chunks.jsonl"
        mtime = cp.stat().st_mtime
    except Exception:
        return {"stale": False, "loading": False}
    with _CACHE_LOCK:
        hit = _CACHE.get(kb_id)
        loading = kb_id in _LOADING
    return {"stale": bool(hit and hit["mtime"] != mtime), "loading": loading}


def name_hits(kb_id, q, max_docs=2, per_doc=2):
    """文件名逐字命中问题短语的文档里最相关的几块（见 BM25.name_hits）。"""
    ca = load_kb_indexes(kb_id)
    if ca is None or ca.N == 0:
        return []
    return ca.name_hits(q, max_docs=max_docs, per_doc=per_doc)


# ---------------- 混合检索(BM25 + 向量 + 可选 Rerank) ----------------

_ROW_CACHE = {}   # kb_id -> {"mtime": float, "rows": {idx: rec}}


def _row_cache(kb_id):
    """chunks.jsonl 行索引缓存(与向量库行序对应), mtime 变则重建。"""
    import indexer
    chunk_path = indexer._kb_dir(kb_id) / "chunks.jsonl"
    mtime = chunk_path.stat().st_mtime
    hit = _ROW_CACHE.get(kb_id)
    if hit and hit["mtime"] == mtime:
        return hit["rows"]
    rows = {}
    with open(chunk_path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            r = json.loads(line)
            rows[i] = {"id": r["id"], "path": r["path"], "chunk": r["chunk"], "text": r["text"]}
    _ROW_CACHE[kb_id] = {"mtime": mtime, "rows": rows}
    return rows


def chunk_at(kb_id, idx):
    """按行序取第 idx 块(与向量库行序一致)。"""
    return _row_cache(kb_id).get(idx)


def hybrid_search(kb_id, q, topk=8, enable_vector=True, enable_rerank=True):
    """BM25 召回 + 向量召回融合 → 可选 Rerank 精排 → topk。

    返回:
      - None: 知识库无索引(BM25 都无效)
      - [ {score, rerank_score?, id, path, chunk, text}, ... ]: 正常
    向量/重排模块缺失或失败时自动退化为仅 BM25。
    """
    import llm as _llm
    cfg = _llm.load_config()
    rc = cfg.get("retrieval", {})
    bm_results = search(kb_id, q, topk=max(topk * 4, 20))
    if bm_results is None:
        return None

    cands = {r["id"]: r for r in bm_results}  # 去重

    # 向量召回
    if enable_vector:
        try:
            import embeddings
            ep = rc.get("embedding", {}).get("provider", "cpu")
            qvec = embeddings.embed_query(q, prefer=None if ep == "cpu" else [ep])
            if qvec is not None:
                v_hits = embeddings.search_vectors(kb_id, qvec, topk=rc.get("hybrid_top_k", 30))
                for idx, sim in v_hits:
                    ch = chunk_at(kb_id, idx)
                    if ch and ch["id"] not in cands:
                        ch = dict(ch)
                        ch["vec_score"] = round(sim, 4)
                        cands[ch["id"]] = ch
                    elif ch:
                        cands[ch["id"]]["vec_score"] = round(sim, 4)
        except Exception as e:
            print(f"[kb] 向量召回跳过: {e}")

    results = list(cands.values())
    # 融合打分: BM25(有则用) 与 vec_score 取强信号(缺失时用对方的均值占位, 简单 RRF)
    if any(r.get("vec_score") for r in results) and enable_vector:
        b25_max = max((r["score"] for r in results if r.get("score")), default=1.0)
        vec_max = max((r.get("vec_score", 0) for r in results), default=1.0)
        for r in results:
            s25 = r["score"] / b25_max if r.get("score") else 0.0
            sv = r.get("vec_score", 0.0) / vec_max if vec_max else 0.0
            r["fused"] = round(0.55 * s25 + 0.45 * sv, 4)
    else:
        for r in results:
            r["fused"] = r.get("score", 0.0) / 100.0

    # 可选 Rerank 精排
    if enable_rerank:
        try:
            import rerank
            rr = rerank.rerank(q, results, topk=None)
            if rr is not None:
                results = rr
        except Exception as e:
            print(f"[kb] rerank 跳过: {e}")
    else:
        results.sort(key=lambda x: -x["fused"])

    # Rerank 只比对"问题 ↔ 片段正文"的语义，**看不到文件名**。
    # 偏偏有一类问题问的就是某份文件的标题（"上线投产配置表清单有哪些内容"），
    # 而那份文件的内容是一行行表格数据、正文里几乎不重复"清单"二字，
    # 于是 rerank 会把它排到很后面 —— 用户看到的就是"库里有却答没找到"。
    # 这里按"问题词命中文件名的比例"把它补回来。
    qt = set(tokenize(q))
    if qt:
        touched = False
        for r in results:
            pt = _PATH_TOK_CACHE.get(r["path"])
            if not pt:
                continue
            ratio = len(qt & pt) / len(qt)
            if ratio < _PATH_HIT_MIN:
                continue
            factor = 1.0 + (_PATH_HIT_GAIN - 1.0) * ratio
            if "rerank_score" in r:
                r["rerank_score"] = r["rerank_score"] * factor
            else:
                r["fused"] = r.get("fused", 0.0) * factor
            touched = True
        if touched:
            if any("rerank_score" in r for r in results):
                results.sort(key=lambda x: -x.get("rerank_score", 0))
            else:
                results.sort(key=lambda x: -x.get("fused", 0))

    return results[:topk]
