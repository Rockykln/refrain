"""The system report tells a maintainer what runs, and nothing about the user."""

from __future__ import annotations

import sys

import pytest

from refrain.config import Config
from refrain.diagnostics import report


@pytest.fixture
def filled(xdg_tmp):
    config = Config()
    config.discord.client_id = "1234567890123456789"
    config.discord.client_id_bluetooth = "1234567890123456780"
    config.sources.bluetooth_device = "12:34:56:78:9A:BC"
    config.lastfm.session_key = "12345session"
    config.lastfm.username = "refrain_demo"
    config.lastfm.api_key = "12345apikey"
    config.lastfm.shared_secret = "12345secret"
    return config


def test_the_report_says_what_refrain_runs_on(filled):
    text = report(filled)
    for line in ("Refrain ", "OS:", "Python:", "PySide6:", "Desktop:", "Sources:", "Privacy:"):
        assert line in text, f"{line!r} missing"
    assert "crash reports:" in text


def test_the_report_gives_nothing_away(filled):
    text = report(filled)
    for secret in (
        "1234567890123456789",  # the Discord Application ID
        "1234567890123456780",
        "12:34:56:78:9A:BC",  # the Bluetooth address
        "12345session",
        "12345apikey",
        "12345secret",
        "refrain_demo",  # the Last.fm account
    ):
        assert secret not in text, f"{secret!r} leaked into the report"
    # It still says whether each of them is set.
    assert "Application ID set yes" in text
    assert "device chosen: yes" in text
    assert "connected yes" in text


def test_the_report_keeps_home_and_the_time_zone_out_of_it(filled):
    from pathlib import Path

    filled.advanced.time_zone = "Europe/Berlin"
    filled.advanced.time_format = "12h"
    text = report(filled)
    assert str(Path.home()) not in text, "the home directory carries the user's name"
    assert "Europe/Berlin" not in text, "the time zone says where someone lives"
    assert "config.toml" in text
    assert "clock: 12h" in text


def test_a_path_under_home_is_written_with_a_tilde():
    from pathlib import Path

    from refrain.diagnostics import _short

    assert _short(Path.home() / ".config/refrain/config.toml") == "~/.config/refrain/config.toml"
    assert _short(Path("/etc/refrain.toml")) == "/etc/refrain.toml"


def test_an_empty_setup_reads_as_empty(xdg_tmp):
    text = report(Config())
    assert "Application ID set no" in text
    assert "connected no" in text


def test_a_machine_without_os_release_still_reports_something(monkeypatch):
    from pathlib import Path

    from refrain import diagnostics

    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("no")))
    assert diagnostics._os_name()


def test_versions_and_install_type_survive_a_missing_piece(monkeypatch):
    from refrain import diagnostics

    monkeypatch.setitem(sys.modules, "PySide6", None)
    assert diagnostics._qt_versions() == ("—", "—")
    monkeypatch.setattr(
        "refrain.updater.detect_install_type",
        lambda: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    assert diagnostics._install_type() == "unknown"
