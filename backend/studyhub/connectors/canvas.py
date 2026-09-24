"""Canvas LMS via its REST API and a personal access token."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from ..config import Settings
from ..ingest.text import html_to_markdown, markdown_chunks, pdf_chunks, pdf_markdown, read_pdf
from ..store import (
    delete_missing,
    ensure_course,
    resource_row,
    save_file,
    upsert_assignment,
    upsert_resource,
)
from ..util import course_codes, iso, lecture_number, local_date
from .base import SyncContext, log

MAX_PDF_BYTES = 60 * 1024 * 1024
_SLIDES_RE = re.compile(r"\b(lec(ture)?s?|slides?|deck)\b|lec(ture)?[\s_\-]*\d", re.IGNORECASE)


class CanvasError(RuntimeError):
    pass


class CanvasClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.site = base_url.rstrip("/")
        self.http = httpx.Client(
            base_url=f"{self.site}/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0, read=120.0),
            follow_redirects=True,
            transport=transport,
        )

    def _check(self, resp: httpx.Response) -> None:
        # Canvas also answers 401 for "you can't see this item"; only a bad token stops the sync.
        if resp.status_code == 401 and ("invalid access token" in resp.text.lower()
                                        or "www-authenticate" in resp.headers):
            raise CanvasError("Canvas rejected the access token (401). Create a new one under Account → Settings.")
        resp.raise_for_status()

    def get(self, path: str, params: list[tuple[str, Any]] | None = None) -> Any:
        resp = self.http.get(path, params=params)
        self._check(resp)
        return resp.json()

    def paginate(self, path: str, params: list[tuple[str, Any]] | None = None) -> Iterator[dict]:
        url: str | None = path
        query: list[tuple[str, Any]] | None = [*(params or []), ("per_page", 100)]
        while url:
            resp = self.http.get(url, params=query)
            self._check(resp)
            data = resp.json()
            yield from data if isinstance(data, list) else [data]
            url = resp.links.get("next", {}).get("url")
            query = None  # the next link already carries the query

    def optional(self, path: str, params: list[tuple[str, Any]] | None = None) -> list[dict] | None:
        """Paginate, but return None when the course has that tool turned off for students."""
        try:
            return list(self.paginate(path, params))
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                return None
            raise

    def download(self, url: str) -> bytes:
        # httpx drops the Authorization header when Canvas redirects to its file store.
        resp = self.http.get(url)
        self._check(resp)
        return resp.content


def _course_title(name: str) -> str:
    title = name
    for display, key in course_codes(name):
        title = re.sub(rf"{re.escape(display)}|{re.escape(key)}", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"\((autumn|fall|winter|spring|summer)[^)]*\)", " ", title, flags=re.IGNORECASE)
    title = re.sub(r"^[\s:\-–|/,]+|[\s:\-–|/,]+$", "", re.sub(r"\s+", " ", title))
    return title or name


def _updated(row) -> str | None:
    return json.loads(row["meta_json"] or "{}").get("updated_at")


def _status(assignment: dict) -> tuple[str, float | None]:
    sub = assignment.get("submission") or {}
    state = sub.get("workflow_state")
    if state == "graded" and sub.get("score") is not None:
        return "graded", sub.get("score")
    if state in ("submitted", "pending_review") or sub.get("submitted_at"):
        return "submitted", None
    if sub.get("missing"):
        return "missing", None
    due = assignment.get("due_at")
    if due is None or due > datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"):
        return "upcoming", None
    return "unknown", None


class CanvasConnector:
    source = "canvas"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def client(self, settings: Settings) -> CanvasClient:
        return CanvasClient(settings.canvas_base_url, settings.canvas_token, self.transport)

    def check(self, settings: Settings) -> str:
        api = self.client(settings)
        me = api.get("/users/self")
        courses = [c for c in api.paginate("/courses", [("enrollment_state", "active")]) if c.get("name")]
        names = ", ".join(c.get("course_code") or c["name"] for c in courses) or "none"
        return f"Signed in as {me.get('name', '?')}. {len(courses)} active courses: {names}."

    def sync(self, ctx: SyncContext) -> None:
        api = self.client(ctx.settings)
        courses = api.paginate(
            "/courses",
            [("enrollment_state", "active"), ("include[]", "term"), ("include[]", "syllabus_body")],
        )
        for c in courses:
            if c.get("access_restricted_by_date") or not c.get("name"):
                continue
            label = f"{c.get('course_code') or ''} {c['name']}"
            term = c.get("term") or {}
            course_id = ensure_course(
                ctx.conn,
                label,
                title=_course_title(c["name"]),
                term=term.get("name"),
                term_start=local_date(term.get("start_at") or c.get("start_at"), ctx.settings.tz),
                canvas_id=str(c["id"]),
                canvas_url=f"{api.site}/courses/{c['id']}",
            )
            if course_id is None:
                log.info("Skipping Canvas course without a course code: %s", label)
                continue
            ctx.mark(course_id, changed=False)
            self._syllabus(ctx, api, c, course_id)
            self._assignments(ctx, api, c["id"], course_id)
            self._files(ctx, api, c["id"], course_id)
            self._pages(ctx, api, c["id"], course_id)
            self._announcements(ctx, api, c["id"], course_id)
            ctx.conn.commit()

    # ------------------------------------------------------------ pieces

    def _syllabus(self, ctx: SyncContext, api: CanvasClient, course: dict, course_id: int) -> None:
        md = html_to_markdown(course.get("syllabus_body"))
        if not md:
            return
        _, changed = upsert_resource(
            ctx.conn, course_id=course_id, source=self.source, kind="page",
            external_id=f"syllabus:{course['id']}", title="Syllabus",
            url=f"{api.site}/courses/{course['id']}/assignments/syllabus",
            markdown=md, chunks=markdown_chunks(md),
        )
        ctx.mark(course_id, changed)

    def _assignments(self, ctx: SyncContext, api: CanvasClient, cid: int, course_id: int) -> None:
        items = api.optional(f"/courses/{cid}/assignments", [("include[]", "submission")]) or []
        for a in items:
            spec_id = None
            md = html_to_markdown(a.get("description"))
            if md:
                spec_id, changed = upsert_resource(
                    ctx.conn, course_id=course_id, source=self.source, kind="spec",
                    external_id=f"assignment:{a['id']}", title=a["name"], url=a.get("html_url"),
                    occurred_at=iso(a.get("due_at") or a.get("created_at")),
                    markdown=md, chunks=markdown_chunks(md),
                )
                ctx.mark(course_id, changed)
            status, score = _status(a)
            _, changed = upsert_assignment(
                ctx.conn, course_id=course_id, source=self.source, external_id=str(a["id"]),
                title=a["name"], due_at=iso(a.get("due_at")), points=a.get("points_possible"),
                score=score, status=status, url=a.get("html_url"), spec_resource_id=spec_id,
            )
            ctx.mark(course_id, changed)

    def _files(self, ctx: SyncContext, api: CanvasClient, cid: int, course_id: int) -> None:
        module_of: dict[int, str] = {}
        modules = api.optional(f"/courses/{cid}/modules", [("include[]", "items")])
        for m in modules or []:
            for item in m.get("items") or []:
                if item.get("type") == "File" and item.get("content_id"):
                    module_of[item["content_id"]] = m.get("name") or ""

        listed = api.optional(f"/courses/{cid}/files")
        files = {f["id"]: f for f in listed or []}
        for fid in module_of.keys() - files.keys():
            for path in (f"/courses/{cid}/files/{fid}", f"/files/{fid}"):
                try:
                    files[fid] = api.get(path)
                    break
                except httpx.HTTPStatusError:
                    continue
        if modules is None and listed is None:
            return  # both hidden: leave what we already have

        seen: set[str] = set()
        for f in files.values():
            external_id = f"file:{f['id']}"
            seen.add(external_id)
            self._file(ctx, api, cid, course_id, f, module_of.get(f["id"]), external_id)
        delete_missing(ctx.conn, self.source, course_id, ("slides", "file"), seen)

    def _file(self, ctx: SyncContext, api: CanvasClient, cid: int, course_id: int, f: dict,
              module: str | None, external_id: str) -> None:
        title = f.get("display_name") or f.get("filename") or f"File {f['id']}"
        meta = {"updated_at": f.get("updated_at"), "size": f.get("size"), "module": module}
        existing = resource_row(ctx.conn, self.source, external_id)
        if existing and existing["course_id"] == course_id and _updated(existing) == f.get("updated_at"):
            if json.loads(existing["meta_json"]).get("module") != module:
                ctx.conn.execute("UPDATE resources SET meta_json = ? WHERE id = ?",
                                 (json.dumps(meta, sort_keys=True), existing["id"]))
            return  # file unchanged since the last download
        is_pdf = f.get("content-type") == "application/pdf" or title.lower().endswith(".pdf")
        slides = bool(_SLIDES_RE.search(title)) or lecture_number(module) is not None
        kind = "slides" if slides and is_pdf else "file"
        common = dict(
            course_id=course_id, source=self.source, kind=kind, external_id=external_id, title=title,
            url=f"{api.site}/courses/{cid}/files/{f['id']}", occurred_at=iso(f.get("created_at")), meta=meta,
        )
        if not is_pdf or not f.get("url") or (f.get("size") or 0) > MAX_PDF_BYTES:
            _, changed = upsert_resource(ctx.conn, **common)
            ctx.mark(course_id, changed)
            return
        try:
            data = api.download(f["url"])
        except httpx.HTTPStatusError as e:
            ctx.warn(f"Couldn't download {title} ({e.response.status_code}); it's listed without its text.")
            _, changed = upsert_resource(ctx.conn, **common)
            ctx.mark(course_id, changed)
            return
        pages = read_pdf(data)
        _, changed = upsert_resource(
            ctx.conn, **common, file_path=save_file(data), page_count=len(pages),
            markdown=pdf_markdown(pages), chunks=pdf_chunks(pages),
        )
        ctx.mark(course_id, changed)

    def _pages(self, ctx: SyncContext, api: CanvasClient, cid: int, course_id: int) -> None:
        pages = api.optional(f"/courses/{cid}/pages")
        if pages is None:
            return
        seen: set[str] = {f"syllabus:{cid}"}
        for p in pages:
            external_id = f"page:{cid}:{p['url']}"
            seen.add(external_id)
            existing = resource_row(ctx.conn, self.source, external_id)
            if existing and existing["course_id"] == course_id and _updated(existing) == p.get("updated_at"):
                continue
            body = api.get(f"/courses/{cid}/pages/{p['url']}").get("body")
            md = html_to_markdown(body)
            _, changed = upsert_resource(
                ctx.conn, course_id=course_id, source=self.source, kind="page", external_id=external_id,
                title=p.get("title") or p["url"], url=p.get("html_url"), occurred_at=iso(p.get("updated_at")),
                markdown=md, chunks=markdown_chunks(md), meta={"updated_at": p.get("updated_at")},
            )
            ctx.mark(course_id, changed)
        delete_missing(ctx.conn, self.source, course_id, ("page",), seen)

    def _announcements(self, ctx: SyncContext, api: CanvasClient, cid: int, course_id: int) -> None:
        topics = api.optional(f"/courses/{cid}/discussion_topics", [("only_announcements", "true")]) or []
        for d in topics:
            md = html_to_markdown(d.get("message"))
            _, changed = upsert_resource(
                ctx.conn, course_id=course_id, source=self.source, kind="announcement",
                external_id=f"announcement:{d['id']}", title=d.get("title") or "Announcement",
                url=d.get("html_url"), occurred_at=iso(d.get("posted_at")),
                markdown=md, chunks=markdown_chunks(md),
            )
            ctx.mark(course_id, changed)
