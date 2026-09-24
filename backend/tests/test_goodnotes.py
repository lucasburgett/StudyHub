import os

import pymupdf

from studyhub.config import get_settings
from studyhub.connectors.base import SyncContext
from studyhub.connectors.goodnotes import GoodNotesConnector
from studyhub.sync import rebuild_course


def _notebook(path, pages):
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 750), text, fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def _sync(conn, root):
    os.environ["GOODNOTES_DIR"] = str(root)  # conftest resets this per test
    get_settings.cache_clear()
    ctx = SyncContext(conn=conn, settings=get_settings())
    GoodNotesConnector().sync(ctx)
    for cid in ctx.touched_courses:
        rebuild_course(conn, cid)
    return ctx


def test_goodnotes_sync_links_pages_by_date(conn, tmp_path):
    root = tmp_path / "GoodNotes"
    _notebook(root / "CS 231N" / "Lecture notes.pdf",
              ["9/22 intro\nvision is hard", "more intro", "9/24 kNN\nL1 vs L2 distance"])
    _notebook(root / "Doodles.pdf", ["no course here"])
    ctx = _sync(conn, root)
    assert ctx.warnings and "Doodles.pdf" in ctx.warnings[0]

    r = conn.execute("SELECT * FROM resources").fetchone()
    assert (r["kind"], r["title"], r["page_count"]) == ("notes", "Lecture notes", 3)
    by_page = {c["page"]: c["date"] for c in conn.execute(
        "SELECT c.page, l.date FROM chunks c JOIN lectures l ON l.id = c.lecture_id")}
    assert by_page == {1: "2026-09-22", 2: "2026-09-22", 3: "2026-09-24"}


def test_goodnotes_unchanged_and_removed(conn, tmp_path):
    root = tmp_path / "GoodNotes"
    path = root / "CS 231N" / "Notes.pdf"
    _notebook(path, ["9/22 intro"])
    _sync(conn, root)
    assert _sync(conn, root).changed == 0
    path.unlink()
    _sync(conn, root)
    assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0


def test_transcriptions_survive_reexport(conn, tmp_path):
    root = tmp_path / "GoodNotes"
    path = root / "CS 231N" / "Notes.pdf"
    _notebook(path, ["9/22 page one", "page two"])
    _sync(conn, root)
    conn.execute("UPDATE chunks SET text = 'transcribed page one', transcribed = 1 WHERE page = 1")
    conn.commit()
    _notebook(path, ["9/22 page one", "page two", "9/24 a new page"])  # re-export with one more page
    os.utime(path, (1, 1))
    _sync(conn, root)
    texts = {c["page"]: (c["text"], c["transcribed"]) for c in conn.execute("SELECT page, text, transcribed FROM chunks")}
    assert texts[1] == ("transcribed page one", 1)
    assert texts[3][1] == 0
