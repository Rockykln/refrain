"""Refrain's own MPRIS server: lifecycle, dispatch setup and properties, without a real bus."""

from __future__ import annotations

import sys
import threading
import types

import pytest

pytest.importorskip("dbus")

import dbus  # noqa: E402
import dbus.service  # noqa: E402

import refrain.sources.mpris_server as M  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402

PLAYER = "org.mpris.MediaPlayer2.Player"


@pytest.fixture
def loop_state(monkeypatch):
    """Fresh module-level dispatch state, restored afterwards."""
    monkeypatch.setattr(M, "_DBUS_LOOP_INITIALIZED", False)
    monkeypatch.setattr(M, "_DBUS_LOOP_INIT_FAILED", False)
    monkeypatch.setattr(M, "_GLIB_THREAD", None)


@pytest.fixture
def fake_glib(monkeypatch):
    """A stand-in for gi.repository.GLib that records idle callbacks."""
    glib = types.SimpleNamespace(idle=[], loops=[])
    glib.idle_add = glib.idle.append

    class _Loop:
        def run(self):
            glib.loops.append(threading.current_thread().name)

    glib.MainLoop = _Loop
    repo = types.ModuleType("gi.repository")
    repo.GLib = glib
    gi = types.ModuleType("gi")
    gi.repository = repo
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    monkeypatch.setitem(sys.modules, "gi.repository.GLib", glib)
    return glib


def _server():
    calls = []
    server = M.MPRISServer(
        on_play_pause=lambda: calls.append("play_pause"),
        on_next=lambda: calls.append("next"),
        on_previous=lambda: calls.append("previous"),
    )
    return server, calls


def _in_thread(fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()))
    t.start()
    t.join(5)
    return out[0]


def test_missing_pygobject_disables_the_dispatch_and_warns_once(loop_state, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "gi.repository.GLib", None)
    with caplog.at_level("WARNING", logger=M.__name__):
        assert M._ensure_dbus_glib_loop() is False
        assert M._ensure_dbus_glib_loop() is False
    assert M._DBUS_LOOP_INIT_FAILED is True
    assert len([r for r in caplog.records if "PyGObject not installed" in r.getMessage()]) == 1


def test_the_dispatch_is_wired_into_glib_once(loop_state, fake_glib, monkeypatch):
    wired = []
    monkeypatch.setattr(M, "DBusGMainLoop", lambda **kw: wired.append(kw))
    monkeypatch.setattr(M, "threads_init", lambda: wired.append("threads"))
    assert M._ensure_dbus_glib_loop() is True
    assert M._ensure_dbus_glib_loop() is True
    assert wired == [{"set_as_default": True}, "threads"]


def test_no_pump_before_the_dispatch_is_wired(loop_state, fake_glib):
    M.ensure_dbus_dispatch_pump()
    assert M._GLIB_THREAD is None


def test_no_pump_when_qt_already_runs_glib(loop_state, fake_glib, monkeypatch):
    monkeypatch.setattr(M, "_DBUS_LOOP_INITIALIZED", True)
    monkeypatch.setattr(M, "_qt_pumps_glib_context", lambda: True)
    M.ensure_dbus_dispatch_pump()
    assert M._GLIB_THREAD is None
    assert M._bus_thread() is threading.main_thread()


def test_an_own_glib_loop_runs_when_qt_does_not(loop_state, fake_glib, monkeypatch):
    monkeypatch.setattr(M, "_DBUS_LOOP_INITIALIZED", True)
    monkeypatch.setattr(M, "_qt_pumps_glib_context", lambda: False)
    M.ensure_dbus_dispatch_pump()
    thread = M._GLIB_THREAD
    thread.join(5)
    assert thread.daemon and thread.name == "refrain-glib-loop"
    assert fake_glib.loops == ["refrain-glib-loop"]
    assert M._bus_thread() is thread
    M.ensure_dbus_dispatch_pump()
    assert M._GLIB_THREAD is thread


def test_without_a_qapplication_qt_pumps_nothing(monkeypatch):
    from PySide6.QtCore import QCoreApplication

    monkeypatch.setattr(QCoreApplication, "instance", staticmethod(lambda: None))
    assert M._qt_pumps_glib_context() is False


@pytest.mark.parametrize(
    ("dispatcher", "pumps"),
    [("QEventDispatcherGlib", True), ("QEventDispatcherUNIX", False), (None, False)],
)
def test_only_a_glib_event_dispatcher_pumps_dbus(monkeypatch, dispatcher, pumps):
    from PySide6.QtCore import QAbstractEventDispatcher, QCoreApplication

    fake = None
    if dispatcher:
        meta = types.SimpleNamespace(className=lambda: dispatcher)
        fake = types.SimpleNamespace(metaObject=lambda: meta)
    monkeypatch.setattr(QCoreApplication, "instance", staticmethod(lambda: object()))
    monkeypatch.setattr(QAbstractEventDispatcher, "instance", staticmethod(lambda: fake))
    assert M._qt_pumps_glib_context() is pumps


