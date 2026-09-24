import os
import stat

import pytest
from fastapi.testclient import TestClient

from studyhub import envfile
from studyhub.api import app


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(envfile, "ENV_PATH", path)
    return path


@pytest.fixture
def client(conn, env):
    return TestClient(app, base_url="http://127.0.0.1:8000")


def _field(page, key):
    return next(f for g in page["groups"] for f in g["fields"] if f["key"] == key)


def test_new_env_starts_from_the_example_and_keeps_comments(env):
    envfile.write_env({"CANVAS_TOKEN": "7~secret-token-1234", "VOYAGE_MODEL": "voyage-4-lite"})
    text = env.read_text()
    assert "# --- Canvas" in text  # comments from .env.example survive
    assert "CANVAS_TOKEN=7~secret-token-1234" in text
    assert "VOYAGE_MODEL=voyage-4-lite" in text and "# VOYAGE_MODEL" not in text  # uncommented in place
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    envfile.write_env({"CANVAS_TOKEN": "new value with spaces"})
    assert envfile.read_env(env)["CANVAS_TOKEN"] == "new value with spaces"
    assert text.count("CANVAS_TOKEN=") == env.read_text().count("CANVAS_TOKEN=")  # replaced, not appended


def test_secrets_are_never_returned(client, env):
    env.write_text("CANVAS_TOKEN=3591~abcdefghijkl\nGRADESCOPE_EMAIL=me@stanford.edu\n")
    page = client.get("/api/settings").json()
    token = _field(page, "CANVAS_TOKEN")
    assert token["value"] is None and token["is_set"] and token["hint"] == "…ijkl"
    assert "abcdefghijkl" not in client.get("/api/settings").text
    assert _field(page, "GRADESCOPE_EMAIL")["value"] == "me@stanford.edu"


def test_save_and_validate(client, env):
    resp = client.put("/api/settings", json={"values": {
        "COURSE_SITES": "CS 231N=https://cs231n.stanford.edu/schedule.html\nMATH 51 = https://math.example.edu",
        "STUDYHUB_EMBEDDINGS": "off",
        "GRANOLA_API_KEY": None,  # unchanged
    }})
    assert resp.status_code == 200
    assert envfile.read_env(env)["COURSE_SITES"] == \
        "CS 231N=https://cs231n.stanford.edu/schedule.html; MATH 51 = https://math.example.edu"
    assert _field(resp.json(), "COURSE_SITES")["value"].count("\n") == 1
    assert envfile.read_env(env).get("GRANOLA_API_KEY", "") == ""  # left alone

    for values, message in [
        ({"COURSE_SITES": "CS 231N cs231n.stanford.edu"}, "course code"),
        ({"STUDYHUB_EMBEDDINGS": "sometimes"}, "must be one of"),
        ({"GOODNOTES_DIR": "/definitely/not/here"}, "No folder"),
        ({"STUDYHUB_TIMEZONE": "Mars/Olympus"}, "time zone"),
        ({"PATH": "/tmp"}, "can't be set"),
        ({"CANVAS_TOKEN": "a\nb"}, "one line"),
    ]:
        resp = client.put("/api/settings", json={"values": values})
        assert resp.status_code == 400 and message in resp.json()["detail"], values


def test_environment_variables_win_and_are_marked(client, env, monkeypatch):
    monkeypatch.setenv("STUDYHUB_TIMEZONE", "America/New_York")
    field = _field(client.get("/api/settings").json(), "STUDYHUB_TIMEZONE")
    assert field["locked"] and field["value"] == "America/New_York"


def test_check_endpoint(client):
    assert client.post("/api/settings/check/canvas").json() == {"ok": False, "message": "Not set up yet."}
    assert client.post("/api/settings/check/search").json()["ok"] is False
    assert client.post("/api/settings/check/nope").status_code == 404


def test_only_local_requests_are_answered(conn, env):
    local = TestClient(app, base_url="http://127.0.0.1:8000")
    assert local.get("/api/status").status_code == 200
    assert TestClient(app, base_url="http://[::1]:8000").get("/api/status").status_code == 200
    # DNS rebinding: a page on another hostname that resolves to 127.0.0.1.
    assert TestClient(app, base_url="http://evil.example").get("/api/courses").status_code == 403
    # Another website posting to the local server.
    evil = {"Origin": "https://evil.example"}
    assert local.put("/api/settings", json={"values": {}}, headers=evil).status_code == 403
    assert local.post("/api/sync", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # The Vite dev server on localhost:5173 is fine.
    ok = local.put("/api/settings", json={"values": {}}, headers={"Origin": "http://localhost:5173"})
    assert ok.status_code == 200


def test_extra_allowed_hosts(conn, env, monkeypatch):
    from studyhub.config import get_settings

    monkeypatch.setenv("STUDYHUB_ALLOWED_HOSTS", "studyhub.tailnet.ts.net")
    get_settings.cache_clear()
    remote = TestClient(app, base_url="https://studyhub.tailnet.ts.net")
    assert remote.get("/api/status").status_code == 200


def test_connection_errors_name_the_host():
    import httpx

    from studyhub.checks import describe_error

    request = httpx.Request("GET", "https://canvas.stanford.edu/api/v1/users/self")
    assert describe_error(httpx.ConnectError("boom", request=request)).startswith("Couldn't reach canvas.stanford.edu")
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("x", request=request, response=response)
    assert describe_error(err) == "canvas.stanford.edu answered 401 Unauthorized."
