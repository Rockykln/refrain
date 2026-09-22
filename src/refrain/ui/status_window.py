"""Status window: what is playing, whether Discord and Last.fm show it, and what to do if not."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QEvent,
    QLocale,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from refrain import __version__
from refrain.cover_art import image_path_for_url
from refrain.history import HistoryEntry, HistorySnapshot
from refrain.paths import assets_dir, state_dir
from refrain.service_status import DiscordStatus, LastfmStatus, StatusSnapshot
from refrain.sources.base import PlaybackStatus, TrackInfo
from refrain.ui import clock, icons
from refrain.ui.cursors import apply_interactive_cursors
from refrain.ui.external_link import confirm_and_open
from refrain.ui.history_window import _ElidedLabel, _muted, _read_scaled, _set_color, song_link
from refrain.ui.legal_dialog import GITHUB_URL
from refrain.ui.tooltips import keep_on_window, show_on_window

log = logging.getLogger(__name__)

_COVER_PX = 80
_COVER_RADIUS = 8
# The list shows as many songs as the window has room for, never more, and
# never enough to need a scrollbar. Both are guesses until a row exists.
_ROW_GUESS_PX = 26
_RECENT_GUESS = 5

# (light theme, dark theme)
_TONES = {
    "ok": ("#1e8a4c", "#4cc27a"),
    "warn": ("#9a6700", "#e8b800"),
    "bad": ("#b3261e", "#ff7777"),
}

_DISCORD_TONE = {
    DiscordStatus.STARTING: "off",
    DiscordStatus.NOT_SET_UP: "warn",
    DiscordStatus.NO_CLIENT: "warn",
    DiscordStatus.REJECTED: "bad",
    DiscordStatus.NOT_LOGGED_IN: "warn",
    DiscordStatus.ERROR: "warn",
    DiscordStatus.READY: "ok",
    DiscordStatus.SHOWING: "ok",
    DiscordStatus.SHOWING_MINIMAL: "ok",
    DiscordStatus.PAUSED: "off",
    DiscordStatus.PRIVACY_OFF: "off",
}

_LASTFM_TONE = {
    LastfmStatus.OFF: "off",
    LastfmStatus.CONNECTED_OFF: "off",
    LastfmStatus.NOT_CONNECTED: "warn",
    LastfmStatus.SCROBBLING: "ok",
    LastfmStatus.WAITING: "warn",
    LastfmStatus.EXPIRED: "bad",
    LastfmStatus.PAUSED: "off",
}


def geometry_path():
    return state_dir() / "status-window.json"


def _tone_color(tone: str, palette: QPalette) -> QColor:
    if tone == "off":
        return _muted(palette)
    dark = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window).lightness() < 128
    return QColor(_TONES[tone][dark])


def _rounded(image, px: int, radius: float, dpr: float) -> QPixmap:
    size = round(px * dpr)
    scaled = QPixmap.fromImage(image).scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius * dpr, radius * dpr)
    p.setClipPath(path)
    p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def _clock(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _placeholder(palette: QPalette, px: int, dpr: float) -> QPixmap:
    size = round(px * dpr)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), _COVER_RADIUS * dpr, _COVER_RADIUS * dpr)
    tint = QColor(palette.color(QPalette.ColorRole.WindowText))
    tint.setAlphaF(0.09)
    p.fillPath(path, tint)
    icon = QIcon(str(assets_dir() / "icons" / "refrain.svg"))
    glyph = round(size * 0.5)
    p.setOpacity(0.55)
    icon.paint(p, (size - glyph) // 2, (size - glyph) // 2, glyph, glyph)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class _Line(_ElidedLabel):
    """A song line that scrolls its full text twice when it does not fit."""

    _PAUSE_MS = 1200
    _END_MS = 2000
    _BETWEEN_MS = 3000
    _FRAME_MS = 16
    _SPEED_PX_S = 30.0
    _PASSES = 2

    def __init__(self, text: str, parent: QWidget | None = None, *, on_change: bool = True) -> None:
        super().__init__(text, parent)
        self.setProperty("refrainScrolls", True)
        # A list of rows must not all start moving at once: those scroll on hover.
        self._scroll_on_change = on_change
        self._offset = 0.0
        self._pass = 0
        self._passes = self._PASSES
        self._at_end = False
        self._hold_until = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self._FRAME_MS)
        self._timer.timeout.connect(self._step)

    def setText(self, text: str) -> None:
        self._full = text
        QLabel.setText(self, text)
        self._elide()
        self.updateGeometry()
        if self._scroll_on_change:
            self.restart_scroll()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._scroll_on_change:
            self.restart_scroll()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        # Asked for by the mouse: one pass is enough.
        self.restart_scroll(passes=1)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def restart_scroll(self, passes: int | None = None) -> None:
        self._timer.stop()
        self._offset = 0.0
        self._pass = 0
        self._passes = self._PASSES if passes is None else passes
        self._at_end = False
        self._hold_until = time.monotonic() + self._PAUSE_MS / 1000
        if self._overflow() > 0:
            self._timer.start()
        self.update()

    def _overflow(self) -> float:
        return self.fontMetrics().horizontalAdvance(self._full) - self.contentsRect().width()

    def _step(self) -> None:
        now = time.monotonic()
        if now < self._hold_until:
            return
        over = self._overflow()
        if over <= 0 or not self.isVisible():
            self._timer.stop()
            self._offset = 0.0
            self.update()
            return
        self._offset += self._SPEED_PX_S * self._FRAME_MS / 1000
        if self._offset >= over:
            self._offset = over
            if not self._at_end:
                # Let the end stand still long enough to be read.
                self._at_end = True
                self._hold_until = now + self._END_MS / 1000
                self.update()
                return
            self._at_end = False
            self._pass += 1
            self._offset = 0.0
            if self._pass >= self._passes:
                self._timer.stop()  # from here on the line stays elided
            else:
                # Back at the start, and long enough to read it before it moves.
                self._hold_until = now + self._BETWEEN_MS / 1000
        self.update()

    def paintEvent(self, event) -> None:
        if not self._timer.isActive():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        rect = self.contentsRect().adjusted(-round(self._offset), 0, round(self._offset), 0)
        painter.drawText(rect, int(self.alignment()), self._full)
        painter.end()


class _Dot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self.color = QColor()

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(QRectF(1, 1, 8, 8))
        p.end()


class _ServiceRow:
    """Name, coloured dot, plain-English state and at most one action."""

    def __init__(self, grid: QGridLayout, row: int, name: str) -> None:
        self.name = QLabel(name)
        bold = QFont(self.name.font())
        bold.setBold(True)
        self.name.setFont(bold)
        self.dot = _Dot()
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.button = QPushButton()
        self.button.setVisible(False)
        self.action = ""
        grid.addWidget(self.name, row, 0, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.dot, row, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.text, row, 2)
        grid.addWidget(self.button, row, 3, Qt.AlignmentFlag.AlignVCenter)

    def show(self, text: str, tone: str, palette: QPalette, button: str, action: str) -> None:
        self.text.setText(text)
        self.tone = tone
        self.dot.set_color(_tone_color(tone, palette))
        _set_color(
            self.text,
            _muted(palette) if tone == "off" else palette.color(QPalette.ColorRole.WindowText),
        )
        self.action = action
        self.button.setText(button)
        # An option most people never use gets a quiet button, not a call to action.
        self.button.setFlat(tone == "off")
        self.button.setVisible(bool(button))


# How long the mouse rests on a song before its title scrolls; config.toml can
# change it, and 0 turns it off.
_hover_ms = 1500


def set_hover_delay(ms: int) -> None:
    global _hover_ms
    _hover_ms = max(0, ms)


class _RecentRow(QWidget):
    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._underline(True)
        if _hover_ms:
            self._hover.start(_hover_ms)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._underline(False)
        self._hover.stop()

    def _underline(self, on: bool) -> None:
        # Shows the row is a link, the way a browser does, without cluttering the list.
        font = QFont(self.title.font())
        font.setUnderline(on)
        self.title.setFont(font)

    def __init__(self, entry: HistoryEntry, when: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.link = song_link(entry)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 5, 0, 5)
        row.setSpacing(8)
        muted = _muted(self.palette())
        self.title = _Line(" · ".join(p for p in (entry.title, entry.artist) if p), on_change=False)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.title, 1)
        if entry.scrobbled:
            check = QLabel("✓")
            check.setToolTip(QCoreApplication.translate("StatusWindow", "Scrobbled to Last.fm"))
            keep_on_window(check)
            _set_color(check, muted)
            row.addWidget(check)
        self.when = QLabel(when)
        _set_color(self.when, muted)
        row.addWidget(self.when)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(QCoreApplication.translate("StatusWindow", "Open in Apple Music"))
        self._hover = QTimer(self)
        self._hover.setSingleShot(True)
        self._hover.setInterval(_hover_ms)
        self._hover.timeout.connect(lambda: self.title.restart_scroll(passes=1))

    def event(self, event) -> bool:
        # The list is rebuilt under the mouse whenever the history changes.
        if event.type() == QEvent.Type.ToolTip:
            show_on_window(self, event)
            return True
        return super().event(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            confirm_and_open(
                self.window(), self.link, self.entry.player if self.entry.source == "mpris" else ""
            )
            return
        super().mouseReleaseEvent(event)


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class StatusWindow(QDialog):
    """Answers "is everything working, and if not, what do I do?" at a glance."""

    settingsRequested = Signal(str)  # "" | "discord" | "lastfm"
    historyRequested = Signal()
    updateRequested = Signal()
    sharingToggled = Signal(bool)  # True = pause sharing
    visibilityChanged = Signal(bool)
    playPauseRequested = Signal()
    nextRequested = Signal()
    previousRequested = Signal()

    def __init__(self, locale: QLocale | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Status"))
        self.setModal(False)
        self.setMinimumWidth(440)
        self.resize(520, 520)
        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._locale = locale if locale is not None else QLocale()
        self._track = TrackInfo.empty()
        self._history = HistorySnapshot()
        self._status = StatusSnapshot()
        self._welcome = False
        self._crash_report: Path | None = None
        self._sharing_paused = False
        self._progress_estimated = False
        self._update_version = ""
        self._cover_url = ""
        self._cover_source = ""
        # Measured from the window itself once it has rows; guesses until then.
        self._row_px = 0

        self._recount = QTimer(self)
        self._recount.setSingleShot(True)
        self._recount.setInterval(0)
        self._recount.timeout.connect(self._recount_rows)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(10)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        self.banner.setObjectName("banner")
        self.banner.setVisible(False)
        self.banner.linkActivated.connect(self._on_banner_link)
        layout.addWidget(self.banner)

        song = QHBoxLayout()
        song.setSpacing(12)
        self.cover = QLabel()
        self.cover.setFixedSize(_COVER_PX, _COVER_PX)
        song.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = _Line("")
        title_font = QFont(self.title.font())
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.2)
        self.title.setFont(title_font)
        self.artist = _Line("")
        self.source = _Line("")
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        for label in (self.title, self.artist, self.source):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text.addWidget(label)
        self.elapsed = QLabel()
        self.elapsed.setVisible(False)
        text.addWidget(self.elapsed)
        text.addWidget(self.hint)
        text.addStretch(1)
        song.addLayout(text, 1)
        layout.addLayout(song)

        controls = QHBoxLayout()
        controls.setSpacing(0)
        self.previous_btn = QToolButton()
        self.previous_btn.setIcon(icons.themed_icon("media-skip-backward"))
        self.previous_btn.setToolTip(self.tr("Previous song"))
        self.previous_btn.clicked.connect(self.previousRequested.emit)
        self.play_btn = QToolButton()
        self.play_btn.setToolTip(self.tr("Play or pause"))
        self.play_btn.clicked.connect(self.playPauseRequested.emit)
        self.next_btn = QToolButton()
        self.next_btn.setIcon(icons.themed_icon("media-skip-forward"))
        self.next_btn.setToolTip(self.tr("Next song"))
        self.next_btn.clicked.connect(self.nextRequested.emit)
        self.controls = (self.previous_btn, self.play_btn, self.next_btn)
        for button in self.controls:
            button.setAutoRaise(True)
            button.setIconSize(QSize(20, 20))
            button.setFixedSize(30, 28)
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)

        layout.addWidget(_separator())
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(2, 1)
        self.discord = _ServiceRow(grid, 0, "Discord")
        self.lastfm = _ServiceRow(grid, 1, "Last.fm")
        self.discord.button.clicked.connect(lambda: self._act(self.discord.action))
        self.lastfm.button.clicked.connect(lambda: self._act(self.lastfm.action))
        layout.addLayout(grid)

        self.update_row = QWidget()
        update_line = QHBoxLayout(self.update_row)
        update_line.setContentsMargins(0, 0, 0, 0)
        self.update_text = QLabel()
        self.update_text.setWordWrap(True)
        update_line.addWidget(self.update_text, 1)
        self.update_btn = QPushButton(self.tr("Update…"))
        self.update_btn.clicked.connect(self.updateRequested.emit)
        update_line.addWidget(self.update_btn)
        self.update_row.setVisible(False)
        layout.addWidget(self.update_row)

        self.recent_box = QWidget()
        recent = QVBoxLayout(self.recent_box)
        recent.setContentsMargins(0, 0, 0, 0)
        recent.setSpacing(2)
        self._recent_line = _separator()
        recent.addWidget(self._recent_line)
        head = QHBoxLayout()
        self._recent_head = head
        heading = QLabel(self.tr("Recently played"))
        heading.setFont(self.discord.name.font())
        head.addWidget(heading)
        head.addStretch(1)
        self.show_all = QPushButton(self.tr("Show all"))
        self.show_all.setFlat(True)
        self.show_all.clicked.connect(self.historyRequested.emit)
        head.addWidget(self.show_all)
        recent.addLayout(head)
        self.recent_rows = QVBoxLayout()
        self.recent_rows.setSpacing(0)
        recent.addLayout(self.recent_rows)
        self.recent_empty = QLabel(self.tr("Songs you play show up here."))
        recent.addWidget(self.recent_empty)
        self.recent_off = QPushButton(self.tr("Turn on Recently played"))
        self.recent_off.setFlat(True)
        self.recent_off.clicked.connect(lambda: self.settingsRequested.emit("history"))
        recent.addWidget(self.recent_off, 0, Qt.AlignmentFlag.AlignLeft)
        # Room the songs don't fill stays below them instead of spreading them out.
        recent.addStretch(1)
        # The list must never hold the window open: it takes the space that is
        # left and gives it back when the window shrinks.
        self.recent_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        self.recent_box.setMinimumHeight(0)
        # The song above the list grows once a track with cover and controls
        # shows up, without the window changing size.
        self.recent_box.installEventFilter(self)
        layout.addWidget(self.recent_box, 1)

        layout.addWidget(_separator())
        buttons = QHBoxLayout()
        self.sharing_btn = QPushButton()
        self.sharing_btn.clicked.connect(lambda: self.sharingToggled.emit(not self._sharing_paused))
        buttons.addWidget(self.sharing_btn)
        buttons.addStretch(1)
        self.settings_btn = QPushButton(self.tr("Settings…"))
        self.settings_btn.setIcon(icons.themed_icon("configure"))
        self.settings_btn.clicked.connect(lambda: self.settingsRequested.emit(""))
        buttons.addWidget(self.settings_btn)
        layout.addLayout(buttons)
        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.addStretch(1)
        self.version = QLabel(f"v{__version__}")
        version_row.addWidget(self.version)
        self.github_btn = QToolButton()
        self.github_btn.setIconSize(QSize(16, 16))
        self.github_btn.setAutoRaise(True)
        self.github_btn.setToolTip(self.tr("View Refrain on GitHub"))
        self.github_btn.clicked.connect(lambda: confirm_and_open(self, GITHUB_URL))
        self._apply_github_icon()
        version_row.addWidget(self.github_btn)
        layout.addLayout(version_row)

        # Enter must not fire whichever action happens to be first.
        for push in self.findChildren(QPushButton):
            push.setAutoDefault(False)
        # Nor should the first button open with a focus frame, as if chosen:
        # the window holds the focus until Tab moves it.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        self._restore_geometry()
        self._refresh()
        apply_interactive_cursors(self)

    # ------------------------------------------------------------- public

    def set_track(self, track: TrackInfo) -> None:
        self._track = track
        self._refresh_song()
        self._refresh_services()

    def set_progress(self, position_ms: int, duration_ms: int) -> None:
        """The player's own clock, when Refrain trusts it — otherwise nothing."""
        if duration_ms <= 0 or position_ms < 0:
            self.elapsed.setVisible(False)
            return
        mark = "~" if self._progress_estimated else ""
        self.elapsed.setText(f"{mark}{_clock(position_ms)} / {_clock(duration_ms)}")
        self.elapsed.setToolTip(
            self.tr("Estimated from where the song was before Refrain restarted")
            if self._progress_estimated
            else ""
        )
        _set_color(self.elapsed, _muted(self.palette()))
        self.elapsed.setVisible(self._track.has_track)

    def set_progress_estimated(self, estimated: bool) -> None:
        """The next times given to `set_progress` are estimates; they get a ~."""
        self._progress_estimated = estimated

    def set_playback(self, status: PlaybackStatus) -> None:
        if status != self._track.status:
            self._track = replace(self._track, status=status)
            self._refresh_song()

    def set_history(self, snapshot: HistorySnapshot) -> None:
        self._history = snapshot
        self._refresh_song()
        self._refresh_recent()

    def set_status(self, status: StatusSnapshot) -> None:
        self._status = status
        self._refresh_services()

    def set_sharing_paused(self, paused: bool) -> None:
        self._sharing_paused = paused
        self._refresh_sharing()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def _apply_github_icon(self) -> None:
        from refrain.ui.settings_window import themed_svg_icon

        svg = (assets_dir() / "icons" / "github-mark.svg").read_bytes()
        color = self.palette().color(QPalette.ColorRole.Text)
        self.github_btn.setIcon(themed_svg_icon(svg, color, 16, max(2.0, self.devicePixelRatioF())))

    def set_update_available(self, version: str) -> None:
        self._update_version = version
        self._refresh_update()

    def set_crash_report(self, path: Path | None) -> None:
        """A notification is easy to miss, so the window says it too."""
        self._crash_report = path
        self._refresh_banner()

    def _on_banner_link(self, href: str) -> None:
        if href == "crash" and self._crash_report is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._crash_report)))
            self._crash_report = None
            self._refresh_banner()

    def set_welcome(self, on: bool) -> None:
        """After the first-run wizard: say it's done, not just list states."""
        self._welcome = on
        self._refresh_banner()

    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ refresh

    def _refresh(self) -> None:
        self._refresh_song()
        self._refresh_services()
        self._refresh_recent()
        self._refresh_sharing()
        self._refresh_update()

    def _refresh_song(self) -> None:
        muted = _muted(self.palette())
        for label in (self.artist, self.source, self.hint):
            _set_color(label, muted)
        track = self._track
        dpr = self.devicePixelRatioF()
        if not track.has_track:
            self.title.setText(self.tr("Nothing playing"))
            self.artist.setText("")
            self.source.setText("")
            self.artist.setVisible(False)
            self.source.setVisible(False)
            if track.player:
                # A browser is playing but MPRIS gave no URL to recognise
                # Apple Music by — Chromium outside KDE, typically.
                self.hint.setText(
                    self.tr(
                        "{player} is playing, but doesn't say which page. Install Plasma "
                        "Browser Integration (package plasma-browser-integration plus the "
                        'browser extension "Plasma Integration") or use Firefox.'
                    ).format(player=track.player)
                )
            else:
                self.hint.setText(
                    self.tr(
                        "Start a song in Apple Music in your browser, or on your phone over Bluetooth."
                    )
                )
            self.hint.setVisible(True)
            self.cover.setPixmap(_placeholder(self.palette(), _COVER_PX, dpr))
            self._cover_url = ""
            self.elapsed.setVisible(False)
            self._refresh_controls()
            return
        self.hint.setVisible(False)
        self.title.setText(track.title)
        self.artist.setText(" — ".join(p for p in (track.artist, track.album) if p))
        self.artist.setVisible(bool(track.artist or track.album))
        if track.source == "bluetooth":
            source = self.tr("Bluetooth")
        else:
            source = self.tr("Apple Music Web")
        if track.player:
            source = f"{source} · {track.player}"
        if track.status == PlaybackStatus.PAUSED:
            source = self.tr("Paused · {source}").format(source=source)
        self.source.setText(source)
        self.source.setVisible(True)
        self._refresh_controls()
        self._show_cover(dpr)

    def set_cover(self, url: str) -> None:
        self._cover_source = url
        self._show_cover(self.devicePixelRatioF())

    def _refresh_controls(self) -> None:
        playing = self._track.status == PlaybackStatus.PLAYING
        self.play_btn.setIcon(
            icons.themed_icon("media-playback-pause" if playing else "media-playback-start")
        )
        for button in self.controls:
            button.setEnabled(self._track.has_track)

    def _show_cover(self, dpr: float) -> None:
        url = self._cover_source
        entries = self._history.entries
        if (
            not url
            and self._history.now_playing
            and entries
            and entries[0].title == self._track.title
        ):
            url = entries[0].cover_url
        if url and url == self._cover_url:
            return
        pixmap = None
        if url:
            path = image_path_for_url(url)
            with contextlib.suppress(OSError):
                if path.exists() and path.stat().st_size > 0:
                    image = _read_scaled(str(path), round(_COVER_PX * dpr))
                    if not image.isNull():
                        pixmap = _rounded(image, _COVER_PX, _COVER_RADIUS, dpr)
        if pixmap is None:
            pixmap = _placeholder(self.palette(), _COVER_PX, dpr)
            url = ""
        self._cover_url = url
        self.cover.setPixmap(pixmap)

    def _refresh_services(self) -> None:
        palette = self.palette()
        s = self._status
        d = s.discord
        button, action = "", ""
        if d is DiscordStatus.NOT_SET_UP:
            text = self.tr("Not set up yet")
            button, action = self.tr("Set up…"), "discord"
        elif d is DiscordStatus.NO_CLIENT:
            text = self.tr("The Discord app isn't running")
        elif d is DiscordStatus.REJECTED:
            text = self.tr("Application ID rejected")
            button, action = self.tr("Fix…"), "discord"
        elif d is DiscordStatus.NOT_LOGGED_IN:
            text = self.tr(
                "Discord is open but not logged in. Log in to Discord to show your status."
            )
        elif d is DiscordStatus.ERROR:
            text = self.tr("Discord isn't answering right now")
        elif d is DiscordStatus.READY:
            text = self.tr("Ready — waiting for music")
        elif d is DiscordStatus.SHOWING:
            text = self.tr("Visible on your profile")
        elif d is DiscordStatus.SHOWING_MINIMAL:
            text = self.tr("Showing “Listening to music”")
        elif d is DiscordStatus.PAUSED:
            text = self.tr("Hidden while the music is paused")
        elif d is DiscordStatus.PRIVACY_OFF:
            text = self.tr("Hidden — sharing is off")
        else:
            text = self.tr("Checking…")
        self.discord.show(text, _DISCORD_TONE[d], palette, button, action)
        self.discord.text.setToolTip(s.discord_detail)

        f = s.lastfm
        button, action = "", ""
        if f is LastfmStatus.OFF:
            text = self.tr("Off")
            button, action = self.tr("Set up…"), "lastfm"
        elif f is LastfmStatus.CONNECTED_OFF:
            text = self.tr("Connected, but scrobbling is off")
            button, action = self.tr("Open Last.fm settings…"), "lastfm"
        elif f is LastfmStatus.NOT_CONNECTED:
            text = self.tr("Not connected yet")
            button, action = self.tr("Set up…"), "lastfm"
        elif f is LastfmStatus.WAITING:
            count = int(s.lastfm_detail) if s.lastfm_detail.isdigit() else 0
            text = self.tr("%n scrobble(s) waiting to be sent", "", count)
        elif f is LastfmStatus.EXPIRED:
            text = self.tr("Sign-in expired")
            button, action = self.tr("Reconnect…"), "lastfm"
        elif f is LastfmStatus.PAUSED:
            text = self.tr("Paused — sharing is off")
        elif s.lastfm_detail:
            text = self.tr("Scrobbling as {user}").format(user=s.lastfm_detail)
        else:
            text = self.tr("Scrobbling")
        self.lastfm.show(text, _LASTFM_TONE[f], palette, button, action)
        self._refresh_banner()

    def _refresh_banner(self) -> None:
        if self._crash_report is not None:
            self.banner.setText(
                "<b>{}</b><br>{}".format(
                    self.tr("Refrain closed unexpectedly last time."),
                    self.tr('The report is in <a href="crash">{path}</a>.').format(
                        path=self._crash_report.name
                    ),
                )
            )
            warn = _tone_color("warn", self.palette())
            self.banner.setStyleSheet(
                f"QLabel#banner {{ border: 1px solid {warn.name()}; border-radius: 6px;"
                " padding: 8px 10px; }"
            )
            self.banner.setVisible(True)
            return
        on = self._welcome and self._status.discord is not DiscordStatus.NOT_SET_UP
        if on:
            self.banner.setText(
                "<b>{}</b><br>{}".format(
                    self.tr("You're all set."),
                    self.tr(
                        "Play a song in Apple Music — it shows up in Discord within a few seconds."
                    ),
                )
            )
            ok = _tone_color("ok", self.palette())
            self.banner.setStyleSheet(
                f"QLabel#banner {{ border: 1px solid {ok.name()}; border-radius: 6px;"
                " padding: 8px 10px; }"
            )
        self.banner.setVisible(on)

    def _fits(self) -> int:
        """How many songs fit in the space the list actually has right now."""
        if not self._row_px:
            return _RECENT_GUESS
        box_layout = self.recent_box.layout()
        spacing = box_layout.spacing() if box_layout is not None else 0
        head = self._recent_line.sizeHint().height() + self._recent_head.sizeHint().height()
        room = self.recent_box.height() - head - 2 * spacing
        if room <= 0:
            return 0
        return int(room // self._row_px)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # After the layout has settled: only then is the list's own height real.
        self._recount.start()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.recent_box and event.type() == QEvent.Type.Resize:
            self._recount.start()
        return super().eventFilter(watched, event)

    def _recount_rows(self) -> None:
        if self._fits() != self.recent_rows.count():
            self._refresh_recent()

    def _refresh_recent(self) -> None:
        snap = self._history
        # An off history leaves the window bare; say so instead of hiding the box.
        self.recent_off.setVisible(not snap.enabled)
        while self.recent_rows.count():
            item = self.recent_rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        available = list(snap.entries[1:] if snap.now_playing else snap.entries)
        entries = available[: self._fits()]
        for entry in entries:
            self.recent_rows.addWidget(
                _RecentRow(entry, clock.when(self._locale, entry.started_at))
            )
        first = self.recent_rows.itemAt(0)
        first_row = first.widget() if first is not None else None
        if first_row is not None:
            self._row_px = max(1, first_row.sizeHint().height())
        self.recent_empty.setText(
            self.tr("Songs you play show up here.")
            if snap.enabled
            else self.tr("Recently played is off, so Refrain keeps no list of your songs.")
        )
        self.recent_empty.setWordWrap(True)
        self.recent_empty.setVisible(not available)
        _set_color(self.recent_empty, _muted(self.palette()))
        # "Show all" matters most when the window cannot show them all.
        self.show_all.setVisible(bool(available))

    def _refresh_sharing(self) -> None:
        if self._sharing_paused:
            self.sharing_btn.setText(self.tr("Resume sharing"))
            self.sharing_btn.setIcon(icons.themed_icon("media-playback-start"))
            self.sharing_btn.setToolTip(
                self.tr("Show your song in Discord and scrobble to Last.fm again.")
            )
        else:
            self.sharing_btn.setText(self.tr("Pause sharing"))
            self.sharing_btn.setIcon(icons.themed_icon("media-playback-pause"))
            self.sharing_btn.setToolTip(
                self.tr("Hide your Discord status and stop scrobbling until you resume.")
            )

    def _refresh_update(self) -> None:
        version = self._update_version
        self.update_row.setVisible(bool(version))
        if version:
            self.update_text.setText(
                self.tr("Refrain {version} is available.").format(version=version)
            )

    def _act(self, action: str) -> None:
        self.settingsRequested.emit(action)

    # ----------------------------------------------------------- geometry

    def _restore_geometry(self) -> None:
        try:
            data = json.loads(geometry_path().read_text(encoding="utf-8"))
            self.restoreGeometry(QByteArray.fromBase64(data["geometry"].encode("ascii")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    def _save_geometry(self) -> None:
        data = {"geometry": bytes(self.saveGeometry().toBase64().data()).decode("ascii")}
        try:
            geometry_path().parent.mkdir(parents=True, exist_ok=True)
            geometry_path().write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            log.debug("Could not remember the Status window's size: %s", e)

    def hideEvent(self, event) -> None:
        self._save_geometry()
        if self._welcome:
            self.set_welcome(False)
        super().hideEvent(event)
        self.visibilityChanged.emit(False)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.PaletteChange:
            self._cover_url = ""
            self._refresh()