def test_on_the_bus_thread_a_call_runs_at_once(loop_state, fake_glib):
    seen = []
    M._on_bus_thread(seen.append, "now")
    assert seen == ["now"]
    assert fake_glib.idle == []


def test_from_another_thread_a_call_is_deferred_to_glib(loop_state, fake_glib):
    seen = []
    _in_thread(lambda: M._on_bus_thread(seen.append, "later"))
    assert seen == []
    [callback] = fake_glib.idle
    assert callback() is False, "runs once"
    assert seen == ["later"]


def test_a_failing_deferred_call_is_logged_not_raised(loop_state, fake_glib, caplog):
    def boom():
        raise RuntimeError("bus went away")

    _in_thread(lambda: M._on_bus_thread(boom))
    with caplog.at_level("ERROR", logger=M.__name__):
        assert fake_glib.idle[0]() is False
    assert "deferred call failed" in caplog.text


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (PlaybackStatus.PLAYING, "Playing"),
        (PlaybackStatus.PAUSED, "Paused"),
        (PlaybackStatus.STOPPED, "Stopped"),
    ],
)
def test_playback_status_uses_the_mpris_words(status, text):
    server, _ = _server()
    server._track = TrackInfo(source="mpris", title="Paper Satellites", status=status)
    assert server.Get(PLAYER, "PlaybackStatus") == text


def test_the_song_url_and_album_reach_the_metadata():
    track = TrackInfo(
        source="mpris",
        title="Paper Satellites",
        album="Low Tide",
        url="https://music.apple.com/us/album/low-tide/1?i=2",
    )
    md = M._build_metadata(track, None, 0)
    assert md["xesam:url"] == "https://music.apple.com/us/album/low-tide/1?i=2"
    assert md["xesam:album"] == "Low Tide"
    assert "mpris:length" not in md, "an effective length of 0 means unknown"


def test_an_untitled_track_still_has_a_track_id():
    assert M._track_id(TrackInfo(source="mpris")) == "/refrain/track/unknown"


def test_the_player_advertises_what_it_can_do():
    server, _ = _server()
    props = server.GetAll(PLAYER)
    assert props["CanGoNext"] and props["CanGoPrevious"] and props["CanControl"]
    assert props["CanPlay"] and props["CanPause"]
    assert not props["CanSeek"]
    assert props["LoopStatus"] == "None"
    root = server.GetAll("org.mpris.MediaPlayer2")
    assert not root["CanQuit"] and not root["CanRaise"]


def test_a_known_property_is_read_on_its_own():
    server, _ = _server()
    server._track = TrackInfo(source="mpris", title="Paper Satellites", position_ms=1_500)
    assert server.Get(PLAYER, "Position") == 1_500_000


def test_an_unknown_interface_has_no_properties():
    server, _ = _server()
    assert dict(server.GetAll("org.example.Nothing")) == {}


@pytest.mark.parametrize(("method", "call"), [("Next", "next"), ("Previous", "previous")])
def test_skip_methods_reach_the_callbacks(method, call):
    server, calls = _server()
    getattr(server, method)()
    assert calls == [call]


def test_a_failing_callback_does_not_reach_the_caller(caplog):
    def boom():
        raise RuntimeError("source gone")

    server = M.MPRISServer(on_play_pause=boom, on_next=boom, on_previous=boom)
    with caplog.at_level("DEBUG", logger=M.__name__):
        server.PlayPause()
        server.Next()
        server.Previous()
    assert caplog.text.count("source gone") == 3


def test_unsupported_methods_touch_no_source():
    server, calls = _server()
    server.Raise()
    server.Quit()
    server.Seek(5_000_000)
    server.SetPosition(dbus.ObjectPath("/refrain/track/x"), 0)
    server.OpenUri("https://example.org/song")
    server.Set(PLAYER, "Volume", dbus.Double(0.5))
    assert calls == []
    assert server.Get(PLAYER, "Volume") == 1.0


def test_a_started_server_does_not_register_again(monkeypatch):
    server, _ = _server()
    server._bus_name = object()
    monkeypatch.setattr(M, "_ensure_dbus_glib_loop", lambda: pytest.fail("no second start"))
    assert server.start() is True


