"""Headless render smoke test for the built React bundle.

Static bundle checks cannot catch runtime startup crashes (e.g. a
useCallback dependency that references a const declared later in the
component — a TDZ ReferenceError that blanks the whole app). This test
drives a real Chrome over CDP, loads gallery mode, and asserts the app
mounted with no uncaught exceptions. Skips cleanly when no Chrome
binary is available on the machine.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

websocket = pytest.importorskip("websocket")

WEB_DIR = Path(__file__).resolve().parents[1] / "app" / "web"


def _find_chrome() -> str | None:
    candidates = [
        os.environ.get("CHROME_BIN", ""),
        str(Path.home() / ".local/share/choreographer/deps/chrome-linux64/chrome"),
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def test_built_frontend_mounts_without_exceptions(tmp_path):
    chrome = _find_chrome()
    if chrome is None or not (WEB_DIR / "index.html").exists():
        pytest.skip("Chrome or built frontend not available")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = int(httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    profile = tmp_path / "chrome-profile"
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS="/dev/null")
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--remote-debugging-port=0",
            "--remote-allow-origins=*", f"--user-data-dir={profile}",
            "--window-size=1280,860", "about:blank",
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # DevToolsActivePort file tells us the chosen debugging port.
        port_file = profile / "DevToolsActivePort"
        deadline = time.time() + 30
        ws_url = None
        while time.time() < deadline:
            try:
                debug_port = int(port_file.read_text().splitlines()[0])
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{debug_port}/json/list", timeout=5
                ) as response:
                    targets = json.load(response)
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page:
                    ws_url = page["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.3)
        assert ws_url, "Chrome DevTools endpoint never came up"

        ws = websocket.create_connection(ws_url, timeout=30)
        seq = 0
        exceptions: list[str] = []

        def pump(message: dict) -> None:
            if message.get("method") == "Runtime.exceptionThrown":
                details = message["params"]["exceptionDetails"]
                exceptions.append(
                    str((details.get("exception") or {}).get("description") or details.get("text"))
                )

        def send(method: str, params: dict | None = None) -> dict:
            nonlocal seq
            seq += 1
            ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            while True:
                message = json.loads(ws.recv())
                if message.get("id") == seq:
                    return message.get("result", {})
                pump(message)

        try:
            send("Page.enable")
            send("Runtime.enable")
            send("Page.navigate", {"url": f"http://127.0.0.1:{port}/index.html?gallery=1"})
            mounted = False
            deadline = time.time() + 30
            while time.time() < deadline:
                result = send(
                    "Runtime.evaluate",
                    {
                        "expression": "Boolean(document.getElementById('root')?.childElementCount) && (document.body.innerText || '').includes('VISION')",
                        "returnByValue": True,
                    },
                )
                if result.get("result", {}).get("value"):
                    mounted = True
                    break
                time.sleep(0.4)
            assert mounted, "React app did not mount (blank screen)"

            def evaluate(expression: str):
                result = send(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True},
                )
                return result.get("result", {}).get("value")

            # Desktop shell contract: Settings sections live in a left-hand
            # rail, and the rich History action menu stays inside the window.
            evaluate("document.querySelector('.settings-footer-btn')?.click()")
            deadline = time.time() + 20
            settings_rail_ok = False
            while time.time() < deadline:
                state = evaluate(
                    "(() => { const nav = document.querySelector('.settings-nav');"
                    " const content = document.querySelector('.settings-content');"
                    " if (!nav || !content) return ''; const n = nav.getBoundingClientRect();"
                    " const c = content.getBoundingClientRect();"
                    " return JSON.stringify({navRight:n.right, contentLeft:c.left}) })()"
                )
                if state:
                    parsed = json.loads(state)
                    assert parsed["navRight"] <= parsed["contentLeft"] + 1, parsed
                    settings_rail_ok = True
                    break
                time.sleep(0.4)
            assert settings_rail_ok, "Settings left-hand section rail not reachable"

            evaluate("document.querySelector('.sheet-head .icon-btn')?.click()")
            time.sleep(0.4)
            evaluate(
                "(() => { const buttons = [...document.querySelectorAll('.chat-menu-button')];"
                " const target = buttons.find((b) => !b.closest('.chat-item')?.classList.contains('active')) || buttons[0];"
                " target?.click() })()"
            )
            deadline = time.time() + 20
            chat_menu_ok = False
            while time.time() < deadline:
                state = evaluate(
                    "(() => { const menu = document.querySelector('.chat-menu'); if (!menu) return '';"
                    " const r = menu.getBoundingClientRect();"
                    " return JSON.stringify({left:r.left, top:r.top, right:r.right, bottom:r.bottom,"
                    " width:innerWidth, height:innerHeight, text:menu.innerText}) })()"
                )
                if state:
                    parsed = json.loads(state)
                    assert "Export as Markdown" in parsed["text"], parsed
                    assert parsed["left"] >= 0 and parsed["top"] >= 0, parsed
                    assert parsed["right"] <= parsed["width"] + 1, parsed
                    assert parsed["bottom"] <= parsed["height"] + 1, parsed
                    chat_menu_ok = True
                    break
                time.sleep(0.4)
            assert chat_menu_ok, "professional chat-history menu not reachable"

            # Responsive contract: at the smallest supported window width
            # there is no sideways overflow (the title bar used to force
            # ~541px), the sidebar becomes the JS-driven icon rail, and
            # Settings -> Voice with its voice picker is reachable.
            send(
                "Emulation.setDeviceMetricsOverride",
                {"width": 520, "height": 700, "deviceScaleFactor": 1, "mobile": False},
            )
            send("Page.navigate", {"url": f"http://127.0.0.1:{port}/index.html?gallery=1"})
            deadline = time.time() + 30
            narrow_ok = False
            while time.time() < deadline:
                state = evaluate(
                    "JSON.stringify({ready: (document.body.innerText || '').includes('VISION'),"
                    " overflow: document.documentElement.scrollWidth > window.innerWidth + 1,"
                    " sidebar: Math.round(document.querySelector('.sidebar')?.getBoundingClientRect().width || 0)})"
                )
                if state:
                    parsed = json.loads(state)
                    if parsed.get("ready") and parsed.get("sidebar"):
                        assert parsed["overflow"] is False, f"horizontal overflow at 520px: {parsed}"
                        assert parsed["sidebar"] <= 72, f"sidebar did not rail at 520px: {parsed}"
                        narrow_ok = True
                        break
                time.sleep(0.4)
            assert narrow_ok, "app did not settle at 520px wide"

            evaluate("document.querySelector('.settings-footer-btn')?.click()")
            deadline = time.time() + 20
            voice_ok = False
            while time.time() < deadline:
                voice_ok = bool(
                    evaluate(
                        "(() => { const tab = [...document.querySelectorAll('.settings-tab')]"
                        ".find((b) => b.textContent.trim().startsWith('Voice')); if (tab) tab.click();"
                        " return Boolean(document.querySelector('#tts-voice-select')) })()"
                    )
                )
                if voice_ok:
                    break
                time.sleep(0.4)
            assert voice_ok, "Settings -> Voice picker not reachable at 520px"
            assert not exceptions, f"uncaught exceptions: {exceptions}"
        finally:
            ws.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        httpd.shutdown()
