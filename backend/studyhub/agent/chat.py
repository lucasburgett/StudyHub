"""The chat loop: stream Claude's answer, run tools, persist the thread.

`run_chat` yields (event, data) pairs that the API forwards as server-sent events:
thread, tool, sources, text, done, error (see docs/API.md).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import anthropic

from ..citations import markers
from ..config import get_settings
from ..db import now_iso
from .prompt import INSTRUCTIONS, context_block, course_map
from .tools import Toolbox, ToolError

log = logging.getLogger("studyhub.chat")

MAX_STEPS = 12
MAX_TOKENS = 32000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_DROP_BEFORE_FALLBACK = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}

Event = tuple[str, dict[str, Any]]


def make_client() -> anthropic.Anthropic:
    key = get_settings().anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or None
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def echo_content(blocks: list[dict]) -> list[dict]:
    """Assistant content to send back next turn.

    After a mid-answer server-side fallback, blocks that the declined model produced before the
    fallback marker (other than text) must not be echoed; the marker itself is only an audit record.
    """
    cut = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=None)
    if cut is None:
        return blocks
    return [b for i, b in enumerate(blocks)
            if b.get("type") != "fallback" and not (i < cut and b.get("type") in _DROP_BEFORE_FALLBACK)]


def _history(conn: sqlite3.Connection, thread_id: int) -> list[dict]:
    turns: list[dict] = []
    for row in conn.execute("SELECT api_json FROM messages WHERE thread_id = ? ORDER BY id", (thread_id,)):
        turns += json.loads(row["api_json"])
    return turns


def _complete_turns(turns: list[dict]) -> list[dict]:
    """Drop a trailing assistant turn whose tool calls never got results (an interrupted step)."""
    if turns and turns[-1]["role"] == "assistant" and any(
        b.get("type") == "tool_use" for b in turns[-1]["content"]
    ):
        return turns[:-1]
    return turns


def _new_thread(conn: sqlite3.Connection, message: str, scope: dict) -> int:
    title = " ".join(message.split())
    title = title if len(title) <= 60 else title[:59].rstrip() + "…"
    now = now_iso()
    return conn.execute(
        "INSERT INTO threads(title, scope_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (title, json.dumps(scope), now, now),
    ).lastrowid


@dataclass
class Turn:
    """One question in a thread, as both chat loops see it."""

    thread_id: int
    message_id: int             # the stored user message
    prompt: str                 # what Claude gets: the context block, then the question
    course_map: str
    citations: dict[str, dict]  # locators Claude may cite so far
    map_citations: dict[str, dict] = field(default_factory=dict)


def begin_turn(conn: sqlite3.Connection, message: str, thread_id: int | None, scope: dict | None) -> Turn:
    """Find or start the thread, and store the question."""
    scope = {k: v for k, v in (scope or {}).items() if v is not None}
    if thread_id is None or not conn.execute("SELECT 1 FROM threads WHERE id = ?", (thread_id,)).fetchone():
        thread_id = _new_thread(conn, message, scope)
    conn.commit()
    citations: dict[str, dict] = {}
    map_citations: dict[str, dict] = {}
    cmap = course_map(conn, map_citations)
    prompt = f"{context_block(conn, scope, citations)}\n\n{message}"
    now = now_iso()
    message_id = conn.execute(
        "INSERT INTO messages(thread_id, role, text, api_json, created_at) VALUES (?, 'user', ?, ?, ?)",
        (thread_id, message, json.dumps([{"role": "user", "content": prompt}]), now),
    ).lastrowid
    conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, thread_id))
    conn.commit()
    return Turn(thread_id, message_id, prompt, cmap, citations, map_citations)


def finish_turn(
    conn: sqlite3.Connection,
    turn: Turn,
    texts: list[str],
    *,
    error: str | None,
    error_kind: str | None,
    api_turns: list[dict],
    tool_log: list[dict],
    backend: str,
    done: dict[str, Any],
    session_id: str | None = None,
) -> Iterator[Event]:
    """Store the answer (with a note if it failed) and send the closing events."""
    text = "\n\n".join(texts)
    # Locators the model took from the course map are valid too.
    late = {loc: turn.map_citations[loc] for loc in markers(text)
            if loc not in turn.citations and loc in turn.map_citations}
    if late:
        turn.citations.update(late)
        yield "sources", {"citations": late}

    if error:
        note = f"_{error}_"
        yield "text", {"delta": ("\n\n" if text else "") + note}
        text = f"{text}\n\n{note}" if text else note
    message_id = conn.execute(
        "INSERT INTO messages(thread_id, role, text, api_json, tools_json, citations_json, created_at)"
        " VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
        (turn.thread_id, text, json.dumps(api_turns), json.dumps(tool_log),
         json.dumps({k: v for k, v in turn.citations.items() if k in set(markers(text))}), now_iso()),
    ).lastrowid
    # An API turn isn't in any Claude Code session, so it ends the thread's session.
    conn.execute("UPDATE threads SET updated_at = ?, backend = ?, agent_session_id = ? WHERE id = ?",
                 (now_iso(), backend, session_id, turn.thread_id))
    conn.commit()
    if error:
        yield "error", {"message": error, "kind": error_kind}
    yield "done", {"message_id": message_id, **done}


def run_chat(
    conn: sqlite3.Connection,
    message: str,
    *,
    thread_id: int | None = None,
    scope: dict | None = None,
    client: anthropic.Anthropic | None = None,
    run_sync: Callable[[str], dict] | None = None,
    effort: str = "medium",
    backend: str | None = None,
) -> Iterator[Event]:
    """Answer one message. A `client` means the API; otherwise `backend`, else the configured backend."""
    if client is None and (backend or get_settings().agent_backend) == "subscription":
        from .subscription import run_chat_subscription

        return run_chat_subscription(conn, message, thread_id=thread_id, scope=scope, run_sync=run_sync,
                                     effort=effort)
    return _run_chat_api(conn, message, thread_id=thread_id, scope=scope, client=client, run_sync=run_sync,
                         effort=effort)


def _run_chat_api(
    conn: sqlite3.Connection,
    message: str,
    *,
    thread_id: int | None,
    scope: dict | None,
    client: anthropic.Anthropic | None,
    run_sync: Callable[[str], dict] | None,
    effort: str,
) -> Iterator[Event]:
    settings = get_settings()
    turn = begin_turn(conn, message, thread_id, scope)
    yield "thread", {"thread_id": turn.thread_id}
    citations = turn.citations
    if citations:
        yield "sources", {"citations": dict(citations)}
    system = [
        {"type": "text", "text": INSTRUCTIONS},
        {"type": "text", "text": turn.course_map, "cache_control": {"type": "ephemeral"}},
    ]

    history = _history(conn, turn.thread_id)  # includes the user turn just stored
    toolbox = Toolbox(conn, run_sync)
    tools = toolbox.definitions()
    client = client or make_client()
    new_turns: list[dict] = []
    texts: list[str] = []
    tool_log: list[dict] = []
    usage: dict[str, int] = {}
    error: str | None = None
    error_kind: str | None = None  # api | refusal | truncated | steps | tool_json
    served_model: str | None = None
    stop_reason: str | None = None
    json_retries = 0

    step = 0
    while step < MAX_STEPS:
        step += 1
        streamed_text = False
        try:
            with client.beta.messages.stream(
                model=settings.model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=tools,
                messages=history + new_turns,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        if not streamed_text and texts:
                            yield "text", {"delta": "\n\n"}
                        streamed_text = True
                        yield "text", {"delta": event.text}
                final = stream.get_final_message()
            json_retries = 0
        except ValueError as e:
            # Tool input JSON the SDK couldn't parse at all (eager input streaming). Re-issue the step.
            json_retries += 1
            if json_retries > 2:
                error, error_kind = f"The model produced unreadable tool input ({e}).", "tool_json"
                break
            step -= 1
            continue
        except anthropic.APIError as e:
            error, error_kind = getattr(e, "message", None) or str(e), "api"
            log.error("Claude API error: %s", error)
            break

        served_model, stop_reason = final.model, final.stop_reason
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[key] = usage.get(key, 0) + (getattr(final.usage, key, 0) or 0)

        content = echo_content([b.to_dict(exclude_none=True) for b in final.content])
        new_turns.append({"role": "assistant", "content": content})
        step_text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if step_text:
            texts.append(step_text)

        if final.stop_reason == "refusal":
            error, error_kind = "Claude declined to answer this one.", "refusal"
            break
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if final.stop_reason == "pause_turn":
            continue
        if not tool_uses:
            break
        if final.stop_reason == "max_tokens":
            new_turns.pop()
            error, error_kind = "The answer ran too long and was cut off.", "truncated"
            break

        results = []
        for tu in tool_uses:
            try:
                args = toolbox.parse(tu["name"], tu.get("input"))
            except ToolError as e:
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": str(e), "is_error": True})
                continue
            label = toolbox.label(tu["name"], args)
            yield "tool", {"id": tu["id"], "name": tu["name"], "label": label, "status": "running"}
            result = toolbox.run(tu["name"], args)
            status = "error" if result.is_error else "done"
            yield "tool", {"id": tu["id"], "name": tu["name"], "label": label, "status": status,
                           "summary": result.summary}
            tool_log.append({"name": tu["name"], "label": label, "summary": result.summary})
            fresh = {k: v for k, v in result.citations.items() if k not in citations}
            if fresh:
                citations.update(fresh)
                yield "sources", {"citations": fresh}
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result.text,
                            "is_error": result.is_error})
        new_turns.append({"role": "user", "content": results})
    else:
        error, error_kind = "Stopped after too many tool calls. Try a narrower question.", "steps"

    yield from finish_turn(conn, turn, texts, error=error, error_kind=error_kind,
                           api_turns=_complete_turns(new_turns), tool_log=tool_log, backend="api",
                           done={"usage": usage, "model": served_model, "stop_reason": stop_reason})