def test_without_dispatch_the_server_does_not_start(monkeypatch):
    server, _ = _server()
    monkeypatch.setattr(M, "_ensure_dbus_glib_loop", lambda: False)
    assert server.start() is False
    assert server._bus_name is None


def test_start_from_the_daemon_thread_defers_registering(loop_state, monkeypatch):
    server, _ = _server()
    deferred = []
    monkeypatch.setattr(M, "_ensure_dbus_glib_loop", lambda: True)
    monkeypatch.setattr(M, "_on_bus_thread", lambda fn, *a: deferred.append(fn))
    assert _in_thread(server.start) is True
    assert deferred == [server._register]


def test_start_on_the_bus_thread_publishes_the_name(loop_state, monkeypatch):
    server, _ = _server()
    bus, exported = object(), []
    monkeypatch.setattr(M, "_ensure_dbus_glib_loop", lambda: True)
    monkeypatch.setattr(dbus, "SessionBus", lambda: bus)
    monkeypatch.setattr(
        dbus.service, "BusName", lambda name, b, do_not_queue: (name, b, do_not_queue)
    )
    monkeypatch.setattr(
        dbus.service.Object, "__init__", lambda self, name, path: exported.append((name, path))
    )
    assert server.start() is True
    assert server._bus_name == ("org.mpris.MediaPlayer2.refrain", bus, True)
    assert exported == [(server._bus_name, "/org/mpris/MediaPlayer2")]
    assert server._register() is True
    assert len(exported) == 1


def test_a_taken_bus_name_fails_the_start_and_allows_a_retry(loop_state, monkeypatch):
    server, _ = _server()
    monkeypatch.setattr(M, "_ensure_dbus_glib_loop", lambda: True)
    monkeypatch.setattr(dbus, "SessionBus", lambda: object())

    def taken(*_a, **_k):
        raise dbus.exceptions.NameExistsException("org.mpris.MediaPlayer2.refrain")

    monkeypatch.setattr(dbus.service, "BusName", taken)
    assert server.start() is False
    assert server._bus_name is None


def test_stop_unpublishes_the_server():
    server, _ = _server()
    removed = []
    server._bus_name = object()
    server.remove_from_connection = lambda: removed.append(True)
    server.stop()
    assert removed == [True]
    assert server._bus_name is None


def test_stop_survives_a_failing_unexport():
    server, _ = _server()
    server._bus_name = object()

    def boom():
        raise LookupError("not exported")

    server.remove_from_connection = boom
    server.stop()
    assert server._bus_name is None


def test_stop_before_start_does_nothing():
    server, _ = _server()
    server.remove_from_connection = lambda: pytest.fail("nothing to remove")
    server.stop()


def test_update_before_start_keeps_the_empty_track(loop_state):
    server, _ = _server()
    server.update(TrackInfo(source="mpris", title="Paper Satellites"), None)
    assert server._track.has_track is False


def test_update_announces_a_new_track(loop_state):
    server, _ = _server()
    server._bus_name = object()
    sent = []
    server.PropertiesChanged = lambda iface, changed, inv: sent.append((iface, changed))
    track = TrackInfo(
        source="mpris",
        title="Paper Satellites",
        artist="The Quiet Hours",
        status=PlaybackStatus.PAUSED,
    )
    server.update(track, "https://example.org/cover.jpg", 201_000)
    [(iface, changed)] = sent
    assert iface == PLAYER
    assert changed["PlaybackStatus"] == "Paused"
    assert changed["Metadata"]["mpris:artUrl"] == "https://example.org/cover.jpg"
    assert server.Get(PLAYER, "Metadata")["mpris:length"] == 201_000_000


def test_a_status_change_alone_is_announced(loop_state):
    server, _ = _server()
    server._bus_name = object()
    sent = []
    server.PropertiesChanged = lambda iface, changed, inv: sent.append(changed["PlaybackStatus"])
    playing = TrackInfo(source="mpris", title="Paper Satellites", status=PlaybackStatus.PLAYING)
    paused = TrackInfo(source="mpris", title="Paper Satellites", status=PlaybackStatus.PAUSED)
    server.update(playing, None)
    server.update(paused, None)
    assert sent == ["Playing", "Paused"]


def test_a_failing_signal_does_not_break_the_update(loop_state):
    server, _ = _server()
    server._bus_name = object()

    def boom(*_a):
        raise dbus.exceptions.DBusException("Disconnected")

    server.PropertiesChanged = boom
    track = TrackInfo(source="mpris", title="Paper Satellites")
    server.update(track, None)
    assert server._track is track


def test_a_deferred_update_after_stop_is_dropped():
    server, _ = _server()
    server._apply(TrackInfo(source="mpris", title="Paper Satellites"), None, None)
    assert server._track.has_track is False
