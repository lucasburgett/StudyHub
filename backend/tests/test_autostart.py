"""The macOS login item. launchctl is faked: tests never touch the real launchd."""

import plistlib
import sys
from types import SimpleNamespace

import pytest

from studyhub import autostart
from studyhub.config import get_settings


@pytest.fixture
def launchd(monkeypatch, tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[1:])
        return SimpleNamespace(returncode=0, stdout="state = running\n\tpid = 4242\n", stderr="")

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart.subprocess, "run", run)
    monkeypatch.setattr(autostart, "plist_path", lambda: tmp_path / "LaunchAgents" / f"{autostart.LABEL}.plist")
    return calls


def test_agent_plist():
    plist = autostart.agent_plist(get_settings())
    assert plist["ProgramArguments"][0].endswith("/studyhub")
    assert plist["ProgramArguments"][1:] == ["serve", "--no-access-log"]
    assert plist["RunAtLoad"] and plist["KeepAlive"]
    assert plist["StandardOutPath"].endswith("/data/logs/studyhub.log")


def test_on_restarts_and_off_removes(launchd, tmp_path):
    message = autostart.enable()
    path = autostart.plist_path()
    assert plistlib.loads(path.read_bytes())["Label"] == autostart.LABEL
    assert [c[0] for c in launchd] == ["bootout", "bootstrap"]  # a running copy restarts with the new code
    assert launchd[1][2] == str(path) and "starts at login" in message

    assert "running (pid 4242)" in autostart.status()
    autostart.disable()
    assert not path.exists() and launchd[-1][0] == "bootout"
    assert autostart.status().startswith("Off.")


def test_only_on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        autostart.enable()
