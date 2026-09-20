"""TrayIcon constructs all menu actions in __init__."""

from __future__ import annotations

import os
import sys

import pytest

# Skip without PySide6 instead of failing at import time.
pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.service_status import DiscordStatus, StatusSnapshot  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.ui.tray import TrayIcon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


def test_tray_init_creates_all_actions(app):
    """All menu items + the tray itself must exist after __init__."""
    # Offscreen reports no system tray; the actions must be built anyway.
    tray = TrayIcon()

    # The slots in daemon.py use these on the first track update.
    for attr in (
        "_title_action",
        "_artist_action",
        "_progress_action",
        "_discord_action",
        "_previous_action",
        "_play_pause_action",
        "_next_action",
        "_update_action",
        "_history_action",
    ):
        assert hasattr(tray, attr), f"TrayIcon missing {attr} after __init__"

    # The QSystemTrayIcon should have a context menu attached.
    assert tray._tray.contextMenu() is not None


def test_a_moment_of_pause_between_songs_does_not_flash(app):
    """Apple Music reports "paused" for a poll between songs."""
    from PySide6.QtTest import QTest

    tray = TrayIcon()
    tray._PAUSE_SHOWN_AFTER_MS = 40
    tray.set_status(PlaybackStatus.PLAYING)
    tray.set_status(PlaybackStatus.PAUSED)
    assert tray._play_pause_action.text() == "Pause"  # still shown as playing
    tray.set_status(PlaybackStatus.PLAYING)
    QTest.qWait(120)
    assert tray._play_pause_action.text() == "Pause"
    assert tray._current_status == PlaybackStatus.PLAYING


def test_a_real_pause_shows_after_the_delay(app):
    from PySide6.QtTest import QTest

    tray = TrayIcon()
    tray._PAUSE_SHOWN_AFTER_MS = 40
    tray.set_status(PlaybackStatus.PLAYING)
    tray.set_status(PlaybackStatus.PAUSED)
    QTest.qWait(120)
    assert tray._play_pause_action.text() == "Play"
    assert tray._current_status == PlaybackStatus.PAUSED
    tray.set_status(PlaybackStatus.PLAYING)  # back to playing: at once
    assert tray._play_pause_action.text() == "Pause"


def test_history_entry_follows_the_switch(app):
    """Hidden while the history is off — it would open a dead end."""
    tray = TrayIcon()
    assert tray._history_action.isVisible()
    tray.set_history_enabled(False)
    assert not tray._history_action.isVisible()
    tray.set_history_enabled(True)
    assert tray._history_action.isVisible()


def test_first_dispatch_fills_the_menu(app):
    """daemon.py's first dispatch calls these right after __init__."""
    # Offscreen reports no system tray; the actions must be built anyway.
    tray = TrayIcon()
    track = TrackInfo(
        source="mpris",
        title="Some Track",
        artist="Some Artist",
        album="Some Album",
        duration_ms=180_000,
        position_ms=0,
        status=PlaybackStatus.PLAYING,
    )
    tray.set_track(track)
    tray.set_status(PlaybackStatus.PLAYING)
    tray.set_progress(42_000, 180_000)
    tray.set_service_status(StatusSnapshot(DiscordStatus.SHOWING))
    tray.set_update_available(True, "1.0.0")
    assert tray._title_action.text() == "Some Track"
    assert tray._artist_action.text() == "Some Artist • Some Album"
    assert tray._play_pause_action.text() == "Pause"
    assert tray._progress_action.text().startswith("0:42 / 3:00")
    assert tray._discord_action.text() == "Discord: visible on your profile"
    assert tray._update_action.isVisible()
    assert "1.0.0" in tray._update_action.text()


def test_progress_line_shows_elapsed_only_without_a_duration(app):
    """A source with no track length (often AVRCP) still gets an elapsed count."""
    tray = TrayIcon()
    tray.set_progress(83_000, 0)
    assert tray._progress_action.text() == "1:23"
    assert tray._progress_action.isVisible()


def test_negative_position_hides_the_progress_line(app):
    """-1 is how the daemon says the position isn't trustworthy at all."""
    tray = TrayIcon()
    tray.set_progress(42_000, 180_000)
    assert tray._progress_action.isVisible()
    tray.set_progress(-1, 0)
    assert not tray._progress_action.isVisible()
    assert tray._progress_action.text() == ""
