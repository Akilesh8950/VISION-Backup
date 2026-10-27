import json

import vision_app.transport.sse as sse_module
from vision_app.transport.sse import StreamAccumulator, ThinkSplitter


def line(payload):
    return "data: " + json.dumps(payload)


def test_tool_call_deltas_accumulate_by_index_and_parallel():
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "second", "arguments": "{\"x\":"}}]}}]}))
    acc.feed_line(line({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "first", "arguments": "{}"}}]}}]}))
    acc.feed_line(line({"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": "1}"}}]}, "finish_reason": "tool_calls"}]}))
    acc.feed_line("data: [DONE]")
    calls = acc.ordered_tool_calls()
    assert [c.function.name for c in calls] == ["first", "second"]
    assert calls[1].function.arguments == '{"x":1}'
    assert not acc.incomplete or acc.finish_reason == "tool_calls"


def test_think_splitter_handles_tags_split_across_chunks():
    splitter = ThinkSplitter()
    events = []
    for chunk in ["<thi", "nk>secret plan</thi", "nk>Answer"]:
        events.extend(splitter.feed(chunk))
    assert [(e.kind, e.text) for e in events] == [("reasoning", "secret plan"), ("content", "Answer")]


def test_truncated_stream_is_incomplete():
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"content": "hello"}}]}))
    assert acc.content == "hello"
    assert acc.incomplete


def test_reasoning_content_delta():
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"reasoning_content": "think", "content": "answer"}}]}))
    assert acc.reasoning == "think"
    assert acc.content == "answer"


def test_stream_accumulator_bounds_lines_text_and_tool_calls(monkeypatch):
    monkeypatch.setattr(sse_module, "MAX_SSE_LINE_CHARS", 10)
    acc = StreamAccumulator()
    events = acc.feed_line("data: " + "x" * 20)
    assert events[0].kind == "error"
    assert "oversized" in acc.error
    monkeypatch.setattr(sse_module, "MAX_SSE_LINE_CHARS", 10_000)
    assert acc.feed_line(line({"choices": [{"delta": {"content": "later"}}]})) == []

    monkeypatch.setattr(sse_module, "MAX_STREAM_TEXT_CHARS", 5)
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"reasoning_content": "abc", "content": "defg"}}]}))
    assert acc.reasoning == "abc"
    assert acc.content == "de"
    assert "maximum supported size" in acc.error

    monkeypatch.setattr(sse_module, "MAX_STREAM_TEXT_CHARS", 10_000)
    monkeypatch.setattr(sse_module, "MAX_TOOL_CALLS", 1)
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "one", "arguments": "{}"}}]}}]}))
    acc.feed_line(line({"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"name": "two", "arguments": "{}"}}]}}]}))
    assert "too many tool calls" in acc.error
    assert len(acc.tool_calls) == 1

    monkeypatch.setattr(sse_module, "MAX_TOOL_CALLS", 128)
    monkeypatch.setattr(sse_module, "MAX_TOOL_ARGUMENT_CHARS", 3)
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "one", "arguments": "12345"}}]}}]}))
    assert "oversized tool call" in acc.error
    assert acc.tool_calls[0].arguments == "123"


def test_done_flush_records_partial_tag_suffix_in_accumulator():
    acc = StreamAccumulator()
    acc.feed_line(line({"choices": [{"delta": {"content": "answer <th"}}]}))
    events = acc.feed_line("data: [DONE]")
    assert any(event.kind == "content" and event.text == "<th" for event in events)
    assert acc.content == "answer <th"
    # ChatClient also calls close() after the stream; it must be idempotent.
    assert acc.close() == []
    assert acc.content == "answer <th"
