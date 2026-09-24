"""Course websites: many CS courses post slides on their own site instead of Canvas.

List sites in COURSE_SITES (`CS 231N=https://cs231n.stanford.edu/schedule.html`). Each sync
reads that page, indexes it, and downloads the slide decks it links to (PDFs, and public
Google Slides exported as PDF). A link's table row or list item says which lecture it
belongs to ("Lecture 2: …", "Apr 02"), which is how the slides join the right lecture.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from ..config import Settings
from ..ingest.text import html_to_markdown, markdown_chunks, pdf_chunks, pdf_markdown, read_pdf
from ..store import delete_missing, ensure_course, resource_row, save_file, upsert_resource
from ..util import find_date, iso, lecture_number
from .base import SyncContext

MAX_PDF_BYTES = 60 * 1024 * 1024
MAX_LINKS = 200
_GOOGLE_SLIDES_RE = re.compile(r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]+)")
_LECTURE_TITLE_RE = re.compile(r"\blec(?:ture)?\s*\d{1,2}\s*[:.\-–]\s*(.+)", re.IGNORECASE)


@dataclass
class SiteLink:
    url: str          # what to download
    page_url: str     # what to show as "open original"
    title: str
    number: int | None
    day: date | None


def _row(a: Tag) -> Tag:
    return a.find_parent(["tr", "li"]) or a.parent or a


def _row_title(row: Tag, context: str) -> str | None:
    """The lecture's name: a bold "Lecture 2: …" when the page has one, else the row text after it."""
    for tag in row.find_all(["b", "strong", "h3", "h4", "span"]):
        text = tag.get_text(" ", strip=True)
        if _LECTURE_TITLE_RE.search(text) and len(text) < 120:
            return text
    m = _LECTURE_TITLE_RE.search(context)
    if not m:
        return None
    rest = re.split(r"\s*\[|\s{2,}", m.group(0))[0]
    return rest[:90].rstrip()


def _cell_name(a: Tag) -> str | None:
    """A non-lecture row's name ("Backprop Review Session"): its cell's text before the first [link]."""
    cell = a.find_parent(["td", "li"]) or a.parent or a
    name = " ".join(cell.get_text(" ", strip=True).split("[", 1)[0].split())
    return name[:90].rstrip() or None


def parse_links(html: str, base_url: str, year: int) -> list[SiteLink]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[SiteLink] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if not href.startswith(("http://", "https://")):
            continue
        google = _GOOGLE_SLIDES_RE.match(href)
        is_pdf = urlparse(href).path.lower().endswith(".pdf")
        if not (is_pdf or google):
            continue
        download = f"https://docs.google.com/presentation/d/{google.group(1)}/export/pdf" if google else href
        if download in seen:
            continue
        seen.add(download)
        row = _row(a)
        context = row.get_text(" ", strip=True)
        link_text = a.get_text(" ", strip=True)
        number = lecture_number(context)
        base_title = _row_title(row, context) if number is not None else None
        if base_title:
            label = link_text.strip("[] ")
            title = base_title if label.lower() in ("slides", "slide", "pdf", "") else f"{base_title} ({label})"
        else:
            title = link_text.strip("[] ") or urlparse(href).path.rsplit("/", 1)[-1]
            if title.lower() in ("slides", "slide", "pdf"):
                title = _cell_name(a) or f"{' '.join(context.split())[:60]} ({title})"
        links.append(SiteLink(url=download, page_url=href, title=title, number=number,
                              day=find_date(context, year)))
        if len(links) >= MAX_LINKS:
            break
    return links


def _signature(resp: httpx.Response) -> dict:
    return {k: resp.headers.get(k) for k in ("etag", "last-modified", "content-length") if resp.headers.get(k)}


class WebConnector:
    source = "web"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def client(self) -> httpx.Client:
        return httpx.Client(
            follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0), transport=self.transport,
            headers={"User-Agent": "StudyHub/0.1 (personal study tool; fetches course pages occasionally)"},
        )

    def check(self, settings: Settings) -> str:
        results = []
        with self.client() as http:
            for code, url in settings.sites:
                resp = http.get(url)
                resp.raise_for_status()
                n = len(parse_links(resp.text, str(resp.url), date.today().year))
                results.append(f"{code}: {n} documents linked")
        return "; ".join(results)

    def sync(self, ctx: SyncContext) -> None:
        with self.client() as http:
            for code, url in ctx.settings.sites:
                course_id = ensure_course(ctx.conn, code, site_url=url)
                if course_id is None:
                    ctx.warn(f"COURSE_SITES: “{code}” isn't a course code.")
                    continue
                ctx.mark(course_id, changed=False)
                try:
                    self._site(ctx, http, course_id, url)
                except httpx.HTTPError as e:
                    ctx.warn(f"{code}: couldn't read {url} ({e}).")
                ctx.conn.commit()

    def _site(self, ctx: SyncContext, http: httpx.Client, course_id: int, url: str) -> None:
        resp = http.get(url)
        resp.raise_for_status()
        html = resp.text
        course = ctx.conn.execute("SELECT term_start FROM courses WHERE id = ?", (course_id,)).fetchone()
        year = int(course["term_start"][:4]) if course["term_start"] else date.today().year

        soup = BeautifulSoup(html, "html.parser")
        page_title = soup.title.get_text(strip=True) if soup.title else "Course website"
        md = html_to_markdown(html)
        seen = {url}
        _, changed = upsert_resource(
            ctx.conn, course_id=course_id, source=self.source, kind="page", external_id=url,
            title=page_title, url=url, markdown=md, chunks=markdown_chunks(md),
        )
        ctx.mark(course_id, changed)

        for link in parse_links(html, str(resp.url), year):
            seen.add(link.url)
            self._document(ctx, http, course_id, link)
        delete_missing(ctx.conn, self.source, course_id, ("slides", "file", "page"), seen)

    def _document(self, ctx: SyncContext, http: httpx.Client, course_id: int, link: SiteLink) -> None:
        occurred = iso(datetime.combine(link.day, time(12, 0), tzinfo=ctx.settings.tz)) if link.day else None
        kind = "slides" if link.number is not None else "file"
        # A schedule row's date is the lecture's own date (unlike a Canvas upload date).
        dated = {"lecture_date": link.day.isoformat()} if link.day and link.number is not None else {}
        common = dict(course_id=course_id, source=self.source, kind=kind, external_id=link.url, title=link.title,
                      url=link.page_url, occurred_at=occurred)
        existing = resource_row(ctx.conn, self.source, link.url)
        try:
            head = http.head(link.url)
            signature = _signature(head) if head.status_code == 200 else {}
        except httpx.HTTPError:
            signature = {}
        if existing and signature and json.loads(existing["meta_json"]).get("signature") == signature \
                and existing["course_id"] == course_id and existing["title"] == link.title:
            return  # unchanged since the last download
        try:
            resp = http.get(link.url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            ctx.warn(f"Couldn't download {link.title} ({e}).")
            _, changed = upsert_resource(ctx.conn, **common, meta=dated)
            ctx.mark(course_id, changed)
            return
        data = resp.content
        meta = {"signature": signature or _signature(resp), **dated}
        if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
            _, changed = upsert_resource(ctx.conn, **common, meta=meta)  # e.g. a private Google Slides deck
            ctx.mark(course_id, changed)
            return
        pages = read_pdf(data)
        _, changed = upsert_resource(ctx.conn, **common, file_path=save_file(data), page_count=len(pages),
                                     markdown=pdf_markdown(pages), chunks=pdf_chunks(pages), meta=meta)
        ctx.mark(course_id, changed)
