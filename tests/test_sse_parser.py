import json

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
