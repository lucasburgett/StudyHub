from __future__ import annotations

import sqlite3
from typing import Iterator

import pytest

from studyhub import config
from studyhub.agent import subscription
from studyhub.db import connect, init_db

SOURCE_VARS = ("CANVAS_BASE_URL", "CANVAS_TOKEN", "GRADESCOPE_EMAIL", "GRADESCOPE_PASSWORD", "GOODNOTES_DIR",
               "GRANOLA_API_KEY", "COURSE_SITES", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "VOYAGE_API_KEY",
               "STUDYHUB_AGENT")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch) -> Iterator[None]:
    """Every test gets its own data dir, and never reads the developer's backend/.env."""
    monkeypatch.setenv("STUDYHUB_DATA_DIR", str(tmp_path / "data"))
    for var in SOURCE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("STUDYHUB_EMBEDDINGS", "off")  # never download a model in tests
    # Never ask the real Claude Code whether it's logged in; tests that need a login patch this.
    monkeypatch.setattr(subscription, "claude_login", lambda max_age=60.0: None)
    monkeypatch.setattr(config.Settings, "model_config", {**config.Settings.model_config, "env_file": None})
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect()
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def demo(conn) -> sqlite3.Connection:
    from datetime import date

    from studyhub.demo import load_demo

    load_demo(conn, today=date(2026, 9, 24))
    return conn
