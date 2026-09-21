"""app.py edge cases: Qt plugin detection, crash-log stamping and main()'s error handling."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain import app  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.startup_check import OK, CheckResult  # noqa: E402
from tests.test_app_main import CLIENT_ID, h  # noqa: E402,F401


def _fake_root(tmp_path, monkeypatch):
    """Resolve the hard-coded /usr/lib candidates inside ``tmp_path``."""
    real_path = app.Path
    monkeypatch.setattr(app, "Path", lambda p: real_path(tmp_path, str(p).lstrip("/")))
    lib = tmp_path / "usr" / "lib"
    lib.mkdir(parents=True)
    return lib


def test_a_broken_qt_symlink_is_skipped_instead_of_stopping_detection(tmp_path, monkeypatch):
    """A candidate that can't be resolved (permission error, odd mount) is not fatal."""
    lib = _fake_root(tmp_path, monkeypatch)
    (lib / "libQt6Core.so.6.11.2").write_bytes(b"")
    (lib / "libQt6Core.so.6").symlink_to("libQt6Core.so.6.11.2")

    real_resolve = Path.resolve

    def _boom(self, *a, **kw):
        if self.name == "libQt6Core.so.6":
            raise OSError("permission denied")
        return real_resolve(self, *a, **kw)

    monkeypatch.setattr(Path, "resolve", _boom)

    assert app._detect_system_qt6_version() is None


def test_no_system_qt_plugin_tree_leaves_the_bundled_one_alone(monkeypatch, tmp_path):
    from types import SimpleNamespace

    bundled_plugins = tmp_path / "wheel" / "plugins"
    (bundled_plugins / "platforms").mkdir(parents=True)

    class _FakeQCoreApplication:
        def __init__(self, initial):
            self._paths = list(initial)

        def libraryPaths(self):
            return list(self._paths)

        def setLibraryPaths(self, paths):
            self._paths = list(paths)

        def addLibraryPath(self, path):
            self._paths.insert(0, str(path))

    fake_info = SimpleNamespace(
        LibraryPath=SimpleNamespace(PluginsPath=object()),
        path=lambda _which: str(bundled_plugins),
        version=lambda: SimpleNamespace(toString=lambda: "6.11.2"),
    )
    monkeypatch.setattr(app, "QLibraryInfo", fake_info)
    monkeypatch.setattr(app.sys, "platform", "linux")
    monkeypatch.setattr(app, "_find_system_qt6_plugin_path", lambda: None)
    fake_qapp = _FakeQCoreApplication([str(bundled_plugins)])
    monkeypatch.setattr(app, "QCoreApplication", fake_qapp)

    app._augment_qt_plugin_path()

    assert fake_qapp.libraryPaths() == [str(bundled_plugins)]


def test_a_stamping_failure_still_closes_the_descriptor(xdg_tmp, monkeypatch):
    """crash.log's fd must never leak, even when stamping the opening line fails."""
    path = xdg_tmp["state"] / "refrain" / "crash.log"
    path.parent.mkdir(parents=True)

    def _refuse(_fd, _mode):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fchmod", _refuse)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(OSError):
        app._open_crash_log(path)
    after = len(os.listdir("/proc/self/fd"))
    assert after == before


def test_a_broken_dbus_glib_loop_init_does_not_stop_startup(h, monkeypatch):  # noqa: F811
    """PyGObject missing (or broken) must not keep Refrain from starting at all."""

    def _boom():
        raise RuntimeError("no pygobject")

    monkeypatch.setattr("refrain.sources.mpris_server._ensure_dbus_glib_loop", _boom)
    assert h.run() == 0


def test_a_broken_dbus_dispatch_pump_does_not_stop_startup(h, monkeypatch):  # noqa: F811
    def _boom():
        raise RuntimeError("no glib main loop")

    monkeypatch.setattr("refrain.sources.mpris_server.ensure_dbus_dispatch_pump", _boom)
    assert h.run() == 0


def test_quitting_before_the_startup_check_finishes_waits_for_it_to_stop(h, monkeypatch):  # noqa: F811
    """Quit must not leave the diagnostics thread running behind it."""
    from PySide6.QtCore import QObject, Signal

    class Worker(QObject):
        finished = Signal(object, object)

        def __init__(self, lastfm_cfg, rpc):
            super().__init__()

        def run(self):
            time.sleep(0.3)
            self.finished.emit(CheckResult(OK), CheckResult(OK))

    monkeypatch.setattr("refrain.startup_check.StartupCheckWorker", Worker)
    elapsed = []

    def quit_while_it_runs(h):
        h.shot(5000)()
        time.sleep(0.1)  # let the QThread actually get going
        started = time.monotonic()
        h.app.aboutToQuit.emit()
        elapsed.append(time.monotonic() - started)

    h.during_exec = quit_while_it_runs
    h.run("--silent")
    # It must have actually waited for the sleeping worker, not returned
    # instantly nor run into the timeout.
    assert 0.15 < elapsed[0] < 2.0


