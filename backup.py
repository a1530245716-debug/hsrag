# -*- coding: utf-8 -*-
"""备份「不可再生」数据到 backups/ 目录, 打包成 zip, 自动保留最近 N 份。

备份什么(小而关键):
    data/registry.json          知识库注册表
    data/users.json             用户表(含 key hash)
    data/<kb>/upload/**         上传文件 + 手写笔记(_notes) + 笔记图片
    data/<kb>/manifest.json     每文件的签名(便于核对)
    app/config.json             配置(含大模型/鉴权设置)
不备份什么(大且可重建):
    data/<kb>/chunks.jsonl      索引块(重建即可, 几十 MB~几百 MB)
    data/<kb>/*.npy             向量缓存
    models/                     模型文件(可重新下载)

两种模式(建议搭配用):
    --light   只备「小而关键」的: registry/users/config + 所有手写笔记 + manifest
              约 2 MB, 几十毫秒, 适合**每天甚至每小时**跑
    (默认)    全量: 再加上 upload 里的原始文档(几百 MB), 适合**每周**跑
              原始文档一般不常变, 丢了重传很麻烦, 所以仍需定期全量

用法:
    python backup.py                 全量备份
    python backup.py --light         轻量备份(秒级)
    python backup.py --keep 14       保留 14 份
    python backup.py --dry-run       只看会备份什么
    python backup.py --list          列出已有备份
"""
import argparse, json, os, shutil, sys, time, zipfile
from pathlib import Path

ROOT = Path(__file__).parent      # 项目根
APP = ROOT / "app"                # 应用目录(config.json/data 在这里)
DEFAULT_KEEP = 7
SKIP_DIRS = {"_vec", "_cache", "node_modules", "__pycache__"}
SKIP_FILES = {"chunks.jsonl", "chunks.jsonl.tmp"}


def _data_dir():
    cfg_path = APP / "config.json"
    d = "app/data"
    try:
        d = json.loads(cfg_path.read_text(encoding="utf-8")).get("data_dir", d)
    except Exception:
        pass
    p = Path(d)
    return p if p.is_absolute() else (ROOT / p if (ROOT / p).exists() else APP / p)


def _is_note(rel_posix: str) -> bool:
    """手写笔记及其图片: 用户直接在系统里录入的内容, 丢了无法恢复。"""
    return "/_notes/" in rel_posix or rel_posix.endswith("/_notes")


def _iter_payload(data: Path, light=False):
    """产出 (绝对路径, zip 内相对路径) 的待备份文件。light=True 只出小文件。"""
    # 顶层关键文件
    for name in ("registry.json", "users.json", "admin_key.txt"):
        p = data / name
        if p.exists():
            yield p, f"data/{name}"
    # 每个知识库目录
    for kb_dir in sorted(data.iterdir()):
        if not kb_dir.is_dir() or kb_dir.name.startswith("_"):
            continue
        for name in ("manifest.json",):
            p = kb_dir / name
            if p.exists():
                yield p, f"data/{kb_dir.name}/{name}"
        up = kb_dir / "upload"
        if not up.exists():
            continue
        for fp in up.rglob("*"):
            if not fp.is_file():
                continue
            if fp.name in SKIP_FILES or any(part in SKIP_DIRS for part in fp.parts):
                continue
            rel = f"data/{kb_dir.name}/upload/{fp.relative_to(up).as_posix()}"
            if light and not _is_note(rel):
                continue        # 轻量模式只保留笔记, 跳过用户上传的大文件
            yield fp, rel
    # 配置
    cfg = APP / "config.json"
    if cfg.exists():
        yield cfg, "app/config.json"


def do_backup(keep=DEFAULT_KEEP, dry_run=False, light=False):
    data = _data_dir()
    if not data.exists():
        print(f"[错误] 数据目录不存在: {data}")
        return 1
    items = list(_iter_payload(data, light=light))
    if not items:
        print("[警告] 没有可备份的文件")
        return 1
    total = sum(p.stat().st_size for p, _ in items)

    print(f"数据目录: {data}  (模式: {'轻量' if light else '全量'})")
    print(f"待备份文件: {len(items)} 个, 共 {total / 1024 / 1024:.1f} MB")
    if dry_run:
        for p, rel in items[:40]:
            print(f"  {rel}  ({p.stat().st_size / 1024:.0f} KB)")
        if len(items) > 40:
            print(f"  … 另有 {len(items) - 40} 个")
        return 0

    out_dir = ROOT / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zpath = out_dir / f"rag-backup-{stamp}{'-light' if light else ''}.zip"
    tmp = zpath.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p, rel in items:
                z.write(p, rel)
        tmp.replace(zpath)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"[错误] 备份失败: {e}")
        return 2

    size = zpath.stat().st_size
    print(f"[完成] {zpath.name}  ({size / 1024 / 1024:.1f} MB)")

    # 清理旧备份(轻量与全量分别计数, 避免互相挤掉)
    pat = "rag-backup-*-light.zip" if light else "rag-backup-*.zip"
    olds = sorted(out_dir.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
    if not light:
        olds = [p for p in olds if not p.name.endswith("-light.zip")]
    for p in olds[keep:]:
        try:
            p.unlink()
            print(f"[清理] 删除旧备份 {p.name}")
        except Exception as e:
            print(f"[警告] 删除失败 {p.name}: {e}")
    print(f"当前保留 {min(len(olds), keep)} 份{'轻量' if light else '全量'}备份于 {out_dir}")
    return 0


def list_backups():
    out_dir = ROOT / "backups"
    if not out_dir.exists():
        print("还没有任何备份")
        return 0
    files = sorted(out_dir.glob("rag-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("还没有任何备份")
        return 0
    print(f"共 {len(files)} 份备份:")
    for p in files:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        print(f"  {t}  {p.stat().st_size / 1024 / 1024:>7.1f} MB  {p.name}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--light", action="store_true", help="只备份用户表/配置/笔记(秒级)")
    a = ap.parse_args()
    sys.exit(list_backups() if a.list else do_backup(a.keep, a.dry_run, a.light))
