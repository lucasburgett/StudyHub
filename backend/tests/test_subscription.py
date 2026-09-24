"""The chat loop on a Claude subscription, against a scripted stand-in for the Agent SDK's `query`.

The stand-in plays SDK messages and calls StudyHub's tool handlers itself, the way Claude Code
would over MCP. Nothing here starts Claude Code or touches the network.
"""

import asyncio
import dataclasses
import json
import threading
import time

import claude_agent_sdk
import jsonschema
import pytest
from claude_agent_sdk import (AssistantMessage, RateLimitEvent, RateLimitInfo, ResultMessage, StreamEvent,
                              SystemMessage, TextBlock, ToolUseBlock)

from studyhub import config
from studyhub.agent import subscription
from studyhub.agent.chat import run_chat
from studyhub.agent.prompt import INSTRUCTIONS
from studyhub.agent.subscription import run_chat_subscription

TOOLS = ["search", "read", "get_lecture", "list_assignments", "get_feedback", "list_resources",
         "list_announcements", "sync_now"]
INIT = SystemMessage("init", {"tools": [f"mcp__studyhub__{t}" for t in TOOLS]})
REAL_LOGIN = subscription.claude_login  # conftest stubs it out for every test


def delta(msg_id, *texts):
    """Stream events for one assistant message's text."""
    events = [StreamEvent("u", "s", {"type": "message_start", "message": {"id": msg_id}})]
    events += [StreamEvent("u", "s", {"type": "content_block_delta", "index": 0,
                                      "delta": {"type": "text_delta", "text": t}}) for t in texts]
    return events


