from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from vision_app.app import build_services
from vision_app.paths import AppPaths
from vision_app.web_bridge import VisionWebApi, _main_window_geometry, find_web_dir

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
FRONTEND = ROOT / "app" / "frontend"
FRONTEND_SRC = FRONTEND / "src"


class DummyCredentials:
    def __init__(self):
        self.values = {"REMOTE_API_KEY": "remote-secret-value", "HF_TOKEN": "hf-secret-value"}

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def make_api(tmp_path):
    paths = AppPaths(app_data=tmp_path / "app", workspace=tmp_path / "workspace")
    services = build_services(paths)
    services.credentials = DummyCredentials()
    return VisionWebApi(services)


def test_find_web_dir_source_tree_and_explicit(tmp_path):
    assert find_web_dir() == WEB.resolve()
    explicit = tmp_path / "web"
    explicit.mkdir()
    (explicit / "index.html").write_text("ok", encoding="utf-8")
    assert find_web_dir(explicit) == explicit.resolve()


def test_main_window_geometry_fits_primary_display():
    def screen(width, height):
        return [SimpleNamespace(width=width, height=height)]

    assert _main_window_geometry() == ((1280, 800), (320, 360))
    assert _main_window_geometry(screen(1366, 768)) == ((1284, 707), (320, 360))
    assert _main_window_geometry(screen(1920, 1080)) == ((1440, 920), (320, 360))
    assert _main_window_geometry(screen(800, 600)) == ((752, 552), (320, 360))
    assert _main_window_geometry(
        [
            SimpleNamespace(x=-1920, y=0, width=1920, height=1080),
            SimpleNamespace(x=0, y=0, width=1366, height=768),
        ]
    ) == ((1284, 707), (320, 360))
    # A display below the normal floor lowers the native minimum to the
    # display itself rather than asking Windows for an off-screen window.
    assert _main_window_geometry(screen(300, 340)) == ((300, 340), (300, 340))
    assert _main_window_geometry([SimpleNamespace(width="wide", height=900)]) == (
        (1280, 800),
        (320, 360),
    )


def test_responsive_shell_contract_covers_tiny_short_and_ultrawide_windows():
    styles = (FRONTEND_SRC / "styles.css").read_text(encoding="utf-8")
    app = (FRONTEND_SRC / "App.jsx").read_text(encoding="utf-8")
    bridge_source = (ROOT / "src/vision_app/web_bridge.py").read_text(encoding="utf-8")

    assert "MAIN_WINDOW_MIN_SIZE = (320, 360)" in bridge_source
    assert "_main_window_geometry" in bridge_source
    assert 'getattr(webview, "screens", [])' in bridge_source
    assert "min_size=native_min_size" in bridge_source

    # The old shell had a hidden ~386px content floor. These constraints
    # are the root-cause fix, not just small-window cosmetics.
    assert "grid-template-columns: minmax(0, 1fr)" in styles
    assert ".composer-row { display: flex;" in styles
    assert ".composer-row .grow { flex: 1 1 0; min-width: 0; }" in styles

    # Normal resizing changes dimensions, not positions: shell geometry is
    # driven by continuous dvw/dvh/dvmin clamps. The only structural width
    # switch is 680px, where labelled navigation no longer fits beside a
    # usable content column. In particular, the old 900px rail switch and
    # the stepped 1700/2300px large-monitor jumps must not come back.
    assert "--titlebar: clamp(40px, calc(27px + 3.7dvh), 50px)" in styles
    assert "--sidebar-w: clamp(260px, calc(193px + 4.65dvw), 300px)" in styles
    assert "--sidebar-c: clamp(44px, calc(18px + 10dvw), 64px)" in styles
    assert "--read: clamp(680px, calc(489px + 20.5dvw), 960px)" in styles
    assert "--sidebar-w: clamp(210px, calc(142px + 10.7dvw), 260px)" in styles
    assert "matchMedia('(max-width: 680px)')" in app
    assert "matchMedia('(max-width: 900px)')" not in app
    assert "@media (max-width: 680px)" in styles
    assert "@media (min-width: 1700px)" not in styles
    assert "@media (min-width: 2300px)" not in styles

    # Any aspect ratio: short/tiny fallbacks remain, while the 21:9 rule is
    # an aspect-excess formula. At the exact ratio boundary the dvw/dvh
    # terms cancel and it evaluates to the ordinary fluid width, so merely
    # crossing 21:9 cannot create a jump.
    assert "@media (max-width: 420px)" in styles
    assert "@media (max-width: 360px)" in styles
    assert "@media (max-width: 300px)" in styles
    assert "@media (max-height: 460px)" in styles
    assert "@media (max-height: 380px)" in styles
    assert "@media (min-aspect-ratio: 21/9)" in styles
    assert "25dvw - 58.333dvh" in styles
    assert "font-size: clamp(13.5px, calc(12.6px + 0.18dvw), 15px)" in styles
    assert ".live-orb" in styles and "min(34dvw, 20dvh)" in styles
    assert ".live-mini-app { height: 100dvh;" in styles

    # While the native frame is being dragged, geometry tracks the pointer
    # instead of lagging behind the deliberate sidebar/panel transitions.
    assert "const [windowResizing, setWindowResizing]" in app
    assert "window.addEventListener('resize', onResize, { passive: true })" in app
    assert "window-resizing" in app and ".app.window-resizing .workspace" in styles
    # The auto-growing textarea asks CSS for its current fluid cap rather
    # than hard-coding the tall-window maximum in JavaScript.
    assert "window.getComputedStyle(node)" in app
    assert "Number.parseFloat(style.maxHeight)" in app