def test_a_startup_check_timer_that_fires_after_quit_began_starts_nothing(h, monkeypatch):  # noqa: F811
    """The loop can still fire the timer while it winds down; nothing may start then."""
    started = []

    class Worker:
        def __init__(self, lastfm_cfg, rpc):
            started.append(self)

    monkeypatch.setattr("refrain.startup_check.StartupCheckWorker", Worker)

    def quit_then_fire(h):
        h.app.aboutToQuit.emit()
        h.shot(5000)()

    h.during_exec = quit_then_fire
    h.run("--silent")
    assert started == []


def test_developer_menu_entry_opens_the_log_window_on_the_developer_tab(h):  # noqa: F811
    h.run("--silent")
    h.tray.developerRequested.emit()
    assert h.log_window.called("show") == [()]
    assert h.log_window.called("show_developer_tab") == [()]


def test_a_wizard_result_that_cannot_be_saved_is_only_logged(h, monkeypatch, caplog):  # noqa: F811
    def _boom(self, path=None):
        raise OSError("disk full")

    monkeypatch.setattr(Config, "save", _boom)
    del h.config.save
    h.config.behavior.first_run_complete = False
    h.config.discord.client_id = ""
    h.run("--silent")
    with caplog.at_level(logging.WARNING):
        h.welcome.applied.emit(CLIENT_ID)
    assert "Could not persist first-run wizard result" in caplog.text
    # Still applied to the running daemon even though the save failed.
    assert h.settings.called("use_config")


def test_a_wizard_apply_hook_failure_is_only_logged(h, monkeypatch, caplog):  # noqa: F811
    h.config.behavior.first_run_complete = False
    h.config.discord.client_id = ""
    h.run("--silent")

    def _boom(_c):
        raise RuntimeError("settings window already gone")

    monkeypatch.setattr(h.settings, "use_config", _boom)
    with caplog.at_level(logging.ERROR):
        h.welcome.applied.emit(CLIENT_ID)
    assert "Welcome wizard apply hook failed" in caplog.text


def test_quitting_waits_for_an_update_check_already_on_the_wire_instead_of_killing_it(monkeypatch):
    """stop() must not abandon the background QThread mid-request."""
    QApplication.instance() or QApplication(sys.argv)
    finished = threading.Event()

    def _slow_check():
        time.sleep(0.2)
        finished.set()
        return None

    monkeypatch.setattr(app, "check_latest_release", _slow_check)
    config = Config()
    config.save = lambda: None
    orch = app.UpdateOrchestrator(config)
    orch.check_now(manual=False, silent=True)

    deadline = time.monotonic() + 5
    while orch._thread is None or not orch._thread.isRunning():
        assert time.monotonic() < deadline, "the check never actually started"
        time.sleep(0.01)

    orch.stop()

    assert finished.is_set(), "stop() returned before the in-flight check had a chance to finish"
    assert not orch._thread.isRunning()


def test_a_crash_log_that_cannot_be_read_counts_as_no_crashes(tmp_path):
    """A directory where the log should be (odd mount, permissions) must not raise."""
    fake_log = tmp_path / "crash.log"
    fake_log.mkdir()
    assert app._crash_reports(fake_log) == 0


def test_a_state_dir_that_cannot_be_written_only_logs_the_failure(tmp_path, monkeypatch, caplog):
    """Losing the crash-seen marker must not stop Refrain from starting."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setattr(app, "state_dir", lambda: blocker / "state")
    with caplog.at_level(logging.DEBUG, logger="refrain.app"):
        app._remember_crash_reports(tmp_path / "crash.log")
    assert "Could not remember the crash reports" in caplog.text


def test_an_update_check_asked_for_after_stop_starts_no_thread():
    """The startup timer can fire while quitting; a stopped orchestrator stays idle."""
    QApplication.instance() or QApplication(sys.argv)
    config = Config()
    config.save = lambda: None
    orch = app.UpdateOrchestrator(config)
    orch.stop()
    orch.check_now(manual=True)
    assert orch._thread is None
