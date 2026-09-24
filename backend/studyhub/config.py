"""Settings, read from backend/.env and the environment."""

from __future__ import annotations

import os
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

    canvas_base_url: str = "https://canvas.stanford.edu"
    canvas_token: str = ""

    gradescope_email: str = ""
    gradescope_password: str = ""

    goodnotes_dir: str = ""

    granola_api_key: str = ""

    studyhub_timezone: str = "America/Los_Angeles"
    studyhub_data_dir: str = ""
    studyhub_auto_sync: bool = True

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

    def configured(self, source: str) -> bool:
        return {
            "canvas": bool(self.canvas_token),
            "gradescope": bool(self.gradescope_email and self.gradescope_password),
            "goodnotes": bool(self.goodnotes_dir),
            "granola": bool(self.granola_api_key),
        }[source]

    @property
    def agent_ready(self) -> bool:
        return bool(
            self.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )


SOURCES = ("canvas", "gradescope", "goodnotes", "granola")


@lru_cache
def get_settings() -> Settings:
    return Settings()
