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


def test_transcription_on_the_subscription(conn, tmp_path, monkeypatch):
    """No API key: pages go to Claude Code in batches, as images, and come back as Markdown."""
    from studyhub.agent import subscription
    from studyhub.ingest import handwriting

    root = tmp_path / "GoodNotes"
    _notebook(root / "MATH 115" / "Notes.pdf", [f"page {n}" for n in range(1, 7)])
    _sync(conn, root)
    monkeypatch.setattr(subscription, "claude_login", lambda max_age=60.0: {"loggedIn": True})
    get_settings.cache_clear()
    calls = []

    def ask_json(prompt, schema, *, images=(), effort="low", query=None):
        calls.append((prompt, len(images)))
        n = len(images)
        # Answer out of order, and skip one page, as a model might.
        return {"pages": [{"page": i, "markdown": f"$x_{i}$ transcribed"} for i in range(n, 0, -1) if (n, i) != (2, 2)]}

    monkeypatch.setattr(subscription, "ask_json", ask_json)
    done = handwriting.transcribe_notes(conn)
    assert sorted(n for _, n in calls) == [2, 4]  # six pages, in batches of at most four
    assert all("handwritten" in p and "LaTeX" in p for p, _ in calls)
    rows = {r["page"]: (r["text"], r["transcribed"]) for r in conn.execute("SELECT page, text, transcribed FROM chunks")}
    assert done == 5 and rows[6] == ("page 6", 0)  # the skipped page keeps its text, to retry next time
    assert rows[1] == ("$x_1$ transcribed", 1) and rows[5] == ("$x_1$ transcribed", 1)


def test_past_classes_stay_out_once_canvas_lists_the_current_ones(conn, tmp_path):
    """A GoodNotes library keeps old classes (Math/Old/Math 51/…); only this term's belong in StudyHub."""
    from studyhub.store import ensure_course

    math115 = ensure_course(conn, "F26-MATH-115-01")
    conn.execute("UPDATE courses SET canvas_id = 1 WHERE id = ?", (math115,))
    root = tmp_path / "GoodNotes"
    _notebook(root / "Math" / "Old" / "Math 51" / "Math Hw" / "Week 1.pdf", ["old homework"])
    _notebook(root / "Math" / "Math 115" / "Lecture notes.pdf", ["9/22 intro"])
    _notebook(root / "Random" / "Groceries.pdf", ["eggs"])
    ctx = _sync(conn, root)

    assert [r["code"] for r in conn.execute("SELECT code FROM courses")] == ["MATH 115"]  # no MATH 51
    assert [r["title"] for r in conn.execute("SELECT title FROM resources")] == ["Lecture notes"]
    assert len(ctx.warnings) == 1 and "Groceries.pdf" in ctx.warnings[0] and "Math 51" not in ctx.warnings[0]


def test_new_pages_of_current_classes_are_transcribed_after_a_sync(conn, tmp_path, monkeypatch):
    """Only this term's notebooks: an old class's notes are never stored, so never sent to Claude."""
    from studyhub import sync
    from studyhub.agent import subscription
    from studyhub.ingest import handwriting
    from studyhub.store import ensure_course

    math115 = ensure_course(conn, "F26-MATH-115-01")
    conn.execute("UPDATE courses SET canvas_id = 1 WHERE id = ?", (math115,))
    conn.commit()
    root = tmp_path / "GoodNotes"
    _notebook(root / "Math" / "Old" / "Math 51" / "Week 1.pdf", ["old homework", "more old homework"])
    _notebook(root / "Math" / "Math 115" / "Notes.pdf", ["9/22 intro", "sup and inf"])
    monkeypatch.setenv("GOODNOTES_DIR", str(root))
    monkeypatch.setattr(subscription, "claude_login", lambda max_age=60.0: {"loggedIn": True})
    get_settings.cache_clear()
    sent = []
    monkeypatch.setattr(handwriting, "_transcribe_batch",
                        lambda pngs: sent.append(len(pngs)) or [f"$x$ page {i + 1}" for i in range(len(pngs))])

    assert sync.run_source("goodnotes")["status"] == "ok"
    assert sent == [2]  # MATH 115's two pages; nothing from Math 51
    assert [r["transcribed"] for r in conn.execute("SELECT transcribed FROM chunks ORDER BY page")] == [1, 1]

    # Settings → GoodNotes can turn it off; new pages then wait for `studyhub transcribe`.
    monkeypatch.setenv("STUDYHUB_AUTO_TRANSCRIBE", "false")
    get_settings.cache_clear()
    _notebook(root / "Math" / "Math 115" / "Section.pdf", ["worksheet"])
    assert sync.run_source("goodnotes")["status"] == "ok"
    assert sent == [2]
