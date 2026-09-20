"""System tray icon — always visible, mirrors current state, hosts player controls."""

from __future__ import annotations

import contextlib
import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from refrain import dev_metrics
from refrain.paths import assets_dir
from refrain.service_status import DiscordStatus, LastfmStatus, StatusSnapshot
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


# A menu is as wide as its longest entry, and a shell cannot elide it for us.
_MENU_CHARS = 52


def _menu_width(text: str) -> str:
    return text if len(text) <= _MENU_CHARS else text[: _MENU_CHARS - 1].rstrip() + "…"


class TrayIcon(QObject):
    statusRequested = Signal()
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
    sharingToggled = Signal(bool)  # True = pause sharing

    # Apple Music reports "paused" for under a second between songs (measured
    # 0.56-0.71 s). The icon and the Play/Pause entry only follow a pause once
    # it has lasted this long, so they don't flash at every song change.
    _PAUSE_SHOWN_AFTER_MS = 1000
    # A pause asked for from the tray itself is real; show it at once.
    _OWN_CONTROL_WINDOW_S = 3.0

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._icons_dir = assets_dir() / "icons"
        self._current_status: PlaybackStatus = PlaybackStatus.STOPPED
        self._pending_status: PlaybackStatus | None = None
        self._own_control_at = -1e9
        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.timeout.connect(self._apply_pending_status)
        self._icons = self._build_icons_for_current_theme()
        self._tray = QSystemTrayIcon(self._icons[PlaybackStatus.STOPPED])
        self._tray.setToolTip("Refrain")
        self._hint_click = None
        self._tray.messageClicked.connect(self._on_message_clicked)
        # Tray menu actions are rendered via DBusMenu by the system shell;
        # action text changes do NOT propagate while the menu is open. We
        # mirror the live track + progress info into the tray *tooltip* too
        # because tooltips DO refresh in real time.
        self._current_track_line = ""
        self._current_progress_line = ""

        # Info rows: title / artist / progress / Discord / Last.fm.
        # Left ENABLED on purpose — KDE Plasma's DBusMenu renderer (and
        # GNOME's AppIndicator) draw disabled QActions in a muted /
        # greyed-out style, which makes the song info read like
        # broken rows next to the white action labels below.
        # A click on one opens the Status window, which says the same
        # thing in full and offers the fix.
        self._title_action = QAction(self.tr("(nothing playing)"))
        self._title_action.setIcon(QIcon.fromTheme("view-media-track"))
        self._title_action.triggered.connect(self.statusRequested.emit)
        self._artist_action = QAction("")
        self._artist_action.setIcon(QIcon.fromTheme("view-media-artist"))
        self._artist_action.triggered.connect(self.statusRequested.emit)
        # Hidden until a real track populates it — otherwise it
        # renders as a tall empty row right under "(nothing playing)".
        self._artist_action.setVisible(False)
        self._progress_action = QAction("")
        self._progress_action.setIcon(QIcon.fromTheme("chronometer"))
        self._progress_action.triggered.connect(self.statusRequested.emit)
        self._progress_action.setVisible(False)
        self._discord_action = QAction(self.tr("Discord: checking…"))
        self._discord_action.setIcon(QIcon.fromTheme("network-disconnect"))
        self._discord_action.triggered.connect(self.statusRequested.emit)
        # Hidden while Last.fm was never set up — a "Last.fm: off" line
        # would just be noise for the majority who never scrobble.
        self._lastfm_action = QAction("")
        self._lastfm_action.setIcon(QIcon.fromTheme("network-disconnect"))
        self._lastfm_action.triggered.connect(self.statusRequested.emit)
        self._lastfm_action.setVisible(False)
        self._developer_action = QAction(self.tr("Developer mode"))
        self._developer_action.setIcon(QIcon.fromTheme("applications-development"))
        self._developer_action.triggered.connect(self.developerRequested.emit)
        self._developer_action.setVisible(False)

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
        self._play_pause_action.triggered.connect(self._request_play_pause)
        self._next_action = QAction(self.tr("Next"))
        self._next_action.setIcon(QIcon.fromTheme("media-skip-forward"))
        self._next_action.triggered.connect(self.nextRequested.emit)

        menu = QMenu()
        menu.addAction(self._title_action)
        menu.addAction(self._artist_action)
        menu.addAction(self._progress_action)
        menu.addAction(self._discord_action)
        menu.addAction(self._lastfm_action)
        menu.addAction(self._developer_action)
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
        # Hidden while the history is switched off (set_history_enabled)
        # — a menu entry that opens an empty "turned off" window is a
        # dead end.
        self._history_action = QAction(self.tr("Recently played…"))
        self._history_action.setIcon(QIcon.fromTheme("document-open-recent"))
        self._history_action.triggered.connect(self.historyRequested.emit)
        # Pausing sharing lives in the Status window, which a click on the icon
        # already opens; Settings stays here too, because this menu is where
        # people look for it.
        self._sharing_paused = False
        menu.addAction(self._history_action)
        self._settings_action = QAction(self.tr("Settings…"))
        self._settings_action.setIcon(QIcon.fromTheme("configure"))
        self._settings_action.triggered.connect(self.settingsRequested.emit)
        menu.addAction(self._settings_action)
        # Rarely needed, and Restart sat right above Quit: tucked away,
        # still two clicks from the top.
        self._more_menu = menu.addMenu(self.tr("Troubleshooting"))
        self._more_menu.setIcon(QIcon.fromTheme("tools-report-bug"))
        self._log_action = self._more_menu.addAction(self.tr("Live log…"))
        self._log_action.setIcon(QIcon.fromTheme("view-list-text"))
        self._log_action.triggered.connect(self.logRequested.emit)
        self._restart_action = self._more_menu.addAction(self.tr("Restart Refrain"))
        self._restart_action.setIcon(QIcon.fromTheme("view-refresh"))
        self._restart_action.triggered.connect(self.restartRequested.emit)
        menu.addSeparator()
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

        menu.triggered.connect(self._on_menu_triggered)
        self._menu = menu
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
            self.statusRequested.emit()
        elif reason == QSystemTrayIcon.MiddleClick:
            # Middle-click toggles playback on the current MPRIS source —
            # same path as the tray-menu Play/Pause item, so a Bluetooth
            # headphone driving Refrain via MPRIS-server gets the same
            # PlayPause command as a click on the Apple Music tab.
            self._request_play_pause()

    def show_hint(self, text: str, on_click=None) -> None:
        self._hint_click = on_click
        self._tray.showMessage("Refrain", text, self._tray.icon(), 6000)

    def _on_message_clicked(self) -> None:
        # Only the hint that asked for it reacts, so a later one is not its click.
        handler, self._hint_click = self._hint_click, None
        if handler is not None:
            handler()

    def _request_play_pause(self) -> None:
        self._own_control_at = time.monotonic()
        self.playPauseRequested.emit()

    def set_status(self, status: PlaybackStatus) -> None:
        own = time.monotonic() - self._own_control_at < self._OWN_CONTROL_WINDOW_S
        if (
            status != PlaybackStatus.PLAYING
            and self._current_status == PlaybackStatus.PLAYING
            and not own
        ):
            # Leaving "playing": wait and see. Back to playing within the
            # window cancels it; the end of playback shows after the delay.
            self._pending_status = status
            if not self._pause_timer.isActive():
                self._pause_timer.start(self._PAUSE_SHOWN_AFTER_MS)
            return
        self._pause_timer.stop()
        self._pending_status = None
        self._apply_status(status)

    def _apply_pending_status(self) -> None:
        status, self._pending_status = self._pending_status, None
        if status is not None:
            self._apply_status(status)

    def _apply_status(self, status: PlaybackStatus) -> None:
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

    def _on_menu_triggered(self, action: QAction) -> None:
        if not dev_metrics.enabled():
            return
        name = next((k.lstrip("_") for k, v in vars(self).items() if v is action), None)
        dev_metrics.tray_action(name or f"action{self._menu.actions().index(action)}")

    def set_developer_mode(self, on: bool) -> None:
        self._developer_action.setVisible(on)

    def set_history_enabled(self, enabled: bool) -> None:
        self._history_action.setVisible(enabled)

    def set_sharing_paused(self, paused: bool) -> None:
        """Kept for the status lines; pausing itself lives in the Status window."""
        self._sharing_paused = paused

    def set_service_status(self, status: StatusSnapshot) -> None:
        """The Discord and Last.fm rows, from the live state."""
        d = status.discord
        if d is DiscordStatus.NOT_SET_UP:
            text, icon = self.tr("Discord: not set up — add your Application ID"), "dialog-warning"
        elif d is DiscordStatus.NO_CLIENT:
            text, icon = self.tr("Discord: app isn't running"), "network-disconnect"
        elif d is DiscordStatus.REJECTED:
            text, icon = self.tr("Discord: Application ID rejected — check it"), "dialog-warning"
        elif d is DiscordStatus.ERROR:
            text, icon = self.tr("Discord: not answering"), "dialog-warning"
        elif d is DiscordStatus.READY:
            text, icon = self.tr("Discord: ready — waiting for music"), "network-connect"
        elif d is DiscordStatus.SHOWING:
            text, icon = self.tr("Discord: visible on your profile"), "network-connect"
        elif d is DiscordStatus.SHOWING_MINIMAL:
            text, icon = self.tr("Discord: showing “Listening to music”"), "network-connect"
        elif d is DiscordStatus.PAUSED:
            text, icon = self.tr("Discord: hidden while paused"), "network-disconnect"
        elif d is DiscordStatus.PRIVACY_OFF:
            text, icon = self.tr("Discord: hidden — sharing is off"), "network-disconnect"
        else:
            text, icon = self.tr("Discord: checking…"), "network-disconnect"
        self._discord_action.setText(text)
        self._discord_action.setIcon(QIcon.fromTheme(icon))

        f = status.lastfm
        self._lastfm_action.setVisible(f is not LastfmStatus.OFF)
        if f is LastfmStatus.CONNECTED_OFF:
            text, icon = self.tr("Last.fm: scrobbling is off"), "network-disconnect"
        elif f is LastfmStatus.NOT_CONNECTED:
            text, icon = self.tr("Last.fm: not connected"), "dialog-warning"
        elif f is LastfmStatus.WAITING:
            count = int(status.lastfm_detail) if status.lastfm_detail.isdigit() else 0
            text = self.tr("Last.fm: %n scrobble(s) waiting", "", count)
            icon = "network-disconnect"
        elif f is LastfmStatus.EXPIRED:
            text, icon = self.tr("Last.fm: sign-in expired — reconnect"), "dialog-warning"
        elif f is LastfmStatus.PAUSED:
            text, icon = self.tr("Last.fm: paused — sharing is off"), "network-disconnect"
        elif f is LastfmStatus.SCROBBLING and status.lastfm_detail:
            text = self.tr("Last.fm: scrobbling as {user}").format(user=status.lastfm_detail)
            icon = "network-connect"
        else:
            text, icon = self.tr("Last.fm: scrobbling"), "network-connect"
        self._lastfm_action.setText(text)
        self._lastfm_action.setIcon(QIcon.fromTheme(icon))

    def set_progress(self, position_ms: int, duration_ms: int) -> None:
        """Render the progress line. A negative position hides it.

        A zero or absent duration means the source gave no track length —
        common on Bluetooth AVRCP, and the case on a streaming source
        whose catalog lookup found nothing. The elapsed count is still
        worth showing on its own; only a position we don't trust at all
        takes the line away, which the daemon signals with -1.
        """
        if position_ms < 0:
            self._progress_action.setText("")
            self._progress_action.setVisible(False)
            self._current_progress_line = ""
            self._refresh_tooltip()
            return
        pos = max(0, position_ms) // 1000
        dur = max(0, duration_ms) // 1000
        if dur <= 0:
            progress = f"{pos // 60}:{pos % 60:02d}"
        else:
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
            # Hide instead of leaving a tall blank line under
            # "(nothing playing)" that reads as a layout glitch.
            self._artist_action.setVisible(False)
            self._progress_action.setVisible(False)
            self._current_track_line = ""
            self._current_progress_line = ""
            self._tray.setToolTip("Refrain")
            return
        self._title_action.setText(_menu_width(track.title))
        if track.artist and track.album:
            line = f"{track.artist} • {track.album}"
        elif track.artist:
            line = track.artist
        else:
            line = track.album or "—"
        self._artist_action.setText(_menu_width(line))
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
