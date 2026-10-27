from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest

import vision_app.library as library_module
from vision_app.app import build_services
from vision_app.config import AppConfig
from vision_app.history import HistoryStore, generate_conversation_title
from vision_app.library import LibraryStore
from vision_app.paths import AppPaths
from vision_app.web_bridge import VisionWebApi


class DummyCredentials:
    def __init__(self):
        self.values = {}

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
    return VisionWebApi(services), paths


def test_generate_conversation_title_uses_first_exchange_in_three_to_five_words():
    title = generate_conversation_title(
        "Please explain SQLite WAL checkpoint behavior in detail",
        "This answer should not be needed.",
    )
    assert title == "SQLite WAL Checkpoint Behavior Detail"
    assert 3 <= len(title.split()) <= 5

    short = generate_conversation_title(
        "Weather?",
        "Coimbatore is sunny with light winds this afternoon.",
    )
    assert short == "Weather Coimbatore Sunny Light"
    assert 3 <= len(short.split()) <= 5

    cleaned = generate_conversation_title(
        "Fix API timeout ```hidden code``` https://example.invalid/secret",
        "Retry backoff prevents repeat failures.",
    )
    assert "hidden" not in cleaned.casefold()
    assert "example" not in cleaned.casefold()
    assert len(cleaned.split()) <= 5
    assert generate_conversation_title("") == "New chat"


def test_history_first_exchange_and_pinned_search_order(tmp_path):
    history = HistoryStore(tmp_path / "history.db")
    first = history.create_conversation("First")
    history.append_message(first, "assistant", "Ignored preface")
    history.append_message(first, "user", "First prompt")
    history.append_message(first, "assistant", "First answer")
    history.append_message(first, "user", "Second prompt")
    history.append_message(first, "assistant", "Second answer")
    assert history.first_exchange(first) == ("First prompt", "First answer")

    pinned = history.create_conversation("Needle pinned")
    newer = history.create_conversation("Needle newer")
    history.set_conversation_flags(pinned, pinned=True)
    history.rename_conversation(newer, "Needle newer")
    results = history.search_conversations("Needle")
    assert results[0]["id"] == pinned
    assert {row["id"] for row in results} >= {pinned, newer}


def test_library_import_dedupe_privacy_thumbnail_and_limits(tmp_path, monkeypatch):
    store = LibraryStore(tmp_path / "history.db", tmp_path / "library")
    source = tmp_path / "notes.txt"
    source.write_text("hello library", encoding="utf-8")

    item = store.import_file(source)
    duplicate_source = tmp_path / "copy.txt"
    duplicate_source.write_bytes(source.read_bytes())
    duplicate = store.import_file(duplicate_source)

    assert duplicate["id"] == item["id"]
    assert store.count() == 1
    assert item["attachable"] is True
    assert "stored_name" not in item
    assert "sha256" not in item
    assert str(tmp_path) not in json.dumps(item)
    assert store.item_path(item["id"]) is not None
    assert store.item_path(item["id"]).parent == (tmp_path / "library").resolve()

    image = tmp_path / "pixel.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image-but-small")
    image_item = store.import_file(image)
    assert image_item["kind"] == "image"
    assert image_item["attachable"] is False
    assert image_item["thumbnail"].startswith("data:image/png;base64,")

    monkeypatch.setattr(library_module, "MAX_LIBRARY_FILE_BYTES", 4)
    too_big = tmp_path / "too-big.txt"
    too_big.write_text("12345", encoding="utf-8")
    with pytest.raises(ValueError, match="25 MB"):
        store.import_file(too_big)
    assert not list((tmp_path / "library").glob(".incoming-*"))

    monkeypatch.setattr(library_module, "MAX_LIBRARY_FILE_BYTES", 25 * 1024 * 1024)
    monkeypatch.setattr(library_module, "MAX_LIBRARY_ITEMS", 2)
    # A duplicate remains allowed at the cap; genuinely new bytes are not.
    assert store.import_file(duplicate_source)["id"] == item["id"]
    other = tmp_path / "other.txt"
    other.write_text("other", encoding="utf-8")
    with pytest.raises(ValueError, match="limited to 2"):
        store.import_file(other)

    with pytest.raises(FileNotFoundError):
        store.import_file(tmp_path / "missing.txt")


