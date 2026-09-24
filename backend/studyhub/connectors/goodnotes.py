"""GoodNotes, through the PDFs that GoodNotes Auto Backup writes to cloud storage.

Point GOODNOTES_DIR at the local copy of the backup folder (for example Google Drive for
Desktop). Each notebook is matched to a course by a course code in its folder or file
name, so keep one GoodNotes folder per class ("CS 231N").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from ..ingest.text import pdf_chunks, pdf_markdown, read_pdf
from ..store import ensure_course, resource_row, save_file, upsert_resource
from ..util import course_codes, iso
from .base import SyncContext


def _root(settings: Settings) -> Path:
    root = Path(settings.goodnotes_dir).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"GOODNOTES_DIR does not exist: {root}")
    return root


class GoodNotesConnector:
    source = "goodnotes"

    def check(self, settings: Settings) -> str:
        root = _root(settings)
        pdfs = sorted(root.rglob("*.pdf"))
        matched = sum(1 for p in pdfs if course_codes(p.relative_to(root).as_posix()))
        return f"Found {len(pdfs)} notebook PDFs in {root}; {matched} are in a folder or file named after a course."

    def sync(self, ctx: SyncContext) -> None:
        root = _root(ctx.settings)
        seen: set[str] = set()
        unmatched: list[str] = []
        for path in sorted(root.rglob("*.pdf")):
            rel = path.relative_to(root).as_posix()
            course_id = ensure_course(ctx.conn, rel)
            if course_id is None:
                unmatched.append(rel)
                continue
            seen.add(rel)
            stat = path.stat()
            meta = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
            existing = resource_row(ctx.conn, self.source, rel)
            if existing and existing["course_id"] == course_id and existing["meta_json"] and \
                    _same(existing["meta_json"], meta):
                ctx.mark(course_id, changed=False)
                continue
            data = path.read_bytes()
            try:
                pages = read_pdf(data, hash_images=True)
            except Exception as e:  # a half-written or corrupt export
                ctx.warn(f"Couldn't read {rel}: {e}")
                continue
            _, changed = upsert_resource(
                ctx.conn, course_id=course_id, source=self.source, kind="notes", external_id=rel,
                title=path.stem, occurred_at=iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
                file_path=save_file(data), page_count=len(pages), markdown=pdf_markdown(pages),
                chunks=pdf_chunks(pages), meta=meta,
            )
            ctx.mark(course_id, changed)
            ctx.conn.commit()

        for row in ctx.conn.execute(
            "SELECT id, course_id, external_id FROM resources WHERE source = ?", (self.source,)
        ).fetchall():
            if row["external_id"] not in seen:
                ctx.conn.execute("DELETE FROM resources WHERE id = ?", (row["id"],))
                ctx.mark(row["course_id"])
        if unmatched:
            shown = ", ".join(unmatched[:3]) + ("…" if len(unmatched) > 3 else "")
            ctx.warn(f"{len(unmatched)} notebooks aren't in a folder named after a course code: {shown}")


def _same(meta_json: str, meta: dict) -> bool:
    old = json.loads(meta_json)
    return old.get("mtime_ns") == meta["mtime_ns"] and old.get("size") == meta["size"]
