"""Settings, read from backend/.env and the environment."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: str = ""
    studyhub_model: str = "claude-opus-5"
    # Which Claude account chat runs on: "api" (an API key), "subscription" (the Claude plan that
    # Claude Code is logged in with), or "auto": the API key when one is set, else the subscription.
    studyhub_agent: str = "auto"

    canvas_base_url: str = "https://canvas.stanford.edu"
    canvas_token: str = ""

    gradescope_email: str = ""
    gradescope_password: str = ""

    goodnotes_dir: str = ""

    granola_api_key: str = ""

    course_sites: str = ""
    # For classes that post homework on a page per class ("Week 1, Day 3"): when they meet.
    # "FRENLANG 1=Mon-Fri 9:30 from 2026-09-21; …"
    class_schedules: str = ""

    studyhub_embeddings: str = "auto"  # auto | voyage | local | off
    voyage_api_key: str = ""
    voyage_model: str = "voyage-4"

    studyhub_timezone: str = "America/Los_Angeles"
    studyhub_data_dir: str = ""
    studyhub_auto_sync: bool = True
    # After a GoodNotes sync, transcribe new note pages with Claude (on the Claude subscription or key).
    studyhub_auto_transcribe: bool = True
    # Extra hostnames the server answers to, comma-separated (only needed if you host it somewhere).
    studyhub_allowed_hosts: str = ""

    @property
    def data_dir(self) -> Path:
        path = Path(self.studyhub_data_dir) if self.studyhub_data_dir else REPO_DIR / "data"
        return path.expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studyhub.db"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.studyhub_timezone)

    @property
    def model(self) -> str:
        return self.studyhub_model

    @property
    def sites(self) -> list[tuple[str, str]]:
        """COURSE_SITES="CS 231N=https://…/schedule.html; MATH 51=https://…" -> [(code, url), …]"""
        pairs = []
        for part in re.split(r"[;\n]", self.course_sites):
            code, sep, url = part.partition("=")
            if sep and code.strip() and url.strip().startswith(("http://", "https://")):
                pairs.append((code.strip(), url.strip()))
        return pairs

    def configured(self, source: str) -> bool:
        return {
            "canvas": bool(self.canvas_token),
            "gradescope": bool(self.gradescope_email and self.gradescope_password),
            "goodnotes": bool(self.goodnotes_dir),
            "granola": bool(self.granola_api_key),
            "web": bool(self.sites),
        }[source]

    @property
    def allowed_hosts(self) -> set[str]:
        extra = {h.strip().lower() for h in self.studyhub_allowed_hosts.split(",") if h.strip()}
        return {"127.0.0.1", "localhost", "::1"} | extra

    @property
    def api_key_set(self) -> bool:
        """Schedule import and handwriting transcription need this; chat can use a subscription instead."""
        return bool(
            self.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

    @property
    def agent_backend(self) -> str | None:
        """How chat reaches Claude: "api", "subscription", or None when it can't."""
        mode = self.studyhub_agent.strip().lower()
        if mode != "subscription" and self.api_key_set:
            return "api"
        if mode != "api":
            from .agent.subscription import claude_login

            if claude_login():
                return "subscription"
        return None

    @property
    def agent_ready(self) -> bool:
        return self.agent_backend is not None


SOURCES = ("canvas", "gradescope", "goodnotes", "granola", "web")


@lru_cache
def get_settings() -> Settings:
    return Settings()