def test_motion_and_performance_contract():
    styles = (FRONTEND_SRC / "styles.css").read_text(encoding="utf-8")
    app = (FRONTEND_SRC / "App.jsx").read_text(encoding="utf-8")
    main = (FRONTEND_SRC / "main.jsx").read_text(encoding="utf-8")
    vite_config = (FRONTEND / "vite.config.js").read_text(encoding="utf-8")

    # Entry splitting: the main window and the floating mini parse only
    # their own surface, and Vision Live's recorder/panel code loads on
    # first activation instead of at startup.
    assert "const App = lazy(() => import('./App.jsx'))" in main
    assert "const LiveMiniApp = lazy(" in main
    assert "const LivePanel = lazy(" in app
    assert "import { LivePanel } from './LivePanel.jsx'" not in app
    assert "liveActivated" in app  # sticky mount keeps mini commands working
    assert "target: 'es2022'" in vite_config
    assert "modulePreload: { polyfill: false }" in vite_config

    # Motion system: shared tokens, sliding Chat/Agent pill, spring
    # entrances, and hero float — all transform/opacity based.
    assert "--ease-spring:" in styles
    assert "@keyframes float-soft" in styles
    assert "@keyframes content-in" in styles
    assert "@keyframes toast-in" in styles
    assert ".mode-toggle.is-agent::before" in styles
    assert "is-agent" in app

    # Long-transcript rendering budget + containment. The calm modes must
    # still be able to switch the decorative motion off.
    assert "content-visibility: auto" in styles
    assert "contain-intrinsic-size: auto 180px" in styles
    assert ".tool-card {" in styles and "contain: layout style" in styles
    assert ".perf-low .hero .logo-mark" in styles
    assert (ROOT / "scripts" / "bench_ui.py").is_file()


def test_bridge_state_settings_and_secret_redaction(tmp_path):
    api = make_api(tmp_path)
    state = api.get_state()
    settings = api.get_settings()
    encoded = json.dumps({"state": state, "settings": settings})

    assert state["current_conversation_id"]
    assert state["app"]["credits"] == "VISION — built by SPIRIT ORG"
    assert state["credentials"] == {"remote_api_key_stored": True, "hf_token_stored": True}
    assert state["paths"]["installation_mode"] == "standard"
    assert settings["paths"]["installation_mode"] == "standard"
    assert state["paths"]["app_data"] == settings["paths"]["app_data"]
    assert "remote-secret-value" not in encoded
    assert "hf-secret-value" not in encoded
    # The renderer does not need (and must not receive) the backend tool
    # inventory in initial state.
    assert "tools" not in state


