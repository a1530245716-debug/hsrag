# -*- coding: utf-8 -*-
"""用 Edge 无头模式 + CDP 截取界面，供操作手册配图。
前置：tools/shot_setup.py 已建好演示知识库；服务在 8000 端口运行。"""
import asyncio, base64, json, subprocess, sys, tempfile, time
from pathlib import Path

import requests
import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images" / "manual"
STATE = json.loads((Path(__file__).with_name("shot_state.json")).read_text(encoding="utf-8"))
BASE = "http://127.0.0.1:8000"
DEBUG_PORT = 9444
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = Path(tempfile.gettempdir()) / "ragshot-profile2"

OUT.mkdir(parents=True, exist_ok=True)


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def cmd(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr, await_promise=False):
        p = {"expression": expr, "returnByValue": True}
        if await_promise:
            p["awaitPromise"] = True
        r = await self.cmd("Runtime.evaluate", **p)
        return r.get("result", {}).get("value")

    async def wait(self, expr, timeout=60, desc=""):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if await self.js(expr):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.4)
        raise TimeoutError(f"等待超时: {desc or expr}")

    async def shot(self, name):
        r = await self.cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        p = OUT / name
        p.write_bytes(base64.b64decode(r["data"]))
        print("  saved", p.relative_to(ROOT), f"{p.stat().st_size//1024}KB")


async def main():
    # 登录拿 token
    s = requests.Session()
    r = s.post(BASE + "/api/login", json={"username": STATE["username"], "password": STATE["password"]})
    assert r.json().get("ok"), r.text
    token = s.cookies.get("rag_token")
    print("token ok")

    # 启动 Edge（若已有调试端口则复用）
    try:
        requests.get(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=2)
    except Exception:
        subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                          "--no-default-browser-check", "--disable-sync", "--disable-background-networking",
                          "--disable-extensions", "--disable-component-extensions-with-background-pages",
                          "--disable-features=msEdgeSidebarV2,msEdgeSearchBar,msEdgeIdentity,msEdgeSync,SigninInterception",
                          f"--remote-debugging-port={DEBUG_PORT}", "--remote-allow-origins=*",
                          f"--user-data-dir={PROFILE}", "--window-size=1440,900", "about:blank"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.5)
            try:
                requests.get(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=2); break
            except Exception:
                pass

    targets = requests.get(f"http://127.0.0.1:{DEBUG_PORT}/json/list").json()
    pages = [t for t in targets if t["type"] == "page"
             and (t["url"].startswith("http") or t["url"] == "about:blank")]
    page = pages[-1]
    print("target", page["url"])
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
        c = CDP(ws)
        await c.cmd("Page.enable")
        await c.cmd("Runtime.enable")
        await c.cmd("Network.enable")
        await c.cmd("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                    deviceScaleFactor=2, mobile=False)

        # 1. 登录页（先清 cookie）
        await c.cmd("Network.clearBrowserCookies")
        await c.cmd("Page.navigate", url=BASE + "/")
        await c.wait("document.readyState==='complete'", 20)
        await asyncio.sleep(1.2)
        # 清掉浏览器自动填充的旧账号，截一张干净的登录页
        await c.js("$('#loginUser').value=''; $('#loginPwd').value='';"
                   "document.getElementById('loginErr').textContent='';")
        await asyncio.sleep(0.3)
        await c.shot("01-login.png")

        # 2. 登录后的主界面
        await c.cmd("Network.setCookie", name="rag_token", value=token,
                    url=BASE + "/", path="/", httpOnly=True)
        await c.cmd("Page.navigate", url=BASE + "/")
        await c.wait("document.querySelectorAll('.kb').length>0", 30, "知识库列表")
        # 已知前端 bug：带有效 cookie 刷新时 initAuth 成功但不会隐藏登录遮罩
        await c.js("document.getElementById('loginMask').classList.add('hidden')")
        await asyncio.sleep(0.8)
        await c.js("document.querySelector('.kb').click()")
        await c.wait("!document.getElementById('q').disabled", 10, "输入框可用")
        await asyncio.sleep(0.5)
        await c.shot("02-main.png")

        # 3. 提问并等回答
        q = "这个项目解决什么问题？"
        await c.js(f"$('#q').value={json.dumps(q)}; $('#sendBtn').click();")
        await c.wait("document.querySelector('.srcbar') && !document.getElementById('sendBtn').disabled",
                     240, "回答完成")
        await asyncio.sleep(1.0)
        await c.js("scrollChat()")
        await c.shot("03-answer.png")

        # 4. 展开引用
        await c.js("const d=document.querySelector('.refs details'); if(d) d.open=true;")
        await asyncio.sleep(0.8)
        await c.js("const rs=document.querySelectorAll('.refs'); if(rs.length) rs[rs.length-1].scrollIntoView({block:'start'});")
        await asyncio.sleep(0.5)
        await c.shot("04-references.png")

        # 5. 使用文档
        await c.js("openDoc(); switchTab('tab-bg');")
        await asyncio.sleep(0.7)
        await c.shot("05-doc-background.png")
        await c.js("switchTab('tab-manual')")
        await asyncio.sleep(0.5)
        await c.shot("06-doc-manual.png")
        await c.js("closeModal('docModal')")

        # 6. 写内容
        await c.js("openNote(true)")
        await asyncio.sleep(0.6)
        await c.js("$('#noteTitle').value='演示环境信息（示例笔记）';"
                   "$('#noteBody').value='服务地址：http://127.0.0.1:8000\\n"
                   "数据库：demo-db.internal:1521 / 实例名 DEMO\\n\\n"
                   "说明：这里可以记录口头经验、临时结论，直接粘贴截图也可以一起入库。';")
        await asyncio.sleep(0.4)
        await c.shot("07-note.png")
        await c.js("closeNote()")

        # 7. 新建知识库
        await c.js("document.getElementById('kbAdd').open=true")
        await asyncio.sleep(0.5)
        await c.shot("08-new-kb.png")
        await c.js("document.getElementById('kbAdd').open=false")

        # 8. 知识库没命中：先让用户选兜底来源（不自动联网/不自动编）
        await c.js("$('#q').value='今天天气怎么样？'; $('#sendBtn').click();")
        await c.wait("Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('联网搜一下'))",
                     240, "兜底选项")
        await asyncio.sleep(0.8)
        await c.js("scrollChat()")
        await c.shot("09-offer.png")

        # 9. 手机端
        await c.cmd("Emulation.setDeviceMetricsOverride", width=390, height=844,
                    deviceScaleFactor=3, mobile=True)
        await c.cmd("Page.navigate", url=BASE + "/")
        await c.wait("document.querySelectorAll('.kb').length>0", 30, "手机端知识库")
        await c.wait("innerWidth<=420", 10, "移动端视口")
        await c.js("document.getElementById('loginMask').classList.add('hidden')")
        await c.js("document.querySelector('.kb').click()")
        await asyncio.sleep(0.8)
        await c.shot("10-mobile.png")

    print("done")


if __name__ == "__main__":
    asyncio.run(main())
