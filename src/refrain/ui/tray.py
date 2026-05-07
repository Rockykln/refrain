"""System tray icon — always visible, mirrors current state, hosts player controls."""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from refrain.paths import assets_dir
from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)


def _detect_color_scheme() -> str:
    """Return ``'dark'`` if the system theme is dark, else ``'light'``.

    Prefers Qt 6.5+'s `styleHints().colorScheme()`. Falls back to a
    luminance check on the WindowText palette colour for older Qt — if
    the *text* the system draws is bright, the surface behind it is
    dark, so we want bright tray glyphs.
    """
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if callable(scheme):
        with contextlib.suppress(Exception):
            value = scheme()
            if value == Qt.ColorScheme.Dark:
                log.debug("Theme detected via styleHints.colorScheme: dark")
                return "dark"
            if value == Qt.ColorScheme.Light:
                log.debug("Theme detected via styleHints.colorScheme: light")
                return "light"
    palette = QGuiApplication.palette()
    text = palette.color(palette.ColorRole.WindowText)
    luminance = 0.299 * text.red() + 0.587 * text.green() + 0.114 * text.blue()
    result = "dark" if luminance > 128 else "light"
    log.debug(
        "Theme detected via palette luminance: WindowText=%s lum=%.0f → %s",
        text.name(),
        luminance,
        result,
    )
    return result


