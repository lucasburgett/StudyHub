"""The chat loop through the real Anthropic SDK, against a local server that replays streamed responses.

This checks what the scripted fake in test_chat.py can't: that the SDK accepts our request
parameters, sends the beta header, and that streamed thinking + tool_use blocks round-trip.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import pytest

from studyhub.agent.chat import run_chat


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _start(content_events, stop_reason):
    return [
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 1}}},
        *content_events,
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": 30}},
        {"type": "message_stop"},
    ]


TOOL_TURN = _start([
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig123"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_lecture", "input": {}}},
    {"type": "content_block_delta", "index": 1,
     "delta": {"type": "input_json_delta", "partial_json": '{"course": "CS 231N", '}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"number": 3}'}},
    {"type": "content_block_stop", "index": 1},
], "tool_use")

ANSWER_TURN = _start([
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Lecture 3 covered "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "softmax [[r6@41:12]]."}},
    {"type": "content_block_stop", "index": 0},
], "end_turn")


@pytest.fixture
def fake_api():
    calls = []
    script = [TOOL_TURN, ANSWER_TURN]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            calls.append({"path": self.path, "beta": self.headers.get("anthropic-beta"), "body": body})
            payload = _sse(script.pop(0))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()


def test_chat_through_sdk(demo, fake_api):
    base_url, calls = fake_api
    client = anthropic.Anthropic(api_key="test-key", base_url=base_url, max_retries=0)
    events = list(run_chat(demo, "Summarize lecture 3", client=client))

    assert [d["status"] for e, d in events if e == "tool"] == ["running", "done"]
    assert "".join(d["delta"] for e, d in events if e == "text") == "Lecture 3 covered softmax [[r6@41:12]]."
    assert events[-1][0] == "done" and events[-1][1]["usage"]["output_tokens"] == 60

    first, second = calls
    assert first["path"].startswith("/v1/messages")
    assert "server-side-fallback-2026-07-01" in first["beta"]
    assert first["body"]["fallbacks"] == "default"
    assert first["body"]["output_config"] == {"effort": "medium"}
    assert first["body"]["stream"] is True
    # The thinking block (with its signature) and the tool call go back unchanged.
    assistant = second["body"]["messages"][1]["content"]
    assert assistant[0] == {"type": "thinking", "thinking": "", "signature": "sig123"}
    assert assistant[1]["input"] == {"course": "CS 231N", "number": 3}
    tool_result = second["body"]["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_1" and "Linear classifiers" in tool_result["content"]
