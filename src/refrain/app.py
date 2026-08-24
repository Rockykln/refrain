"""Refrain entry point.

Wires together: config, single-instance lock, logging, system tray,
settings window, and the background daemon. Keeps QApplication alive
even when the settings window is hidden so the tray + daemon persist.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QLibraryInfo,
    QLocale,
    QObject,
    QThread,
    QTimer,
    QtMsgType,
    QTranslator,
    Signal,
    qInstallMessageHandler,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from refrain import __version__
from refrain.autostart import disable as autostart_disable
from refrain.autostart import enable as autostart_enable
from refrain.autostart import is_enabled as autostart_is_enabled
from refrain.autostart import resolve_exec_line
from refrain.config import Config
from refrain.daemon import Daemon
from refrain.logging_setup import attach_qt_log_bridge, setup_logging
from refrain.paths import assets_dir
from refrain.single_instance import AlreadyRunning, SessionBusUnavailable
from refrain.single_instance import acquire as acquire_lock
from refrain.ui.log_window import LogWindow
from refrain.ui.settings_window import SettingsWindow
from refrain.ui.tray import TrayIcon
from refrain.ui.update_dialog import UpdateDialog
from refrain.ui.welcome_dialog import WelcomeDialog
from refrain.updater import ReleaseInfo, check_latest_release, cleanup_orphan_downloads

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
        "--uninstall",
        action="store_true",
        help="Remove ALL Refrain data (config, logs, cache, autostart, "
        "menu entry) and the Last.fm credentials from the keyring, print "
        "the command to remove the package itself, then exit.",
    )
    p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt for --uninstall (non-interactive).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Set log level to DEBUG and open the live-log window on startup.",
    )
    return p.parse_args(argv)


def _detect_system_qt6_version() -> str | None:
    """Walk libQt6Core.so symlinks to read the system Qt 6 version.

    Returns ``"6.11.0"``-shaped strings, or ``None`` if no system Qt 6 is
    discoverable. Cheap (two stat calls) — avoids spawning ``qmake6``.
    """
    candidates = [
        Path("/usr/lib/libQt6Core.so.6"),
        Path("/usr/lib64/libQt6Core.so.6"),
        Path("/usr/lib/x86_64-linux-gnu/libQt6Core.so.6"),
        Path("/usr/lib/aarch64-linux-gnu/libQt6Core.so.6"),
    ]
    for c in candidates:
        if not c.is_symlink() and not c.exists():
            continue
        try:
            real = c.resolve()
        except OSError:
            continue
        # libQt6Core.so.6.11.0 → "6.11.0"
        _, _, version = real.name.partition(".so.")
        if version and version[0].isdigit():
            return version
    return None


def _qt_version_tuple(version: str) -> tuple[int, ...]:
    """Parse ``"6.11.2"`` into ``(6, 11, 2)``, stopping at the first
    non-numeric component so suffixed versions still compare sanely."""
    parts: list[int] = []
    for chunk in version.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _system_qt_plugins_loadable(bundled: str, system: str) -> bool:
    """Whether the system Qt's plugins can load into our bundled Qt.

    Qt refuses a plugin built against a *newer* Qt than the one running,
    so the system tree is only usable when it shares our MAJOR.MINOR and
    is no newer at the patch level. A distro that is a single patch ahead
    (bundled 6.11.1, system 6.11.2 — the common case on a rolling distro)
    would otherwise hand us platform plugins Qt then rejects.
    """
    b = _qt_version_tuple(bundled)
    s = _qt_version_tuple(system)
    if len(b) < 2 or len(s) < 2:
        return False
    if b[:2] != s[:2]:
        return False
    return s <= b


# Where distros put Qt 6 plugins. Order matters: try the
# multiarch path first (Debian/Ubuntu derivatives), then lib64
# (Fedora/RHEL/openSUSE), then the plain lib (Arch/CachyOS).
_SYSTEM_QT6_PLUGIN_PATHS = (
    Path("/usr/lib/x86_64-linux-gnu/qt6/plugins"),
    Path("/usr/lib/aarch64-linux-gnu/qt6/plugins"),
    Path("/usr/lib64/qt6/plugins"),
    Path("/usr/lib/qt6/plugins"),
)


def _find_system_qt6_plugin_path() -> Path | None:
    for p in _SYSTEM_QT6_PLUGIN_PATHS:
        if (p / "styles").is_dir():
            return p
    return None


def _augment_qt_plugin_path() -> None:
    """Make the system's Qt style plugins discoverable when running
    against a bundled PySide6 wheel.

    The PySide6 PyPI wheel ships its own copy of Qt **without** distro
    style plugins (no ``styles/breeze6.so`` etc.), so a pip/pipx install
    of Refrain on KDE Plasma falls back to the Fusion style and looks
    visibly different from the AUR / system build that runs against the
    distro PySide6.

    If a system Qt 6 plugin tree exists and its plugins can actually load
    into our Qt (see ``_system_qt_plugins_loadable``), we *append* it so
    Qt finds the styles. Appending matters: the bundled Qt must keep first
    claim on the platform plugin, otherwise a system plugin that Qt then
    refuses takes the whole startup down with it.

    Must be called before ``QApplication`` is constructed — Qt resolves
    the platform/style plugin during construction.
    """
    if sys.platform != "linux":
        return
    bundled_plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    if (bundled_plugins / "styles").is_dir():
        # PySide6 already shipped style plugins (or we're running against
        # the distro PySide6 whose plugin path *is* the system one).
        return
    system_plugins = _find_system_qt6_plugin_path()
    if system_plugins is None:
        return
    bundled_version = QLibraryInfo.version().toString()
    system_version = _detect_system_qt6_version()
    if not system_version:
        log.debug("Could not determine system Qt 6 version; not augmenting plugin path")
        return
    if not _system_qt_plugins_loadable(bundled_version, system_version):
        log.debug(
            "System Qt %s cannot supply plugins for bundled Qt %s; not augmenting plugin path",
            system_version,
            bundled_version,
        )
        return
    log.info(
        "Augmenting Qt plugin path with %s (bundled Qt %s, system Qt %s)",
        system_plugins,
        bundled_version,
        system_version,
    )
    # Append, never prepend (``addLibraryPath`` prepends): the bundled Qt
    # keeps first claim on the platform plugin, so a mismatched system tree
    # can only cost us the styles, never the ability to start.
    QCoreApplication.setLibraryPaths(QCoreApplication.libraryPaths() + [str(system_plugins)])


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

    # Rewrite Exec= to point at the actual launcher we're running
    # under. The bundled refrain.desktop has Exec=refrain which only
    # works when refrain is on $PATH (distro packages, pipx). For
    # AppImage / source-checkout users, the bare name is unresolvable
    # at session-startup time and clicking the menu entry would do
    # nothing.
    desktop_text = src_desktop.read_text(encoding="utf-8")
    new_exec = resolve_exec_line()
    desktop_text = re.sub(r"^Exec=.*$", f"Exec={new_exec}", desktop_text, flags=re.MULTILINE)
    dst_desktop.write_text(desktop_text, encoding="utf-8")

    shutil.copy2(src_icon, dst_icon)

    print("Installed:")
    print(f"  {dst_desktop}")
    print(f"  {dst_icon}")
    print(f"  Exec={new_exec}")
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


def run_uninstall_cli(assume_yes: bool = False) -> int:
    """`refrain --uninstall` — wipe all data + credentials, print the
    package-removal command. One-shot, runs before the GUI / lock."""
    from refrain.uninstall import collect_paths, purge, removal_command
    from refrain.updater import detect_install_type

    install_type = detect_install_type()
    appimage = os.environ.get("APPIMAGE")
    paths = collect_paths()
    cmd = removal_command(install_type, appimage)

    print("This will permanently remove all Refrain data:")
    if paths:
        for p in paths:
            print(f"  {p}")
    else:
        print("  (no data files found)")
    print("  + the Last.fm credentials stored in your OS keyring")
    print()
    print(f"Detected install type: {install_type}")
    print(f"It will NOT remove the program itself — do that with:\n  {cmd}")
    print()

    if not assume_yes:
        try:
            answer = input("Proceed with removing all Refrain data? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer not in ("y", "yes"):
            print("Aborted — nothing was removed.")
            return 1

    report = purge()
    print()
    if report.removed:
        print("Removed:")
        for r in report.removed:
            print(f"  {r}")
    if report.secrets_purged:
        print("  Last.fm credentials cleared from the keyring")
    if report.failed:
        print("Could not remove (check permissions):")
        for f in report.failed:
            print(f"  {f}")
    if not report.removed and not report.failed:
        print("Nothing to remove — Refrain left no data on this machine.")
    print()
    print(f"Refrain data is gone. To remove the program itself, run:\n  {cmd}")
    print("(Quit any running Refrain instance first.)")
    return 0


def _install_translators(app: QApplication, language_override: str = "system") -> list[QTranslator]:
    """Load Refrain's own .qm files plus Qt's built-in translations.

    ``language_override`` is the value of ``advanced.language`` from
    config — ``"system"`` (default) follows ``QLocale.system()``,
    explicit codes (``"en"``, ``"de"``, …) force that translation
    instead. English source strings are the fallback whenever a
    translation is missing or unfinished. Translators are kept alive
    via the returned list — Qt drops them silently if they're
    garbage-collected.
    """
    keep_alive: list[QTranslator] = []
    package_i18n = Path(__file__).parent / "i18n"

    # Only the languages we ship a real (non-stub) `.qm` for are
    # candidates. QTranslator.load() walks `locale.uiLanguages()` looking
    # for ANY matching .qm, which on systems with weird $LANGUAGE
    # fallbacks would happily load an empty stub and "translate"
    # everything to nothing. Restricting the candidate list prevents that.
    available = {
        p.stem.split("_", 1)[1]
        for p in package_i18n.glob("refrain_*.qm")
        if p.stat().st_size > 100  # skip header-only stubs
    }
    log.debug("Available Refrain translations: %s", sorted(available))

    if language_override and language_override != "system":
        target = language_override
    else:
        sys_locale = QLocale.system()
        target = sys_locale.name()  # e.g. "de_DE"

    # Try the exact code first, then the language-only prefix
    # ("de_DE" → "de"), then give up cleanly (English source strings).
    candidates = [target, target.split("_", 1)[0]]
    chosen = next((c for c in candidates if c in available), None)
    if chosen is not None:
        refrain_t = QTranslator(app)
        if refrain_t.load(QLocale(chosen), "refrain", "_", str(package_i18n), ".qm"):
            app.installTranslator(refrain_t)
            keep_alive.append(refrain_t)
            log.info("Loaded Refrain translation: %s", chosen)
    else:
        log.info("No translation for %r — using English source strings", target)

    # Qt's own translations for stock widgets (button labels, menus).
    qt_t = QTranslator(app)
    qt_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_t.load(QLocale.system(), "qtbase", "_", qt_path, ".qm"):
        app.installTranslator(qt_t)
        keep_alive.append(qt_t)
    return keep_alive


_QT_NOISE_SUBSTRINGS = (
    # Harmless on systems where xdg-desktop-portal isn't running or doesn't
    # know about us yet. Confuses users when they open the live log.
    "Failed to register with host portal",
)

_QT_LEVEL_MAP = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


_qt_logger = logging.getLogger("refrain.qt")


def _qt_message_handler(msg_type, _context, message: str) -> None:
    if any(noise in message for noise in _QT_NOISE_SUBSTRINGS):
        return
    _qt_logger.log(_QT_LEVEL_MAP.get(msg_type, logging.INFO), "%s", message)


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


def _apply_log_level(config: Config) -> None:
    """Push the configured log level onto the running root logger.

    Wired to ``settings.applied`` so toggling between INFO/DEBUG in the
    Settings dialog takes effect immediately, instead of silently
    waiting for the next restart.
    """
    # Defensive str() coercion: a hand-edited config with
    # `log_level = 5` would otherwise AttributeError on .upper().
    raw = config.advanced.log_level
    level_name = str(raw if raw else "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if root.level != level:
        root.setLevel(level)
        log.info("Log level changed to %s", level_name)


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
    # Fires after every check (auto or manual, success or failure) with
    # the latest ReleaseInfo (or None on network failure). The Settings
    # → Updates tab subscribes to this to render the inline release
    # notes — independent of whether the user is on the latest version.
    releaseInfoFetched = Signal(object)  # ReleaseInfo | None

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self._config = config
        self._latest: ReleaseInfo | None = None
        self._thread: QThread | None = None
        self._worker: _UpdateCheckWorker | None = None
        self._manual = False
        # When True, the in-flight check is purely for populating the
        # in-app release-notes pane — suppress all popups and don't
        # bump last_check_ts (which gates the auto-nag cooldown).
        self._silent = False

    @property
    def latest(self) -> ReleaseInfo | None:
        return self._latest

    def maybe_check_on_startup(self) -> None:
        """Always fire one fetch on startup so the Settings → Updates
        pane has data immediately. The 24-hour cooldown only gates
        whether this fetch can ALSO trigger the auto-popup for new
        releases — within the cooldown the fetch runs silently and
        only refreshes the inline release-notes pane."""
        if not self._config.update.auto_check:
            return
        elapsed = time.time() - self._config.update.last_check_ts
        silent = elapsed < self.UPDATE_CHECK_COOLDOWN_S
        if silent:
            log.debug("Auto-update check running silently (cooldown active)")
        self.check_now(manual=False, silent=silent)

    def check_now(self, manual: bool = True, silent: bool = False) -> None:
        if self._thread is not None:
            log.debug("Update check already in progress")
            return
        self._manual = manual
        self._silent = silent
        self._thread = QThread()
        self._thread.setObjectName("refrain-update-check")
        self._worker = _UpdateCheckWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished_with_release.connect(self._on_check_finished)
        self._thread.start()

    def _on_check_finished(self, release: ReleaseInfo | None) -> None:
        manual = self._manual
        silent = self._silent
        self._manual = False
        self._silent = False

        # Only bump the cooldown timestamp when this was a real auto-
        # nag check. A silent pane-refresh shouldn't reset the cooldown
        # — otherwise every startup would block the next day's auto-
        # nag. Manual checks always count as "I just checked".
        if not silent:
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

        # Always fan out the raw fetch result so subscribers (Settings
        # tab, etc.) can render whatever state they want regardless of
        # whether this constitutes "an update". Cache it too so a
        # later subscriber can pull it on demand.
        if release is not None:
            self._latest = release
        self.releaseInfoFetched.emit(release)

        # Silent fetches stop here — the pane is populated, no nags.
        if silent:
            log.info(
                "Silent update check finished: latest=%s, current=%s",
                release.version if release else "?",
                __version__,
            )
            return

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
        self.updateAvailable.emit(release)


def main() -> int:
    args = _parse_args(sys.argv[1:])

    if args.install_desktop:
        return install_desktop_files()
    if args.uninstall_desktop:
        return uninstall_desktop_files()
    if args.uninstall:
        # One-shot: runs before the single-instance lock + QApplication
        # so it works headless and never collides with a running tray.
        return run_uninstall_cli(assume_yes=args.yes)

    # Bring logging up at INFO before anything that might log — Config.load
    # in particular emits "Created default config" and "Config unreadable"
    # messages we want captured in the file log. The level is adjusted to
    # whatever the user configured once the config is loaded.
    setup_logging("DEBUG" if args.debug else "INFO")
    log_bridge = attach_qt_log_bridge()
    qInstallMessageHandler(_qt_message_handler)

    config = Config.load()
    # Last.fm secrets live in the OS keyring, never in config.toml.
    # Overlay them onto the in-memory config (and migrate any legacy
    # plaintext out of an old config.toml) before anything reads them.
    from refrain.secrets_store import load_into as _load_lastfm_secrets

    _load_lastfm_secrets(config.lastfm)
    if not args.debug:
        _apply_log_level(config)
    log.info("Refrain %s starting", __version__)

    # Self-heal a previously interrupted AppImage update before anything
    # else touches the filesystem.
    cleanup_orphan_downloads()

    # Wire dbus-python's dispatch into a GLib main loop *before* anything
    # touches the session bus. Has to happen before MPRISSource /
    # BluetoothSource construct their first SessionBus, otherwise the
    # bus connection won't have a main loop attached and our published
    # MPRIS server fails to register ("D-Bus connections must be
    # attached to a main loop"). No-op if PyGObject is missing — the
    # Discord-RPC + tray sides keep working without it.
    try:
        from refrain.sources.mpris_server import _ensure_dbus_glib_loop

        _ensure_dbus_glib_loop()
    except Exception as e:
        log.debug("dbus-glib loop init skipped: %s", e)

    _augment_qt_plugin_path()

    app = QApplication(sys.argv)
    app.setApplicationName("Refrain")
    app.setApplicationDisplayName("Refrain")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("refrain")
    app.setQuitOnLastWindowClosed(False)

    icon_path = assets_dir() / "icons" / "refrain.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Translators must be installed BEFORE any user-visible widget is
    # created — strings are looked up at construction time.
    app._refrain_translators = _install_translators(app, config.advanced.language)

    _tr = QCoreApplication.translate

    try:
        bus_lock = acquire_lock()
    except AlreadyRunning:
        QMessageBox.information(
            None,
            _tr("app", "Already running"),
            _tr("app", "Refrain is already running."),
        )
        return 0
    except SessionBusUnavailable as e:
        log.error("Cannot start without a session bus")
        QMessageBox.critical(
            None,
            _tr("app", "D-Bus session bus unavailable"),
            _tr(
                "app",
                "Refrain needs a working D-Bus session bus to run "
                "(it's used for the single-instance lock, MPRIS metadata "
                "from your browser, and the Plasma-panel media-controls "
                "publication).\n\n"
                "Underlying error: {error}\n\n"
                "On a desktop session this should normally be available "
                "automatically. Check that dbus-daemon is running and "
                "that DBUS_SESSION_BUS_ADDRESS is set in your environment.",
            ).format(error=str(e)),
        )
        return 1
    app._refrain_bus_lock = bus_lock  # keep alive for the lifetime of the app

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.error("No system tray available — refusing to start")
        QMessageBox.critical(
            None,
            _tr("app", "No system tray"),
            _tr(
                "app",
                "No system tray available. Refrain lives in the tray, so it "
                "needs a StatusNotifierItem-aware host. Common fixes:\n\n"
                "• GNOME — install the 'AppIndicator and KStatusNotifierItem "
                "Support' extension, then re-run Refrain.\n"
                "• MATE — install 'mate-applet-statusnotifier'.\n"
                "• XFCE — add 'xfce4-statusnotifier-plugin' to your panel.\n"
                "• Hyprland / Sway / i3 / river — use a status bar with "
                "StatusNotifierItem support (waybar's 'tray' module, polybar, "
                "i3status-rust 'tray', …).\n"
                "• KDE Plasma / Cinnamon / LXQt / Budgie — should work out "
                "of the box; if not, your panel/bar may have crashed — try "
                "logging out and back in.",
            ),
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
    daemon.worker.discordConnectionChanged.connect(tray.set_discord_connected)
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
    settings.applied.connect(_apply_log_level)

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
            _tr("app", "Updates"),
            _tr("app", "You're already on the latest version ({version}).").format(version=v),
        )
    )
    updater.checkFailed.connect(
        lambda msg: QMessageBox.warning(settings, _tr("app", "Updates"), msg)
    )
    updater.releaseInfoFetched.connect(settings.set_latest_release)

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

    # Uninstall wireup — the SettingsWindow already showed + confirmed
    # the destructive dialog. Stop the daemon first (so nothing rewrites
    # config after the purge), wipe everything in-process, show the
    # package-removal command, then quit. Same core as `--uninstall`.
    def _uninstall() -> None:
        from refrain.uninstall import purge, removal_command
        from refrain.updater import detect_install_type

        log.info("Uninstall requested from Settings")
        cmd = removal_command(detect_install_type(), os.environ.get("APPIMAGE"))
        with contextlib.suppress(Exception):
            daemon.stop()
        report = purge()
        log.info(
            "Uninstall purge: %d removed, %d failed, secrets=%s",
            len(report.removed),
            len(report.failed),
            report.secrets_purged,
        )
        body = _tr(
            "app",
            "All Refrain data and the Last.fm keyring credentials were "
            "removed. Refrain will now close.\n\nTo remove the program "
            "itself, run:\n\n  {cmd}",
        ).format(cmd=cmd)
        if report.failed:
            body += "\n\n" + _tr("app", "Some files could not be removed:") + "\n" + "\n".join(
                f"  {f}" for f in report.failed
            )
        QMessageBox.information(settings, _tr("app", "Uninstall"), body)
        app.quit()

    settings.uninstallRequested.connect(_uninstall)

    _install_signal_handlers(app)
    _sync_autostart(config)

    daemon.start()

    # First-run wizard fires once: when the user has never finished it AND
    # hasn't yet pasted a Discord client_id. Shows tray-icon orientation,
    # runs Discord IPC + iTunes probes for live diagnostics, prompts for
    # the Application ID. Skipping is fine — the user can paste it later
    # from Settings → General.
    if not config.behavior.first_run_complete and not config.discord.client_id:
        welcome = WelcomeDialog()
        # Pin so neither the QDialog nor its diagnostics QThread are
        # collected before the user closes the wizard. Without this,
        # `welcome` falls out of scope as soon as the if-branch ends
        # and the still-running diagnostics thread crashes refrain
        # with "QThread: Destroyed while thread is still running".
        app._refrain_welcome = welcome

        def _on_welcome_applied(client_id: str) -> None:
            log.info(
                "Welcome wizard applied — client_id=%s",
                "(empty)" if not client_id else f"set ({len(client_id)} chars)",
            )
            try:
                config.behavior.first_run_complete = True
                if client_id:
                    config.discord.client_id = client_id
                try:
                    config.save()
                    log.info("Welcome wizard: config persisted to disk")
                except Exception as e:
                    log.warning("Could not persist first-run wizard result: %s", e)
                # Cross-thread safe: emit the existing `settings.applied`
                # signal (already QueuedConnection-wired to
                # `daemon.worker.update_config`) instead of trying to
                # use QMetaObject.invokeMethod with Q_ARG(object, ...),
                # which PySide6 rejects with "Unable to find a
                # QMetaType for 'object'".
                settings.applied.emit(config)
            except Exception as e:
                log.exception("Welcome wizard apply hook failed: %s", e)

        # Connect to BOTH the explicit Apply signal AND the dialog's
        # `finished` signal. `finished` fires for any close path
        # (accept, reject, X, Esc) — listening to it guarantees the
        # post-wizard hook runs even if the user dismisses without
        # confirming. When applied has already fired, finished fires
        # *after* it, so the post-dialog handler sees the persisted
        # state.
        welcome.applied.connect(_on_welcome_applied)

        def _after_wizard(_result: int) -> None:
            log.info("Welcome wizard closed (result=%s)", _result)
            if not args.silent:
                try:
                    # SettingsWindow loaded its form from the original
                    # (empty client_id) config when it was constructed,
                    # before the wizard saved the user's input. Reload
                    # so the Discord-ID input shows the value the user
                    # just entered instead of staying blank.
                    settings._load_into_form()
                    settings.show()
                    settings.raise_()
                    settings.activateWindow()
                    log.info("Settings window shown")
                except Exception as e:
                    log.exception("Could not open Settings after wizard: %s", e)

        welcome.finished.connect(_after_wizard)
        welcome.show()
        welcome.raise_()
        welcome.activateWindow()
        welcome.start_diagnostics()
    elif not args.silent:
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
        # Pick the right binary to re-exec:
        #   - Inside an AppImage, $APPIMAGE is the original .AppImage path
        #     (sys.argv[0] points into the AppImage's mount, which exec
        #     would resolve correctly but is less stable across mounts).
        #   - Otherwise, sys.argv[0] is the entry-point script the user
        #     actually launched (venv shim, system /usr/bin/refrain, etc.).
        binary = os.environ.get("APPIMAGE") or sys.argv[0]
        # Drop our reference to the bus connection. Note that
        # `dbus.SessionBus()` is a process-singleton wrapper, so this
        # alone doesn't trigger ReleaseName — we rely on os.execvp
        # replacing the process image, which closes the underlying
        # socket via CLOEXEC and lets the bus daemon reclaim the name.
        app._refrain_bus_lock = None
        log.info("Re-executing %s for restart", binary)
        # Flush + close handlers before execv replaces the process image
        # so the rotating file handler's buffer doesn't lose the last
        # restart line.
        logging.shutdown()
        # Two failure modes we have to handle:
        #   1. binary is empty or not a real path (sys.argv[0] missing,
        #      or `python -m refrain` invocation where argv[0] is the
        #      __main__.py file rather than an executable).
        #   2. execvp itself fails (binary not on PATH, permission denied).
        # Fall back to `<sys.executable> -m refrain`, which always works
        # if Refrain is currently importable (which it must be, since
        # we just ran it). Without this fallback, source-checkout dev
        # users would get an OSError traceback instead of a working
        # restart.
        is_runnable_script = binary and (
            os.path.isabs(binary)
            and os.access(binary, os.X_OK)
            and not binary.endswith(".py")
        )
        if not is_runnable_script:
            log.info(
                "Restart: %r isn't a runnable script; falling back to `%s -m refrain`",
                binary,
                sys.executable,
            )
            binary = sys.executable
            new_argv = ["-m", "refrain", *new_argv]
        try:
            os.execvp(binary, [binary, *new_argv])
        except OSError as e:
            log.error(
                "Re-exec failed (%s) — falling back to `%s -m refrain`",
                e,
                sys.executable,
            )
            try:
                os.execvp(sys.executable, [sys.executable, "-m", "refrain", *new_argv])
            except OSError as e2:
                log.error("Fallback re-exec also failed (%s); exiting non-zero", e2)
                return 1

    log.info("Refrain shutting down with rc=%d", rc)
    return rc


def _open_update_dialog_factory(updater: UpdateOrchestrator, parent_widget):
    def _open(_release=None) -> None:
        release = _release if _release is not None else updater.latest
        if release is None:
            QMessageBox.information(
                parent_widget,
                QCoreApplication.translate("app", "Updates"),
                QCoreApplication.translate(
                    "app", "No update information available yet. Try again in a moment."
                ),
            )
            return
        dlg = UpdateDialog(release, parent=parent_widget)
        dlg.exec()

    return _open


if __name__ == "__main__":
    sys.exit(main())
