"""Refrain entry point.

Wires together: config, single-instance lock, logging, system tray,
settings window, and the background daemon. Keeps QApplication alive
even when the settings window is hidden so the tray + daemon persist.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from refrain import __version__
from refrain.autostart import disable as autostart_disable
from refrain.autostart import enable as autostart_enable
from refrain.autostart import is_enabled as autostart_is_enabled
from refrain.config import Config
from refrain.daemon import Daemon
from refrain.logging_setup import attach_qt_log_bridge, setup_logging
from refrain.paths import assets_dir
from refrain.single_instance import AlreadyRunning
from refrain.single_instance import acquire as acquire_lock
from refrain.ui.log_window import LogWindow
from refrain.ui.settings_window import SettingsWindow
from refrain.ui.tray import TrayIcon
from refrain.ui.update_dialog import UpdateDialog
from refrain.updater import ReleaseInfo, check_latest_release

log = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="refrain",
        description="Discord Rich Presence for Apple Music on Linux",
    )
    p.add_argument("--version", action="version", version=f"refrain {__version__}")
    p.add_argument(
        "--silent",
        action="store_true",
        help="Start minimized to tray; don't open the settings window",
    )
    p.add_argument(
        "--install-desktop",
        action="store_true",
        help="Copy refrain.desktop + icon to ~/.local/share so Refrain shows up "
        "in your application menu, then exit. Useful when installed via pip.",
    )
    p.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="Remove the files written by --install-desktop, then exit.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Set log level to DEBUG and open the live-log window on startup.",
    )
    return p.parse_args(argv)


def _user_apps_dir() -> Path:
    return Path.home() / ".local" / "share" / "applications"


def _user_icons_dir() -> Path:
    return Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"


def install_desktop_files() -> int:
    apps = _user_apps_dir()
    icons = _user_icons_dir()
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)

    src_desktop = assets_dir() / "refrain.desktop"
    src_icon = assets_dir() / "icons" / "refrain.svg"

    dst_desktop = apps / "refrain.desktop"
    dst_icon = icons / "refrain.svg"

    shutil.copy2(src_desktop, dst_desktop)
    shutil.copy2(src_icon, dst_icon)

    print("Installed:")
    print(f"  {dst_desktop}")
    print(f"  {dst_icon}")
    print()
    print("Refrain should now appear in your application menu.")
    print("Run with --uninstall-desktop to remove these files.")
    return 0


def uninstall_desktop_files() -> int:
    removed: list[Path] = []
    for p in (_user_apps_dir() / "refrain.desktop", _user_icons_dir() / "refrain.svg"):
        if p.exists():
            p.unlink()
            removed.append(p)
    if removed:
        print("Removed:")
        for p in removed:
            print(f"  {p}")
    else:
        print("Nothing to remove (refrain.desktop and refrain.svg are not installed).")
    return 0


def _install_signal_handlers(app: QApplication) -> None:
    """Make Ctrl+C / SIGTERM cleanly quit the Qt event loop."""
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    # Qt's event loop blocks Python signal delivery on Linux; a no-op timer
    # wakes Python frequently enough to deliver them.
    timer = QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)
    app._refrain_signal_timer = timer  # keep a strong reference


def _sync_autostart(config: Config) -> None:
    if config.behavior.autostart and not autostart_is_enabled():
        autostart_enable()
    elif not config.behavior.autostart and autostart_is_enabled():
        autostart_disable()


class _UpdateCheckWorker(QObject):
    """Run check_latest_release on a background QThread."""

    finished_with_release = Signal(object)  # ReleaseInfo | None

    def run(self) -> None:
        try:
            release = check_latest_release()
        except Exception as e:
            log.debug("Background update check failed: %s", e)
            release = None
        self.finished_with_release.emit(release)


class UpdateOrchestrator(QObject):
    """Coordinates GitHub update checks, tray badge, and the update dialog.

    The actual HTTP call runs on a worker QThread so the GUI stays responsive.
    """

    UPDATE_CHECK_COOLDOWN_S = 24 * 3600  # at most once per day on auto-check

    updateAvailable = Signal(object)  # ReleaseInfo
    # Manual checks emit one of these so the user always gets feedback.
    checkUpToDate = Signal(str)  # current version string
    checkFailed = Signal(str)  # error message

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self._config = config
        self._latest: ReleaseInfo | None = None
        self._thread: QThread | None = None
        self._worker: _UpdateCheckWorker | None = None
        self._manual = False

    @property
    def latest(self) -> ReleaseInfo | None:
        return self._latest

    def maybe_check_on_startup(self) -> None:
        if not self._config.update.auto_check:
            return
        elapsed = time.time() - self._config.update.last_check_ts
        if elapsed < self.UPDATE_CHECK_COOLDOWN_S:
            log.debug("Auto-update check skipped (cooldown, %.0fs since last)", elapsed)
            return
        self.check_now(manual=False)

    def check_now(self, manual: bool = True) -> None:
        if self._thread is not None:
            log.debug("Update check already in progress")
            return
        self._manual = manual
        self._thread = QThread()
        self._thread.setObjectName("refrain-update-check")
        self._worker = _UpdateCheckWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished_with_release.connect(self._on_check_finished)
        self._thread.start()

    def _on_check_finished(self, release: ReleaseInfo | None) -> None:
        self._config.update.last_check_ts = int(time.time())
        try:
            self._config.save()
        except Exception as e:
            log.debug("Could not persist last_check_ts: %s", e)

        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
            self._thread = None
            self._worker = None

        manual = self._manual
        self._manual = False

        if release is None:
            log.info("Update check: no release info returned")
            if manual:
                self.checkFailed.emit("Could not reach GitHub. Check your network and try again.")
            return
        if not release.is_newer_than_current:
            log.info("Update check: already on latest (%s)", release.version)
            if manual:
                self.checkUpToDate.emit(__version__)
            return

        log.info("Update check: %s available (current: %s)", release.version, __version__)
        self._latest = release
        self.updateAvailable.emit(release)


def main() -> int:
    args = _parse_args(sys.argv[1:])

    if args.install_desktop:
        return install_desktop_files()
    if args.uninstall_desktop:
        return uninstall_desktop_files()

    config = Config.load()
    log_level = "DEBUG" if args.debug else config.advanced.log_level
    setup_logging(log_level)
    log_bridge = attach_qt_log_bridge()
    log.info("Refrain %s starting", __version__)

    app = QApplication(sys.argv)
    app.setApplicationName("Refrain")
    app.setApplicationDisplayName("Refrain")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("refrain")
    app.setQuitOnLastWindowClosed(False)

    icon_path = assets_dir() / "icons" / "refrain.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        bus_lock = acquire_lock()
    except AlreadyRunning:
        QMessageBox.information(None, "Refrain", "Refrain is already running.")
        return 0
    app._refrain_bus_lock = bus_lock  # keep alive for the lifetime of the app

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "Refrain",
            "No system tray available.\n\n"
            "On GNOME, install the 'AppIndicator and KStatusNotifierItem' "
            "extension and re-run Refrain.",
        )
        return 1

    tray = TrayIcon()
    daemon = Daemon(config)
    settings = SettingsWindow(config)
    updater = UpdateOrchestrator(config)
    log_window = LogWindow(log_bridge)

    daemon.worker.trackChanged.connect(tray.set_track)
    daemon.worker.statusChanged.connect(tray.set_status)
    daemon.worker.progressTick.connect(tray.set_progress)
    tray.settingsRequested.connect(settings.show)
    tray.settingsRequested.connect(settings.raise_)
    tray.settingsRequested.connect(settings.activateWindow)
    tray.playPauseRequested.connect(daemon.worker.control_play_pause)
    tray.nextRequested.connect(daemon.worker.control_next)
    tray.previousRequested.connect(daemon.worker.control_previous)
    tray.quitRequested.connect(app.quit)
    # Two connections: worker.update_config gets queued onto the worker thread,
    # _sync_autostart runs on the main thread (file I/O, OK).
    settings.applied.connect(daemon.worker.update_config)
    settings.applied.connect(_sync_autostart)

    # Updater wireup — Settings button = manual check (always shows feedback);
    # the auto-check on startup goes through maybe_check_on_startup() which
    # passes manual=False and stays silent on no-update / error.
    settings.checkUpdatesRequested.connect(lambda: updater.check_now(manual=True))
    tray.updateRequested.connect(_open_update_dialog_factory(updater, settings))
    updater.updateAvailable.connect(lambda r: tray.set_update_available(True, r.version))
    updater.updateAvailable.connect(_open_update_dialog_factory(updater, settings))
    updater.checkUpToDate.connect(
        lambda v: QMessageBox.information(
            settings,
            "Refrain — Updates",
            f"You're already on the latest version ({v}).",
        )
    )
    updater.checkFailed.connect(lambda msg: QMessageBox.warning(settings, "Refrain — Updates", msg))

    # Log-window wireup
    def _show_log() -> None:
        log_window.show()
        log_window.raise_()
        log_window.activateWindow()

    tray.logRequested.connect(_show_log)
    settings.showLogRequested.connect(_show_log)

    # Restart wireup — set a flag and quit; main() re-execs after the Qt
    # event loop returns so the daemon, RPC and DBus name release cleanly
    # before the new process starts.
    def _restart() -> None:
        log.info("Restart requested")
        app._refrain_should_restart = True
        app.quit()

    tray.restartRequested.connect(_restart)
    settings.restartRequested.connect(_restart)

    _install_signal_handlers(app)
    _sync_autostart(config)

    daemon.start()

    if not args.silent:
        settings.show()

    if args.debug:
        _show_log()

    # Run the auto-check shortly after the window is up — non-blocking.
    QTimer.singleShot(2000, updater.maybe_check_on_startup)

    rc = app.exec()
    daemon.stop()

    if getattr(app, "_refrain_should_restart", False):
        log.info("Re-execing for restart")
        # Drop one-shot CLI flags from the next launch.
        new_argv = [
            arg for arg in sys.argv[1:] if arg not in ("--install-desktop", "--uninstall-desktop")
        ]
        # Release the bus name explicitly before exec so the new process
        # never races with the dying old one for the single-instance lock.
        app._refrain_bus_lock = None
        os.execvp(sys.argv[0], [sys.argv[0], *new_argv])

    log.info("Refrain shutting down with rc=%d", rc)
    return rc


def _open_update_dialog_factory(updater: UpdateOrchestrator, parent_widget):
    def _open(_release=None) -> None:
        release = _release if _release is not None else updater.latest
        if release is None:
            QMessageBox.information(
                parent_widget,
                "Refrain — Updates",
                "No update information available yet. Try again in a moment.",
            )
            return
        dlg = UpdateDialog(release, parent=parent_widget)
        dlg.exec()

    return _open


if __name__ == "__main__":
    sys.exit(main())
