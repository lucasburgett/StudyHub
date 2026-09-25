"""Granola lecture recordings through the public API (https://public-api.granola.ai/v1).

API keys come from Granola → Settings → Connectors → API keys (Business plan). Without a key,
the connector uses Granola's MCP server instead (granola_mcp.py, `studyhub granola login`). Each
class's recordings should live in a Granola folder named after the course code.

StudyHub keeps its own copy of every transcript. Granola can auto-delete transcripts
after a retention period, and when that happens upstream this connector keeps the copy
it already has instead of overwriting it with nothing.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from ..config import Settings
from ..ingest.text import transcript_chunks, transcript_markdown, utterances_from_granola
from ..store import ensure_course, find_course, resource_row, upsert_resource
from ..util import iso
from .base import SyncContext

API_URL = "https://public-api.granola.ai/v1"


class GranolaClient:
    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(
            base_url=API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(30.0, read=120.0),
            transport=transport,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(6):
            resp = self.http.get(path, params=params)
            if resp.status_code != 429:
                break
            time.sleep(float(resp.headers.get("retry-after") or 2 ** attempt))
        if resp.status_code == 401:
            raise RuntimeError("Granola rejected the API key (401). Create one under Settings → Connectors → API keys.")
        time.sleep(0.2)  # stay well under 5 requests/second
        return resp

    def pages(self, path: str, key: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        params = {**(params or {}), "page_size": 30}
        while True:
            resp = self.get(path, params)
            resp.raise_for_status()
            data = resp.json()
            yield from data.get(key) or []
            if not data.get("hasMore") or not data.get("cursor"):
                return
            params = {**params, "cursor": data["cursor"]}

    def note(self, note_id: str) -> dict | None:
        resp = self.get(f"/notes/{note_id}", {"include": "transcript"})
        if resp.status_code == 404:
            return None  # still processing, or never summarized
        if resp.status_code == 413:
            note = self.get(f"/notes/{note_id}").json()
            note["transcript"] = self.transcript(note_id)
            return note
        resp.raise_for_status()
        return resp.json()

    def transcript(self, note_id: str) -> list[dict]:
        items: list[dict] = []
        cursor = None
        while True:
            resp = self.get(f"/notes/{note_id}/transcript", {"cursor": cursor} if cursor else None)
            resp.raise_for_status()
            data = resp.json()
            items += data.get("transcript") or data.get("items") or data.get("data") or []
            cursor = data.get("cursor")
            if not data.get("hasMore") or not cursor:
                return items


class GranolaConnector:
    source = "granola"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def check(self, settings: Settings) -> str:
        if not settings.granola_api_key:  # plans without the public API sign in through Granola's MCP server
            from . import granola_mcp

            return granola_mcp.check()
        api = GranolaClient(settings.granola_api_key, self.transport)
        folders = list(api.pages("/folders", "folders"))
        names = ", ".join(f["name"] for f in folders) or "none"
        return f"API key works. {len(folders)} folders: {names}."

    def sync(self, ctx: SyncContext) -> None:
        if not ctx.settings.granola_api_key:
            from . import granola_mcp

            return granola_mcp.sync(ctx)
        api = GranolaClient(ctx.settings.granola_api_key, self.transport)
        folders = list(api.pages("/folders", "folders"))
        matched = 0
        # With Canvas connected, Canvas decides which classes exist: folders for past classes stay out.
        canvas = ctx.conn.execute("SELECT 1 FROM courses WHERE canvas_id IS NOT NULL").fetchone()
        for folder in folders:
            course_id = find_course(ctx.conn, folder["name"]) if canvas else ensure_course(ctx.conn, folder["name"])
            if course_id is None:
                continue
            ctx.conn.execute("UPDATE courses SET granola_folder_id = ? WHERE id = ?", (folder["id"], course_id))
            matched += 1
            ctx.mark(course_id, changed=False)
            for summary in api.pages("/notes", "notes", {"folder_id": folder["id"]}):
                try:
                    self._note(ctx, api, summary, course_id)
                except httpx.HTTPStatusError as e:
                    ctx.warn(f"Skipped Granola note “{summary.get('title')}” ({e.response.status_code}).")
            ctx.conn.commit()
        if folders and not matched:
            ctx.warn("No Granola folder matches a class you're taking. Record each class into a folder named "
                     "after its course code, like “MATH 115”.")

    def _note(self, ctx: SyncContext, api: GranolaClient, summary: dict, course_id: int) -> None:
        existing = resource_row(ctx.conn, self.source, summary["id"])
        if existing and json.loads(existing["meta_json"]).get("updated_at") == summary.get("updated_at"):
            return
        note = api.note(summary["id"])
        if note is None:
            return
        utterances, started, duration = utterances_from_granola(note.get("transcript") or [])
        if not utterances and existing is not None:
            return  # transcript deleted upstream: keep our copy
        chunks = transcript_chunks(utterances)
        text_summary = note.get("summary_markdown") or note.get("summary_text")
        event = note.get("calendar_event") or {}
        title = note.get("title") or event.get("event_title") or "Lecture recording"
        _, changed = upsert_resource(
            ctx.conn, course_id=course_id, source=self.source, kind="transcript", external_id=note["id"],
            title=title, url=note.get("web_url"),
            occurred_at=iso(event.get("scheduled_start_time") or started or note.get("created_at")),
            summary=text_summary, markdown=transcript_markdown(text_summary, chunks),
            duration_min=round(duration / 60) if duration else None, chunks=chunks,
            meta={"updated_at": note.get("updated_at")},
        )
        ctx.mark(course_id, changed)
