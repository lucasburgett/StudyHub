"""The settings page's view of backend/.env: which fields exist, their values, and safe writes.

Secrets never leave the server: the page only learns whether one is set and its last four
characters. Writes keep the file's comments and order, and change only the lines they touch.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import BACKEND_DIR, get_settings

ENV_PATH = BACKEND_DIR / ".env"
EXAMPLE_PATH = BACKEND_DIR / ".env.example"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"  # text | secret | path | lines | select | bool
    help: str = ""
    placeholder: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    id: str        # a source name, or "claude" / "search" / "general"
    title: str
    fields: tuple[Field, ...]
    checkable: bool = True
    intro: str = ""


GROUPS: tuple[Group, ...] = (
    Group("claude", "Claude", (
        Field("ANTHROPIC_API_KEY", "API key", "secret",
              "From console.anthropic.com → API keys. Chat, schedule import and handwriting transcription use it."),
    ), intro="Answers your questions and reads your handwriting."),
    Group("canvas", "Canvas", (
        Field("CANVAS_BASE_URL", "Canvas address", placeholder="https://canvas.stanford.edu"),
        Field("CANVAS_TOKEN", "Access token", "secret",
              "Canvas → Account → Settings → + New access token. Give it an expiry date."),
    )),
    Group("gradescope", "Gradescope", (
        Field("GRADESCOPE_EMAIL", "Email", placeholder="you@stanford.edu"),
        Field("GRADESCOPE_PASSWORD", "Password", "secret",
              "With Stanford single sign-on, set a Gradescope password first: gradescope.com → Log in → Forgot password."),
    )),
    Group("goodnotes", "GoodNotes", (
        Field("GOODNOTES_DIR", "Backup folder", "path",
              "The folder GoodNotes Auto Backup writes PDFs into. Keep one GoodNotes folder per class, named "
              "after the course code.",
              "~/Library/CloudStorage/GoogleDrive-you@stanford.edu/My Drive/GoodNotes"),
    )),
    Group("granola", "Granola", (
        Field("GRANOLA_API_KEY", "API key", "secret",
              "Granola → Settings → Connectors → API keys (needs a Business plan). Record each class into a folder "
              "named after its course code."),
    )),
    Group("web", "Course websites", (
        Field("COURSE_SITES", "Sites", "lines",
              "One course per line: its code, “=”, and the page that links the slides (usually the schedule).",
              "CS 231N=https://cs231n.stanford.edu/schedule.html"),
    )),
    Group("search", "Semantic search", (
        Field("STUDYHUB_EMBEDDINGS", "Mode", "select",
              "Auto uses Voyage when a key is set, else a local model if installed, else keyword search only.",
              options=("auto", "voyage", "local", "off")),
        Field("VOYAGE_API_KEY", "Voyage API key", "secret",
              "From dash.voyageai.com (200M free tokens). Course text is sent to Voyage to be embedded."),
    ), intro="Finds material phrased differently from your question."),
    Group("general", "General", (
        Field("STUDYHUB_AUTO_SYNC", "Sync automatically", "bool",
              "While StudyHub runs: Canvas and Granola every 30 minutes, GoodNotes every 10, Gradescope twice a "
              "day, course sites every 6 hours."),
        Field("STUDYHUB_TIMEZONE", "Time zone", placeholder="America/Los_Angeles",
              help="Used to turn timestamps into lecture dates."),
    ), checkable=False),
)

FIELDS = {f.key: f for g in GROUPS for f in g.fields}
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if raw[0] == '"' else inner
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _quote(value: str) -> str:
    if value and re.search(r"[\s#\"'\\;=]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_PATH
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = _LINE_RE.match(line)
            if m:
                values[m.group(1)] = _unquote(m.group(2))
    return values


def write_env(updates: dict[str, str], path: Path | None = None) -> None:
    """Set KEY=value for each update, in place when the key already has a line, else appended."""
    path = path or ENV_PATH
    if not path.exists():
        if EXAMPLE_PATH.exists():
            shutil.copy(EXAMPLE_PATH, path)
        else:
            path.write_text("")
    lines = path.read_text().splitlines()
    pending = dict(updates)
    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m and m.group(1) in pending:
            lines[i] = f"{m.group(1)}={_quote(pending.pop(m.group(1)))}"
    # Uncomment a documented example line ("# VOYAGE_MODEL=…") rather than duplicating it.
    for i, line in enumerate(lines):
        m = re.match(r"^\s*#\s*([A-Z_][A-Z0-9_]*)=", line)
        if m and m.group(1) in pending:
            lines[i] = f"{m.group(1)}={_quote(pending.pop(m.group(1)))}"
    if pending:
        lines += ["", "# Set from the StudyHub settings page"]
        lines += [f"{k}={_quote(v)}" for k, v in pending.items()]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)  # it holds tokens and passwords
    tmp.replace(path)
    get_settings.cache_clear()


def _current(key: str, file_values: dict[str, str]) -> str:
    """The value the running app sees: an environment variable wins over the file."""
    if key in os.environ:
        return os.environ[key]
    if key in file_values:
        return file_values[key]
    settings = get_settings()
    value = getattr(settings, key.lower(), "")
    return str(value).lower() if isinstance(value, bool) else str(value)


def describe(path: Path | None = None) -> dict:
    path = path or ENV_PATH
    file_values = read_env(path)
    groups = []
    for g in GROUPS:
        fields = []
        for f in g.fields:
            value = _current(f.key, file_values)
            entry = {
                "key": f.key, "label": f.label, "kind": f.kind, "help": f.help, "placeholder": f.placeholder,
                "options": list(f.options), "is_set": bool(value), "locked": f.key in os.environ,
            }
            if f.kind == "secret":
                entry["value"] = None
                entry["hint"] = f"…{value[-4:]}" if len(value) >= 8 else ("set" if value else None)
            elif f.kind == "lines":
                entry["value"] = "\n".join(p.strip() for p in re.split(r"[;\n]", value) if p.strip())
            else:
                entry["value"] = value
            fields.append(entry)
        groups.append({"id": g.id, "title": g.title, "intro": g.intro, "checkable": g.checkable, "fields": fields})
    return {"env_file": str(path), "groups": groups}


class SettingsError(ValueError):
    pass


def validate(values: dict[str, str | None]) -> dict[str, str]:
    """Normalize submitted values; None means "leave as is"."""
    out: dict[str, str] = {}
    for key, raw in values.items():
        f = FIELDS.get(key)
        if f is None:
            raise SettingsError(f"{key} can't be set from the settings page.")
        if raw is None:
            continue
        value = raw.strip()
        if f.kind == "lines":
            parts = [p.strip() for p in re.split(r"[;\n]", value) if p.strip()]
            bad = [p for p in parts if "=" not in p or not p.partition("=")[2].strip().startswith(("http://", "https://"))]
            if bad:
                raise SettingsError(f"Each line needs a course code, “=”, and a web address: {bad[0]!r}")
            value = "; ".join(parts)
        elif "\n" in value or "\r" in value:
            raise SettingsError(f"{f.label} must be on one line.")
        if f.kind == "select" and value not in f.options:
            raise SettingsError(f"{f.label} must be one of: {', '.join(f.options)}.")
        if f.kind == "bool":
            if value.lower() not in ("true", "false"):
                raise SettingsError(f"{f.label} must be true or false.")
            value = value.lower()
        if f.kind == "path" and value:
            if not Path(value).expanduser().is_dir():
                raise SettingsError(f"No folder at {value}.")
        if key == "CANVAS_BASE_URL" and value and not value.startswith("https://"):
            raise SettingsError("The Canvas address should start with https://.")
        if key == "STUDYHUB_TIMEZONE" and value:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(value)
            except Exception as e:
                raise SettingsError(f"Unknown time zone {value!r}.") from e
        out[key] = value
    return out
