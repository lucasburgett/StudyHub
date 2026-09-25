"""Granola through its MCP server (https://mcp.granola.ai/mcp), for plans without the public API.

Sign-in is OAuth's device flow: `studyhub granola login` shows a link and a code, the student
approves it in the browser, and the tokens (with a refresh token) are kept in
backend/.granola-auth.json, next to .env and like it never committed.

The server's tools answer in text meant for a model: folders as JSON, meetings as XML-ish tags,
a transcript as a JSON object after a line of preamble. The transcript has no per-line times,
so each line's time is estimated from how far into the transcript it falls, at a typical
lecture's speaking rate. Resources made here carry meta.approx_times, and their citations say so.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx

from ..config import BACKEND_DIR, get_settings
from ..ingest.text import Utterance, transcript_chunks, transcript_markdown
from ..store import ensure_course, find_course, resource_row, upsert_resource
from ..util import iso, parse_dt
from .base import SyncContext, log

AUTH_URL = "https://mcp-auth.granola.ai/oauth2"
MCP_URL = "https://mcp.granola.ai/mcp"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "openid offline_access"
AUTH_PATH = BACKEND_DIR / ".granola-auth.json"
CHARS_PER_SECOND = 15  # ~150 spoken words a minute
RECENT = timedelta(days=2)  # meetings this new may still be processing, so they're fetched again

Call = Callable[[str, dict], Awaitable[str]]


class NotSignedIn(RuntimeError):
    pass


# ---------------------------------------------------------------- sign-in

def load_auth() -> dict | None:
    try:
        return json.loads(AUTH_PATH.read_text())
    except (OSError, ValueError):
        return None


def _save_auth(data: dict) -> None:
    tmp = AUTH_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)  # it holds a login
    tmp.replace(AUTH_PATH)


def signed_in() -> bool:
    return bool((load_auth() or {}).get("refresh_token") or (load_auth() or {}).get("access_token"))


def logout() -> None:
    AUTH_PATH.unlink(missing_ok=True)


@dataclass
class DeviceLogin:
    client_id: str
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int
    expires_in: int


def _json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"error": f"HTTP {resp.status_code}"}


def start_login(http: httpx.Client) -> DeviceLogin:
    """Register StudyHub with Granola (once) and ask for a device code for the student to approve."""
    client_id = (load_auth() or {}).get("client_id")
    if not client_id:
        resp = http.post(f"{AUTH_URL}/register", json={
            "client_name": "StudyHub", "grant_types": [DEVICE_GRANT, "refresh_token"],
            "token_endpoint_auth_method": "none",
            # Required by Granola's registration, though the device flow never redirects.
            "redirect_uris": ["http://127.0.0.1/callback"], "response_types": ["code"],
        })
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Granola refused to register StudyHub: {_json(resp).get('error_description') or resp.status_code}")
        client_id = resp.json()["client_id"]
        _save_auth({"client_id": client_id})
    resp = http.post(f"{AUTH_URL}/device_authorization",
                     data={"client_id": client_id, "scope": SCOPE, "resource": MCP_URL})
    body = _json(resp)
    if resp.status_code != 200:
        raise RuntimeError(f"Granola didn't start the sign-in: {body.get('error_description') or body.get('error')}")
    return DeviceLogin(client_id, body["device_code"], body["user_code"], body["verification_uri"],
                       body.get("verification_uri_complete") or body["verification_uri"],
                       int(body.get("interval", 5)), int(body.get("expires_in", 300)))


def _store_tokens(client_id: str, body: dict, old: dict | None = None) -> None:
    _save_auth({
        "client_id": client_id,
        "access_token": body["access_token"],
        # Granola may rotate the refresh token; keep the old one if it doesn't send a new one.
        "refresh_token": body.get("refresh_token") or (old or {}).get("refresh_token"),
        "expires_at": time.time() + int(body.get("expires_in", 3600)),
    })


def finish_login(login: DeviceLogin, http: httpx.Client, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """Wait for the student to approve in the browser, then keep the tokens."""
    interval = login.interval
    deadline = time.monotonic() + login.expires_in
    while time.monotonic() < deadline:
        sleep(interval)
        resp = http.post(f"{AUTH_URL}/token", data={"grant_type": DEVICE_GRANT, "device_code": login.device_code,
                                                   "client_id": login.client_id})
        body = _json(resp)
        if resp.status_code == 200:
            _store_tokens(login.client_id, body)
            return
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error == "access_denied":
            raise RuntimeError("Sign-in was declined in the browser.")
        raise RuntimeError(f"Granola sign-in failed: {body.get('error_description') or error}")
    raise RuntimeError("The sign-in code expired before it was approved. Run `studyhub granola login` again.")


def access_token(http: httpx.Client) -> str:
    """A current access token, refreshed when it's about to expire."""
    auth = load_auth() or {}
    if auth.get("access_token") and auth.get("expires_at", 0) - 60 > time.time():
        return auth["access_token"]
    if not auth.get("refresh_token"):
        raise NotSignedIn("StudyHub isn't signed in to Granola. Run `studyhub granola login`.")
    resp = http.post(f"{AUTH_URL}/token", data={"grant_type": "refresh_token", "refresh_token": auth["refresh_token"],
                                               "client_id": auth["client_id"]})
    if resp.status_code != 200:
        raise NotSignedIn("StudyHub's Granola sign-in has expired. Run `studyhub granola login` again.")
    _store_tokens(auth["client_id"], _json(resp), old=auth)
    return load_auth()["access_token"]


