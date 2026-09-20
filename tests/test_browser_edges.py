"""browser.py edge cases: a /proc entry that vanishes mid-read, and no browser to open a link in at all."""

from __future__ import annotations

import refrain.browser as browser
from refrain.browser import running_processes
from tests.test_browser import _patch_qt


def test_running_processes_skips_a_comm_it_cannot_read(tmp_path):
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "comm").write_text("systemd\n", encoding="utf-8")
    (tmp_path / "42").mkdir()
    unreadable = tmp_path / "42" / "comm"
    unreadable.write_text("chromium\n", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        assert running_processes(tmp_path) == {"systemd"}
    finally:
        unreadable.chmod(0o644)


def test_when_neither_the_player_nor_the_default_browser_can_open_it(monkeypatch):
    opened = _patch_qt(monkeypatch, default_ok=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert browser.open_url("https://music.apple.com/x") is False
    assert opened == ["https://music.apple.com/x"]