def test_library_rename_use_counts_forget_and_delete(tmp_path):
    store = LibraryStore(tmp_path / "history.db", tmp_path / "library")
    source = tmp_path / "report.txt"
    source.write_text("body", encoding="utf-8")
    item = store.import_file(source)
    managed = store.item_path(item["id"])
    assert managed is not None and managed.is_file()

    renamed = store.rename_item(item["id"], " ../Quarterly   Report.txt ")
    assert renamed["name"] == "Quarterly Report.txt"
    assert store.item_path(item["id"]) == managed
    with pytest.raises(ValueError, match="cannot be empty"):
        store.rename_item(item["id"], "   ")
    with pytest.raises(KeyError):
        store.rename_item("missing", "Name")

    store.record_use(item["id"], "chat-1")
    store.record_use(item["id"], "chat-1")
    store.record_use(item["id"], "chat-2")
    assert store.get_item(item["id"])["used_in_chats"] == 2
    store.forget_conversation("chat-1")
    assert store.get_item(item["id"])["used_in_chats"] == 1

    assert store.delete_item(item["id"]) is True
    assert store.get_item(item["id"]) is None
    assert not managed.exists()
    assert store.delete_item(item["id"]) is False
    store.record_use(item["id"], "chat-3")
    assert store.count() == 0


def test_bridge_projects_assignments_and_auto_title(tmp_path):
    api, _paths = make_api(tmp_path)

    assert api.create_project("")["ok"] is False
    created = api.create_project("  Research   Notes ")
    assert created["ok"] is True
    project = created["project"]
    assert project["name"] == "Research Notes"
    assert set(project) == {"id", "name"}
    assert all(set(row) == {"id", "name"} for row in created["projects"])

    conversation_id = api.create_conversation("")["conversation"]["id"]
    assigned = api.assign_conversation_project(conversation_id, project["id"])
    assert assigned["ok"] is True
    assert assigned["conversation"]["project_id"] == project["id"]
    assert api.assign_conversation_project(conversation_id, "missing")["ok"] is False
    assert api.assign_conversation_project("missing", project["id"])["ok"] is False
    unassigned = api.assign_conversation_project(conversation_id, "")
    assert not unassigned["conversation"]["project_id"]

    api.history.append_message(conversation_id, "user", "Explain SQLite WAL checkpoint behavior")
    api.history.append_message(conversation_id, "assistant", "A detailed response")
    api._rename_new_conversation(SimpleNamespace(conversation_id=conversation_id))
    title = api.get_conversation(conversation_id)["conversation"]["title"]
    assert title == "SQLite WAL Checkpoint Behavior"

    api.rename_conversation(conversation_id, "Manual title")
    api.history.append_message(conversation_id, "user", "Another unrelated prompt")
    api.history.append_message(conversation_id, "assistant", "Another response")
    api._rename_new_conversation(SimpleNamespace(conversation_id=conversation_id))
    assert api.get_conversation(conversation_id)["conversation"]["title"] == "Manual title"