def test_bridge_conversation_crud_and_confirmation_default_deny(tmp_path):
    api = make_api(tmp_path)
    created = api.create_conversation("")
    conversation_id = created["conversation"]["id"]

    assert api.get_conversation(conversation_id)["messages"] == []
    renamed = api.rename_conversation(conversation_id, "Bridge smoke")
    assert renamed["conversation"]["title"] == "Bridge smoke"
    pinned = api.set_conversation_pinned(conversation_id, True)
    assert any(item["id"] == conversation_id and item["pinned"] for item in pinned["conversations"])

    # A native confirmation can never be implicitly approved before the
    # renderer has declared itself ready.
    assert api._confirm("Allow?", "Test", {"path": "x"}) is False

    deleted = api.delete_conversation(conversation_id)
    assert all(item["id"] != conversation_id for item in deleted["conversations"])


def test_bridge_prepare_turn_reads_attachment_server_side(tmp_path):
    api = make_api(tmp_path)
    state = api.get_state()
    attachment = tmp_path / "note.txt"
    attachment.write_text("attachment body", encoding="utf-8")
    api._attachments["a1"] = {
        "id": "a1",
        "name": attachment.name,
        "path": str(attachment),
        "size": attachment.stat().st_size,
    }
    prepared = api._prepare_turn(
        {
            "conversation_id": state["current_conversation_id"],
            "text": "Use this",
            "attachment_ids": ["a1"],
        },
        regenerate=False,
    )
    assert "attachment body" in prepared["user_text"]
    assert "a1" not in api._attachments
    assert len(prepared["tools"]) >= 28


