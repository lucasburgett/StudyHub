"""The subscription loop through the real Claude Code CLI, against a local server that plays the Messages API.

This checks what the scripted SDK in test_subscription.py can't: the flags Claude Code gets, the
request it makes (only StudyHub's tools, our system prompt), that a whole lecture reaches Claude
inline, that streamed text isn't doubled, and that a follow-up resumes the session. Claude Code
authenticates with a dummy key against localhost, and keeps its files in a temporary directory.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from studyhub.agent import subscription
from studyhub.agent.prompt import INSTRUCTIONS
from studyhub.agent.subscription import run_chat_subscription

pytestmark = pytest.mark.skipif(subscription.cli_path() is None, reason="Claude Code CLI not available")


def _sse(msg_id, blocks, stop_reason):
    events = [{"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": "claude-opus-5", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 1}}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            events.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            for part in (b["text"][:10], b["text"][10:]):
                events.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": part}})
        else:
            events.append({"type": "content_block_start", "index": i,
                           "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        events.append({"type": "content_block_stop", "index": i})
    events += [{"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 30}}, {"type": "message_stop"}]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    """Asked a question, it opens lecture 3; given the lecture, it answers citing the first transcript locator."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"input_tokens": 100}')
                return
            main = INSTRUCTIONS[:40] in json.dumps(body.get("system", ""))
            if main:
                requests.append(body)
            user = [m for m in body["messages"] if m["role"] == "user"][-1]["content"]
            results = [b for b in user if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(user, list) else []
            n = len(requests)
            if not main:
                out = _sse(f"msg_side{n}", [{"type": "text", "text": "ok"}], "end_turn")
            elif not results:
                out = _sse(f"msg_{n}", [{"type": "text", "text": "Let me open the lecture."},
                                        {"type": "tool_use", "id": f"toolu_{n}", "name": "mcp__studyhub__get_lecture",
                                         "input": {"course": "CS 231N", "number": 3}}], "tool_use")
            else:
                loc = re.search(r"\[(r\d+@\d+:\d+)\]", json.dumps(results[0]["content"])).group(1)
                out = _sse(f"msg_{n}", [{"type": "text", "text": f"Lecture 3 covered softmax [[{loc}]]."}], "end_turn")
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(out)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))  # sessions land here, not in ~/.claude
    monkeypatch.delenv("CLAUDECODE", raising=False)
    real_build = subscription.build_options

    def build(**kw):
        options = real_build(**kw)
        options.env.update({"ANTHROPIC_API_KEY": "sk-ant-test",
                            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
                            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        return options

    monkeypatch.setattr(subscription, "build_options", build)
    yield requests
    server.shutdown()


def test_real_claude_code_round_trip(demo, fake_api):
    # A long lecture: more than Claude Code shows inline by default (it would save it to a file).
    rows = demo.execute("SELECT c.id FROM chunks c JOIN resources r ON r.id = c.resource_id JOIN lectures l"
                        " ON l.id = r.lecture_id WHERE l.number = 3 AND r.kind = 'transcript' ORDER BY seq").fetchall()
    for r in rows:
        demo.execute("UPDATE chunks SET text = text || ? WHERE id = ?", (" words" * (90_000 // 6 // len(rows)), r["id"]))
    demo.execute("UPDATE chunks SET text = text || ' END-OF-LECTURE' WHERE id = ?", (rows[-1]["id"],))
    demo.commit()

    events = list(run_chat_subscription(demo, "Summarize lecture 3"))
    names = [e for e, _ in events]
    assert "error" not in names, [d for e, d in events if e == "error"]
    assert [d["status"] for e, d in events if e == "tool"] == ["running", "done"]
    streamed = "".join(d["delta"] for e, d in events if e == "text")
    stored = demo.execute("SELECT text, citations_json FROM messages WHERE role = 'assistant'").fetchone()
    assert streamed == stored["text"] and streamed.startswith("Let me open the lecture.\n\nLecture 3 covered softmax [[r")
    assert len(json.loads(stored["citations_json"])) == 1

    first, second = fake_api
    assert sorted(t["name"] for t in first["tools"]) == sorted(
        f"mcp__studyhub__{t}" for t in ("search", "read", "get_lecture", "list_assignments", "get_feedback",
                                        "list_resources", "list_announcements", "sync_now"))
    assert (first["model"], first["thinking"]["type"], first["output_config"]["effort"]) == \
        ("claude-opus-5", "adaptive", "medium")
    tool_result = next(b for m in second["messages"] if isinstance(m["content"], list)
                       for b in m["content"] if b.get("type") == "tool_result")
    assert "END-OF-LECTURE" in json.dumps(tool_result["content"])  # the whole lecture, inline

    thread = demo.execute("SELECT id, agent_session_id FROM threads").fetchone()
    assert thread["agent_session_id"]
    list(run_chat_subscription(demo, "And the SVM loss?", thread_id=thread["id"]))
    follow_up = fake_api[2]
    said = json.dumps(follow_up["messages"])
    assert "Summarize lecture 3" in said and "And the SVM loss?" in said and "<earlier_conversation>" not in said
    assert demo.execute("SELECT agent_session_id FROM threads").fetchone()[0] == thread["agent_session_id"]
