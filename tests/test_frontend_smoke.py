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
        *(
            str(path)
            for path in sorted(Path("/tmp/browsers/chrome").glob("linux-*/chrome-linux64/chrome"))
        ),
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

            evaluate(
                "(() => { const tab = document.getElementById('settings-tab-general');"
                " tab?.focus(); tab?.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true})) })()"
            )
            deadline = time.time() + 10
            settings_keyboard_ok = False
            while time.time() < deadline:
                settings_keyboard_ok = bool(
                    evaluate(
                        "document.getElementById('settings-tab-voice')?.getAttribute('aria-selected') === 'true'"
                        " && document.activeElement?.id === 'settings-tab-voice'"
                    )
                )
                if settings_keyboard_ok:
                    break
                time.sleep(0.3)
            assert settings_keyboard_ok, "Settings rail keyboard navigation did not select Voice"

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

            # Motion contract: the Chat/Agent toggle slides its indicator
            # (transform on the ::before pill) instead of swapping colours.
            evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
            time.sleep(0.3)
            evaluate(
                "[...document.querySelectorAll('.mode-toggle button')]"
                ".find((b) => b.textContent.trim() === 'Agent')?.click()"
            )
            deadline = time.time() + 10
            mode_slide_ok = False
            while time.time() < deadline:
                mode_slide_ok = bool(
                    evaluate(
                        "(() => { const t = document.querySelector('.mode-toggle'); if (!t) return false;"
                        " const transform = getComputedStyle(t, '::before').transform;"
                        " return t.classList.contains('is-agent') && transform.startsWith('matrix') })()"
                    )
                )
                if mode_slide_ok:
                    break
                time.sleep(0.3)
            assert mode_slide_ok, "Chat/Agent sliding indicator did not engage"
            evaluate(
                "[...document.querySelectorAll('.mode-toggle button')]"
                ".find((b) => b.textContent.trim() === 'Chat')?.click()"
            )
            time.sleep(0.3)

            # Lazy Vision Live: the panel chunk must load on first open and
            # render its real controls (not just the Suspense fallback),
            # without widening the document.
            evaluate("document.querySelector('.live-title-btn')?.click()")
            deadline = time.time() + 25
            live_ok = False
            while time.time() < deadline:
                live_ok = bool(
                    evaluate(
                        "Boolean(document.querySelector('.live-panel .live-talk'))"
                        " && (document.querySelector('.live-panel')?.innerText || '').includes('Talk with VISION')"
                        " && document.documentElement.scrollWidth <= window.innerWidth + 1"
                    )
                )
                if live_ok:
                    break
                time.sleep(0.4)
            assert live_ok, "lazy Vision Live panel did not open with push-to-talk controls"
            evaluate("document.querySelector('button[aria-label=\"Close Vision Live\"]')?.click()")
            deadline = time.time() + 10
            live_closed = False
            while time.time() < deadline:
                live_closed = bool(evaluate("!document.querySelector('.live-panel')"))
                if live_closed:
                    break
                time.sleep(0.3)
            assert live_closed, "Vision Live panel did not close"

            # Responsive contract: from the 320x360 native floor through
            # laptop and ultrawide windows there is no sideways overflow,
            # the sidebar uses the intended rail/full width, and the
            # composer + Send stay inside the viewport. This replaces the
            # old 520px-only check that missed a hidden ~386px floor.
            responsive_sizes = [
                (5120, 1440, 300, False),
                (2560, 1080, 300, False),
                (2300, 800, 299.9, False),
                (2200, 800, 295.3, False),
                (2000, 800, 286, False),
                (1920, 1080, 282.3, False),
                (1800, 800, 276.7, False),
                (901, 800, 238.4, False),
                (900, 800, 238.3, False),
                (800, 800, 227.6, False),
                (681, 800, 214.9, False),
                (680, 800, 64, True),
                (1080, 1920, 257.5, False),
                (480, 480, 64, True),
                (1600, 360, 267.4, False),
                (520, 460, 64, True),
                (360, 640, 54, True),
                (320, 360, 50, True),
            ]
            sidebar_widths: dict[tuple[int, int], float] = {}
            for width, height, expected_sidebar, expected_collapsed in responsive_sizes:
                send(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
                )
                send("Page.navigate", {"url": f"http://127.0.0.1:{port}/index.html?gallery=1"})
                deadline = time.time() + 30
                size_ok = False
                while time.time() < deadline:
                    state = evaluate(
                        "JSON.stringify((() => {"
                        " const rect = (s) => { const e = document.querySelector(s); if (!e) return null;"
                        " const r = e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom} };"
                        " return {ready:(document.body.innerText || '').includes('VISION'),"
                        " overflow:document.documentElement.scrollWidth > window.innerWidth + 1,"
                        " collapsed:document.querySelector('.workspace')?.classList.contains('collapsed'),"
                        " inner:[innerWidth,innerHeight], app:rect('.app'), sidebar:rect('.sidebar'),"
                        " main:rect('.main'), composer:rect('.composer'), send:rect('.send-btn')} })())"
                    )
                    if state:
                        parsed = json.loads(state)
                        if (
                            parsed.get("ready")
                            and parsed.get("inner") == [width, height]
                            and parsed.get("app")
                            and parsed.get("sidebar")
                            and parsed.get("main")
                            and parsed.get("composer")
                            and parsed.get("send")
                        ):
                            assert parsed["overflow"] is False, f"horizontal overflow at {width}x{height}: {parsed}"
                            assert abs(parsed["app"]["w"] - width) <= 1, parsed
                            assert abs(parsed["app"]["h"] - height) <= 1, parsed
                            assert abs(parsed["sidebar"]["w"] - expected_sidebar) <= 1.5, parsed
                            assert parsed["collapsed"] is expected_collapsed, parsed
                            sidebar_widths[(width, height)] = parsed["sidebar"]["w"]
                            for name in ("main", "composer", "send"):
                                rect = parsed[name]
                                assert rect["x"] >= -1 and rect["right"] <= width + 1, (name, parsed)
                            assert parsed["send"]["bottom"] <= height + 1, parsed
                            size_ok = True
                            break
                    time.sleep(0.4)
                assert size_ok, f"app did not settle at {width}x{height}"

            # Fluid continuity around the old breakpoint ladder: a 1px width
            # change at 901/900 moves the sidebar edge by about a tenth of a
            # pixel, and 100-200px changes across 1700/2300 are proportional.
            assert abs(sidebar_widths[(901, 800)] - sidebar_widths[(900, 800)]) <= 1
            assert abs(sidebar_widths[(2300, 800)] - sidebar_widths[(2200, 800)]) <= 6
            assert abs(sidebar_widths[(2000, 800)] - sidebar_widths[(1800, 800)]) <= 11

            # Settings mirrors the shell's one structural threshold: its
            # professional section rail stays on the left at 681px and only
            # becomes the horizontal top tab bar at 680px.
            for width, height, expect_left in ((681, 800, True), (680, 800, False)):
                send(
                    "Emulation.setDeviceMetricsOverride",
                    {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
                )
                send("Page.navigate", {"url": f"http://127.0.0.1:{port}/index.html?gallery=1"})
                deadline = time.time() + 30
                settings_ready = False
                while time.time() < deadline:
                    if evaluate("Boolean(document.querySelector('.settings-footer-btn'))"):
                        settings_ready = True
                        break
                    time.sleep(0.3)
                assert settings_ready, f"Settings button not reachable at {width}x{height}"
                evaluate("document.querySelector('.settings-footer-btn')?.click()")
                deadline = time.time() + 20
                settings_position_ok = False
                while time.time() < deadline:
                    state = evaluate(
                        "JSON.stringify((() => {"
                        " const rect = (s) => { const e = document.querySelector(s); if (!e) return null;"
                        " const r = e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom} };"
                        " return {nav:rect('.settings-nav'), content:rect('.settings-content'),"
                        " overflow:document.documentElement.scrollWidth > innerWidth + 1} })())"
                    )
                    parsed = json.loads(state) if state else {}
                    nav = parsed.get("nav")
                    content = parsed.get("content")
                    if nav and content:
                        assert parsed["overflow"] is False, parsed
                        if expect_left:
                            assert nav["right"] <= content["x"] + 1, parsed
                        else:
                            assert nav["bottom"] <= content["y"] + 1, parsed
                            assert abs(nav["w"] - content["w"]) <= 2, parsed
                        settings_position_ok = True
                        break
                    time.sleep(0.3)
                assert settings_position_ok, f"Settings position not reachable at {width}x{height}"
                evaluate("document.querySelector('.settings-head .icon-btn')?.click()")
                time.sleep(0.3)

            # Live frame dragging suppresses geometry transitions only until
            # the resize stream settles.
            evaluate("window.dispatchEvent(new Event('resize'))")
            time.sleep(0.05)
            assert evaluate("document.querySelector('.app')?.classList.contains('window-resizing')")
            time.sleep(0.3)
            assert not evaluate("document.querySelector('.app')?.classList.contains('window-resizing')")

            send(
                "Emulation.setDeviceMetricsOverride",
                {"width": 320, "height": 360, "deviceScaleFactor": 1, "mobile": False},
            )
            send("Page.navigate", {"url": f"http://127.0.0.1:{port}/index.html?gallery=1"})
            deadline = time.time() + 30
            tiny_ready = False
            while time.time() < deadline:
                if evaluate("Boolean(document.querySelector('.settings-footer-btn'))"):
                    tiny_ready = True
                    break
                time.sleep(0.3)
            assert tiny_ready, "app did not reset to the 320x360 Settings check"

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
            assert voice_ok, "Settings -> Voice picker not reachable at 320x360"
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