def test_bridge_library_attach_prepare_rename_and_delete(tmp_path):
    api, _paths = make_api(tmp_path)
    state = api.get_state()
    conversation_id = state["current_conversation_id"]
    assert state["library_count"] == 0

    source = tmp_path / "context.md"
    source.write_text("library body", encoding="utf-8")
    item = api.library.import_file(source)
    library = api.get_library()
    assert library["library_count"] == 1
    assert library["items"][0]["id"] == item["id"]
    assert "stored_name" not in library["items"][0]

    attached = api.attach_library_item(item["id"])
    assert attached["ok"] is True
    assert len(attached["attachments"]) == 1
    attachment = attached["attachments"][0]
    assert attachment["library_item_id"] == item["id"]
    assert "path" not in attachment

    renamed = api.rename_library_item(item["id"], "Renamed context.md")
    assert renamed["ok"] is True
    assert renamed["attachments"][0]["name"] == "Renamed context.md"

    prepared = api._prepare_turn(
        {
            "conversation_id": conversation_id,
            "text": "Use the file",
            "attachment_ids": [attachment["id"]],
        },
        regenerate=False,
    )
    assert "library body" in prepared["user_text"]
    assert "Renamed context.md" in prepared["user_text"]
    assert api.get_library()["items"][0]["used_in_chats"] == 1

    image = tmp_path / "image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nsmall")
    image_item = api.library.import_file(image)
    rejected = api.attach_library_item(image_item["id"])
    assert rejected["ok"] is False
    assert "PDF/DOCX" in rejected["message"]

    # Deleting an attached library item also clears the ephemeral chip.
    api.attach_library_item(item["id"])
    deleted = api.delete_library_item(item["id"])
    assert deleted["ok"] is True
    assert deleted["attachments"] == []
    assert all(row["id"] != item["id"] for row in deleted["items"])


def test_bridge_choose_attachments_saves_all_but_attaches_only_text(tmp_path, monkeypatch):
    api, _paths = make_api(tmp_path)
    text_file = tmp_path / "attach.txt"
    text_file.write_text("attach me", encoding="utf-8")
    image_file = tmp_path / "save-only.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\nsmall")

    class FakeWindow:
        def create_file_dialog(self, *args, **kwargs):
            return (str(text_file), str(image_file))

    fake_webview = types.ModuleType("webview")
    fake_webview.OPEN_DIALOG = object()
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    api.window = FakeWindow()

    result = api.choose_attachments()
    assert result["library_count"] == 2
    assert [row["name"] for row in result["attachments"]] == ["attach.txt"]
    assert "only supported UTF-8 text" in result["message"]
    by_name = {row["name"]: row for row in result["items"]}
    assert by_name["attach.txt"]["attachable"] is True
    assert by_name["save-only.png"]["attachable"] is False


def test_bridge_malformed_payloads_queries_and_empty_feedback(tmp_path):
    api, paths = make_api(tmp_path)
    api.get_state()

    assert api.list_conversations(None)["conversations"]
    assert api.list_conversations(123)["conversations"]
    assert api._validated_project_id(None) == ""
    assert api._validated_project_id(123) == ""
    assert api._validated_project_id(object()) == ""

    api._refresh_binary = lambda: None
    assert api.save_settings("bad")["ok"] is True

    api._start_server_internal = lambda: None
    finished = api.finish_setup("bad")
    assert finished["ok"] is True
    assert finished["config"]["first_run_completed"] is True

    captured = {}

    def fake_run(payload, regenerate):
        captured.update(payload)
        api._chat_busy = False

    api._run_chat_thread = fake_run
    accepted = api.send_message("bad")
    assert accepted["accepted"] is True
    assert api._chat_thread is not None
    api._chat_thread.join(timeout=5)
    assert captured == {}

    empty_id = api.create_conversation("")["conversation"]["id"]
    assert api.record_feedback(empty_id, None) == {"ok": False}
    needs_path = paths.needs_log
    before = needs_path.read_text(encoding="utf-8") if needs_path.exists() else ""
    api.history.append_message(empty_id, "user", "Question")
    api.history.append_message(empty_id, "assistant", "Answer")
    assert api.record_feedback(empty_id, None) == {"ok": True}
    after = needs_path.read_text(encoding="utf-8")
    assert len(after.splitlines()) == len(before.splitlines()) + 1