# ---------------------------------------------------------------- reading the tools' answers

def parse_folders(text: str) -> list[dict]:
    """list_meeting_folders answers with JSON: {"folders": [{"id", "title", …}]}."""
    data = json.JSONDecoder().raw_decode(text[text.index("{"):])[0] if "{" in text else {}
    return [f for f in data.get("folders") or [] if isinstance(f, dict) and f.get("id")]


_MEETING_RE = re.compile(r"<meeting\s+([^>]*)>(.*?)</meeting>", re.S)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def parse_meetings(text: str) -> list[dict]:
    """list_meetings / get_meetings answer with <meeting id=… title=… date=… url=…> tags; get_meetings
    adds a <summary> of Markdown inside."""
    out = []
    for attrs, body in _MEETING_RE.findall(text):
        m = {k: html.unescape(v) for k, v in _ATTR_RE.findall(attrs)}
        summary = re.search(r"<summary>(.*?)</summary>", body, re.S)
        if summary:
            m["summary"] = html.unescape(summary.group(1)).strip()
        if m.get("id"):
            out.append(m)
    return out


def parse_transcript(text: str) -> dict:
    """get_meeting_transcript answers with a line of preamble, then a JSON object."""
    return json.JSONDecoder().raw_decode(text[text.index("{"):])[0] if "{" in text else {}


