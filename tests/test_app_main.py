"""main(): startup refusals, how the windows, daemon and updater are wired, and restart."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from refrain import app, uninstall  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.single_instance import AlreadyRunning, SessionBusUnavailable  # noqa: E402
from refrain.startup_check import INVALID, OK, CheckResult  # noqa: E402
from refrain.updater import ReleaseInfo  # noqa: E402

CLIENT_ID = "1234567890123456789"


class _Recorder(QObject):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def _rec(self, name, *args):
        self.calls.append((name, *args))

    def called(self, name) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == name]


class _Window(_Recorder):
    def show(self):
        self._rec("show")

    def raise_(self):
        self._rec("raise_")

    def activateWindow(self):
        self._rec("activateWindow")

    def hide(self):
        self._rec("hide")

    def set_developer_mode(self, on):
        self._rec("set_developer_mode", on)

    def set_log_level(self, level):
        self._rec("set_log_level", level)

    def use_config(self, config):
        self._rec("use_config", config)

    def show_developer_tab(self):
        self._rec("show_developer_tab")


class FakeApp(_Recorder):
    aboutToQuit = Signal()

    def __init__(self, h, argv):
        super().__init__()
        self.h = h
        h.app = self

    def setApplicationName(self, name):
        self._rec("name", name)

    def setApplicationDisplayName(self, name):
        pass

    def setApplicationVersion(self, version):
        pass

    def setDesktopFileName(self, name):
        self._rec("desktop_file", name)

    def setQuitOnLastWindowClosed(self, value):
        self._rec("quit_on_last_window", value)

    def setWindowIcon(self, icon):
        self._rec("icon")

    def exec(self):
        self.h.order.append("exec")
        if self.h.during_exec:
            self.h.during_exec(self.h)
        return self.h.rc

    def quit(self):
        self._rec("quit")


class FakeTray(_Recorder):
    statusRequested = Signal()
    sharingToggled = Signal(bool)
    settingsRequested = Signal()
    quitRequested = Signal()
    restartRequested = Signal()
    playPauseRequested = Signal()
    nextRequested = Signal()
    previousRequested = Signal()
    updateRequested = Signal()
    logRequested = Signal()
    historyRequested = Signal()
    developerRequested = Signal()

    def set_track(self, t):
        self._rec("set_track", t)

    def set_status(self, s):
        self._rec("set_status", s)

    def set_progress(self, pos, dur):
        self._rec("set_progress", pos, dur)

    def set_service_status(self, snapshot):
        self._rec("set_service_status", snapshot)

    def set_sharing_paused(self, paused):
        self._rec("set_sharing_paused", paused)

    def show_hint(self, text, on_click=None):
        self._rec("show_hint", text, on_click)

    def set_history_enabled(self, on):
        self._rec("set_history_enabled", on)

    def set_developer_mode(self, on):
        self._rec("set_developer_mode", on)

    def set_update_available(self, on, version):
        self._rec("set_update_available", on, version)


class FakeWorker(_Recorder):
    trackChanged = Signal(object)
    statusChanged = Signal(object)
    progressTick = Signal(int, int)
    discordStateChanged = Signal(str, str)
    lastfmStateChanged = Signal(str, str)
    historyChanged = Signal(object)
    coverChanged = Signal(str)

    _rpc = None

    def history_snapshot(self):
        return "snapshot-0"

    def control_play_pause(self):
        self._rec("play_pause")

    def control_next(self):
        self._rec("next")

    def control_previous(self):
        self._rec("previous")

    def update_config(self, c):
        self._rec("update_config", c)

    def set_notifications_muted(self, muted):
        self._rec("set_notifications_muted", muted)

    def clear_history(self):
        self._rec("clear_history")

    def remove_from_history(self, entry):
        self._rec("remove_from_history", entry)


class FakeDaemon:
    def __init__(self, h, config):
        h.daemon = self
        self.h = h
        self.config = config
        self.worker = FakeWorker()

    def start(self):
        self.h.order.append("daemon.start")

    def stop(self):
        self.h.order.append("daemon.stop")


class FakeSettings(_Window):
    applied = Signal(object)
    finished = Signal(int)
    checkUpdatesRequested = Signal()
    showLogRequested = Signal()
    showHistoryRequested = Signal()
    restartRequested = Signal()
    uninstallRequested = Signal()

    def set_latest_release(self, r):
        self._rec("set_latest_release", r)

    def use_config(self, c):
        self._rec("use_config", c)


class FakeHistory(_Window):
    clearRequested = Signal()
    removeRequested = Signal(object)
    sizeRemembered = Signal(int, int)

    def set_snapshot(self, s):
        self._rec("set_snapshot", s)


class FakeStatus(_Window):
    finished = Signal(int)
    visibilityChanged = Signal(bool)
    playPauseRequested = Signal()
    nextRequested = Signal()
    previousRequested = Signal()
    settingsRequested = Signal(str)
    historyRequested = Signal()
    updateRequested = Signal()
    sharingToggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.visible = False

    def show(self):
        self.visible = True
        self._rec("show")

    def isVisible(self):
        return self.visible

    def set_track(self, t):
        self._rec("set_track", t)

    def set_playback(self, s):
        self._rec("set_playback", s)

    def set_cover(self, url):
        self._rec("set_cover", url)

    def set_crash_report(self, path):
        self._rec("set_crash_report", path)

    def set_progress(self, position_ms, duration_ms):
        self._rec("set_progress", position_ms, duration_ms)

    def set_history(self, s):
        self._rec("set_history", s)

    def set_status(self, s):
        self._rec("set_status", s)

    def set_sharing_paused(self, paused):
        self._rec("set_sharing_paused", paused)

    def set_update_available(self, version):
        self._rec("set_update_available", version)

    def set_welcome(self, on):
        self._rec("set_welcome", on)


class FakeWelcome(_Window):
    applied = Signal(str)
    finished = Signal(int)

    def start_diagnostics(self):
        self._rec("start_diagnostics")


class Harness:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.order: list[str] = []
        self.rc = 0
        self.during_exec = None
        self.app = None
        self.daemon = None
        self.tray = None
        self.settings = None
        self.history = None
        self.log_window = None
        self.welcome = None
        self.status = None
        self.activation = None
        self.acquire_kwargs: list[dict] = []
        self.updater = None
        self.boxes: list[tuple[str, str]] = []
        self.shown_boxes: list = []
        self.closed_boxes: list = []
        self.dialogs: list = []
        self.shots: list[tuple[int, object]] = []
        self.timers: list = []
        self.log_levels: list[str] = []
        self.execs: list[tuple[str, list[str]]] = []
        self.exec_errors: list[OSError] = []
        self.tray_available = True
        self.lock: object = object()
        self.missing: list = []
        self.config = Config()
        self.config.behavior.first_run_complete = True
        self.config.discord.client_id = CLIENT_ID
        self.saves = 0
        self.config.save = self._save
        self._patch()

    def _save(self):
        self.saves += 1

    def _patch(self):
        h, mp = self, self.mp
        mp.setattr(app, "_forced_debug", False)
        mp.setattr(app, "setup_logging", lambda level: h.log_levels.append(level))
        # Real detection, without arming faulthandler on a real descriptor.
        mp.setattr(
            app,
            "_crash_log",
            lambda: contextlib.nullcontext(
                app._crash_since_last_start(app.state_dir() / "crash.log")
            ),
        )
        mp.setattr(app, "attach_qt_log_bridge", lambda: "bridge")
        mp.setattr(app, "qInstallMessageHandler", lambda fn: None)
        mp.setattr("refrain.sources.mpris_server._ensure_dbus_glib_loop", lambda: None)
        mp.setattr("refrain.sources.mpris_server.ensure_dbus_dispatch_pump", lambda: None)
        mp.setattr(Config, "load", classmethod(lambda cls, path=None: h.config))
        mp.setattr("refrain.secrets_store.load_into", lambda lastfm: None)
        mp.setattr(app, "cleanup_orphan_downloads", lambda: h.order.append("cleanup"))
        mp.setattr(app, "_augment_qt_plugin_path", lambda: None)
        mp.setattr(app.qt_libraries, "missing_libraries", lambda path: h.missing)
        mp.setattr(app, "QApplication", lambda argv: FakeApp(h, argv))
        mp.setattr(app, "_install_translators", lambda a, lang: ["translator", lang])
        mp.setattr(app, "install_global_interactive_cursors", lambda a: None)
        mp.setattr(app, "_install_signal_handlers", lambda a: None)
        mp.setattr(app, "_sync_autostart", lambda c: h.order.append("autostart"))
        mp.setattr(app, "acquire_lock", self._acquire)

        class Tray:
            @staticmethod
            def isSystemTrayAvailable():
                return h.tray_available

        mp.setattr(app, "QSystemTrayIcon", Tray)

        class Box:
            Icon = QMessageBox.Icon
            StandardButton = QMessageBox.StandardButton

            def __init__(self, icon, title, text, buttons=None):
                h.boxes.append(("information", text))
                self.accepted = False

            def accept(self):
                self.accepted = True
                h.closed_boxes.append(self)

            def exec(self):
                h.shown_boxes.append(self)
                return 0

            @staticmethod
            def information(parent, title, text):
                h.boxes.append(("information", text))

            @staticmethod
            def warning(parent, title, text):
                h.boxes.append(("warning", text))

            @staticmethod
            def critical(parent, title, text):
                h.boxes.append(("critical", text))

        mp.setattr(app, "QMessageBox", Box)

        class Timer(QObject):
            def __init__(self, parent=None):
                super().__init__()
                self.timeout = _Hook()
                self.interval = None
                h.timers.append(self)

            def setInterval(self, ms):
                self.interval = ms

            def start(self):
                pass

            @staticmethod
            def singleShot(ms, context, fn=None):
                h.shots.append((ms, fn if fn is not None else context))

        mp.setattr(app, "QTimer", Timer)
        mp.setattr(app, "TrayIcon", lambda: self._keep("tray", FakeTray()))
        mp.setattr(app, "Daemon", lambda c: FakeDaemon(h, c))
        mp.setattr(app, "SettingsWindow", lambda c: self._keep("settings", FakeSettings()))
        mp.setattr(app, "LogWindow", lambda bridge: self._keep("log_window", _Window()))
        mp.setattr(app, "HistoryWindow", lambda loc, size: self._keep("history", FakeHistory()))
        mp.setattr(app, "WelcomeDialog", lambda: self._keep("welcome", FakeWelcome()))
        mp.setattr(app, "StatusWindow", lambda locale: self._keep("status", FakeStatus()))
        mp.setattr(app, "listen_for_activation", self._listen)

        class Updater(app.UpdateOrchestrator):
            def __init__(self, config):
                super().__init__(config)
                h.updater = self
                self.checks = []

            def check_now(self, manual=True, silent=False):
                self.checks.append((manual, silent))

        mp.setattr(app, "UpdateOrchestrator", Updater)

        class Dialog:
            def __init__(self, release, parent=None):
                self.release = release

            def exec(self):
                h.dialogs.append(self.release)

        mp.setattr(app, "UpdateDialog", Dialog)
        mp.setattr(app.os, "execvp", self._execvp)
        mp.setattr(logging, "shutdown", lambda: None)

    def _keep(self, name, obj):
        setattr(self, name, obj)
        return obj

    def _listen(self, bus, on_activate):
        self.activation = (bus, on_activate)
        return "activator"

    def _acquire(self, **kwargs):
        self.acquire_kwargs.append(kwargs)
        if isinstance(self.lock, Exception):
            raise self.lock
        return self.lock

    def _execvp(self, binary, argv):
        self.execs.append((binary, argv))
        if self.exec_errors:
            raise self.exec_errors.pop(0)

    def shot(self, ms):
        return next(fn for delay, fn in self.shots if delay == ms)

    def run(self, *args: str) -> int:
        self.mp.setattr(sys, "argv", ["refrain", *args])
        return app.main()


class _Hook:
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)


@pytest.fixture
def h(monkeypatch):
    QApplication.instance() or QApplication(sys.argv)
    root = logging.getLogger()
    level = root.level
    yield Harness(monkeypatch)
    root.setLevel(level)


def _release(version="999.0.0") -> ReleaseInfo:
    return ReleaseInfo(
        tag=f"v{version}",
        version=version,
        name=f"Refrain v{version}",
        body="notes",
        html_url=f"https://github.com/Rockykln/refrain/releases/tag/v{version}",
    )


def test_missing_qt_libraries_stop_before_qt(h, monkeypatch, capsys):
    notified = []
    h.missing = ["libxcb-cursor.so.0"]
    monkeypatch.setattr(app.qt_libraries, "message", lambda missing: "need xcb-cursor")
    monkeypatch.setattr(app.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(app.subprocess, "run", lambda cmd, check: notified.append(cmd))
    assert h.run() == 1
    assert h.app is None
    assert "need xcb-cursor" in capsys.readouterr().err
    assert notified[0][0] == "/usr/bin/notify-send"
    assert notified[0][-1] == "need xcb-cursor"


def test_second_instance_says_so_and_closes_the_note_by_itself(h):
    h.lock = AlreadyRunning()
    assert h.run() == 0
    assert h.boxes == [("information", "Refrain is already running.")]
    assert h.daemon is None
    # Nothing to decide, so the note goes away on its own.
    (box,) = h.shown_boxes
    h.shot(app._ALREADY_RUNNING_MS)()
    assert box.accepted is True


@pytest.mark.parametrize(
    ("flatpak_id", "expected"),
    [(None, "refrain"), ("io.github.Rockykln.Refrain", "io.github.Rockykln.Refrain")],
)
def test_desktop_file_name_matches_the_installed_one(h, monkeypatch, flatpak_id, expected):
    if flatpak_id:
        monkeypatch.setenv("FLATPAK_ID", flatpak_id)
    else:
        monkeypatch.delenv("FLATPAK_ID", raising=False)
    h.run()
    assert h.app.called("desktop_file") == [(expected,)]


def test_second_instance_leaves_keyring_and_update_download_alone(h, monkeypatch):
    monkeypatch.setattr("refrain.secrets_store.load_into", lambda lastfm: h.order.append("keyring"))
    h.lock = AlreadyRunning()
    h.run()
    assert h.order == []


def test_first_instance_loads_secrets_and_cleans_up_once_it_holds_the_lock(h, monkeypatch):
    monkeypatch.setattr("refrain.secrets_store.load_into", lambda lastfm: h.order.append("keyring"))
    lock = h.lock

    def acquire(**kwargs):
        h.order.append("lock")
        return lock

    monkeypatch.setattr(app, "acquire_lock", acquire)
    h.run()
    assert h.order[:3] == ["lock", "cleanup", "keyring"]


def test_no_session_bus_is_an_error_with_the_reason(h):
    h.lock = SessionBusUnavailable("no DBUS_SESSION_BUS_ADDRESS")
    assert h.run() == 1
    kind, text = h.boxes[0]
    assert kind == "critical"
    assert "no DBUS_SESSION_BUS_ADDRESS" in text
    assert h.daemon is None


def test_no_system_tray_refuses_to_start(h):
    h.tray_available = False
    assert h.run() == 1
    assert h.boxes[0][0] == "critical"
    assert "No system tray available" in h.boxes[0][1]
    assert h.daemon is None


def test_normal_start_runs_the_daemon_around_the_event_loop(h):
    assert h.run() == 0
    assert h.log_levels == ["INFO"]
    assert h.order == ["cleanup", "autostart", "daemon.start", "exec", "daemon.stop"]
    assert h.app.called("name") == [("Refrain",)]
    assert h.app.called("quit_on_last_window") == [(False,)]
    assert h.app._refrain_bus_lock is h.lock
    assert h.app._refrain_translators == ["translator", "system"]
    assert h.settings.called("show") == []
    assert h.status.called("show") == [()]
    assert h.log_window.called("show") == []
    assert h.history.called("set_snapshot") == [("snapshot-0",)]
    assert h.status.called("set_history") == [("snapshot-0",)]
    assert h.tray.called("set_history_enabled") == [(True,)]
    assert h.shot(2000) == h.updater.maybe_check_on_startup


def test_exit_code_of_the_event_loop_is_returned(h):
    h.rc = 3
    assert h.run() == 3


def test_silent_start_keeps_every_window_hidden(h):
    h.run("--silent")
    assert h.settings.called("show") == []
    assert h.status.called("show") == []


def test_debug_logs_everything_and_opens_the_live_log(h, monkeypatch):
    h.config.advanced.log_level = "WARNING"
    logging.getLogger().setLevel(logging.DEBUG)
    h.run("--debug")
    assert h.log_levels == ["DEBUG"]
    assert app._forced_debug is True
    assert h.log_window.called("show") == [()]
    h.settings.applied.emit(h.config)
    assert logging.getLogger().level == logging.DEBUG


def test_configured_log_level_applies_without_debug(h):
    h.config.advanced.log_level = "WARNING"
    h.run()
    assert logging.getLogger().level == logging.WARNING


def test_configured_language_reaches_the_translators(h):
    h.config.advanced.language = "de"
    h.run()
    assert h.app._refrain_translators == ["translator", "de"]


def test_tray_buttons_reach_daemon_settings_and_app(h):
    h.run()
    h.tray.playPauseRequested.emit()
    h.tray.nextRequested.emit()
    h.tray.previousRequested.emit()
    assert [c[0] for c in h.daemon.worker.calls] == ["play_pause", "next", "previous"]
    h.tray.settingsRequested.emit()
    assert [c[0] for c in h.settings.calls[-3:]] == ["show", "raise_", "activateWindow"]
    h.tray.logRequested.emit()
    assert h.log_window.called("show") == [()]
    h.tray.quitRequested.emit()
    assert h.app.called("quit") == [()]


def test_daemon_updates_reach_the_tray_and_the_status_window(h):
    h.run()
    h.daemon.worker.trackChanged.emit("track")
    h.daemon.worker.statusChanged.emit("playing")
    h.daemon.worker.progressTick.emit(1000, 2000)
    h.daemon.worker.discordStateChanged.emit("ready", "")
    h.daemon.worker.lastfmStateChanged.emit("scrobbling", "refrain_demo")
    assert [c[0] for c in h.tray.calls[-5:]] == [
        "set_track",
        "set_status",
        "set_progress",
        "set_service_status",
        "set_service_status",
    ]
    assert h.tray.called("set_progress") == [(1000, 2000)]
    assert h.status.called("set_track") == [("track",)]
    assert h.status.called("set_playback") == [("playing",)]
    (last,) = h.status.called("set_status")[-1]
    assert (last.discord, last.lastfm, last.lastfm_detail) == (
        "ready",
        "scrobbling",
        "refrain_demo",
    )


def test_applied_settings_reach_daemon_updater_and_autostart(h):
    h.run()
    new = Config()
    new.save = lambda: None
    h.settings.applied.emit(new)
    assert h.daemon.worker.called("update_config") == [(new,)]
    assert h.updater._config is new
    assert h.order.count("autostart") == 2


def test_check_button_asks_for_a_manual_check(h):
    h.run()
    h.settings.checkUpdatesRequested.emit()
    assert h.updater.checks == [(True, False)]


def test_found_update_badges_the_tray_and_opens_the_dialog(h):
    h.run()
    release = _release()
    h.updater.updateAvailable.emit(release)
    assert h.tray.called("set_update_available") == [(True, "999.0.0")]
    assert h.dialogs == [release]


def test_tray_update_entry_opens_the_last_release(h):
    h.run()
    h.updater._latest = _release()
    h.tray.updateRequested.emit()
    assert h.dialogs == [h.updater._latest]


def test_manual_check_results_are_shown_to_the_user(h):
    h.run()
    h.updater.checkUpToDate.emit("0.5.3")
    h.updater.checkFailed.emit("offline")
    assert h.boxes[0][0] == "information"
    assert "0.5.3" in h.boxes[0][1]
    assert h.boxes[1] == ("warning", "offline")


def test_fetched_release_notes_reach_settings(h):
    h.run()
    release = _release()
    h.updater.releaseInfoFetched.emit(release)
    assert h.settings.called("set_latest_release") == [(release,)]


def test_history_window_opens_only_while_history_is_on(h):
    h.run()
    h.tray.historyRequested.emit()
    assert h.history.called("show") == [()]
    off = Config()
    off.history.enabled = False
    off.save = lambda: None
    h.settings.applied.emit(off)
    assert h.history.called("hide") == [()]
    assert h.tray.called("set_history_enabled")[-1] == (False,)
    h.settings.showHistoryRequested.emit()
    assert h.history.called("show") == [()]


def test_history_actions_reach_the_daemon(h):
    h.run()
    h.history.clearRequested.emit()
    h.history.removeRequested.emit("entry")
    h.daemon.worker.historyChanged.emit("snapshot-1")
    assert h.daemon.worker.called("clear_history") == [()]
    assert h.daemon.worker.called("remove_from_history") == [("entry",)]
    assert h.history.called("set_snapshot")[-1] == ("snapshot-1",)


def test_history_size_is_saved_only_when_it_changed(h):
    h.run()
    width, height = h.config.history.window_width, h.config.history.window_height
    h.history.sizeRemembered.emit(width, height)
    assert h.saves == 0
    h.history.sizeRemembered.emit(width + 10, height + 20)
    assert (h.config.history.window_width, h.config.history.window_height) == (
        width + 10,
        height + 20,
    )
    assert h.saves == 1


def test_history_size_follows_a_reset_config(h):
    h.run()
    fresh = Config()
    fresh_saves = []
    fresh.save = lambda: fresh_saves.append(True)
    h.settings.applied.emit(fresh)
    h.history.sizeRemembered.emit(fresh.history.window_width + 1, fresh.history.window_height)
    assert fresh_saves == [True]
    assert h.saves == 0


def test_history_size_that_cannot_be_saved_is_only_logged(h, caplog):
    def _refuse():
        raise OSError("disk full")

    h.run()
    h.config.save = _refuse
    h.history.sizeRemembered.emit(1, 2)
    assert "disk full" in caplog.text


def test_uninstall_stops_the_daemon_purges_and_quits(h, monkeypatch):
    report = uninstall.UninstallReport(removed=["a"], failed=["/x/locked"])
    monkeypatch.setattr(uninstall, "purge", lambda: h.order.append("purge") or report)
    monkeypatch.setattr("refrain.updater.detect_install_type", lambda: "pip")
    h.run()
    h.settings.uninstallRequested.emit()
    assert h.order[-2:] == ["daemon.stop", "purge"]
    kind, text = h.boxes[-1]
    assert kind == "information"
    assert uninstall.removal_command("pip") in text
    assert "/x/locked" in text
    assert h.app.called("quit") == [()]


def test_first_run_shows_the_wizard_instead_of_settings(h):
    h.config.behavior.first_run_complete = False
    h.config.discord.client_id = ""
    h.run()
    assert h.welcome.called("start_diagnostics") == [()]
    assert h.welcome.called("show") == [()]
    assert h.settings.called("show") == []
    assert h.app._refrain_welcome is h.welcome
    h.welcome.finished.emit(1)
    assert h.settings.called("show") == []
    assert h.status.called("set_welcome") == [(True,)]
    assert h.status.called("show") == [()]


def test_wizard_hands_its_client_id_to_everyone(h, monkeypatch):
    saved = []
    monkeypatch.setattr(Config, "save", lambda self, path=None: saved.append(self))
    del h.config.save
    h.config.behavior.first_run_complete = False
    h.config.discord.client_id = ""
    h.run("--silent")
    h.welcome.applied.emit(CLIENT_ID)
    (new,) = h.settings.called("use_config")[0]
    assert new is not h.config
    assert new.discord.client_id == CLIENT_ID
    assert new.behavior.first_run_complete
    assert saved == [new]
    assert h.daemon.worker.called("update_config") == [(new,)]
    assert h.config.discord.client_id == ""
    h.welcome.finished.emit(0)
    assert h.settings.called("show") == []
    assert h.status.called("show") == [()]


def test_startup_check_reports_an_expired_lastfm_session_and_stops_on_quit(h, monkeypatch):
    class Worker(QObject):
        finished = Signal(object, object)

        def __init__(self, lastfm_cfg, rpc):
            super().__init__()

        def run(self):
            self.finished.emit(CheckResult(INVALID, "Invalid session key"), CheckResult(OK))

    monkeypatch.setattr("refrain.startup_check.StartupCheckWorker", Worker)
    h.run("--silent")
    h.daemon.worker.lastfmStateChanged.emit("scrobbling", "refrain_demo")
    h.shot(5000)()
    deadline = time.monotonic() + 5
    while not h.status.called("show"):
        assert time.monotonic() < deadline
        QCoreApplication.processEvents()
        time.sleep(0.002)
    (last,) = h.tray.called("set_service_status")[-1]
    assert last.lastfm == "expired"
    h.app.aboutToQuit.emit()


def test_app_name_is_refreshed_off_the_ui_thread(h, monkeypatch):
    seen = []
    done = threading.Event()

    def _refresh(config):
        seen.append((config, threading.current_thread().name))
        done.set()
        raise RuntimeError("offline")

    monkeypatch.setattr(app, "refresh_application_name", _refresh)
    h.run()
    (timer,) = h.timers
    assert timer.interval == int(app.NAME_TTL_S * 1000)
    assert timer.timeout.slots == [h.shot(3000)]
    h.shot(3000)()
    assert done.wait(5)
    assert seen == [(h.config, "refrain-appname")]


def _restart(h):
    h.tray.restartRequested.emit()


@pytest.fixture
def restart(h, monkeypatch):
    h.during_exec = _restart
    monkeypatch.delenv("APPIMAGE", raising=False)
    return h


def test_restart_re_execs_the_launcher_after_stopping_the_daemon(restart, tmp_path, monkeypatch):
    launcher = tmp_path / "refrain"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    restart.mp.setattr(sys, "argv", [str(launcher), "--silent", "--debug"])
    assert app.main() == 0
    assert restart.order[-2:] == ["exec", "daemon.stop"]
    assert restart.execs == [(str(launcher), [str(launcher), "--silent", "--debug"])]
    assert restart.app._refrain_bus_lock is None


def test_settings_restart_button_restarts_too(restart, tmp_path):
    restart.during_exec = lambda h: h.settings.restartRequested.emit()
    restart.run()
    assert len(restart.execs) == 1


def test_restart_inside_an_appimage_uses_the_appimage(restart, tmp_path, monkeypatch):
    image = tmp_path / "Refrain.AppImage"
    image.write_text("")
    image.chmod(0o755)
    monkeypatch.setenv("APPIMAGE", str(image))
    restart.run("--silent")
    assert restart.execs == [(str(image), [str(image), "--silent"])]


def test_restart_from_a_script_path_falls_back_to_python_m(restart):
    restart.mp.setattr(sys, "argv", ["/src/refrain/__main__.py", "--silent"])
    app.main()
    assert restart.execs == [
        (sys.executable, [sys.executable, "-m", "refrain", "--silent"]),
    ]


def test_failed_re_exec_falls_back_to_python_m(restart, tmp_path):
    launcher = tmp_path / "refrain"
    launcher.write_text("")
    launcher.chmod(0o755)
    restart.exec_errors = [OSError("ENOEXEC")]
    restart.mp.setattr(sys, "argv", [str(launcher)])
    app.main()
    assert restart.execs[1] == (sys.executable, [sys.executable, "-m", "refrain"])


def test_failed_python_m_restart_retries_without_doubling_the_module_args(restart):
    restart.mp.setattr(sys, "argv", ["/src/refrain/__main__.py", "--silent"])
    restart.exec_errors = [OSError("ENOENT")]
    app.main()
    assert restart.execs[1] == (sys.executable, [sys.executable, "-m", "refrain", "--silent"])


def test_restart_that_cannot_exec_at_all_exits_non_zero(restart):
    restart.exec_errors = [OSError("a"), OSError("b")]
    assert restart.run() == 1
    assert len(restart.execs) == 2


def test_plain_quit_does_not_restart(h):
    h.during_exec = lambda h: h.tray.quitRequested.emit()
    assert h.run() == 0
    assert h.execs == []


# Status window


def test_second_start_asks_the_running_one_and_exits_quietly(h):
    h.lock = AlreadyRunning("in use", activated=True)
    assert h.run() == 0
    assert h.acquire_kwargs == [{"activate": True}]
    assert h.boxes == []
    assert h.daemon is None


def test_an_activation_from_a_second_start_brings_up_the_status_window(h):
    h.run("--silent")
    bus, on_activate = h.activation
    assert bus is h.lock
    assert h.app._refrain_activator == "activator"
    on_activate()
    assert [c[0] for c in h.status.calls[-3:]] == ["show", "raise_", "activateWindow"]


def test_tray_click_opens_the_status_window_and_the_menu_keeps_settings(h):
    h.run("--silent")
    h.tray.statusRequested.emit()
    assert h.status.called("show") == [()]
    assert h.settings.called("show") == []
    h.tray.settingsRequested.emit()
    assert h.settings.called("show") == [()]


@pytest.mark.parametrize(
    ("state", "shown"),
    [("not_set_up", True), ("rejected", True), ("ready", False), ("no_client", False)],
)
def test_autostart_opens_the_status_window_only_when_discord_needs_the_user(h, state, shown):
    h.run("--silent")
    h.daemon.worker.discordStateChanged.emit(state, "")
    assert bool(h.status.called("show")) is shown


def test_autostart_opens_it_for_an_expired_lastfm_session(h):
    h.run("--silent")
    h.daemon.worker.lastfmStateChanged.emit("expired", "")
    assert h.status.called("show") == [()]


def test_each_problem_opens_the_window_once(h):
    h.run("--silent")
    worker = h.daemon.worker
    worker.discordStateChanged.emit("rejected", "Invalid Client ID")
    worker.discordStateChanged.emit("ready", "")
    worker.discordStateChanged.emit("rejected", "Invalid Client ID")
    assert h.status.called("show") == [()]
    worker.lastfmStateChanged.emit("expired", "")
    assert h.status.called("show") == [(), ()]


def test_the_wizard_is_not_covered_by_the_status_window(h):
    h.config.behavior.first_run_complete = False
    h.config.discord.client_id = ""
    h.run("--silent")
    h.daemon.worker.discordStateChanged.emit("not_set_up", "")
    assert h.status.called("show") == []
    h.welcome.finished.emit(0)
    assert h.status.called("show") == [()]
    h.daemon.worker.discordStateChanged.emit("ready", "")
    h.daemon.worker.discordStateChanged.emit("not_set_up", "")
    assert h.status.called("show") == [()]


class _Tabs:
    def __init__(self, names):
        self.names = names
        self.current = None

    def count(self):
        return len(self.names)

    def tabText(self, i):
        return self.names[i]

    def setCurrentIndex(self, i):
        self.current = i


class _Field:
    def __init__(self):
        self.focused = False

    def setFocus(self):
        self.focused = True


def test_status_actions_open_the_right_settings_page(h):
    h.run()
    h.settings.tabs = _Tabs(["General", "Sources", "Last.fm", "History"])
    h.settings.tab_pages = {"general": 0, "sources": 1, "lastfm": 2, "history": 3}
    h.settings.client_id_input = _Field()
    h.status.settingsRequested.emit("lastfm")
    assert h.settings.tabs.current == 2
    h.status.settingsRequested.emit("history")
    assert h.settings.tabs.current == 3
    assert not h.settings.client_id_input.focused
    h.status.settingsRequested.emit("discord")
    assert h.settings.tabs.current == 0
    assert h.settings.client_id_input.focused
    h.settings.tabs.current = None
    h.status.settingsRequested.emit("")
    assert h.settings.tabs.current is None
    assert h.settings.called("show") == [(), (), (), ()]


def test_status_window_opens_the_history_and_the_update(h):
    h.run()
    h.status.historyRequested.emit()
    assert h.history.called("show") == [()]
    h.updater._latest = _release()
    h.status.updateRequested.emit()
    assert h.dialogs == [h.updater._latest]
    h.daemon.worker.historyChanged.emit("snapshot-1")
    assert h.status.called("set_history")[-1] == ("snapshot-1",)


def _sharing_clock(monkeypatch):
    """Lets a test switch sharing as often as it likes, seconds apart."""
    now = [1000.0]

    def tick(step=2.0):
        now[0] += step

    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    return tick


def _pausable(h, monkeypatch, mode="full"):
    saved = []
    monkeypatch.setattr(Config, "save", lambda self, path=None: saved.append(self.privacy.mode))
    del h.config.save
    h.config.privacy.mode = mode
    return saved


def test_pause_sharing_turns_privacy_off_and_resume_restores_the_mode(h, monkeypatch):
    saved = _pausable(h, monkeypatch, mode="minimal")
    tick = _sharing_clock(monkeypatch)
    h.run()
    assert h.tray.called("set_sharing_paused") == [(False,)]
    h.status.sharingToggled.emit(True)
    (paused,) = h.daemon.worker.called("update_config")[-1]
    assert paused.privacy.mode == "off"
    assert h.settings.called("use_config")[-1] == (paused,)
    assert h.tray.called("set_sharing_paused")[-1] == (True,)
    assert h.status.called("set_sharing_paused")[-1] == (True,)
    tick()
    h.tray.sharingToggled.emit(True)
    tick()
    h.tray.sharingToggled.emit(False)
    (resumed,) = h.daemon.worker.called("update_config")[-1]
    assert resumed.privacy.mode == "minimal"
    assert saved == ["off", "minimal"]
    assert h.status.called("set_sharing_paused")[-1] == (False,)


def test_switching_sharing_twice_in_a_second_only_counts_once(h, monkeypatch):
    _pausable(h, monkeypatch, mode="full")
    tick = _sharing_clock(monkeypatch)
    h.run()
    h.status.sharingToggled.emit(True)
    h.status.sharingToggled.emit(False)  # too soon: Discord would lose track
    (paused,) = h.daemon.worker.called("update_config")[-1]
    assert paused.privacy.mode == "off"
    assert h.status.called("set_sharing_paused")[-1] == (True,)
    tick()
    h.status.sharingToggled.emit(False)
    (resumed,) = h.daemon.worker.called("update_config")[-1]
    assert resumed.privacy.mode == "full"


def test_resume_after_a_restart_with_sharing_off_goes_back_to_full(h, monkeypatch):
    _pausable(h, monkeypatch, mode="off")
    h.run()
    assert h.status.called("set_sharing_paused") == [(True,)]
    h.status.sharingToggled.emit(False)
    (resumed,) = h.daemon.worker.called("update_config")[-1]
    assert resumed.privacy.mode == "full"


def test_a_sharing_switch_that_cannot_be_saved_still_applies(h, monkeypatch, caplog):
    def refuse(self, path=None):
        raise OSError("read-only")

    monkeypatch.setattr(Config, "save", refuse)
    del h.config.save
    h.run()
    h.status.sharingToggled.emit(True)
    assert "read-only" in caplog.text
    (paused,) = h.daemon.worker.called("update_config")[-1]
    assert paused.privacy.mode == "off"


def test_first_closed_window_says_once_that_refrain_keeps_running(h, monkeypatch):
    saved = []
    monkeypatch.setattr(
        Config, "save", lambda self, path=None: saved.append(self.behavior.tray_hint_shown)
    )
    del h.config.save
    h.run()
    h.status.finished.emit(0)
    assert [c[0] for c in h.tray.called("show_hint")] == ["Refrain keeps running in the tray."]
    assert saved == [True]
    (remembered,) = h.settings.called("use_config")[-1]
    assert remembered.behavior.tray_hint_shown
    h.settings.applied.emit(remembered)
    h.settings.finished.emit(0)
    h.status.finished.emit(0)
    assert len(h.tray.called("show_hint")) == 1
    assert saved == [True]


def test_tray_hint_stays_quiet_once_shown(h):
    h.config.behavior.tray_hint_shown = True
    h.run()
    h.settings.finished.emit(0)
    assert h.tray.called("show_hint") == []


def test_tray_hint_that_cannot_be_saved_is_still_shown_and_logged(h, monkeypatch, caplog):
    def refuse(self, path=None):
        raise OSError("read-only")

    monkeypatch.setattr(Config, "save", refuse)
    del h.config.save
    h.run()
    h.status.finished.emit(0)
    assert len(h.tray.called("show_hint")) == 1
    assert "read-only" in caplog.text


def test_new_lastfm_credentials_check_again(h):
    h.run()
    before = len([ms for ms, _ in h.shots if ms == 0])
    same = Config()
    same.save = lambda: None
    h.settings.applied.emit(same)
    assert len([ms for ms, _ in h.shots if ms == 0]) == before
    changed = Config()
    changed.save = lambda: None
    changed.lastfm.enabled = True
    changed.lastfm.session_key = "new-session"
    h.settings.applied.emit(changed)
    assert len([ms for ms, _ in h.shots if ms == 0]) == before + 1


def test_update_found_at_startup_opens_the_dialog_and_the_status_hint(h, monkeypatch):
    """The owner never saw the startup popup: run the real check end to end."""
    release = _release()
    monkeypatch.setattr(app, "check_latest_release", lambda: release)
    h.config.update.auto_check = True
    h.config.update.last_check_ts = 0
    h.run()
    real_check = type(h.updater).__mro__[1].check_now
    monkeypatch.setattr(h.updater, "check_now", lambda **kw: real_check(h.updater, **kw))
    h.shot(2000)()
    deadline = time.monotonic() + 5
    while not h.dialogs:
        assert time.monotonic() < deadline
        QCoreApplication.processEvents()
        time.sleep(0.002)
    assert h.dialogs == [release]
    assert h.tray.called("set_update_available") == [(True, "999.0.0")]
    assert h.status.called("set_update_available") == [("999.0.0",)]


def test_auto_check_off_never_looks_for_an_update(h):
    h.config.update.auto_check = False
    h.run()
    h.shot(2000)()
    assert h.updater.checks == []


def test_missing_qt_libraries_are_reported_over_d_bus_without_notify_send(h, monkeypatch):
    sent = []

    class Bus:
        def get_object(self, name, path):
            return (name, path)

    class Notifications:
        def __init__(self, obj, interface):
            pass

        def Notify(self, *args):
            sent.append(args)

    h.missing = ["libxcb-cursor.so.0"]
    monkeypatch.setattr(app.qt_libraries, "message", lambda missing: "need xcb-cursor")
    monkeypatch.setattr(app.shutil, "which", lambda name: None)
    monkeypatch.setattr("dbus.SessionBus", Bus)
    monkeypatch.setattr("dbus.Interface", Notifications)
    assert h.run() == 1
    assert sent[0][3:5] == ("Refrain can't start", "need xcb-cursor")


def test_no_way_to_notify_is_only_logged(h, monkeypatch, caplog):
    def no_bus():
        raise RuntimeError("no session bus")

    h.missing = ["libxcb-cursor.so.0"]
    monkeypatch.setattr(app.qt_libraries, "message", lambda missing: "need xcb-cursor")
    monkeypatch.setattr(app.shutil, "which", lambda name: None)
    monkeypatch.setattr("dbus.SessionBus", no_bus)
    assert h.run() == 1
    assert "no session bus" in caplog.text


def _crash_log_with(size_seen: int) -> Path:
    state = Path(os.environ["XDG_STATE_HOME"]) / "refrain"
    state.mkdir(parents=True, exist_ok=True)
    (state / "crash.log").write_text("--- Refrain, pid 1\nFatal Python error: Aborted\n")
    (state / "crash-seen").write_text(str(size_seen))
    return state / "crash.log"


def test_a_crash_last_time_is_said_once_and_opens_the_report(h, monkeypatch):
    crash_log = _crash_log_with(0)
    opened = []
    monkeypatch.setattr(
        app.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile())
    )
    h.run()
    ((text, on_click),) = h.tray.called("show_hint")
    assert text == "Refrain closed unexpectedly last time. Click to open the report."
    on_click()
    assert opened == [str(crash_log)]
    assert h.status.called("set_crash_report") == [(crash_log,)]


def test_a_clean_start_says_nothing_about_crashes(h):
    crash_log = _crash_log_with(0)
    crash_log.parent.joinpath("crash-seen").write_text(str(crash_log.stat().st_size))
    h.run()
    assert h.tray.called("show_hint") == []


def test_an_unreadable_crash_marker_is_no_crash(tmp_path):
    (tmp_path / "crash-seen").write_text("not a number")
    assert app._crash_since_last_start(tmp_path / "missing.log") is False


def test_a_start_remembers_the_reports_so_the_next_one_stays_quiet():
    crash_log = _crash_log_with(0)
    assert app._crash_since_last_start(crash_log) is True
    app._remember_crash_reports(crash_log)
    assert app._crash_since_last_start(crash_log) is False


def test_another_version_starting_is_not_a_crash():
    crash_log = _crash_log_with(0)
    app._remember_crash_reports(crash_log)
    # An older Refrain only stamps its start into the same file.
    with crash_log.open("a", encoding="utf-8") as fh:
        fh.write("--- Refrain 0.5.2, pid 12345, started 2026-09-20 17:18:56\n")
    assert app._crash_since_last_start(crash_log) is False
    with crash_log.open("a", encoding="utf-8") as fh:
        fh.write("Fatal Python error: Segmentation fault\n")
    assert app._crash_since_last_start(crash_log) is True