def result(session_id="sess-1", **kw):
    return ResultMessage(subtype=kw.pop("subtype", "success"), duration_ms=10, duration_api_ms=8,
                         is_error=kw.pop("is_error", False), num_turns=2, session_id=session_id,
                         stop_reason=kw.pop("stop_reason", "end_turn"),
                         usage={"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 900}, **kw)


class Script:
    """Stands in for `claude_agent_sdk.query`: records what it was given, then plays `steps`.

    A step is an SDK message to yield, ("call", tool, args) to run a StudyHub tool the way Claude
    Code would, or an exception to raise.
    """

    def __init__(self, *steps):
        self.steps = steps
        self.prompt = self.options = None
        self.results: list[dict] = []
        self.finished = False

    async def __call__(self, *, prompt, options):
        self.prompt, self.options = prompt, options
        server = options.mcp_servers.get("studyhub")
        tools = {t.name: t for t in server["tools"]} if server else {}
        for step in self.steps:
            if isinstance(step, tuple):
                _, name, args = step
                tool = tools[name]
                jsonschema.validate(args, tool.input_schema)  # the SDK checks this before calling
                self.results.append(await tool.handler(args))
            elif isinstance(step, BaseException):
                raise step
            else:
                yield step
        self.finished = True


@pytest.fixture(autouse=True)
def recording_server(monkeypatch):
    """Keep the tool definitions on the server config so the script can call their handlers."""
    real = claude_agent_sdk.create_sdk_mcp_server
    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server",
                        lambda name, version="1.0.0", tools=None: {**real(name, version, tools), "tools": tools})


def transcript_id(demo, number=3):
    return demo.execute("SELECT r.id FROM resources r JOIN lectures l ON l.id = r.lecture_id"
                        " WHERE r.kind = 'transcript' AND l.number = ?", (number,)).fetchone()["id"]


def answer(demo, rec):
    return Script(
        INIT,
        *delta("msg_1", "Let me ", "check."),
        AssistantMessage([TextBlock("Let me check."),
                          ToolUseBlock("tu_1", "mcp__studyhub__search", {"query": "cross entropy initialization"})],
                         "claude-opus-5", message_id="msg_1"),
        ("call", "search", {"query": "cross entropy initialization"}),
        *delta("msg_2", "About $\\ln 10 \\approx 2.3$ ", f"[[r{rec}@41:12]] [[r999]]."),
        AssistantMessage([TextBlock(f"About $\\ln 10 \\approx 2.3$ [[r{rec}@41:12]] [[r999]].")],
                         "claude-opus-5", message_id="msg_2"),
        result(),
    )


def test_answer_streams_runs_tools_and_persists(demo):
    rec = transcript_id(demo)
    script = answer(demo, rec)
    events = list(run_chat_subscription(demo, "What should the loss be at init?", scope={"course_id": 1},
                                        query=script))
    names = [e for e, _ in events]
    assert names[0] == "thread" and names[-1] == "done" and "error" not in names

    streamed = "".join(d["delta"] for e, d in events if e == "text")
    assert streamed == f"Let me check.\n\nAbout $\\ln 10 \\approx 2.3$ [[r{rec}@41:12]] [[r999]]."
    tools = [d for e, d in events if e == "tool"]
    assert [t["status"] for t in tools] == ["running", "done"]
    assert tools[0]["name"] == "search" and tools[0]["id"] == tools[1]["id"]
    assert tools[0]["label"] == "Searched all classes for “cross entropy initialization”"
    sources = {}
    for e, d in events:
        if e == "sources":
            sources.update(d["citations"])
    assert f"r{rec}@41:12" in sources and "r999" not in sources

    # The tool result went back to Claude with its locators.
    [tool_result] = script.results
    assert tool_result["is_error"] is False and f"[r{rec}@41:12]" in tool_result["content"][0]["text"]
    assert script.prompt.startswith("<context>") and script.prompt.endswith("What should the loss be at init?")

    done = events[-1][1]
    assert done["usage"]["input_tokens"] == 120 and done["model"] == "claude-opus-5"
    thread = demo.execute("SELECT * FROM threads").fetchone()
    assert (thread["backend"], thread["agent_session_id"]) == ("subscription", "sess-1")
    stored = demo.execute("SELECT * FROM messages WHERE role = 'assistant'").fetchone()
    assert stored["text"] == f"Let me check.\n\nAbout $\\ln 10 \\approx 2.3$ [[r{rec}@41:12]] [[r999]]."
    assert json.loads(stored["citations_json"]).keys() == {f"r{rec}@41:12"}
    assert json.loads(stored["tools_json"])[0]["summary"].endswith("hits")
    # Kept as plain text, so the API can continue this thread if a key is added later.
    assert json.loads(stored["api_json"]) == [{"role": "assistant", "content": [{"type": "text", "text": stored["text"]}]}]


def test_claude_code_is_locked_down(demo, tmp_path):
    script = answer(demo, transcript_id(demo))
    list(run_chat_subscription(demo, "hi", query=script))
    o = script.options
    assert o.tools == []                        # no Bash, Read, WebFetch, ...
    assert o.setting_sources == [] and o.strict_mcp_config and o.skills == []
    assert o.permission_mode == "dontAsk" and o.verbatim_prompts
    assert sorted(o.allowed_tools) == sorted(f"mcp__studyhub__{t}" for t in TOOLS)
    assert list(o.mcp_servers) == ["studyhub"]
    assert isinstance(o.system_prompt, str) and o.system_prompt.startswith(INSTRUCTIONS)
    assert "# Course map" in o.system_prompt and "CS 231N" in o.system_prompt
    assert o.cwd == str(tmp_path / "data" / "agent") and not list((tmp_path / "data" / "agent").iterdir())
    assert o.env["ANTHROPIC_API_KEY"] == ""     # use the Claude Code login, never an exported key
    assert o.env["CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH"] == ""  # refresh it here, not via a parent Claude Code
    assert int(o.env["MAX_MCP_OUTPUT_TOKENS"]) >= 50_000
    assert (o.model, o.effort, o.max_turns, o.thinking) == ("claude-opus-5", "medium", 12, {"type": "adaptive"})
    assert o.include_partial_messages and o.resume is None

    tools = {t.name: t for t in o.mcp_servers["studyhub"]["tools"]}
    assert set(tools) == set(TOOLS)
    for name, t in tools.items():
        wire = t.annotations.model_dump(by_alias=True)
        assert wire["readOnlyHint"] is (name != "sync_now")
        assert wire["maxResultSizeChars"] >= 200_000   # a whole lecture stays inline
    assert tools["search"].input_schema["required"] == ["query"]

    # And the flags Claude Code actually gets.
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    server = {k: v for k, v in o.mcp_servers["studyhub"].items() if k != "tools"}
    transport = SubprocessCLITransport(prompt="hi", options=dataclasses.replace(o, mcp_servers={"studyhub": server}))
    transport._cli_path = "claude"
    cmd = transport._build_command()
    assert cmd[cmd.index("--tools") + 1] == "" and "--setting-sources=" in cmd and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--resume" not in " ".join(cmd)


def test_follow_up_resumes_the_session(demo, monkeypatch):
    list(run_chat_subscription(demo, "What should the loss be at init?", query=answer(demo, transcript_id(demo))))
    thread_id = demo.execute("SELECT id FROM threads").fetchone()["id"]
    monkeypatch.setattr(subscription, "_session_exists", lambda session_id, cwd: session_id == "sess-1")

    follow = Script(INIT, AssistantMessage([TextBlock("Sure.")], "claude-opus-5", message_id="m3"),
                    result("sess-1"))
    events = list(run_chat_subscription(demo, "Why ln 10?", thread_id=thread_id, query=follow))
    assert events[0][1]["thread_id"] == thread_id
    assert follow.options.resume == "sess-1"
    assert "<earlier_conversation>" not in follow.prompt  # Claude Code already has it


def test_new_session_gets_the_earlier_messages(demo, monkeypatch):
    """No session to resume (it was cleaned up, or the API answered last): replay the thread as text."""
    list(run_chat_subscription(demo, "What should the loss be at init?", query=answer(demo, transcript_id(demo))))
    thread_id = demo.execute("SELECT id FROM threads").fetchone()["id"]
    monkeypatch.setattr(subscription, "_session_exists", lambda session_id, cwd: False)

    follow = Script(INIT, AssistantMessage([TextBlock("Sure.")], "claude-opus-5", message_id="m3"),
                    result("sess-2"))
    list(run_chat_subscription(demo, "Why ln 10?", thread_id=thread_id, query=follow))
    assert follow.options.resume is None
    earlier, _, now = follow.prompt.partition("</earlier_conversation>")
    assert "<user>\nWhat should the loss be at init?\n</user>" in earlier and "<assistant>\nLet me check." in earlier
    assert "Why ln 10?" not in earlier and now.strip().endswith("Why ln 10?")
    assert demo.execute("SELECT agent_session_id FROM threads").fetchone()[0] == "sess-2"


def test_not_logged_in(demo, monkeypatch):
    forgot = []
    monkeypatch.setattr(subscription, "forget_login", lambda: forgot.append(True))
    script = Script(
        INIT,
        AssistantMessage([TextBlock("Failed to authenticate: OAuth session expired")], "<synthetic>",
                         error="authentication_failed"),
        result(is_error=True, stop_reason="stop_sequence", result="Failed to authenticate: OAuth session expired"),
        claude_agent_sdk.ProcessError("Command failed with exit code 1", exit_code=1),
    )
    events = list(run_chat_subscription(demo, "hi", query=script))
    [error] = [d for e, d in events if e == "error"]
    assert error["kind"] == "auth" and "claude auth login" in error["message"]
    assert events[-1][0] == "done" and forgot
    assert "Failed to authenticate" not in "".join(d["delta"] for e, d in events if e == "text")
    # Nothing happened in that session, so there's nothing to resume.
    assert demo.execute("SELECT agent_session_id FROM threads").fetchone()[0] is None


def test_usage_limit(demo):
    resets = int(time.time()) + 3600
    script = Script(
        INIT,
        RateLimitEvent(RateLimitInfo("rejected", resets_at=resets, rate_limit_type="five_hour"), "u", "s"),
        AssistantMessage([TextBlock("You've hit your limit")], "<synthetic>", error="rate_limit"),
        result(is_error=True, api_error_status=429, result="You've hit your limit"),
    )
    events = list(run_chat_subscription(demo, "hi", query=script))
    [error] = [d for e, d in events if e == "error"]
    assert error["kind"] == "limit" and "usage limit" in error["message"] and "resets at" in error["message"]


def test_extra_tools_stop_the_run(demo):
    script = Script(SystemMessage("init", {"tools": ["Bash", "mcp__studyhub__search"]}),
                    AssistantMessage([TextBlock("Running a command")], "claude-opus-5", message_id="m1"),
                    result())
    events = list(run_chat_subscription(demo, "hi", query=script))
    [error] = [d for e, d in events if e == "error"]
    assert error["kind"] == "setup" and "Bash" in error["message"]
    assert not script.finished and "Running a command" not in "".join(d["delta"] for e, d in events if e == "text")


def test_bad_tool_input_goes_back_as_an_error(demo):
    script = Script(INIT, ("call", "read", {"locator": "not-a-locator"}),
                    AssistantMessage([TextBlock("Sorry.")], "claude-opus-5", message_id="m1"), result())
    events = list(run_chat_subscription(demo, "hi", query=script))
    [res] = script.results
    assert res["is_error"] is True
    assert "done" in [e for e, _ in events]


def test_max_turns(demo):
    script = Script(INIT, AssistantMessage([TextBlock("Looking…")], "claude-opus-5", message_id="m1"),
                    result(subtype="error_max_turns", is_error=True, stop_reason="tool_use"))
    events = list(run_chat_subscription(demo, "hi", query=script))
    assert [d["kind"] for e, d in events if e == "error"] == ["steps"]


def test_closing_the_stream_stops_claude_code(demo):
    started, cancelled = threading.Event(), threading.Event()

    async def slow(*, prompt, options):
        yield INIT
        for ev in delta("m1", "Thinking"):
            yield ev
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield result()

    gen = run_chat_subscription(demo, "hi", query=slow)
    for event, _ in gen:
        if event == "text":
            break
    t = time.monotonic()
    gen.close()
    assert cancelled.wait(5) and time.monotonic() - t < 5


def test_run_chat_picks_the_backend(demo, monkeypatch):
    calls = []
    monkeypatch.setattr(subscription, "run_chat_subscription", lambda *a, **kw: calls.append(kw) or iter(()))
    monkeypatch.setattr(subscription, "claude_login", lambda max_age=60.0: {"loggedIn": True})
    config.get_settings.cache_clear()
    list(run_chat(demo, "hi"))
    assert calls and calls[0]["effort"] == "medium"


@pytest.mark.parametrize("mode, key, login, expected", [
    ("auto", True, True, "api"),
    ("auto", False, True, "subscription"),
    ("auto", False, False, None),
    ("api", False, True, None),
    ("subscription", True, True, "subscription"),
    ("subscription", True, False, None),
])
def test_backend_choice(monkeypatch, mode, key, login, expected):
    monkeypatch.setenv("STUDYHUB_AGENT", mode)
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(subscription, "claude_login", lambda max_age=60.0: {"loggedIn": True} if login else None)
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.agent_backend == expected and settings.agent_ready is (expected is not None)
    assert settings.api_key_set is key


def test_login_check_runs_the_cli_once_a_minute(monkeypatch, tmp_path):
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\necho run >> \"$0.log\"\n"
                   "echo '{\"loggedIn\": true, \"authMethod\": \"claude.ai\", \"subscriptionType\": \"max\"}'\n")
    cli.chmod(0o755)
    monkeypatch.setattr(subscription, "cli_path", lambda: str(cli))
    subscription.forget_login()
    assert REAL_LOGIN()["subscriptionType"] == "max"
    assert REAL_LOGIN()["subscriptionType"] == "max"
    assert (tmp_path / "claude.log").read_text().count("run") == 1

    cli.write_text("#!/bin/sh\necho '{\"loggedIn\": false}'\n")
    subscription.forget_login()
    assert REAL_LOGIN() is None
    subscription.forget_login()
