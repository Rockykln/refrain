"""System tray icon — always visible, mirrors current state, hosts player controls."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from refrain.paths import assets_dir
from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)


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
        icons_dir = assets_dir() / "icons"
        self._icons = {
            PlaybackStatus.PLAYING: QIcon(str(icons_dir / "tray-playing.svg")),
            PlaybackStatus.PAUSED: QIcon(str(icons_dir / "tray-paused.svg")),
            PlaybackStatus.STOPPED: QIcon(str(icons_dir / "tray-stopped.svg")),
        }
        self._tray = QSystemTrayIcon(self._icons[PlaybackStatus.STOPPED])
        self._tray.setToolTip("Refrain")
        # Tray menu actions are rendered via DBusMenu by the system shell;
        # action text changes do NOT propagate while the menu is open. We
        # mirror the live track + progress info into the tray *tooltip* too
        # because tooltips DO refresh in real time.
        self._current_track_line = ""
        self._current_progress_line = ""

        self._title_action = QAction("(nothing playing)")
        self._title_action.setEnabled(False)
        self._artist_action = QAction("")
        self._artist_action.setEnabled(False)
        self._progress_action = QAction("")
        self._progress_action.setEnabled(False)
        self._progress_action.setVisible(False)
        self._discord_action = QAction("○  Discord: not connected")
        self._discord_action.setEnabled(False)

        self._previous_action = QAction("⏮  Previous")
        self._previous_action.triggered.connect(self.previousRequested.emit)
        self._play_pause_action = QAction("⏵  Play")
        self._play_pause_action.triggered.connect(self.playPauseRequested.emit)
        self._next_action = QAction("⏭  Next")
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
        self._update_action = QAction("⬆  Update available")
        self._update_action.setVisible(False)
        self._update_action.triggered.connect(self.updateRequested.emit)
        menu.addAction(self._update_action)
        settings_action = menu.addAction("Settings…")
        settings_action.triggered.connect(self.settingsRequested.emit)
        log_action = menu.addAction("Live log…")
        log_action.triggered.connect(self.logRequested.emit)
        menu.addSeparator()
        restart_action = menu.addAction("⟳  Restart Refrain")
        restart_action.triggered.connect(self.restartRequested.emit)
        quit_action = menu.addAction("Quit Refrain")
        quit_action.triggered.connect(self.quitRequested.emit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.settingsRequested.emit()

    def set_status(self, status: PlaybackStatus) -> None:
        icon = self._icons.get(status)
        if icon is not None:
            self._tray.setIcon(icon)
        if status == PlaybackStatus.PLAYING:
            self._play_pause_action.setText("⏸  Pause")
        else:
            self._play_pause_action.setText("⏵  Play")

    def set_update_available(self, available: bool, version: str = "") -> None:
        if available and version:
            self._update_action.setText(f"⬆  Update available — v{version}")
        else:
            self._update_action.setText("⬆  Update available")
        self._update_action.setVisible(available)

    def set_discord_connected(self, connected: bool) -> None:
        if connected:
            self._discord_action.setText("●  Discord: connected")
        else:
            self._discord_action.setText("○  Discord: not connected")

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
            f"⏱  {pos // 60}:{pos % 60:02d} / {dur // 60}:{dur % 60:02d} "
            f"(–{rem // 60}:{rem % 60:02d})"  # noqa: RUF001 — en-dash for "minus"
        )
        self._progress_action.setText(progress)
        self._progress_action.setVisible(True)
        self._current_progress_line = progress
        self._refresh_tooltip()

    def set_track(self, track: TrackInfo) -> None:
        if not track.has_track:
            self._title_action.setText("(nothing playing)")
            self._artist_action.setText("")
            self._progress_action.setVisible(False)
            self._current_track_line = ""
            self._current_progress_line = ""
            self._tray.setToolTip("Refrain")
            return
        self._title_action.setText(f"♪ {track.title}")
        if track.artist and track.album:
            line = f"{track.artist} • {track.album}"
        elif track.artist:
            line = track.artist
        else:
            line = track.album or "—"
        self._artist_action.setText(line)
        self._current_track_line = f"{track.title}\n{line}"
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
