"""daemon.py edge cases: a broken cover cache, a stale cover-replace watch, and the real Daemon thread."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.config import Config  # noqa: E402
from refrain.daemon import Daemon, DaemonWorker  # noqa: E402
from tests.daemon_fakes import Player, install, make_config  # noqa: E402

ARTIST, TITLE = "Mara Keel", "Paper Satellites"
COVER_FILE = Path("/nonexistent/covers/paper-lanterns.jpg")
NOTIFY_BIN = "/usr/bin/notify-send"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(monkeypatch, qapp):
    def build(notify_bin=None, **sections):
        clock, notifier = install(monkeypatch, notify_bin)
        sections["behavior"] = {"notifications": True, **sections.get("behavior", {})}
        worker = DaemonWorker(make_config(**sections))
        worker._cover_fetcher.urls[(ARTIST, TITLE)] = "https://example.org/paper-lanterns.jpg"
        return worker, Player(worker, clock), clock, notifier

    return build


def test_a_broken_cover_cache_does_not_stop_history_from_updating(rig, caplog):
    """keep_covers() failing (unreadable cache dir, disk full) must not crash clear_history()."""
    worker, *_ = rig()

    def _boom(_urls):
        raise OSError("cache dir vanished")

    worker._cover_fetcher.keep_covers = _boom
    seen = []
    worker.historyChanged.connect(lambda snap: seen.append(snap))
    with caplog.at_level(logging.ERROR):
        worker.clear_history()
    assert "Could not sync the cover images with the history" in caplog.text
    assert len(seen) == 1, "the history itself is still cleared and announced"


def test_a_broken_cover_cache_does_not_stop_a_song_change_from_being_announced(rig, caplog):
    """drop_temp_covers() failing must not stop the daemon from noticing a new song."""
    worker, player, *_ = rig()

    def _boom(*_a, **_k):
        raise OSError("cache dir vanished")

    worker._cover_fetcher.drop_temp_covers = _boom
    changed = []
    worker.trackChanged.connect(lambda t: changed.append(t.title))
    with caplog.at_level(logging.ERROR):
        player.play()
        player.tick()
    assert "Could not delete the temporary cover images" in caplog.text
    assert changed == [TITLE]


def test_a_lost_notification_id_cancels_the_cover_watch_without_replacing_anything(rig):
    """The captured notification id must still belong to the watched track."""
    worker, player, _clock, notifier = rig(notify_bin=NOTIFY_BIN)
    player.play()
    player.tick()
    for _ in range(worker._NOTIFY_MAX_RETRIES + 1):
        worker._fire_pending_notify()
    assert worker._notify_id == 4242
    assert worker._replace_timer.isActive()

    worker._notify_id = None  # the shown bubble's id got lost somehow
    worker._cover_fetcher.local[(ARTIST, TITLE)] = COVER_FILE
    worker._fire_cover_replace()

    assert worker._replace_track is None
    assert len(notifier.run_calls) == 1, "no replace notification was ever issued"
    worker._replace_timer.stop()


def test_muting_notifications_keeps_new_songs_from_popping_up(rig):
    worker, player, _clock, notifier = rig(notify_bin=NOTIFY_BIN)
    worker.set_notifications_muted(True)
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert notifier.run_calls == [], "no bubble should have popped up while muted"
    assert worker._config.behavior.notifications is True, "the setting itself is untouched"


def test_the_catalog_album_fills_in_when_the_player_sends_none(rig):
    """Last.fm needs an album to find cover art; the daemon asks the catalog for one
    and hands the completed track — not the player's bare one — to the history."""
    worker, player, *_ = rig()
    worker._cover_fetcher.albums[(ARTIST, TITLE)] = "Iron Garden"
    player.play(album="")
    player.tick()
    recorded = worker._history.calls[-1]["track"]
    assert recorded.album == "Iron Garden"


def test_the_daemon_starts_a_real_thread_and_a_second_stop_returns_at_once(monkeypatch, qapp):
    """Daemon owns a real QThread: start() must run it, and stop() must be idempotent."""
    monkeypatch.setattr(DaemonWorker, "start_polling", lambda self: None)
    d = Daemon(Config())
    d.start()
    deadline = time.monotonic() + 5
    while not d.thread.isRunning():
        assert time.monotonic() < deadline
        QCoreApplication.processEvents()
        time.sleep(0.005)

    d.stop()
    assert not d.thread.isRunning()

    started = time.monotonic()
    d.stop()  # nothing to wait on: must return immediately, not hang
    assert time.monotonic() - started < 1.0