def test_react_frontend_build_is_local_flat_and_complete():
    required = [
        FRONTEND / "package.json",
        FRONTEND / "package-lock.json",
        FRONTEND / "vite.config.js",
        FRONTEND / "index.html",
        FRONTEND_SRC / "main.jsx",
        FRONTEND_SRC / "App.jsx",
        FRONTEND_SRC / "bridge.js",
        FRONTEND_SRC / "icons.jsx",
        FRONTEND_SRC / "markdown.jsx",
        FRONTEND_SRC / "widgets.jsx",
        FRONTEND_SRC / "demo.js",
        FRONTEND_SRC / "styles.css",
        FRONTEND / "public" / "screenshots.html",
        WEB / "index.html",
        WEB / "screenshots.html",
        WEB / "licenses" / "react.txt",
        WEB / "licenses" / "react-dom.txt",
        WEB / "licenses" / "chartjs.txt",
        WEB / "licenses" / "kurkle-color.txt",
    ]
    assert all(path.is_file() and path.stat().st_size > 0 for path in required)

    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["build"] == "vite build"
    assert {"react", "react-dom", "chart.js"}.issubset(package["dependencies"])
    assert {"vite", "@vitejs/plugin-react"}.issubset(package["devDependencies"])
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    for package_path, version in {
        "node_modules/react": "19.3.1",
        "node_modules/react-dom": "19.3.1",
        "node_modules/chart.js": "4.6.1",
        "node_modules/@kurkle/color": "0.3.4",
        "node_modules/vite": "8.3.5",
        "node_modules/@vitejs/plugin-react": "6.1.2",
    }.items():
        assert lock["packages"][package_path]["version"] == version

    vite_config = (FRONTEND / "vite.config.js").read_text(encoding="utf-8")
    assert "base: './'" in vite_config
    assert "emptyOutDir: true" in vite_config
    assert "outDir: webDir" in vite_config

    source_index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert '<div id="root"></div>' in source_index
    assert 'src="/src/main.jsx"' in source_index
    built_index = (WEB / "index.html").read_text(encoding="utf-8")
    asset_refs = re.findall(r'(?:src|href)="(\./assets/[^"]+)"', built_index)
    assert len(asset_refs) == 2
    assert {Path(ref).suffix for ref in asset_refs} == {".js", ".css"}
    assert all((WEB / ref.removeprefix("./")).is_file() for ref in asset_refs)
    assert not (WEB / "js").exists()
    assert not (WEB / "styles").exists()
    assert not (WEB / "vendor").exists()

    for screenshots in (FRONTEND / "public" / "screenshots.html", WEB / "screenshots.html"):
        assert "./index.html?gallery=1" in screenshots.read_text(encoding="utf-8")
    review_pngs = sorted((ROOT / "docs" / "screenshots").glob("react-*.png"))
    assert len(review_pngs) >= 6
    assert all(path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for path in review_pngs)

    external_ref = re.compile(
        r"""<(?:script|link|img)[^>]+(?:src|href)=["'](?:https?:)?//""",
        re.IGNORECASE,
    )
    for path in (
        FRONTEND / "index.html",
        FRONTEND / "public" / "screenshots.html",
        WEB / "index.html",
        WEB / "screenshots.html",
    ):
        assert not external_ref.search(path.read_text(encoding="utf-8"))

    frontend_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(FRONTEND_SRC.rglob("*"))
        if path.is_file()
    )
    frontend_source += "\n" + source_index
    frontend_source += "\n" + (FRONTEND / "public" / "screenshots.html").read_text(encoding="utf-8")
    for forbidden in (
        "linear-gradient",
        "radial-gradient",
        "conic-gradient",
        "createLinearGradient",
        "createRadialGradient",
        "createConicGradient",
    ):
        assert forbidden not in frontend_source
    assert "@import" not in frontend_source
    assert "url(" not in (FRONTEND_SRC / "styles.css").read_text(encoding="utf-8")

    built_css = "\n".join(path.read_text(encoding="utf-8") for path in (WEB / "assets").glob("*.css"))
    assert "linear-gradient" not in built_css
    assert "radial-gradient" not in built_css
    assert "conic-gradient" not in built_css

    styles = (FRONTEND_SRC / "styles.css").read_text(encoding="utf-8")
    for expected in ("#100f0e", "#1b1a17", "#f7f4ec", "#fffdf7", "#7aa8ff", "#dd8a63"):
        assert expected in styles
    assert ":focus-visible" in styles
    assert "prefers-reduced-motion" in styles


def test_react_frontend_bridge_calls_and_events_exist():
    bridge_source = (ROOT / "src" / "vision_app" / "web_bridge.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'_emit\(\s*["\']([^"\']+)["\']', bridge_source))
    js = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(FRONTEND_SRC.rglob("*"))
        if path.suffix in {".js", ".jsx"}
    )

    called = set(
        re.findall(
            r"""\b(?:bridgeApi|callApi|callOptional)\(\s*["']([a-z_]+)["']""",
            js,
        )
    )
    assert called
    assert all(hasattr(VisionWebApi, name) for name in called)
    assert {
        "get_state",
        "get_conversation",
        "frontend_ready",
        "send_message",
        "choose_attachments",
        "save_settings",
        "resolve_confirmation",
        "save_export",
        "download_model",
        "download_llama",
        "trust_extension",
    }.issubset(called)
    assert "bootstrap" not in called
    assert "pick_attachments" not in called

    listened = set(re.findall(r"""\bon\(\s*["']([a-z_]+)["']""", js))
    assert listened.issubset(emitted)
    assert emitted.issubset(listened)
    assert {
        "content_delta",
        "reasoning_delta",
        "tool_started",
        "tool_finished",
        "turn_finished",
        "confirmation_requested",
        "sources",
        "server_state",
        "task_finished",
    }.issubset(listened)
