"""TrayIcon constructs all menu actions in __init__.

Regression test for v0.2.0 ship-blocker: the QAction setup, the
contextMenu wiring and `self._tray.show()` were accidentally indented
into `_on_color_scheme_changed`, so on a normal startup nothing got
created. The tray icon never appeared, and the daemon's first track
update crashed with `AttributeError: '_title_action'`.

Runs against ``QT_QPA_PLATFORM=offscreen`` so it works in headless CI.
"""

from __future__ import annotations

import os
import sys

import pytest

# Skip without PySide6 — the tests target build environments that
# already have it (see release.yml / tests.yml). On a stripped-down
# environment, the test gracefully bows out instead of erroring at
# import time.
pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.ui.tray import TrayIcon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


def test_tray_init_creates_all_actions(app):
    """All menu items + the tray itself must exist after __init__."""
    # offscreen platform reports `isSystemTrayAvailable() == False`, but
    # we explicitly want to verify construction even there — the bug
    # was that QActions / contextMenu were never built, regardless of
    # whether the tray itself actually shows.
    tray = TrayIcon()

    # Every QAction the menu wires up has to be a real attribute on
    # the instance — otherwise the slots in daemon.py would raise on
    # the first track update.
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


def test_tray_set_methods_dont_crash(app):
    """daemon.py's first dispatch calls these — they must not AttributeError."""
    # offscreen platform reports `isSystemTrayAvailable() == False`, but
    # we explicitly want to verify construction even there — the bug
    # was that QActions / contextMenu were never built, regardless of
    # whether the tray itself actually shows.
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
    tray.set_discord_connected(True)
    tray.set_update_available(True, "1.0.0")


def test_progress_line_shows_elapsed_only_without_a_duration(app):
    """A source with no track length still gets an elapsed count.

    Bluetooth AVRCP often reports no length, and neither does a
    streaming source whose catalog lookup came up empty. Hiding the line
    there threw away a number we do trust.
    """
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
