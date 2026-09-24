from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Protocol

from ..config import Settings

log = logging.getLogger("studyhub.sync")


@dataclass
class SyncContext:
    conn: sqlite3.Connection
    settings: Settings
    changed: int = 0
    warnings: list[str] = field(default_factory=list)
    touched_courses: set[int] = field(default_factory=set)

    def warn(self, message: str) -> None:
        log.warning(message)
        self.warnings.append(message)

    def mark(self, course_id: int, changed: bool = True) -> None:
        self.touched_courses.add(course_id)
        if changed:
            self.changed += 1


class Connector(Protocol):
    source: str

    def check(self, settings: Settings) -> str:
        """Log in and describe what's visible, e.g. "3 courses: CS 231N, …". Raises on failure."""

    def sync(self, ctx: SyncContext) -> None:
        """Copy everything new or changed into the store via ctx.conn."""