def test_available_models_lists_gguf_switch_options(tmp_path):
    api, paths = make_api(tmp_path)
    (paths.models_dir / "vision-ornith-1.5-9b-q4_k_m.gguf").write_bytes(b"GGUFone")
    (paths.models_dir / "another-model.gguf").write_bytes(b"GGUFtwo")
    (paths.models_dir / "old.previous.gguf").write_bytes(b"GGUFold")

    result = api.get_available_models()
    names = {row["name"] for row in result["available_models"]}
    assert names == {"vision-ornith-1.5-9b-q4_k_m.gguf", "another-model.gguf"}


def test_checkpoints_list_and_restore_via_bridge(tmp_path):
    import time

    api, paths = make_api(tmp_path)
    target = paths.workspace / "code.py"
    target.write_text("original\n", encoding="utf-8")
    checkpoint_id = api.checkpoints.snapshot(target, "conv-1")
    target.write_text("changed\n", encoding="utf-8")

    listed = api.list_checkpoints()
    assert listed["ok"] is True
    assert [row["id"] for row in listed["checkpoints"]] == [checkpoint_id]
    assert listed["checkpoints"][0]["file_name"] == "code.py"

    assert api.restore_checkpoint(checkpoint_id).get("accepted") is True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and target.read_text(encoding="utf-8") != "original\n":
        time.sleep(0.05)
    assert target.read_text(encoding="utf-8") == "original\n"


def test_library_docx_is_attachable_and_text_extracts(tmp_path):
    docx = pytest.importorskip("docx")
    from vision_app.knowledge.parsers import parse_file as parse_document_file

    api, _paths = make_api(tmp_path)
    source = tmp_path / "notes.docx"
    document = docx.Document()
    document.add_paragraph("Quarterly plan for VISION.")
    document.save(str(source))

    item = api.library.import_file(source)
    assert item["attachable"] is True
    attached = api.attach_library_item(item["id"])
    assert attached.get("ok", True) is not False
    attachment = attached["attachments"][0]

    conversation_id = api.history.create_conversation()
    prepared = api._prepare_turn(
        {
            "conversation_id": conversation_id,
            "text": "Summarise the document",
            "attachment_ids": [attachment["id"]],
        },
        regenerate=False,
    )
    assert "Quarterly plan for VISION." in prepared["user_text"]

    stored = api.library.item_path(item["id"])
    sections = parse_document_file(stored)
    assert "Quarterly plan" in " ".join(section.text for section in sections)


def test_save_settings_persists_voice_and_motion_preferences(tmp_path):
    api, paths = make_api(tmp_path)
    api._refresh_binary = lambda: None

    result = api.save_settings(
        {
            "tts_voice_uri": "Microsoft Zira - English (United States)",
            "tts_voice_name": "Microsoft Zira",
            "tts_style": "custom",
            "tts_rate": 42,  # clamped by the config validator, never an error
            "tts_pitch": 0.88,
            "tts_volume": 0.75,
            "tts_auto_read": True,
            "reduce_motion": True,
        }
    )
    assert result["ok"] is True
    payload = result["config"]
    assert payload["tts_voice_uri"] == "Microsoft Zira - English (United States)"
    assert payload["tts_style"] == "custom"
    assert payload["tts_rate"] == 1.5
    assert payload["tts_pitch"] == 0.88
    assert payload["tts_volume"] == 0.75
    assert payload["tts_auto_read"] is True
    assert payload["reduce_motion"] is True

    persisted = AppConfig.load(paths)
    assert persisted.tts_voice_name == "Microsoft Zira"
    assert persisted.reduce_motion is True

    # Junk from the page degrades to the soothing defaults, not a crash.
    degraded = api.save_settings({"tts_style": "screamo", "tts_rate": "fast"})
    assert degraded["ok"] is True
    assert degraded["config"]["tts_style"] == "soothing"
    assert degraded["config"]["tts_rate"] == 0.92
