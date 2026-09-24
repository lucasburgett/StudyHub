import httpx
import pymupdf

from studyhub.config import get_settings
from studyhub.connectors.base import SyncContext
from studyhub.connectors.web import WebConnector, parse_links
from studyhub.sync import rebuild_course

# Modeled on a Stanford CS course schedule page: a table of dated rows with slide links.
PAGE = """
<html><head><title>CS231n: Deep Learning for Computer Vision</title></head><body>
<h2>Schedule</h2>
<table>
<tr><th>Date</th><th>Description</th><th>Course Materials</th></tr>
<tr><td>Sep 22</td><td><b>Lecture 1: Introduction</b><br>Computer vision overview
  [<a href="slides/2026/lecture_1_part_1.pdf">slides 1</a>] [<a href="slides/2026/lecture_1_part_2.pdf">slides 2</a>]</td><td></td></tr>
<tr><td>Sep 24</td><td><b>Lecture 2: Image Classification with Linear Classifiers</b><br>The data-driven approach
  [<a href="slides/2026/lecture_2.pdf">slides</a>]</td><td><a href="notes/linear.html">Linear classification</a></td></tr>
<tr><td>Sep 25</td><td>Python / Numpy Review Session<br>[<a href="https://colab.example.com/numpy.ipynb">Colab</a>]
            [<a href="https://docs.google.com/presentation/d/abc123/edit">slides</a>]
  <td>12:30-1:20pm PT</td></td></tr>
<tr><td>Sep 29</td><td><b>Lecture 3: Regularization and Optimization</b> [<a href="https://docs.google.com/presentation/d/private9/edit">slides</a>]</td></tr>
</table>
<p><a href="https://example.org/syllabus.pdf">Syllabus (PDF)</a></p>
</body></html>
"""


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class FakeSite:
    def __init__(self):
        self.gets: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET":
            self.gets.append(url)
        if url == "https://cs231n.example.edu/schedule.html":
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
        if url.endswith(".pdf") or url.endswith("/abc123/export/pdf"):
            name = url.rsplit("/", 1)[-1]
            return httpx.Response(200, content=_pdf(f"Content of {name}"), headers={"etag": f'"{name}-v1"'})
        if "private9" in url:
            return httpx.Response(200, text="<html>Sign in</html>")
        return httpx.Response(404)


def test_parse_links():
    links = parse_links(PAGE, "https://cs231n.example.edu/schedule.html", 2026)
    by_url = {link.url: link for link in links}
    l1 = by_url["https://cs231n.example.edu/slides/2026/lecture_1_part_1.pdf"]
    assert (l1.number, l1.day.isoformat(), l1.title) == (1, "2026-09-22", "Lecture 1: Introduction (slides 1)")
    l2 = by_url["https://cs231n.example.edu/slides/2026/lecture_2.pdf"]
    assert (l2.number, l2.title) == (2, "Lecture 2: Image Classification with Linear Classifiers")
    review = by_url["https://docs.google.com/presentation/d/abc123/export/pdf"]
    assert review.number is None and review.page_url.endswith("/abc123/edit")
    assert (review.title, review.day.isoformat()) == ("Python / Numpy Review Session", "2026-09-25")
    assert "https://cs231n.example.edu/notes/linear.html" not in by_url  # not a document


def test_web_sync(conn, monkeypatch):
    monkeypatch.setenv("COURSE_SITES", "CS 231N=https://cs231n.example.edu/schedule.html")
    get_settings.cache_clear()
    fake = FakeSite()
    connector = WebConnector(transport=httpx.MockTransport(fake))

    def sync():
        ctx = SyncContext(conn=conn, settings=get_settings())
        connector.sync(ctx)
        for cid in ctx.touched_courses:
            rebuild_course(conn, cid)
        return ctx

    sync()
    course = conn.execute("SELECT * FROM courses").fetchone()
    assert course["site_url"] == "https://cs231n.example.edu/schedule.html"
    rows = {r["title"]: r for r in conn.execute("SELECT * FROM resources")}
    assert rows["Lecture 2: Image Classification with Linear Classifiers"]["page_count"] == 1
    assert rows["Lecture 3: Regularization and Optimization"]["file_path"] is None  # private deck: link only
    assert rows["CS231n: Deep Learning for Computer Vision"]["kind"] == "page"
    lectures = {r["number"]: r["date"] for r in conn.execute("SELECT number, date FROM lectures")}
    assert lectures == {1: "2026-09-22", 2: "2026-09-24", 3: "2026-09-29"}  # dated by the schedule rows

    # Unchanged files aren't downloaded again.
    before = len([u for u in fake.gets if u.endswith(".pdf")])
    sync()
    assert len([u for u in fake.gets if u.endswith(".pdf")]) == before


def test_sites_setting(monkeypatch):
    monkeypatch.setenv("COURSE_SITES", "CS 231N=https://a.edu/s.html?x=1; MATH 51 = https://b.edu ;junk")
    get_settings.cache_clear()
    assert get_settings().sites == [("CS 231N", "https://a.edu/s.html?x=1"), ("MATH 51", "https://b.edu")]
    assert get_settings().configured("web")