def utterances_from_text(transcript: str) -> list[Utterance]:
    """"Microphone: …" blocks, timed by how far into the transcript they start."""
    out, chars = [], 0
    for block in re.split(r"\n\s*\n", transcript or ""):
        block = block.strip()
        if not block:
            continue
        source, sep, said = block.partition(":")
        text = said.strip() if sep and len(source) <= 40 else block
        if text:
            out.append(Utterance(seconds=chars // CHARS_PER_SECOND, text=text))
        chars += len(text) + 1
    return out


def _meeting_time(meeting: dict) -> datetime | None:
    """"Sep 24, 2026 1:24 PM PDT" -> an aware datetime, read in the configured time zone."""
    raw = re.sub(r"\s+[A-Z]{2,5}$", "", (meeting.get("date") or "").strip())
    try:
        local = datetime.strptime(raw, "%b %d, %Y %I:%M %p")
    except ValueError:
        return None
    return local.replace(tzinfo=get_settings().tz).astimezone(timezone.utc)


# ---------------------------------------------------------------- sync

async def sync_meetings(ctx: SyncContext, call: Call, *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    folders = parse_folders(await call("list_meeting_folders", {}))
    canvas = ctx.conn.execute("SELECT 1 FROM courses WHERE canvas_id IS NOT NULL").fetchone()
    matched = 0
    for folder in folders:
        name = folder.get("title") or folder.get("name") or ""
        # With Canvas connected, Canvas decides which classes exist: folders for past classes stay out.
        course_id = find_course(ctx.conn, name) if canvas else ensure_course(ctx.conn, name)
        if course_id is None:
            continue
        matched += 1
        ctx.conn.execute("UPDATE courses SET granola_folder_id = ? WHERE id = ?", (folder["id"], course_id))
        ctx.mark(course_id, changed=False)
        listed = parse_meetings(await call("list_meetings", {"folder_id": folder["id"], "time_range": "last_30_days"}))
        wanted = []
        for m in listed:
            started = _meeting_time(m)
            existing = resource_row(ctx.conn, "granola", m["id"])
            if existing is None or (started and now - started < RECENT):
                wanted.append(m)
        summaries = {}
        for i in range(0, len(wanted), 10):  # get_meetings takes up to ten
            detail = await call("get_meetings", {"meeting_ids": [m["id"] for m in wanted[i:i + 10]]})
            summaries.update({m["id"]: m.get("summary") for m in parse_meetings(detail)})
        for m in wanted:
            try:
                await _meeting(ctx, call, m, summaries.get(m["id"]), course_id)
            except Exception as e:  # one recording shouldn't stop the rest
                log.warning("granola meeting %s failed: %s", m["id"], e)
                ctx.warn(f"Skipped Granola recording “{m.get('title')}” ({e}).")
        ctx.conn.commit()
    if folders and not matched:
        ctx.warn("No Granola folder matches a class you're taking. Record each class into a folder named "
                 "after its course code, like “MATH 115”.")


async def _meeting(ctx: SyncContext, call: Call, m: dict, summary: str | None, course_id: int) -> None:
    data = parse_transcript(await call("get_meeting_transcript", {"meeting_id": m["id"]}))
    utterances = utterances_from_text(data.get("transcript") or "")
    existing = resource_row(ctx.conn, "granola", m["id"])
    if not utterances and existing is not None:
        return  # transcript deleted upstream: keep our copy
    chunks = transcript_chunks(utterances)
    duration = utterances[-1].seconds if utterances else 0
    started = parse_dt(data.get("created_at")) or _meeting_time(m)
    _, changed = upsert_resource(
        ctx.conn, course_id=course_id, source="granola", kind="transcript", external_id=m["id"],
        title=data.get("title") or m.get("title") or "Lecture recording", url=m.get("url"),
        occurred_at=iso(started), summary=summary, markdown=transcript_markdown(summary, chunks),
        duration_min=round(duration / 60) if duration else None, chunks=chunks,
        meta={"approx_times": True, "via": "mcp"},
    )
    ctx.mark(course_id, changed)


async def _with_session(fn: Callable[[Call], Awaitable[Any]], token: str) -> Any:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http, \
            streamable_http_client(MCP_URL, http_client=http) as (read, write), \
            ClientSession(read, write) as session:
        await session.initialize()

        async def call(tool: str, args: dict) -> str:
            result = await session.call_tool(tool, args)
            text = "\n".join(getattr(c, "text", "") for c in result.content)
            if getattr(result, "is_error", False) or getattr(result, "isError", False):
                raise RuntimeError(f"Granola's {tool} failed: {text[:200]}")
            return text

        return await fn(call)


def run(fn: Callable[[Call], Awaitable[Any]]) -> Any:
    """Run `fn(call)` against Granola's MCP server with StudyHub's own sign-in."""
    with httpx.Client(timeout=30) as http:
        token = access_token(http)
    return asyncio.run(_with_session(fn, token))


def check() -> str:
    async def folders(call: Call) -> list[dict]:
        return parse_folders(await call("list_meeting_folders", {}))

    found = run(folders)
    names = ", ".join(f.get("title") or "?" for f in found) or "none"
    return f"Signed in to Granola. {len(found)} folders: {names}."


def sync(ctx: SyncContext) -> None:
    run(lambda call: sync_meetings(ctx, call))
