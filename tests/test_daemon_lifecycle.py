"""Daemon start and stop, settings changes, player controls, history slots and desktop notifications."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain import daemon  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.sources.base import PlaybackStatus  # noqa: E402
from tests.daemon_fakes import (  # noqa: E402
    CLIENT_ID,
    CLIENT_ID_MPRIS,
    FakeRPC,
    Player,
    install,
    make_config,
    song,
)

ARTIST, TITLE = "Mara Keel", "Paper Satellites"
COVER = "https://is1-ssl.mzstatic.example/image/paper-lanterns.jpg"
COVER_FILE = Path("/nonexistent/covers/paper-lanterns.jpg")
NOTIFY_BIN = "/usr/bin/notify-send"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(monkeypatch, qapp):
    def build(notify_bin=None, **sections):
        clock, notifier = install(monkeypatch, notify_bin)
        # Song notifications are off by default; these tests are about them.
        sections["behavior"] = {"notifications": True, **sections.get("behavior", {})}
        worker = DaemonWorker(make_config(**sections))
        worker._cover_fetcher.urls[(ARTIST, TITLE)] = COVER
        return worker, Player(worker, clock), clock, notifier

    return build


def signals(worker, name):
    got = []
    getattr(worker, name).connect(lambda *args: got.append(args))
    return got


def rpc() -> FakeRPC:
    return FakeRPC.instances[-1]


def test_polling_starts_at_the_configured_interval_and_publishes_the_player(rig):
    worker, *_ = rig(advanced={"poll_interval_ms": 800})
    worker.start_polling()
    assert worker._timer.isActive()
    assert worker._timer.interval() == 800
    assert worker._mpris_server.started
    worker._timer.stop()


def test_polling_interval_has_a_floor(rig):
    worker, *_ = rig(advanced={"poll_interval_ms": 10})
    worker.start_polling()
    assert worker._timer.interval() == 250
    worker._timer.stop()


def test_cleanup_clears_discord_and_shuts_everything_down(rig):
    worker, player, _, _ = rig()
    worker.start_polling()
    player.play()
    player.tick()
    worker._schedule_notify(song())
    worker._start_cover_replace_watch(song())
    connection = rpc()
    worker.cleanup()
    assert worker._timer is None
    assert worker._notify_timer is None
    assert worker._replace_timer is None
    assert connection.last == ("clear", {"force": True})
    assert connection.closed
    assert worker._mpris_server.stopped
    assert worker._scrobbler.shut
    assert worker._history.shut
    assert worker._cover_fetcher.shut


def test_cleanup_goes_on_when_discord_fails(rig):
    worker, *_ = rig()

    def broken():
        raise OSError("pipe closed")

    rpc().clear = broken
    rpc().close = broken
    worker.cleanup()
    assert worker._scrobbler.shut
    assert worker._history.shut
    assert worker._cover_fetcher.shut


def test_poll_queued_before_cleanup_does_not_run_after_it(rig, qapp):
    from PySide6.QtTest import QTest

    worker, player, _, _ = rig()
    player.play()
    player.tick()
    worker.update_config(make_config(discord={"client_id": CLIENT_ID_MPRIS}))
    worker.cleanup()
    connection = rpc()
    QTest.qWait(20)
    assert connection.last == ("clear", {"force": True})


def test_new_app_id_reconnects_discord_straight_away(rig):
    worker, player, _, _ = rig()
    old = rpc()
    config = make_config()
    config.discord.client_id = "123459999999999999"
    worker.update_config(config)
    assert old.closed
    assert rpc() is not old
    assert rpc().client_id == "123459999999999999"
    assert rpc().ensure_calls == 1
    player.play()
    player.tick()
    assert rpc().last[1]["details"] == TITLE
    assert old.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"client_id_mpris": CLIENT_ID_MPRIS},
        {"client_id_bluetooth": "123450000000000009"},
        {"all_clients": True},
    ],
)
def test_any_discord_setting_reconnects(rig, change):
    worker, *_ = rig()
    old = rpc()
    worker.update_config(make_config(discord=change))
    assert old.closed
    assert rpc() is not old
    assert rpc().client_id == CLIENT_ID
    assert rpc().all_clients == change.get("all_clients", False)


def test_unrelated_setting_keeps_the_discord_connection(rig):
    worker, *_ = rig()
    old = rpc()
    worker.update_config(make_config(behavior={"show_buttons": False}))
    assert rpc() is old
    assert not old.closed


def test_new_poll_interval_applies_to_the_running_timer(rig):
    worker, *_ = rig()
    worker.start_polling()
    worker.update_config(make_config(advanced={"poll_interval_ms": 1500}))
    assert worker._timer.interval() == 1500
    worker.update_config(make_config(advanced={"poll_interval_ms": 100}))
    assert worker._timer.interval() == 250
    worker._timer.stop()


def test_cover_images_follow_the_history_from_the_start(rig):
    worker, *_ = rig()
    assert worker._cover_fetcher.kept == [[]]
    seen = []
    worker.historyChanged.connect(lambda _snap: seen.append(len(worker._cover_fetcher.kept)))
    worker.clear_history()
    assert seen == [2], "the images are sorted out before the window hears of it"


def test_a_new_song_drops_the_last_ones_temporary_image(rig):
    worker, player, *_ = rig()
    player.play()
    player.tick()
    player.play(title="Low Tide")
    player.tick()
    player.stop()
    player.tick()
    assert worker._cover_fetcher.temp_kept == [TITLE, "Low Tide", ""]


def test_settings_reach_sources_scrobbler_and_history(rig):
    worker, *_ = rig()
    snapshots = signals(worker, "historyChanged")
    config = make_config(
        sources={"bluetooth_device": "12:34:56:78:9A:BC", "browser_hints": "Zen, chromium"},
        lastfm={"enabled": True},
    )
    worker.update_config(config)
    assert worker._bluetooth.device == "12:34:56:78:9A:BC"
    assert worker._mpris.hints == ["zen", "chromium"]
    assert worker._scrobbler.reconfigured == [config.lastfm]
    assert snapshots == []
    worker._history.reconfigure_result = True
    worker.update_config(make_config(history={"max_entries": 10}))
    assert len(snapshots) == 1
    assert snapshots[0][0].limit == 10


def test_history_slots_publish_the_new_list(rig):
    worker, *_ = rig()
    snapshots = signals(worker, "historyChanged")
    worker.clear_history()
    entry = type("Entry", (), {"started_at": 12345, "title": TITLE, "artist": ARTIST})()
    worker.remove_from_history(entry)
    worker._on_scrobble_queued(ARTIST, TITLE)
    assert worker._history.cleared
    assert worker._history.removed == [(12345, TITLE, ARTIST)]
    assert worker._history.scrobbled == [(ARTIST, TITLE)]
    assert len(snapshots) == 3
    assert worker.history_snapshot().limit == 30


def test_scrobbler_reports_back_to_the_history(rig):
    worker, *_ = rig()
    assert worker._scrobbler.on_queued == worker._on_scrobble_queued


def test_control_goes_to_the_active_source(rig):
    worker, player, clock, _ = rig()
    player.play()
    player.tick()
    worker.control_next()
    worker.control_play_pause()
    worker.control_previous()
    assert worker._mpris.controls == ["next", "play_pause", "previous"]
    assert worker._bluetooth.controls == []
    assert worker._control_at == clock.mono


def test_control_falls_back_when_the_active_source_refuses(rig):
    worker, player, clock, _ = rig()
    bt = Player(worker, clock, source="bluetooth")
    bt.play()
    bt.tick()
    worker._bluetooth.accepts = False
    worker.control_play_pause()
    assert worker._bluetooth.controls == ["play_pause"]
    assert worker._mpris.controls == ["play_pause"]
    assert worker._active_source == "mpris"


def test_control_reaches_bluetooth_when_the_browser_is_off(rig):
    worker, *_ = rig(sources={"mpris_enabled": False})
    worker.control_next()
    assert worker._mpris.controls == []
    assert worker._bluetooth.controls == ["next"]
    assert worker._active_source == "bluetooth"


def test_control_nobody_takes_changes_nothing(rig):
    worker, *_ = rig()
    worker._mpris.accepts = False

    def broken():
        raise TypeError("no such method")

    worker._bluetooth.next = broken
    worker.control_next()
    assert worker._control_at == 0.0
    assert worker._active_source == "none"


def test_panel_controls_are_queued_onto_the_worker(rig, qapp):
    worker, *_ = rig()
    worker._mpris_server.callbacks["on_next"]()
    assert worker._mpris.controls == []
    qapp.processEvents()
    assert worker._mpris.controls == ["next"]


def test_new_song_notifies_with_its_cover(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN)
    worker._cover_fetcher.local[(ARTIST, TITLE)] = COVER_FILE
    player.play()
    player.tick()
    assert worker._notify_timer.isActive()
    assert worker._notify_timer.interval() == 50
    worker._fire_pending_notify()
    assert notifier.popen == [
        [
            NOTIFY_BIN,
            "-a",
            "Refrain",
            "-i",
            str(COVER_FILE),
            "--hint",
            f"string:image-path:file://{COVER_FILE}",
            "--",
            TITLE,
            f"{ARTIST} — Tidal",
        ]
    ]
    worker._notify_timer.stop()


def test_uncached_cover_gets_the_configured_delay_and_a_download(rig):
    worker, player, _, _ = rig(notify_bin=NOTIFY_BIN, behavior={"notify_delay_ms": 1500})
    player.play()
    player.tick()
    assert worker._notify_timer.interval() == 1500
    assert (ARTIST, TITLE) in worker._cover_fetcher.requested
    worker._notify_timer.stop()


def test_resume_does_not_notify_again(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN, behavior={"cover_art": False})
    player.play()
    player.tick()
    worker._fire_pending_notify()
    player.pause()
    player.tick()
    player.resume()
    player.tick()
    worker._fire_pending_notify()
    assert len(notifier.popen) == 1


def test_notifications_off_sends_none(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN, behavior={"notifications": False})
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert notifier.popen == []


def test_privacy_off_still_notifies_on_this_desktop(rig):
    worker, player, _, notifier = rig(
        notify_bin=NOTIFY_BIN, privacy={"mode": "off"}, behavior={"cover_art": False}
    )
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert len(notifier.popen) == 1


def test_paused_song_is_not_announced(rig):
    worker, player, _, _ = rig(notify_bin=NOTIFY_BIN)
    player.play(status=PlaybackStatus.PAUSED)
    player.tick()
    assert worker._notify_timer is None


def test_notification_for_a_song_already_gone_is_dropped(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN, behavior={"cover_art": False})
    player.play()
    player.tick()
    player.play(title="Silk Road Radio")
    player.tick()
    worker._pending_notify_track = song()
    worker._fire_pending_notify()
    assert notifier.popen == []
    assert worker._pending_notify_track is None


def test_cover_art_off_uses_the_bundled_icon(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN, behavior={"cover_art": False})
    player.play(artist="", album="Tidal")
    player.tick()
    worker._fire_pending_notify()
    argv = notifier.popen[0]
    assert argv[4].endswith("icons/refrain.png")
    assert argv[-1] == "Tidal"


def test_missing_notify_send_sends_nothing(rig):
    worker, player, _, notifier = rig(notify_bin=None, behavior={"cover_art": False})
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert notifier.popen == []
    assert notifier.run_calls == []


def test_late_cover_is_swapped_into_the_shown_notification(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN)
    player.play()
    player.tick()
    for _ in range(worker._NOTIFY_MAX_RETRIES):
        worker._fire_pending_notify()
        assert notifier.run_calls == []
    worker._fire_pending_notify()
    first = notifier.run_calls[0]
    assert "--print-id" in first
    assert first[4].endswith("icons/refrain.png")
    assert worker._notify_id == 4242
    assert worker._replace_timer.isActive()

    worker._fire_cover_replace()
    assert len(notifier.run_calls) == 1
    worker._cover_fetcher.local[(ARTIST, TITLE)] = COVER_FILE
    worker._fire_cover_replace()
    replace = notifier.run_calls[1]
    assert replace[replace.index("--replace-id") + 1] == "4242"
    assert replace[4] == str(COVER_FILE)
    assert worker._replace_track is None
    assert notifier.popen == []
    worker._replace_timer.stop()


def test_cover_watch_gives_up_after_its_attempts(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN)
    player.play()
    player.tick()
    for _ in range(worker._NOTIFY_MAX_RETRIES + 1):
        worker._fire_pending_notify()
    for _ in range(worker._COVER_REPLACE_MAX_ATTEMPTS):
        worker._fire_cover_replace()
    assert worker._replace_track is None
    worker._cover_fetcher.local[(ARTIST, TITLE)] = COVER_FILE
    worker._fire_cover_replace()
    assert len(notifier.run_calls) == 1
    worker._replace_timer.stop()


def test_cover_watch_stops_when_the_song_changes(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN)
    player.play()
    player.tick()
    for _ in range(worker._NOTIFY_MAX_RETRIES + 1):
        worker._fire_pending_notify()
    player.play(title="Silk Road Radio")
    player.tick()
    worker._cover_fetcher.local[(ARTIST, TITLE)] = COVER_FILE
    worker._fire_cover_replace()
    assert len(notifier.run_calls) == 1
    assert worker._replace_track is None
    worker._replace_timer.stop()
    worker._notify_timer.stop()


def test_no_cover_watch_without_a_notification_id(rig):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN)
    notifier.next_id = ""
    player.play()
    player.tick()
    for _ in range(worker._NOTIFY_MAX_RETRIES + 1):
        worker._fire_pending_notify()
    assert len(notifier.run_calls) == 1
    assert worker._replace_timer is None


def test_notify_send_failure_is_not_fatal(rig, monkeypatch):
    worker, player, _, notifier = rig(notify_bin=NOTIFY_BIN, behavior={"cover_art": False})

    def broken(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(notifier, "Popen", broken)
    monkeypatch.setattr(notifier, "run", broken)
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert worker._notify(song(), capture_id=True) is None


def test_notify_send_installed_later_is_found(rig, monkeypatch):
    worker, player, _, notifier = rig(notify_bin=None, behavior={"cover_art": False})
    monkeypatch.setattr(daemon.shutil, "which", lambda name: NOTIFY_BIN)
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert notifier.popen[0][0] == NOTIFY_BIN


def test_control_stays_with_active_bluetooth(rig):
    worker, _, clock, _ = rig()
    bt = Player(worker, clock, source="bluetooth")
    bt.play()
    bt.tick()
    worker.control_previous()
    assert worker._bluetooth.controls == ["previous"]
    assert worker._mpris.controls == []


def test_play_and_pause_reach_the_active_source_as_themselves(rig):
    worker, player, _, _ = rig()
    player.play()
    player.tick()
    worker.control_pause()
    worker.control_pause()
    worker.control_play()
    assert worker._mpris.controls == ["pause", "pause", "play"]


def test_without_notify_send_the_notification_still_goes_out(rig, monkeypatch):
    """libnotify is only suggested, not required — the service is asked directly."""
    worker, player, _, _ = rig(notify_bin=None)
    worker._cover_fetcher.local[(ARTIST, TITLE)] = COVER_FILE
    sent = []
    monkeypatch.setattr(
        daemon,
        "notify_over_dbus",
        lambda image, title, body, **kw: sent.append((image, title, body)) or 11,
    )
    player.play()
    player.tick()
    worker._fire_pending_notify()
    assert sent and sent[0][1] == TITLE
    assert sent[0][0] == str(COVER_FILE)
