# -*- coding: utf-8 -*-
"""管理员可调的系统参数（存 data/system_settings.json，不写只读 config.json）。

目前只有注册默认值：
  registration.perms = {"kb": bool, "defense": bool}
  registration.kb_ids = [kb_id, ...] | None
      None = 未配置，沿用旧行为（自动共享业务手册）
      []   = 明确不自动共享
"""
import json
import os
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()
DEFAULT = {
    "registration": {
        "perms": {"kb": True, "defense": False},
        "kb_ids": None,
    }
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
        }
    }


def save(data):
    reg = (data or {}).get("registration") or {}
    perms = reg.get("perms") or {}
    out = {
        "registration": {
            "perms": {"kb": bool(perms.get("kb")), "defense": bool(perms.get("defense"))},
            "kb_ids": _norm_kb_ids(reg.get("kb_ids")),
        }
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


def registration_defaults():
    return load()["registration"]
