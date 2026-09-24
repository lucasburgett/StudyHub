import pytest
from fastapi.testclient import TestClient

from studyhub.api import app


@pytest.fixture
def client(demo):
    return TestClient(app)


def test_status_and_courses(client):
    status = client.get("/api/status").json()
    assert status["demo"] is True and status["agent_ready"] is False
    assert [s["source"] for s in status["sources"]] == ["canvas", "gradescope", "goodnotes", "granola"]
    courses = client.get("/api/courses").json()
    assert courses[0]["code"] == "CS 231N"
    assert courses[0]["counts"]["assignments"] == 3


def test_timeline(client):
    weeks = client.get("/api/courses/1/timeline").json()["weeks"]
    assert [w["label"] for w in weeks][:3] == ["Week 1", "Week 2", "Week 3"]
    l1 = weeks[0]["lectures"][0]
    assert l1["number"] == 1 and "recording" in l1["missing"]
    notes = [r for r in l1["resources"] if r["kind"] == "notes"]
    assert notes and notes[0]["pages"] == [1, 1]


def test_resource_detail_and_file(client):
    resources = client.get("/api/courses/1/resources?kind=transcript").json()
    assert resources and all(r["kind"] == "transcript" for r in resources)
    detail = client.get(f"/api/resources/{resources[0]['id']}").json()
    assert detail["segments"][0]["label"] == "0:00" and detail["file_url"] is None
    slides = client.get("/api/courses/1/resources?kind=slides").json()[0]
    detail = client.get(f"/api/resources/{slides['id']}").json()
    assert detail["pages"] and detail["file_url"].endswith("/file")
    pdf = client.get(detail["file_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-disposition"].startswith("inline")


def test_assignment_detail(client):
    assignments = client.get("/api/courses/1/assignments").json()
    quiz = next(a for a in assignments if a["title"] == "Quiz 1")
    detail = client.get(f"/api/assignments/{quiz['id']}").json()
    assert detail["feedback"][1]["rubric_items"] == ["Used log base 10 instead of the natural log (-1.5)"]
    a1 = next(a for a in assignments if a["title"] == "Assignment 1")
    assert "two-layer" in client.get(f"/api/assignments/{a1['id']}").json()["description"]


def test_search_highlights(client):
    hits = client.get("/api/search", params={"q": "cross entrop"}).json()
    assert hits and "<mark>" in hits[0]["snippet"]
    assert {"locator", "label", "page", "seconds"} <= hits[0].keys()


def test_chat_needs_a_key(client):
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 400 and "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_sync_rejects_unconfigured_source(client):
    assert client.post("/api/sync", json={"source": "canvas"}).status_code == 400
