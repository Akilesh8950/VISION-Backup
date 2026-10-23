from __future__ import annotations

import json
import re
from pathlib import Path

from vision_app.app import build_services
from vision_app.paths import AppPaths
from vision_app.web_bridge import VisionWebApi, find_web_dir

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"


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


def test_bridge_state_settings_and_secret_redaction(tmp_path):
    api = make_api(tmp_path)
    state = api.get_state()
    settings = api.get_settings()
    encoded = json.dumps({"state": state, "settings": settings})

    assert state["current_conversation_id"]
    assert state["app"]["credits"] == "VISION — built by SPIRIT ORG"
    assert state["credentials"] == {"remote_api_key_stored": True, "hf_token_stored": True}
    assert "remote-secret-value" not in encoded
    assert "hf-secret-value" not in encoded
    tool_names = {item["name"] for item in state["tools"]}
    assert {"get_weather", "system_status", "read_url", "ui_chart"}.issubset(tool_names)


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


def test_frontend_assets_are_local_and_complete():
    required = [
        WEB / "index.html",
        WEB / "screenshots.html",
        WEB / "styles" / "tokens.css",
        WEB / "styles" / "app.css",
        WEB / "js" / "chat.js",
        WEB / "js" / "stream.js",
        WEB / "js" / "tools.js",
        WEB / "js" / "widgets.js",
        WEB / "js" / "motion.js",
        WEB / "js" / "ipc-bridge.js",
        WEB / "vendor" / "three.module.js",
        WEB / "vendor" / "three.core.js",
        WEB / "vendor" / "anime.esm.min.js",
        WEB / "vendor" / "chart.js",
        WEB / "vendor" / "chunks" / "index.js",
        WEB / "vendor" / "kurkle-color.esm.js",
        WEB / "vendor" / "VERSIONS.md",
    ]
    assert all(path.is_file() and path.stat().st_size > 0 for path in required)

    index = (WEB / "index.html").read_text(encoding="utf-8")
    assert '"@kurkle/color": "./vendor/kurkle-color.esm.js"' in index
    assert '"three": "./vendor/three.module.js"' in index
    assert '"animejs": "./vendor/anime.esm.min.js"' in index
    assert '"chart.js": "./vendor/chart.js"' in index

    external_ref = re.compile(r"""<(?:script|link|img)[^>]+(?:src|href)=["'](?:https?:)?//""", re.IGNORECASE)
    for path in [WEB / "index.html", WEB / "screenshots.html"]:
        assert not external_ref.search(path.read_text(encoding="utf-8"))

    tokens = (WEB / "styles" / "tokens.css").read_text(encoding="utf-8")
    for expected in ("#0B0D10", "#12151A", "#171B21", "#4F8CFF", "#E6E9EF", "#8A93A3"):
        assert expected.lower() in tokens.lower()
    app_css = (WEB / "styles" / "app.css").read_text(encoding="utf-8")
    assert "linear-gradient" not in app_css
    assert "radial-gradient" not in app_css
    assert "prefers-reduced-motion" in app_css


def test_frontend_bridge_calls_and_events_exist():
    bridge_source = (ROOT / "src" / "vision_app" / "web_bridge.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'_emit\(\s*"([^"]+)"', bridge_source))
    js = "\n".join(path.read_text(encoding="utf-8") for path in (WEB / "js").glob("*.js"))

    called = set(re.findall(r'\bapi\(\s*"([a-z_]+)"', js))
    called.update(re.findall(r'\bcallOptional\(\s*"([a-z_]+)"', js))
    called.update(re.findall(r'\bcall\(\s*"([a-z_]+)"', js))
    # Server actions are intentionally composed as `${action}_server`.
    called.update({"start_server", "stop_server", "restart_server"})
    assert called
    assert all(hasattr(VisionWebApi, name) for name in called)

    listened = set(re.findall(r'\bon\(\s*"([a-z_]+)"', js))
    assert listened.issubset(emitted)
    assert {"content_delta", "reasoning_delta", "tool_started", "tool_finished", "turn_finished"}.issubset(listened)