class TrayIcon(QObject):
    settingsRequested = Signal()
    quitRequested = Signal()
    restartRequested = Signal()
    playPauseRequested = Signal()
    nextRequested = Signal()
    previousRequested = Signal()
    updateRequested = Signal()
    logRequested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._icons_dir = assets_dir() / "icons"
        self._current_status: PlaybackStatus = PlaybackStatus.STOPPED
        self._icons = self._build_icons_for_current_theme()
        self._tray = QSystemTrayIcon(self._icons[PlaybackStatus.STOPPED])
        self._tray.setToolTip("Refrain")
        # Tray menu actions are rendered via DBusMenu by the system shell;
        # action text changes do NOT propagate while the menu is open. We
        # mirror the live track + progress info into the tray *tooltip* too
        # because tooltips DO refresh in real time.
        self._current_track_line = ""
        self._current_progress_line = ""

        # Info rows: title / artist / progress / Discord-status.
        # Left ENABLED on purpose — KDE Plasma's DBusMenu renderer (and
        # GNOME's AppIndicator) draw disabled QActions in a muted /
        # greyed-out style, which made the song info read like
        # "broken" rows with their text washed out next to the white
        # action labels below. Enabled rows render in the standard
        # menu-item colour. Click-handlers for these rows fall back
        # to opening Settings (their natural "tell me more" target);
        # we don't want them to look greyed-out + indented + iconless.
        self._title_action = QAction(self.tr("(nothing playing)"))
        self._title_action.setIcon(QIcon.fromTheme("view-media-track"))
        self._title_action.triggered.connect(self.settingsRequested.emit)
        self._artist_action = QAction("")
        self._artist_action.setIcon(QIcon.fromTheme("view-media-artist"))
        self._artist_action.triggered.connect(self.settingsRequested.emit)
        # Hidden until a real track populates it — otherwise it
        # rendered as a tall empty row right under "(nothing playing)".
        self._artist_action.setVisible(False)
        self._progress_action = QAction("")
        self._progress_action.setIcon(QIcon.fromTheme("chronometer"))
        self._progress_action.triggered.connect(self.settingsRequested.emit)
        self._progress_action.setVisible(False)
        self._discord_action = QAction(self.tr("Discord: not connected"))
        self._discord_action.setIcon(QIcon.fromTheme("network-disconnect"))
        self._discord_action.triggered.connect(self.settingsRequested.emit)

        # Once any item in a QMenu has an icon, the menu reserves the
        # icon column for ALL items. Without icons here the playback /
        # navigation rows would render as blank-icon-column + text,
        # while Update / Settings / Log / Restart / Quit had glyphs —
        # visually unbalanced. Theme icons (freedesktop names) match
        # the user's Plasma / GNOME / Breeze icon set; a missing theme
        # icon falls back to a null QIcon and the row degrades to
        # text-only without breaking layout.
        self._previous_action = QAction(self.tr("Previous"))
        self._previous_action.setIcon(QIcon.fromTheme("media-skip-backward"))
        self._previous_action.triggered.connect(self.previousRequested.emit)
        self._play_pause_action = QAction(self.tr("Play"))
        self._play_pause_action.setIcon(QIcon.fromTheme("media-playback-start"))
        self._play_pause_action.triggered.connect(self.playPauseRequested.emit)
        self._next_action = QAction(self.tr("Next"))
        self._next_action.setIcon(QIcon.fromTheme("media-skip-forward"))
        self._next_action.triggered.connect(self.nextRequested.emit)

        menu = QMenu()
        menu.addAction(self._title_action)
        menu.addAction(self._artist_action)
        menu.addAction(self._progress_action)
        menu.addAction(self._discord_action)
        menu.addSeparator()
        menu.addAction(self._previous_action)
        menu.addAction(self._play_pause_action)
        menu.addAction(self._next_action)
        menu.addSeparator()
        # Hidden by default — only shown when an update has been detected.
        # Blue up-arrow icon (KDE Breeze accent) instead of a plain
        # unicode glyph so the line stands out from the rest of the
        # white menu text. Without this, "Update available — vX.Y.Z"
        # is a quiet white line easy to miss; with the colored icon
        # in the menu's icon column, it reads as the obvious action.
        self._update_action = QAction(self.tr("Update available"))
        update_icon_path = self._icons_dir / "menu-update.svg"
        if update_icon_path.exists():
            self._update_action.setIcon(QIcon(str(update_icon_path)))
        self._update_action.setVisible(False)
        self._update_action.triggered.connect(self.updateRequested.emit)
        menu.addAction(self._update_action)
        settings_action = menu.addAction(self.tr("Settings…"))
        settings_action.setIcon(QIcon.fromTheme("configure"))
        settings_action.triggered.connect(self.settingsRequested.emit)
        log_action = menu.addAction(self.tr("Live log…"))
        log_action.setIcon(QIcon.fromTheme("view-list-text"))
        log_action.triggered.connect(self.logRequested.emit)
        menu.addSeparator()
        restart_action = menu.addAction(self.tr("Restart Refrain"))
        restart_action.setIcon(QIcon.fromTheme("view-refresh"))
        restart_action.triggered.connect(self.restartRequested.emit)
        quit_action = menu.addAction(self.tr("Quit Refrain"))
        # Red "✕" icon marks the destructive action — KDE Plasma's
        # DBusMenu renderer shows it in the menu's icon column, GNOME
        # extensions and AppIndicator do the same. Qt's QAction has no
        # per-action text-colour API and DBusMenu has no portable
        # disposition flag, so a coloured icon is the most reliable
        # cross-DE way to signal "this exits the app".
        quit_icon_path = self._icons_dir / "menu-quit.svg"
        if quit_icon_path.exists():
            quit_action.setIcon(QIcon(str(quit_icon_path)))
            quit_action.setIconVisibleInMenu(True)
        quit_action.triggered.connect(self.quitRequested.emit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

        # Re-render tray glyphs when the system flips between dark and
        # light theme — Qt 6.5+ exposes this signal on styleHints().
        hints = QGuiApplication.styleHints()
        signal = getattr(hints, "colorSchemeChanged", None)
        if signal is not None:
            with contextlib.suppress(Exception):
                signal.connect(self._on_color_scheme_changed)

    def _build_icons_for_current_theme(self) -> dict[PlaybackStatus, QIcon]:
        # On a dark system theme the tray panel is dark, so the glyph has
        # to be bright (the existing `tray-<state>.svg` set). On a light
        # theme it has to be dark — that's the `*-dark.svg` variants.
        scheme = _detect_color_scheme()
        suffix = "-dark" if scheme == "light" else ""
        return {
            PlaybackStatus.PLAYING: QIcon(str(self._icons_dir / f"tray-playing{suffix}.svg")),
            PlaybackStatus.PAUSED: QIcon(str(self._icons_dir / f"tray-paused{suffix}.svg")),
            PlaybackStatus.STOPPED: QIcon(str(self._icons_dir / f"tray-stopped{suffix}.svg")),
        }

    def _on_color_scheme_changed(self, *_args) -> None:
        log.debug("System color scheme changed; refreshing tray icons")
        self._icons = self._build_icons_for_current_theme()
        icon = self._icons.get(self._current_status)
        if icon is not None:
            self._tray.setIcon(icon)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.settingsRequested.emit()
        elif reason == QSystemTrayIcon.MiddleClick:
            # Middle-click toggles playback on the current MPRIS source —
            # same path as the tray-menu Play/Pause item, so a Bluetooth
            # headphone driving Refrain via MPRIS-server gets the same
            # PlayPause command as a click on the Apple Music tab.
            self.playPauseRequested.emit()

    def set_status(self, status: PlaybackStatus) -> None:
        self._current_status = status
        icon = self._icons.get(status)
        if icon is not None:
            self._tray.setIcon(icon)
        if status == PlaybackStatus.PLAYING:
            self._play_pause_action.setText(self.tr("Pause"))
            self._play_pause_action.setIcon(QIcon.fromTheme("media-playback-pause"))
        else:
            self._play_pause_action.setText(self.tr("Play"))
            self._play_pause_action.setIcon(QIcon.fromTheme("media-playback-start"))

    def set_update_available(self, available: bool, version: str = "") -> None:
        if available and version:
            self._update_action.setText(
                self.tr("Update available — v{version}").format(version=version)
            )
        else:
            self._update_action.setText(self.tr("Update available"))
        self._update_action.setVisible(available)

    def set_discord_connected(self, connected: bool) -> None:
        if connected:
            self._discord_action.setText(self.tr("Discord: connected"))
            self._discord_action.setIcon(QIcon.fromTheme("network-connect"))
        else:
            self._discord_action.setText(self.tr("Discord: not connected"))
            self._discord_action.setIcon(QIcon.fromTheme("network-disconnect"))

    def set_progress(self, position_ms: int, duration_ms: int) -> None:
        if duration_ms <= 0:
            self._progress_action.setText("")
            self._progress_action.setVisible(False)
            self._current_progress_line = ""
            self._refresh_tooltip()
            return
        pos = max(0, position_ms) // 1000
        dur = max(0, duration_ms) // 1000
        rem = max(0, dur - pos)
        progress = (
            f"{pos // 60}:{pos % 60:02d} / {dur // 60}:{dur % 60:02d} "
            f"(–{rem // 60}:{rem % 60:02d})"  # noqa: RUF001 — en-dash for "minus"
        )
        self._progress_action.setText(progress)
        self._progress_action.setVisible(True)
        self._current_progress_line = progress
        self._refresh_tooltip()

    def set_track(self, track: TrackInfo) -> None:
        if not track.has_track:
            self._title_action.setText(self.tr("(nothing playing)"))
            self._artist_action.setText("")
            # Hide instead of leaving an empty row — without this the
            # menu showed "(nothing playing)" followed by a tall blank
            # line that read as a layout glitch.
            self._artist_action.setVisible(False)
            self._progress_action.setVisible(False)
            self._current_track_line = ""
            self._current_progress_line = ""
            self._tray.setToolTip("Refrain")
            return
        self._title_action.setText(track.title)
        if track.artist and track.album:
            line = f"{track.artist} • {track.album}"
        elif track.artist:
            line = track.artist
        else:
            line = track.album or "—"
        self._artist_action.setText(line)
        self._artist_action.setVisible(True)
        new_track_line = f"{track.title}\n{line}"
        # If the track text actually changed, drop the stale progress
        # line so the tooltip doesn't briefly show "Song B • 1:30/2:11"
        # using Song A's elapsed counter while a paused new track waits
        # for its first progressTick.
        if new_track_line != self._current_track_line:
            self._current_progress_line = ""
            self._progress_action.setVisible(False)
        self._current_track_line = new_track_line
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        """Rebuild the tray-icon tooltip from current track + progress.

        Tooltip is the only menu surface that refreshes live on KDE / GNOME
        — DBusMenu holds the popup menu's text static once it's open, so a
        progress timer in the menu visibly freezes mid-song. The tooltip
        gives users a real ticker by hovering the tray icon.
        """
        if not self._current_track_line:
            self._tray.setToolTip("Refrain")
            return
        if self._current_progress_line:
            self._tray.setToolTip(f"{self._current_track_line}\n{self._current_progress_line}")
        else:
            self._tray.setToolTip(self._current_track_line)
