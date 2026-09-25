"""Keep `studyhub serve` running in the background on macOS: a launchd agent in the user's login
session. It starts at login, restarts after a crash, and syncs on its schedule while it runs.

A per-user agent (not a system daemon) because StudyHub needs the user's own session: the Claude
Code login it chats with lives in their keychain.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .config import REPO_DIR, Settings, get_settings

LABEL = "local.studyhub.serve"
URL = "http://127.0.0.1:8000"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path(settings: Settings) -> Path:
    return settings.data_dir / "logs" / "studyhub.log"


def agent_plist(settings: Settings) -> dict:
    exe = Path(sys.executable).with_name("studyhub")  # this virtualenv's entry point
    log = str(log_path(settings))
    return {
        "Label": LABEL,
        "ProgramArguments": [str(exe), "serve", "--no-access-log"],
        "WorkingDirectory": str(REPO_DIR),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,  # after a crash, wait before starting again
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Autostart uses macOS launchd. On other systems, run `studyhub serve` another way.")


def enable(settings: Settings | None = None) -> str:
    """Install (or reinstall, restarting it) the login item and start it now."""
    _require_macos()
    settings = settings or get_settings()
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path(settings).parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(agent_plist(settings)))
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # a running copy stops first, so new code loads
    for _ in range(10):  # right after a bootout, launchd can refuse for a moment
        result = _launchctl("bootstrap", _domain(), str(path))
        if result.returncode == 0:
            return f"StudyHub now starts at login and keeps running at {URL}. Log: {log_path(settings)}"
        time.sleep(0.5)
    raise RuntimeError(f"launchctl couldn't start StudyHub: {result.stderr.strip() or result.returncode}")


def disable() -> str:
    _require_macos()
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    plist_path().unlink(missing_ok=True)
    return "StudyHub no longer starts at login, and the background copy is stopped."


def status(settings: Settings | None = None) -> str:
    _require_macos()
    settings = settings or get_settings()
    if not plist_path().exists():
        return "Off. `studyhub autostart on` (or `make autostart`) keeps StudyHub running in the background."
    loaded = _launchctl("print", f"{_domain()}/{LABEL}")
    pid = next((line.split("=", 1)[1].strip() for line in loaded.stdout.splitlines()
                if line.strip().startswith("pid =")), None)
    try:
        with urllib.request.urlopen(f"{URL}/api/status", timeout=3) as resp:
            answering = resp.status == 200
    except OSError:
        answering = False
    if loaded.returncode != 0:
        return "Installed but not loaded. Run `studyhub autostart on` again."
    state = f"running (pid {pid})" if pid else "loaded, not running"
    web = f"answering at {URL}" if answering else f"not answering at {URL}; see {log_path(settings)}"
    return f"On: {state}, {web}."
