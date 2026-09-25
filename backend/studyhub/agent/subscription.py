"""The chat loop on a Claude subscription: Claude Code, through the Agent SDK, runs the conversation.

It yields the same events as the API loop (`chat.run_chat`). StudyHub's tools are served to Claude
Code in-process over MCP. Everything else Claude Code can do is switched off: its built-in tools
(shell, files, web), user and project settings, hooks, CLAUDE.md, skills and other MCP servers.
Course content is untrusted text, and this keeps it away from anything but the read-only tools.

Claude Code keeps the conversation in its own session files; a follow-up resumes the thread's
session. When there is none (an older thread, or one the API answered last), the new session is
given the earlier messages as text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from ..config import Settings, get_settings
from .chat import MAX_STEPS, Event, Turn, begin_turn, finish_turn
from .prompt import INSTRUCTIONS
from .tools import Toolbox, ToolError, _schema

log = logging.getLogger("studyhub.subscription")

SERVER = "studyhub"
# Claude Code moves big tool results into a file (which this setup can't read) and caps MCP output
# at 25k tokens. The tools already cap their own output (a whole lecture is at most ~120k characters).
MAX_RESULT_CHARS = 250_000
MAX_OUTPUT_TOKENS = "100000"

_END = object()


def tool_name(name: str) -> str:
    return f"mcp__{SERVER}__{name}"


# ---------------------------------------------------------------- is Claude Code logged in?

def cli_path() -> str | None:
    """The Claude Code the SDK runs: its bundled copy, else one on PATH."""
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(bundled) if bundled.is_file() else shutil.which("claude")


# Set over the inherited environment. The API key is blanked so Claude Code uses its own login, not
# a key that happens to be exported. The refresh flags come from a parent Claude Code (StudyHub
# started from its terminal) and would make this one wait for that parent to refresh the login.
CLI_ENV = {"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH": "", "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH": ""}


def _cli_env() -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if k != "CLAUDECODE"}, **CLI_ENV}


_login: dict[str, Any] = {"at": float("-inf"), "status": None}


def claude_login(max_age: float = 60.0) -> dict | None:
    """Claude Code's `auth status` when it's logged in, else None. Cached: it starts a process."""
    if time.monotonic() - _login["at"] < max_age:
        return _login["status"]
    status = None
    cli = cli_path()
    if cli:
        try:
            out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=20,
                                 env=_cli_env())
            status = json.loads(out.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            log.info("Couldn't ask Claude Code whether it's logged in: %s", e)
    status = status if isinstance(status, dict) and status.get("loggedIn") else None
    _login.update(at=time.monotonic(), status=status)
    return status


def forget_login() -> None:
    _login["at"] = float("-inf")


LOGIN_HELP = "Run “claude auth login” in a terminal to log in with your Claude account, then try again."


# ---------------------------------------------------------------- options

def build_options(*, system: str, cwd: Path, model: str, effort: str, server: Any | None,
                  tools: list[str], resume: str | None = None, max_turns: int = MAX_STEPS,
                  stderr: Callable[[str], None] | None = None):
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        system_prompt=system,                 # replaces Claude Code's own system prompt
        tools=[],                             # no built-in tools: no shell, files or web
        mcp_servers={SERVER: server} if server else {},
        strict_mcp_config=True,               # and no MCP servers but ours
        allowed_tools=[tool_name(t) for t in tools],
        permission_mode="dontAsk",            # anything not allowed above is denied, never asked about
        setting_sources=[],                   # ignore ~/.claude and project settings, hooks, CLAUDE.md
        skills=[],
        verbatim_prompts=True,                # "@/path" in a question stays text; it never reads a file
        cwd=str(cwd),
        model=model,
        thinking={"type": "adaptive"},
        effort=effort,
        max_turns=max_turns,
        include_partial_messages=True,
        resume=resume,
        env={**CLI_ENV, "MAX_MCP_OUTPUT_TOKENS": MAX_OUTPUT_TOKENS},
        stderr=stderr,
    )


def agent_dir(settings: Settings) -> Path:
    """Claude Code's working directory: empty, so there's no CLAUDE.md or project config to find."""
    path = settings.data_dir / "agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_exists(session_id: str, cwd: Path) -> bool:
    from claude_agent_sdk import get_session_info

    try:
        return get_session_info(session_id, directory=str(cwd)) is not None
    except Exception:
        return False


def _earlier_messages(conn, turn: Turn) -> str:
    rows = conn.execute("SELECT role, text FROM messages WHERE thread_id = ? AND id < ? ORDER BY id",
                        (turn.thread_id, turn.message_id)).fetchall()
    if not rows:
        return ""
    said = "\n".join(f"<{r['role']}>\n{r['text']}\n</{r['role']}>" for r in rows)
    return f"<earlier_conversation>\n{said}\n</earlier_conversation>\n\n"


# ---------------------------------------------------------------- one run

def _describe_reset(resets_at: int | None) -> str:
    if not resets_at:
        return ""
    tz = get_settings().tz
    when = datetime.fromtimestamp(resets_at, tz)
    fmt = "%-I:%M %p" if when.date() == datetime.now(tz).date() else "%a %b %-d, %-I:%M %p"
    return f" It resets at {when.strftime(fmt)}."


class _Run:
    """State for one answer. The SDK side runs on its own thread and event loop; events come back
    through a queue. Tool handlers use the SQLite connection from worker threads, one at a time."""

    def __init__(self, toolbox: Toolbox | None, citations: dict[str, dict], also_allowed: frozenset = frozenset()):
        self.toolbox = toolbox
        self.citations = citations
        self.also_allowed = also_allowed  # Claude Code's own tools this run may see, e.g. StructuredOutput
        self.events: queue.Queue = queue.Queue()
        self.db_lock = threading.Lock()
        self.texts: dict[str, str] = {}   # assistant message id -> its text, in order
        self.tool_log: list[dict] = []
        self.usage: dict[str, int] = {}
        self.model: str | None = None
        self.stop_reason: str | None = None
        self.session_id: str | None = None
        self.structured: Any = None
        self.acted = False                # Claude wrote or called something, so the session is worth keeping
        self.error: str | None = None
        self.error_kind: str | None = None
        self.stderr: deque[str] = deque(maxlen=50)
        self._limit_resets: int | None = None
        self._streaming_id: str | None = None
        self._streamed: set[str] = set()
        self._calls = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    def emit(self, event: str, data: dict) -> None:
        self.events.put((event, data))

    def fail(self, message: str, kind: str) -> None:
        if self.error is None:
            self.error, self.error_kind = message, kind

    # ------------------------------------------------------------ tools

    def sdk_tools(self) -> list:
        from claude_agent_sdk import ToolAnnotations, tool

        return [
            tool(t.name, t.description, _schema(t.model),
                 annotations=ToolAnnotations(readOnlyHint=t.name != "sync_now",
                                             maxResultSizeChars=MAX_RESULT_CHARS))(self._handler(t.name))
            for t in self.toolbox.tools
        ]

    def _handler(self, name: str):
        async def handle(args: dict) -> dict:
            return await asyncio.to_thread(self.call_tool, name, args)
        return handle

    def call_tool(self, name: str, raw: Any) -> dict:
        self.acted = True
        try:
            args = self.toolbox.parse(name, raw)
        except ToolError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        with self.db_lock:
            self._calls += 1
            call_id = f"call_{self._calls}"
            label = self.toolbox.label(name, args)
            self.emit("tool", {"id": call_id, "name": name, "label": label, "status": "running"})
            result = self.toolbox.run(name, args)
            self.emit("tool", {"id": call_id, "name": name, "label": label,
                               "status": "error" if result.is_error else "done", "summary": result.summary})
            self.tool_log.append({"name": name, "label": label, "summary": result.summary})
            fresh = {k: v for k, v in result.citations.items() if k not in self.citations}
            if fresh:
                self.citations.update(fresh)
                self.emit("sources", {"citations": fresh})
        return {"content": [{"type": "text", "text": result.text}], "is_error": result.is_error}

    # ------------------------------------------------------------ the SDK side

    def drive(self, query: Callable, prompt: str, options: Any) -> None:
        try:
            asyncio.run(self._consume(query, prompt, options))
        except BaseException as e:  # never leave the reader waiting
            self.fail(f"Claude Code stopped unexpectedly ({e.__class__.__name__}: {e}).", "api")
        finally:
            self.events.put(_END)

    def cancel(self) -> None:
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)

    async def _consume(self, query: Callable, prompt: str, options: Any) -> None:
        from claude_agent_sdk import CLINotFoundError

        self._loop, self._task = asyncio.get_running_loop(), asyncio.current_task()
        try:
            async for message in query(prompt=prompt, options=options):
                if not self.handle(message):
                    break
        except asyncio.CancelledError:
            self.fail("Stopped.", "api")
        except CLINotFoundError:
            self.fail("Claude Code isn't installed, so chat can't use your Claude subscription. "
                      "Reinstall StudyHub's dependencies (`make setup`) or add an API key.", "setup")
        except Exception as e:
            # The SDK raises after an error result; that result already said what went wrong.
            detail = self.stderr[-1] if self.stderr else str(e)
            self.fail(f"Claude Code stopped unexpectedly: {detail}", "api")

    def handle(self, message: Any) -> bool:
        """Take one SDK message. False stops the run."""
        from claude_agent_sdk import (AssistantMessage, RateLimitEvent, ResultMessage, StreamEvent,
                                      SystemMessage, TextBlock, ToolUseBlock)

        if isinstance(message, StreamEvent):
            event = message.event
            if message.parent_tool_use_id:
                return True
            if event.get("type") == "message_start":
                self._streaming_id = (event.get("message") or {}).get("id")
            elif event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
                self._stream(self._streaming_id or "", event["delta"]["text"])
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            offered = message.data.get("tools") or []
            extra = sorted(t for t in offered if not t.startswith(f"mcp__{SERVER}__") and t not in self.also_allowed)
            if extra:
                log.error("Claude Code offered tools besides StudyHub's: %s", extra)
                self.fail("Chat stopped: Claude Code offered tools besides StudyHub's own "
                          f"({', '.join(extra)}). This needs a fix in StudyHub.", "setup")
                return False
        elif isinstance(message, AssistantMessage):
            if message.parent_tool_use_id:
                return True
            if message.error:
                self._assistant_error(message.error, message.content)
                return True
            self.model = message.model
            msg_id = message.message_id or self._streaming_id or f"m{len(self.texts)}"
            text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
            if text:
                self.acted = True
                self.texts[msg_id] = self.texts.get(msg_id, "") + text
                if msg_id not in self._streamed:
                    self._stream(msg_id, text)
            if any(isinstance(b, ToolUseBlock) for b in message.content):
                self.acted = True
        elif isinstance(message, RateLimitEvent):
            if message.rate_limit_info.status == "rejected":
                self._limit_resets = message.rate_limit_info.resets_at
        elif isinstance(message, ResultMessage):
            self.session_id = message.session_id
            self.stop_reason = message.stop_reason
            self.structured = message.structured_output
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                self.usage[key] = (message.usage or {}).get(key, 0) or 0
            if message.subtype == "error_max_turns":
                self.fail("Stopped after too many tool calls. Try a narrower question.", "steps")
            elif message.is_error:
                if message.api_error_status == 429 or self._limit_resets:
                    self._assistant_error("rate_limit", [])
                self.fail(message.result or "; ".join(message.errors or []) or "Claude Code reported an error.",
                          "api")
            elif message.stop_reason == "refusal":
                self.fail("Claude declined to answer this one.", "refusal")
        return True

    def _assistant_error(self, error: str, content: list) -> None:
        detail = "".join(getattr(b, "text", "") for b in content).strip()
        if error == "authentication_failed":
            forget_login()
            self.fail(f"Claude Code isn't logged in, or its login expired. {LOGIN_HELP}", "auth")
        elif error == "rate_limit":
            self.fail("You've reached your Claude plan's usage limit." + _describe_reset(self._limit_resets),
                      "limit")
        elif error == "billing_error":
            self.fail(f"Claude reported a billing problem with this account. {detail}".strip(), "api")
        else:
            self.fail(detail or f"Claude Code reported an error ({error}).", "api")

    def _stream(self, msg_id: str, delta: str) -> None:
        if msg_id not in self._streamed:
            if self._streamed:
                self.emit("text", {"delta": "\n\n"})
            self._streamed.add(msg_id)
        self.emit("text", {"delta": delta})


def ask_json(prompt: str, schema: dict, *, effort: str = "low", query: Callable | None = None) -> Any:
    """One question answered as JSON matching `schema`, with no tools (schedule import)."""
    if query is None:
        from claude_agent_sdk import query

    settings = get_settings()
    options = build_options(system="Answer with the requested JSON.", cwd=agent_dir(settings), model=settings.model,
                            effort=effort, server=None, tools=[], max_turns=3)  # the answer itself is a tool call
    options.output_format = {"type": "json_schema", "schema": schema}
    options.include_partial_messages = False
    run = _Run(None, {}, also_allowed=frozenset({"StructuredOutput"}))
    run.drive(query, prompt, options)
    if run.error:
        raise RuntimeError(run.error)
    if run.structured is None:
        raise RuntimeError("Claude didn't return an answer in the requested format.")
    return run.structured


def check_login(settings: Settings) -> tuple[bool, str]:
    """(ok, message): one short question through the same locked-down setup chat uses."""
    from claude_agent_sdk import query

    status = claude_login(max_age=0)
    if not status:
        return False, f"Claude Code isn't logged in. {LOGIN_HELP}"
    run = _Run(None, {})
    run.drive(query, "Reply with one word: ready", build_options(
        system="Reply in one word.", cwd=agent_dir(settings), model=settings.model, effort="low", server=None,
        tools=[], max_turns=1))
    if run.error:
        return False, run.error
    plan = status.get("subscriptionType")
    return True, (f"Logged in to Claude Code{f' ({plan} plan)' if plan else ''}. "
                  f"Chat runs on your subscription with {run.model or settings.model}.")


def run_chat_subscription(
    conn,
    message: str,
    *,
    thread_id: int | None = None,
    scope: dict | None = None,
    run_sync: Callable[[str], dict] | None = None,
    effort: str = "medium",
    query: Callable | None = None,
) -> Iterator[Event]:
    """`query` stands in for the SDK's `query` in tests."""
    if query is None:
        from claude_agent_sdk import query

    settings = get_settings()
    turn = begin_turn(conn, message, thread_id, scope)
    yield "thread", {"thread_id": turn.thread_id}
    if turn.citations:
        yield "sources", {"citations": dict(turn.citations)}

    cwd = agent_dir(settings)
    thread = conn.execute("SELECT backend, agent_session_id FROM threads WHERE id = ?", (turn.thread_id,)).fetchone()
    session = thread["agent_session_id"] if thread["backend"] == "subscription" else None
    resume = session if session and _session_exists(session, cwd) else None
    prompt = turn.prompt if resume else _earlier_messages(conn, turn) + turn.prompt

    run = _Run(Toolbox(conn, run_sync), turn.citations)
    from claude_agent_sdk import create_sdk_mcp_server

    server = create_sdk_mcp_server(SERVER, tools=run.sdk_tools())
    options = build_options(system=f"{INSTRUCTIONS}\n\n{turn.course_map}", cwd=cwd, model=settings.model,
                            effort=effort, server=server, tools=[t.name for t in run.toolbox.tools],
                            resume=resume, stderr=run.stderr.append)
    worker = threading.Thread(target=run.drive, args=(query, prompt, options), daemon=True,
                              name=f"chat-{turn.thread_id}")
    worker.start()
    try:
        while (item := run.events.get()) is not _END:
            yield item
    finally:
        if worker.is_alive():  # the reader went away (tab closed): stop Claude Code
            run.cancel()
        worker.join(15)

    texts = [t.strip() for t in run.texts.values() if t.strip()]
    answer = "\n\n".join(texts)
    if run.session_id:
        log.debug("thread %s: Claude Code session %s", turn.thread_id, run.session_id)
    yield from finish_turn(
        conn, turn, texts, error=run.error, error_kind=run.error_kind,
        # Plain text, so the API can carry on this thread if a key is added later.
        api_turns=[{"role": "assistant", "content": [{"type": "text", "text": answer}]}] if answer else [],
        tool_log=run.tool_log, backend="subscription",
        session_id=run.session_id if run.session_id and run.acted else resume,
        done={"usage": run.usage, "model": run.model, "stop_reason": run.stop_reason},
    )
