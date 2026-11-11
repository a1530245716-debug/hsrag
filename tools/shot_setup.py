# -*- coding: utf-8 -*-
"""临时演示环境：注册演示用户 + 建演示知识库 + 上传示例文档，供界面截图使用。
用完请执行 shot_cleanup.py 清理。"""
import json, sys, time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
STATE = Path(__file__).with_name("shot_state.json")

USERNAME = "demo_shot"
PASSWORD = "Demo@2026"
_code_file = ROOT / "app" / "data" / "register_code.txt"
CODE = _code_file.read_text(encoding="utf-8").strip() if _code_file.exists() else ""

s = requests.Session()


def show(tag, r):
    try:
        data = r.json()
    except Exception:
        data = r.text[:200]
    print(tag, r.status_code, json.dumps(data, ensure_ascii=False)[:400])
    return data


def main():
    # 已存在就不重复注册
    r = s.get(BASE + "/api/auth/status")
    show("status", r)
    reg = s.post(BASE + "/api/register", json={
        "username": USERNAME, "password": PASSWORD, "name": "演示用户", "code": CODE})
    if reg.status_code not in (200, 400):
        print("register failed, abort"); sys.exit(1)
    if reg.status_code == 400:
        print("user exists, login instead")
        r = s.post(BASE + "/api/login", json={"username": USERNAME, "password": PASSWORD})
        show("login", r)
        if not r.json().get("ok"):
            sys.exit(1)

    kbs = s.get(BASE + "/api/kb").json().get("kbs", [])
    demo = next((k for k in kbs if k.get("name") == "演示项目知识库"), None)
    if not demo:
        d = show("create kb", s.post(BASE + "/api/kb", json={
            "name": "演示项目知识库", "folder": ""}))
        demo = d["kb"]
    kid = demo["id"]
    print("KID", kid)

    files = [
        ("files", ("项目智能问答助手-金点子大赛.pptx",
                   (ROOT / "项目智能问答助手-金点子大赛.pptx").read_bytes(),
                   "application/vnd.openxmlformats-officedocument.presentationml.presentation")),
        ("files", ("README-系统说明.md", (ROOT / "README.md").read_bytes(), "text/markdown")),
    ]
    show("upload", s.post(f"{BASE}/api/kb/{kid}/upload?index=true", files=files))

    # 等索引 + 向量就绪
    for _ in range(60):
        time.sleep(2)
        kb = s.get(f"{BASE}/api/kb/{kid}").json().get("kb", {})
        print("  ..", kb.get("status"), kb.get("chunk_count"), kb.get("vector_status"))
        if kb.get("status") == "ready" and kb.get("chunk_count") and kb.get("vector_status") == "ready":
            break

    STATE.write_text(json.dumps({"kid": kid, "username": USERNAME, "password": PASSWORD},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print("STATE saved", STATE)


if __name__ == "__main__":
    main()
