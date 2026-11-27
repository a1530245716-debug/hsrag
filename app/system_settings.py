# -*- coding: utf-8 -*-
"""管理员可调的系统参数（存 data/system_settings.json，不写只读 config.json）。

目前有两类：
  registration.perms = {"kb": bool, "defense": bool}
  registration.kb_ids = [kb_id, ...] | None
      None = 未配置，沿用旧行为（自动共享业务手册）
      []   = 明确不自动共享
  notice.kb / notice.defense = {"enabled", "title", "body"}
      用户进入知识库 / 模拟答辩时看到的提醒文案，管理员可改。
"""
import json
import os
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()

_DEFAULT_NOTICE = {
    "kb": {
        "enabled": True,
        "title": "📢 使用提示",
        "body": (
            "本项目目前处于实验推广阶段：多人同时提问时系统会自动排队，"
            "回答会变慢，主要受本地资料检索/重排资源影响（不是大模型接口并发）。请耐心等待，不要重复点击发送。\n\n"
            "如果大模型回答不好，欢迎点 👎 并填写原因（例如“希望补充审核/分发步骤”），"
            "我们会据此补充资料、修复问题。\n\n"
            "加内容、觉得系统有改进的地方，或者你的需求，可以点下面的「💬 反馈 / 提需求」填写，"
            "也可以随时在右上角「⚙ 更多 → 💬 反馈」里提交。"
        ),
    },
    "defense": {
        "enabled": True,
        "title": "📣 温馨提示",
        "body": (
            "本项目目前处于实验推广阶段：多人同时提问时系统会自动排队，"
            "回答会变慢，主要受本地资料检索/重排资源影响（不是大模型接口并发）。请耐心等待。\n\n"
            "有问题、改进建议或新需求，可以点右上角「💬 反馈」提交。"
        ),
    },
}

DEFAULT = {
    "registration": {
        "perms": {"kb": True, "defense": False},
        "kb_ids": None,              # 问答入口默认共享库
        "defense_kb_ids": None,      # 模拟答辩默认共享库（None=业务手册）
    },
    "notice": _DEFAULT_NOTICE,
}


def _data_dir():
    try:
        import llm
        d = Path(llm.load_config().get("data_dir", "app/data"))
    except Exception:
        d = Path(__file__).parent / "data"
    if not d.is_absolute():
        app = Path(__file__).parent
        alt = app.parent / d
        d = alt if alt.exists() else app / d
    return d


def _path():
    return _data_dir() / "system_settings.json"


def _norm_kb_ids(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    out = []
    for x in (v or []):
        x = str(x or "").strip()
        if x and x not in out:
            out.append(x)
    return out


_OLD_NOTICE_SENTENCE = {
    "kb": (
        "本项目目前处于实验推广阶段，服务器性能有限：多人同时提问时会自动排队，"
        "回答速度会明显变慢。请耐心等待，不要重复点击发送。",
        "本项目目前处于实验推广阶段：多人同时提问时系统会自动排队，"
        "回答会变慢，主要受本地资料检索/重排资源影响（不是大模型接口并发）。"
        "请耐心等待，不要重复点击发送。",
    ),
    "defense": (
        "本项目目前处于实验推广阶段，服务器性能有限：多人同时提问时会自动排队，"
        "回答速度会明显变慢，请耐心等待。",
        "本项目目前处于实验推广阶段：多人同时提问时系统会自动排队，"
        "回答会变慢，主要受本地资料检索/重排资源影响（不是大模型接口并发）。"
        "请耐心等待。",
    ),
}


def _migrate_notice_body(key, body):
    """旧版默认提示把变慢归因于服务器/大模型并发；只替换旧默认句，保留管理员自定义部分。"""
    old, new = _OLD_NOTICE_SENTENCE.get(key, ("", ""))
    return body.replace(old, new, 1) if old and old in body else body


def _norm_notice(v):
    v = v if isinstance(v, dict) else {}
    out = {}
    for key in ("kb", "defense"):
        d = _DEFAULT_NOTICE[key]
        x = v.get(key) if isinstance(v.get(key), dict) else {}
        body = str(x.get("body") or d["body"])[:4000]
        out[key] = {
            "enabled": bool(x.get("enabled", d["enabled"])),
            "title": str(x.get("title") or d["title"]).strip()[:120],
            "body": _migrate_notice_body(key, body),
        }
    return out


def load():
    data = {}
    p = _path()
    with _LOCK:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
    reg = data.get("registration") if isinstance(data.get("registration"), dict) else {}
    perms = reg.get("perms") if isinstance(reg.get("perms"), dict) else {}
    return {
        "registration": {
            "perms": {
                "kb": bool(perms.get("kb", DEFAULT["registration"]["perms"]["kb"])),
                "defense": bool(perms.get("defense", DEFAULT["registration"]["perms"]["defense"])),
            },
            "kb_ids": _norm_kb_ids(reg.get("kb_ids")),
            "defense_kb_ids": _norm_kb_ids(reg.get("defense_kb_ids")),
        },
        "notice": _norm_notice(data.get("notice")),
    }


def save(data):
    """保存传入的段落；未传入的段落保留当前值（避免注册设置覆盖提示文案）。"""
    cur = load()
    data = data if isinstance(data, dict) else {}
    reg_in = data.get("registration")
    notice_in = data.get("notice")
    reg = reg_in if isinstance(reg_in, dict) else cur["registration"]
    notice = notice_in if isinstance(notice_in, dict) else cur["notice"]
    perms = reg.get("perms") or {}
    out = {
        "registration": {
            "perms": {"kb": bool(perms.get("kb")), "defense": bool(perms.get("defense"))},
            "kb_ids": _norm_kb_ids(reg.get("kb_ids")),
            "defense_kb_ids": _norm_kb_ids(reg.get("defense_kb_ids")),
        },
        "notice": _norm_notice(notice),
    }
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
    with _LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    return out


def save_notice(notice):
    return save({"notice": notice})


def registration_defaults():
    return load()["registration"]


def notice_defaults():
    return load()["notice"]
