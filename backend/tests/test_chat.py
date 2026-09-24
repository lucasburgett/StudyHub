"""The chat loop against a scripted stand-in for the streaming Messages API."""

import json
from types import SimpleNamespace

from anthropic.types.beta import BetaMessage

from studyhub.agent.chat import FALLBACK_BETA, echo_content, run_chat


def _message(content, stop_reason):
    return BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 20},
    })


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.message.content:
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))
        return FakeStream(self.script.pop(0))


def test_chat_runs_tools_streams_and_persists(demo):
    rec = demo.execute("SELECT r.id FROM resources r JOIN lectures l ON l.id = r.lecture_id"
                       " WHERE r.kind = 'transcript' AND l.number = 3").fetchone()["id"]
    client = FakeClient([
        _message([{"type": "thinking", "thinking": "", "signature": "sig"},
                  {"type": "text", "text": "Let me check."},
                  {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"query": "cross entropy initialization"}}],
                 "tool_use"),
        _message([{"type": "text", "text": f"About $\\ln 10 \\approx 2.3$ [[r{rec}@41:12]] [[r999]]."}], "end_turn"),
    ])
    events = list(run_chat(demo, "What should the loss be at init?", scope={"course_id": 1}, client=client))
    names = [e for e, _ in events]
    assert names[0] == "thread" and names[-1] == "done"
    tools = [d for e, d in events if e == "tool"]
    assert [t["status"] for t in tools] == ["running", "done"]
    assert tools[0]["label"] == "Searched all classes for “cross entropy initialization”"
    sources = {}
    for e, d in events:
        if e == "sources":
            sources.update(d["citations"])
    assert f"r{rec}@41:12" in sources and "r999" not in sources
    streamed = "".join(d["delta"] for e, d in events if e == "text")
    assert streamed.startswith("Let me check.\n\nAbout")

    first, second = client.requests
    assert first["fallbacks"] == "default" and FALLBACK_BETA in first["betas"]
    assert first["thinking"] == {"type": "adaptive"}
    assert first["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert "CS 231N" in first["system"][1]["text"]
    assert all(t["eager_input_streaming"] for t in first["tools"])
    assert first["messages"][0]["content"].startswith("<context>")
    assert second["messages"][1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    result = second["messages"][2]["content"][0]
    assert result["tool_use_id"] == "tu_1" and f"[r{rec}@41:12]" in result["content"]

    stored = demo.execute("SELECT * FROM messages WHERE role = 'assistant'").fetchone()
    assert json.loads(stored["citations_json"]).keys() == {f"r{rec}@41:12"}
    assert json.loads(stored["tools_json"])[0]["summary"].endswith("hits")

    # A follow-up in the same thread replays the whole earlier exchange.
    thread_id = events[0][1]["thread_id"]
    client2 = FakeClient([_message([{"type": "text", "text": "Sure."}], "end_turn")])
    list(run_chat(demo, "Thanks", thread_id=thread_id, client=client2))
    roles = [m["role"] for m in client2.requests[0]["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]


def test_invalid_tool_input_goes_back_as_an_error(demo):
    client = FakeClient([
        _message([{"type": "tool_use", "id": "tu_1", "name": "search", "input": {"qurey": "typo"}}], "tool_use"),
        _message([{"type": "text", "text": "Sorry."}], "end_turn"),
    ])
    list(run_chat(demo, "hi", client=client))
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "INVALID_INPUT" in result["content"]


def test_api_error_is_reported_and_thread_stays_usable(demo):
    import anthropic
    import httpx2

    class Failing(FakeClient):
        def _stream(self, **kwargs):
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.APIConnectionError(request=request)

    events = list(run_chat(demo, "hi", client=Failing([])))
    assert ("error" in [e for e, _ in events]) and events[-1][0] == "done"
    stored = demo.execute("SELECT api_json FROM messages WHERE role = 'assistant'").fetchone()
    assert json.loads(stored["api_json"]) == []


def test_echo_content_after_fallback():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "partial"},
        {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
        {"type": "fallback", "from": {"model": "x"}, "to": {"model": "y"}},
        {"type": "text", "text": "rest"},
        {"type": "tool_use", "id": "t2", "name": "search", "input": {}},
    ]
    assert [b.get("text") or b.get("id") for b in echo_content(blocks)] == ["partial", "rest", "t2"]
    plain = [{"type": "text", "text": "hi"}]
    assert echo_content(plain) == plain
