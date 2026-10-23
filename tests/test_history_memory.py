from vision_app.history import HistoryStore


def test_history_and_memory_round_trip(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    conversation = store.create_conversation("Weather")
    store.append_message(conversation, "user", "What's the weather?")
    store.append_message(conversation, "assistant", "Sunny")
    rows = store.list_messages(conversation)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert store.last_exchange(conversation) == ("What's the weather?", "Sunny")
    store.memory_set("preferred_city", "Coimbatore")
    assert store.memory_get("preferred_city") == "Coimbatore"
