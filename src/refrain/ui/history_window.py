"""Recently played window: the songs the history kept, newest first, from immutable snapshots.

Row widgets in a scroll area rather than a QListView, whose item widgets lay out unreliably."""

from __future__ import annotations

import html
import logging
import math
import unicodedata
from collections.abc import Callable
from urllib.parse import quote

from PySide6.QtCore import (
    QCoreApplication,
    QDate,
    QDateTime,
    QEvent,
    QLocale,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from refrain.cover_art import image_path_for_url
from refrain.history import HistoryEntry, HistorySnapshot
from refrain.paths import assets_dir
from refrain.ui import clock, icons
from refrain.ui.cursors import apply_interactive_cursors
from refrain.ui.external_link import confirm_and_open

log = logging.getLogger(__name__)

# Below this many songs everything fits on a screen or two, and a search
# field would only be clutter.
_SEARCH_FROM = 10
# How long typing pauses before the list is filtered — rebuilding up to a
# hundred rows on every keystroke makes the field lag.
_FILTER_DELAY_MS = 120

_COVER_PX = 44
_COVER_RADIUS = 6
_ROW_MARGINS = (10, 7, 12, 7)
# The right-hand column (time + source) grows to fit its widest row, but
# never past this — a long Bluetooth device name elides instead.
_META_MAX_WIDTH = 210

_CONFIRM_MIN_WIDTH = 420

# Freedesktop names, first hit wins; a theme without any of them just
# shows the source text without an icon.
_SOURCE_ICONS = {
    "mpris": ("internet-web-browser", "applications-internet"),
    "bluetooth": ("preferences-system-bluetooth", "bluetooth", "network-bluetooth"),
}


# Row-level strings go through QCoreApplication.translate with the
# window's context spelled out literally at every call: pylupdate only
# extracts string literals, so a helper taking the text as a variable
# would leave them all untranslatable.


def _duration_text(ms: int) -> str:
    s = max(0, ms) // 1000
    h, m, sec = s // 3600, (s // 60) % 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _source_text(entry: HistoryEntry) -> str:
    if entry.source == "mpris":
        base = QCoreApplication.translate("HistoryWindow", "Apple Music Web")
    elif entry.source == "bluetooth":
        base = QCoreApplication.translate("HistoryWindow", "Bluetooth")
    else:
        base = entry.source or "—"
    return f"{base} · {entry.player}" if entry.player else base


def _source_icon(source: str) -> QIcon:
    names = _SOURCE_ICONS.get(source)
    if not names:
        return QIcon()
    return icons.themed_icon(names[0], *names[1:])


def _subtitle(entry: HistoryEntry) -> str:
    parts = [p for p in (entry.artist, entry.album) if p]
    return (
        " — ".join(parts)
        if parts
        else QCoreApplication.translate("HistoryWindow", "Unknown artist")
    )


def _fold(text: str) -> str:
    """Case- and accent-blind form for searching: "Ilse Moréau" → "ilse moreau"."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def highlight_ranges(text: str, words: list[str]) -> list[tuple[int, int]]:
    """Where the search words occur in ``text``, as merged (start, end)
    offsets into the text as written.

    Matched like the search itself — case- and accent-blind — and mapped
    back character by character, since folding can change the length
    ("ß" becomes "ss", "é" loses its accent mark).
    """
    folded: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for c in _fold(ch):
            folded.append(c)
            origin.append(i)
    haystack = "".join(folded)
    spans: list[tuple[int, int]] = []
    for word in words:
        if not word:
            continue
        start = haystack.find(word)
        while start != -1:
            spans.append((origin[start], origin[start + len(word) - 1] + 1))
            start = haystack.find(word, start + 1)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def matches(entry: HistoryEntry, query: str) -> bool:
    """Does every word of ``query`` appear in the title, artist or album?"""
    haystack = _fold(f"{entry.title} {entry.artist} {entry.album}")
    return all(word in haystack for word in _fold(query).split())


def song_link(entry: HistoryEntry) -> str:
    """The song's Apple Music page — or, without one, a search for it.

    Songs the catalog lookup found, or whose browser tab was on the song
    itself, carry their own page. Bluetooth plays and songs the lookup
    never found get a search for artist and title, so every row still
    leads somewhere.
    """
    if entry.url.startswith("https://"):
        return entry.url
    term = " ".join(p for p in (entry.artist, entry.title) if p)
    return "https://music.apple.com/search?term=" + quote(term)


def _muted(palette: QPalette) -> QColor:
    """Secondary text: text blended toward the background.

    Not ``palette(mid)`` / ``PlaceholderText`` — both come out close to
    invisible on Breeze Dark (see the settings window's ``_hint``).
    """
    # Active group: a window built before it is focused would bake in the
    # inactive colours, which Fusion greys out.
    active = QPalette.ColorGroup.Active
    fg = palette.color(active, QPalette.ColorRole.WindowText)
    bg = palette.color(active, QPalette.ColorRole.Window)
    k = 0.62
    return QColor(
        round(fg.red() * k + bg.red() * (1 - k)),
        round(fg.green() * k + bg.green() * (1 - k)),
        round(fg.blue() * k + bg.blue() * (1 - k)),
    )


def _with_alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(alpha)
    return c


def _set_color(widget: QWidget, color: QColor) -> None:
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.WindowText, color)
    widget.setPalette(pal)


class _ElidedLabel(QLabel):
    """One line of text that ends in "…" instead of widening the row."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = text
        self._highlights: list[tuple[int, int]] = []
        self.setProperty("refrainElides", True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def set_highlights(self, words: list[str]) -> None:
        """Put a background behind these (folded) search words."""
        self._highlights = highlight_ranges(self._full, words) if words else []
        self.update()

    def paintEvent(self, event) -> None:
        # Backgrounds first, then QLabel draws the text over them — plain
        # text stays plain text, and eliding keeps working. Only the part
        # still on screen gets one: an elided label shows a prefix of the
        # text followed by "…".
        if self._highlights:
            shown = self.text()
            visible = len(shown) if shown == self._full else max(0, len(shown) - 1)
            fm = QFontMetricsF(self.font())
            rect = self.contentsRect()
            top = rect.top() + (rect.height() - fm.height()) / 2
            # The active group's accent, whether or not the window has focus:
            # Breeze dims the inactive one almost to the background, and a
            # match should stand out either way. The text on top
            # keeps its own colour, so it stays readable on a light theme.
            accent = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
            color = _with_alpha(accent, 0.55)
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            for start, end in self._highlights:
                if start >= visible:
                    break
                x0 = rect.left() + fm.horizontalAdvance(self._full[:start])
                x1 = rect.left() + fm.horizontalAdvance(self._full[: min(end, visible)])
                path = QPainterPath()
                path.addRoundedRect(QRectF(x0 - 1.5, top, x1 - x0 + 3, fm.height()), 3, 3)
                p.fillPath(path, color)
            p.end()
        super().paintEvent(event)

    def sizeHint(self) -> QSize:
        # Measured in fractional pixels and rounded up, with a hair to
        # spare: elidedText compares in sub-pixels, so a width of exactly
        # the integer advance cuts short titles like "Glass Tid…" at 2x.
        width = math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self._full)) + 2
        return QSize(width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == event.Type.FontChange:
            self._elide()

    def _elide(self) -> None:
        width = max(0, self.contentsRect().width())
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width)
        if elided != self.text():
            super().setText(elided)


def _read_scaled(path: str, px: int) -> QImage:
    """Decode an image straight at the size that covers ``px`` × ``px``.

    Covers are stored at 600 × 600; decoding them in full for a 44 px
    thumbnail costs time and leaves large freed blocks on the heap.
    """
    reader = QImageReader(path)
    size = reader.size()
    if size.isValid() and size.width() > px and size.height() > px:
        scale = max(px / size.width(), px / size.height())
        reader.setScaledSize(
            QSize(math.ceil(size.width() * scale), math.ceil(size.height() * scale))
        )
    return reader.read()


def _round_cover(pix: QPixmap, dpr: float) -> QPixmap:
    px = round(_COVER_PX * dpr)
    scaled = pix.scaled(
        px,
        px,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), _COVER_RADIUS * dpr, _COVER_RADIUS * dpr)
    p.setClipPath(path)
    p.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


def _placeholder_cover(palette: QPalette, dpr: float) -> QPixmap:
    px = round(_COVER_PX * dpr)
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), _COVER_RADIUS * dpr, _COVER_RADIUS * dpr)
    p.fillPath(path, _with_alpha(palette.color(QPalette.ColorRole.WindowText), 0.09))
    icon = icons.themed_icon("audio-x-generic")
    if icon.isNull():
        icon = QIcon(str(assets_dir() / "icons" / "refrain.svg"))
    glyph = round(px * 0.5)
    p.setOpacity(0.55)
    icon.paint(p, (px - glyph) // 2, (px - glyph) // 2, glyph, glyph)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class _SongRow(QWidget):
    """One song: cover, title + artist/album, time + source."""

    def __init__(
        self,
        entry: HistoryEntry,
        started: QDateTime,
        now_playing: bool,
        playing: bool,
        locale: QLocale,
        cover: QPixmap,
        meta_width: int,
        when_text: Callable[[QDateTime], str],
        on_remove: Callable[[], None],
        highlight: list[str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        # Song titles and device names elide; the layout check leaves rows alone.
        self.setProperty("refrainElides", True)
        self._entry = entry
        self._when_text = when_text
        self._on_remove = on_remove
        self._now_playing = now_playing
        self._hover = False
        muted = _muted(self.palette())

        row = QHBoxLayout(self)
        row.setContentsMargins(*_ROW_MARGINS)
        row.setSpacing(12)

        self._cover_label = QLabel()
        self._cover_label.setFixedSize(_COVER_PX, _COVER_PX)
        self._cover_label.setPixmap(cover)
        self.has_cover = False
        row.addWidget(self._cover_label)

        # ---- title + duration / artist — album --------------------------
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        # The length follows the title directly. At the far end of the
        # column it would float on its own halfway across the row whenever
        # the title is short.
        title_line = QHBoxLayout()
        title_line.setSpacing(8)
        title = _ElidedLabel(entry.title)
        bold = QFont(title.font())
        bold.setBold(True)
        title.setFont(bold)
        # Preferred: as wide as the title needs, shrinking (and eliding)
        # only when the row is too narrow; the stretch takes the rest.
        title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        title_line.addWidget(title)
        if entry.duration_ms > 0:
            length = QLabel(_duration_text(entry.duration_ms))
            _set_color(length, muted)
            title_line.addWidget(length)
        title_line.addStretch(1)
        text_col.addLayout(title_line)
        subtitle = _ElidedLabel(_subtitle(entry))
        _set_color(subtitle, muted)
        text_col.addWidget(subtitle)
        self._texts = (title, subtitle)
        self._highlight: list[str] = []
        if highlight:
            self.set_highlights(highlight)
        row.addLayout(text_col, 1)

        # ---- time (or "Now playing") / source ---------------------------
        meta = QWidget()
        meta.setFixedWidth(meta_width)
        meta_col = QVBoxLayout(meta)
        meta_col.setContentsMargins(0, 0, 0, 0)
        meta_col.setSpacing(2)

        when_line = QHBoxLayout()
        when_line.setSpacing(6)
        when_line.addStretch(1)
        if entry.scrobbled:
            check = QLabel("✓")
            check.setToolTip(QCoreApplication.translate("HistoryWindow", "Scrobbled to Last.fm"))
            _set_color(check, muted)
            when_line.addWidget(check)
        if now_playing:
            when = QLabel(
                QCoreApplication.translate("HistoryWindow", "Now playing")
                if playing
                else QCoreApplication.translate("HistoryWindow", "Paused")
            )
            accent = QFont(when.font())
            accent.setBold(True)
            when.setFont(accent)
            _set_color(when, self.palette().color(QPalette.ColorRole.Highlight))
        else:
            when = QLabel(clock.when(locale, entry.started_at))
        when_line.addWidget(when)
        meta_col.addLayout(when_line)

        # Right-aligned as a unit, so the icon sits against its text; only
        # a name too long for the column elides.
        source_line = QHBoxLayout()
        source_line.setSpacing(5)
        source_line.addStretch(1)
        text_room = meta_width
        source = _ElidedLabel(_source_text(entry))
        _set_color(source, muted)
        icon = _source_icon(entry.source)
        if not icon.isNull():
            source_line.addWidget(self._icon_beside(icon, source))
            text_room -= 14 + source_line.spacing()
        source.setFixedWidth(max(0, min(text_room, source.sizeHint().width())))
        source.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Fixed, not the Ignored an elided label defaults to: with Ignored
        # the layout counts its width as zero, hands everything to the
        # stretch, and the label ends up past the column's right edge.
        source.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        source_line.addWidget(source)
        meta_col.addLayout(source_line)
        row.addWidget(meta)

        # The whole row is the link.
        self._link = song_link(entry)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._started = started
        self._locale = locale
        self.setToolTip(self._tooltip(started, locale))

    def set_highlights(self, words: list[str]) -> None:
        if words == self._highlight:
            return
        self._highlight = list(words)
        for label in self._texts:
            label.set_highlights(words)

    def set_cover(self, cover: QPixmap) -> None:
        self._cover_label.setPixmap(cover)
        self.has_cover = True

    def event(self, event) -> bool:
        # Rebuilt as the tooltip opens, so "5 minutes ago" is true now and
        # not as of whenever the list was last rebuilt.
        if event.type() == QEvent.Type.ToolTip:
            self.setToolTip(self._tooltip(self._started, self._locale))
        return super().event(event)

    @staticmethod
    def _icon_beside(icon: QIcon, text: QLabel) -> QLabel:
        """A 14 px icon centred on the text's letters, not on its line box.

        Centred on the line box it sits a touch high: the box keeps room
        for descenders below the baseline that a name like "Apple Music
        Web" hardly uses, so the letters' middle is lower than the box's.
        """
        fm = text.fontMetrics()
        height = text.sizeHint().height()
        line_top = (height - fm.height()) / 2
        letters_middle = line_top + fm.ascent() - fm.capHeight() / 2
        label = QLabel()
        label.setPixmap(icon.pixmap(14, 14))
        label.setFixedSize(14, height)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        label.setContentsMargins(0, max(0, round(letters_middle - 7)), 0, 0)
        return label

    def _tooltip(self, started: QDateTime, locale: QLocale) -> str:
        e = self._entry
        lines = [f"<b>{html.escape(e.title)}</b>"]
        byline = " · ".join(v for v in (e.artist, e.album) if v)
        if byline:
            lines.append(html.escape(byline))
        lines.append(
            html.escape(
                QCoreApplication.translate("HistoryWindow", "Started: {when}").format(
                    when=self._when_text(started)
                )
            )
        )
        lines.append(
            html.escape(
                QCoreApplication.translate("HistoryWindow", "Source: {source}").format(
                    source=_source_text(e)
                )
            )
        )
        if e.duration_ms > 0:
            lines.append(
                html.escape(
                    QCoreApplication.translate("HistoryWindow", "Length: {length}").format(
                        length=_duration_text(e.duration_ms)
                    )
                )
            )
        if e.scrobbled:
            lines.append(
                html.escape(QCoreApplication.translate("HistoryWindow", "Scrobbled to Last.fm"))
            )
        lines.append(
            "<i>"
            + html.escape(
                QCoreApplication.translate("HistoryWindow", "Click to open in Apple Music.")
            )
            + "</i>"
        )
        return "<qt>" + "<br>".join(lines) + "</qt>"

    # ------------------------------------------------------------ painting

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        if not (self._hover or self._now_playing):
            return
        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.fillPath(path, _with_alpha(highlight, 0.16 if self._hover else 0.09))
        p.end()

    # ------------------------------------------------------------- actions

    def mousePressEvent(self, event) -> None:
        # Accepted, so the release comes back here rather than to the
        # scroll area underneath.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self._open()
            return
        super().mouseReleaseEvent(event)

    def _open(self) -> None:
        # Only a browser play names a browser; a Bluetooth device name
        # would never match one anyway, but there's no reason to look.
        confirm_and_open(
            self.window(), self._link, self._entry.player if self._entry.source == "mpris" else ""
        )

    def contextMenuEvent(self, event) -> None:
        e = self._entry
        menu = QMenu(self)
        open_ = menu.addAction(
            icons.themed_icon("internet-web-browser", "applications-internet"),
            QCoreApplication.translate("HistoryWindow", "Open in Apple Music"),
        )
        copy = menu.addAction(
            icons.themed_icon("edit-copy"),
            QCoreApplication.translate("HistoryWindow", "Copy artist and title"),
        )
        menu.addSeparator()
        # No "are you sure?": it's one song, and not a file on disk.
        remove = menu.addAction(
            icons.themed_icon("edit-delete"),
            QCoreApplication.translate("HistoryWindow", "Remove from history"),
        )
        chosen = menu.exec(event.globalPos())
        if chosen is copy:
            QGuiApplication.clipboard().setText(f"{e.artist} — {e.title}" if e.artist else e.title)
        elif chosen is open_:
            self._open()
        elif chosen is remove:
            self._on_remove()


class HistoryWindow(QDialog):
    """Non-modal, built once by app.py and shown from the tray or Settings."""

    clearRequested = Signal()
    removeRequested = Signal(object)  # HistoryEntry
    sizeRemembered = Signal(int, int)  # width, height — emitted on close

    def __init__(
        self,
        locale: QLocale | None = None,
        size: tuple[int, int] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Recently played"))
        self.setMinimumSize(440, 380)
        self.resize(600, 660)
        # The size it was last closed at (0 × 0 means never). Only the
        # size: on Wayland the compositor decides where windows go.
        if size and size[0] > 0 and size[1] > 0:
            self.resize(max(440, size[0]), max(380, size[1]))
        # Same as the live log: no windowFlags override, QDialog's
        # defaults are what every compositor handles.
        self.setModal(False)
        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        # Dates and times follow the language picked in Settings, not
        # the desktop's — the same locale the translators use.
        self._locale = locale if locale is not None else QLocale()
        self._snapshot = HistorySnapshot()
        # (cover URL, device pixel ratio) → rounded thumbnail; "" is the placeholder.
        self._covers: dict[tuple[str, float], QPixmap] = {}
        # What the list shows, in order, with the key each widget was built
        # for: a rebuild reuses every widget whose key it asks for again.
        self._shown_widgets: list[QWidget] = []
        self._shown_keys: list[tuple] = []

        # ---- top bar ------------------------------------------------------
        self.count_label = QLabel()
        self.clear_btn = QPushButton(self.tr("Clear history…"))
        self.clear_btn.setIcon(icons.themed_icon("edit-clear-history"))
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        top = QHBoxLayout()
        top.addWidget(self.count_label)
        top.addStretch(1)
        top.addWidget(self.clear_btn)

        # ---- search + source filter ---------------------------------------
        # Each half shows only once it has something to do: the search from
        # _SEARCH_FROM songs, the source picker once there's more than one
        # source to pick from. Both reset when the window closes.
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Search title, artist or album…"))
        self.search.setClearButtonEnabled(True)
        self.source_filter = QComboBox()
        # Narrow on purpose — the search gets the room. A name longer than
        # the box elides; the open list is sized to show every name in
        # full (_update_filter_bar).
        self.source_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source_filter.setMinimumContentsLength(14)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(_FILTER_DELAY_MS)
        self._filter_timer.timeout.connect(self._rebuild)
        self.search.textChanged.connect(self._schedule_filter)
        self.source_filter.currentIndexChanged.connect(self._schedule_filter)
        self._filter_bar = QWidget()
        filters = QHBoxLayout(self._filter_bar)
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.source_filter)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_search)

        # ---- list ---------------------------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setProperty("refrainScrolls", True)
        self._scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rows_host = QWidget()
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(6, 4, 6, 6)
        self._rows.setSpacing(1)
        self._scroll.setWidget(self._rows_host)

        # ---- empty state --------------------------------------------------
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        empty_icon = QLabel()
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph = icons.themed_icon("document-open-recent")
        if glyph.isNull():
            glyph = QIcon(str(icon_path))
        empty_icon.setPixmap(glyph.pixmap(48, 48))
        ev.addWidget(empty_icon)
        self.empty_title = QLabel()
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont(self.empty_title.font())
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.15)
        self.empty_title.setFont(title_font)
        ev.addWidget(self.empty_title)
        self.empty_text = QLabel()
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setWordWrap(True)
        ev.addWidget(self.empty_text)
        ev.addStretch(2)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._scroll)
        self._stack.addWidget(empty)

        # ---- bottom bar ---------------------------------------------------
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.hide)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._filter_bar)
        layout.addWidget(self._stack, 1)
        layout.addLayout(bottom)

        self._apply_muted_colors()
        self._rebuild()
        apply_interactive_cursors(self)

    # ------------------------------------------------------------- public

    def relative_time(self, seconds: int) -> str:
        """ "just now", "5 minutes ago", "2 hours ago", "3 days ago".

        Methods using self.tr rather than module functions using
        QCoreApplication.translate: pylupdate only recognises the plural
        form of tr(), and never sees a call whose arguments hold "//".
        """
        if seconds < 60:
            return self.tr("just now")
        minutes = seconds // 60
        if minutes < 60:
            return self.tr("%n minute(s) ago", "", minutes)
        hours = minutes // 60
        if hours < 24:
            return self.tr("%n hour(s) ago", "", hours)
        days = hours // 24
        return self.tr("%n day(s) ago", "", days)

    def when_text(self, started: QDateTime) -> str:
        """How long ago, then the moment itself with the zone abbreviated.

        Not QLocale's long format: that spells the zone out in full
        ("Mitteleuropäische Sommerzeit") and adds the seconds.
        """
        ago = self.relative_time(max(0, started.secsTo(QDateTime.currentDateTime())))
        date = self._locale.toString(started.date(), QLocale.FormatType.ShortFormat)
        at = clock.when(self._locale, started.toSecsSinceEpoch())
        return f"{ago} · {date}, {at} {started.timeZoneAbbreviation()}".rstrip()

    def set_snapshot(self, snapshot: HistorySnapshot) -> None:
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self._forget_old_covers()
        # Closed, the window only keeps the snapshot: showEvent rebuilds
        # anyway, and a hundred rows rebuilt on every song change, cover
        # and pause while nobody looks is work for nothing.
        if self.isVisible():
            self._rebuild()

    def reset_filters(self) -> None:
        for widget in (self.search, self.source_filter):
            widget.blockSignals(True)
        self.search.clear()
        self.source_filter.setCurrentIndex(0)
        for widget in (self.search, self.source_filter):
            widget.blockSignals(False)
        self._filter_timer.stop()
        if self.isVisible():
            self._rebuild()

    # ---------------------------------------------------------- lifecycle

    def showEvent(self, event) -> None:
        # "Today" / "Yesterday" are relative to when the window is looked
        # at, not to when the last song arrived.
        self._rebuild()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        # Spontaneous means minimised, not closed: the search stays, and
        # there's no size worth writing to disk yet.
        if not event.spontaneous():
            self.sizeRemembered.emit(self.width(), self.height())
            # Every opening starts from the whole list, not last time's search.
            self.reset_filters()
        super().hideEvent(event)

    def keyPressEvent(self, event) -> None:
        # Esc clears a search first; only an Esc with nothing to clear
        # closes the window, as it would anywhere else.
        if event.key() == Qt.Key.Key_Escape and self.search.text():
            self.search.clear()
            return
        super().keyPressEvent(event)

    def _schedule_filter(self, *_args) -> None:
        self._filter_timer.start()

    def _focus_search(self) -> None:
        if self.search.isVisible():
            self.search.setFocus()
            self.search.selectAll()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (event.Type.PaletteChange, event.Type.StyleChange):
            # Placeholders and muted text are derived from the palette.
            self._covers.clear()
            self._clear_rows()
            self._apply_muted_colors()
            self._rebuild()

    # ----------------------------------------------------------- building

    def _apply_muted_colors(self) -> None:
        muted = _muted(self.palette())
        for label in (self.count_label, self.empty_text):
            _set_color(label, muted)

    @staticmethod
    def _drop(widget: QWidget) -> None:
        # Out of the tree now, freed later: deleteLater alone leaves the
        # old rows as children until the event loop runs, where
        # findChildren (and a11y) would still see them.
        widget.setParent(None)
        widget.deleteLater()

    def _clear_rows(self) -> None:
        while self._rows.count():
            widget = self._rows.takeAt(0).widget()
            if widget is not None:
                self._drop(widget)
        self._shown_widgets = []
        self._shown_keys = []

    def _rebuild(self) -> None:
        snap = self._snapshot
        count = len(snap.entries)
        self._update_filter_bar(snap)
        query = self.search.text()
        source = self.source_filter.currentData() or ""
        filtering = bool(query.strip() or source)
        shown = [
            e
            for e in snap.entries
            if (not source or _source_text(e) == source) and matches(e, query)
        ]

        # Plurals through Qt's numerus forms ("Last song", "Last 12 songs"),
        # not sentences built around the number — Russian and Polish change
        # the noun's ending with it. Hidden at zero: the empty state already
        # says there's nothing.
        if filtering:
            self.count_label.setText(
                self.tr("%1 of %n song(s)", "", count).replace("%1", str(len(shown)))
            )
        else:
            self.count_label.setText(self.tr("Last %n song(s)", "", count))
        self.clear_btn.setEnabled(snap.enabled and count > 0)
        self.count_label.setVisible(snap.enabled and count > 0)

        empty = None
        if not snap.enabled:
            empty = (
                self.tr("History is turned off"),
                self.tr("Turn it on under Settings → History to see what you played."),
            )
        elif not count:
            empty = (self.tr("No songs yet"), self.tr("Play something and it shows up here."))
        elif not shown:
            empty = (self.tr("No matches"), self.tr("Try a different search or source."))
        if empty is not None:
            self._clear_rows()
            self.empty_title.setText(empty[0])
            self.empty_text.setText(empty[1])
            self._stack.setCurrentIndex(1)
            return
        self._stack.setCurrentIndex(0)

        # By identity, not position: filtered, the song playing right now
        # need not be first in `shown` — or in it at all.
        now_entry = snap.entries[0] if snap.now_playing else None
        words = _fold(query).split()
        meta_width = self._meta_width(snap)
        dpr = self.devicePixelRatioF()
        today = QDate.currentDate()
        spare: dict[tuple, list[QWidget]] = {}
        for widget, key in zip(self._shown_widgets, self._shown_keys, strict=True):
            spare.setdefault(key, []).append(widget)
        widgets: list[QWidget] = []
        keys: list[tuple] = []
        last_day: QDate | None = None
        for entry in shown:
            started = (
                clock.date(self._locale, entry.started_at)
                if entry.started_at
                else QDateTime.currentDateTime()
            )
            day = started.date()
            if day != last_day:
                first = last_day is None
                key = ("day", day.toJulianDay(), today.toJulianDay(), first)
                reused = spare.get(key)
                widgets.append(reused.pop() if reused else self._day_header(day, today, first))
                keys.append(key)
                last_day = day
            is_now = entry is now_entry
            key = (
                "song",
                # Without a start time the row shows the current one, so
                # it is never taken over from an earlier rebuild.
                tuple(vars(entry).values()) if entry.started_at else object(),
                is_now,
                is_now and snap.playing,
                meta_width,
                dpr,
            )
            reused = spare.get(key)
            if reused:
                row = reused.pop()
                row.set_highlights(words)
                if entry.cover_url and not row.has_cover:
                    cover = self._stored_cover(entry.cover_url, dpr)
                    if cover is not None:
                        row.set_cover(cover)
            else:
                cover = self._stored_cover(entry.cover_url, dpr) if entry.cover_url else None
                row = _SongRow(
                    entry,
                    started,
                    now_playing=is_now,
                    playing=snap.playing,
                    locale=self._locale,
                    cover=cover if cover is not None else self._placeholder(dpr),
                    meta_width=meta_width,
                    when_text=self.when_text,
                    on_remove=lambda e=entry: self.removeRequested.emit(e),
                    highlight=words,
                )
                row.has_cover = cover is not None
            widgets.append(row)
            keys.append(key)
        for leftovers in spare.values():
            for widget in leftovers:
                self._drop(widget)
        same_order = len(widgets) == len(self._shown_widgets) and all(
            a is b for a, b in zip(widgets, self._shown_widgets, strict=True)
        )
        self._shown_widgets = widgets
        self._shown_keys = keys
        if same_order:
            return
        bar = self._scroll.verticalScrollBar()
        scroll_pos = bar.value()
        # Taken out and put back in the new order: the widgets that stay
        # keep their parent, only their place in the layout changes.
        while self._rows.count():
            self._rows.takeAt(0)
        for widget in widgets:
            self._rows.addWidget(widget)
            # Child order follows too, so accessibility reads the list
            # in the order it is shown.
            widget.raise_()
        self._rows.addStretch(1)
        # The new rows lay out on the next event-loop pass; restoring the
        # position before that would clamp it to the old, empty range.
        QTimer.singleShot(0, lambda: bar.setValue(scroll_pos))

    def _update_filter_bar(self, snap: HistorySnapshot) -> None:
        """Offer the sources in the list, keep the pick, show what's useful."""
        sources = list(dict.fromkeys(_source_text(e) for e in snap.entries))
        offered = [self.source_filter.itemData(i) for i in range(1, self.source_filter.count())]
        if offered != sources or self.source_filter.count() == 0:
            picked = self.source_filter.currentData() or ""
            self.source_filter.blockSignals(True)
            self.source_filter.clear()
            self.source_filter.addItem(self.tr("All sources"), "")
            for text in sources:
                self.source_filter.addItem(text, text)
            # A source that has left the list takes its filter with it.
            index = self.source_filter.findData(picked) if picked else 0
            self.source_filter.setCurrentIndex(max(0, index))
            self.source_filter.blockSignals(False)
            view = self.source_filter.view()
            view.setMinimumWidth(view.sizeHintForColumn(0) + 24)
        # Neither half disappears while it is in use.
        show_search = len(snap.entries) >= _SEARCH_FROM or bool(self.search.text())
        show_sources = len(sources) > 1 or self.source_filter.currentIndex() > 0
        self.search.setVisible(show_search)
        self.source_filter.setVisible(show_sources)
        self._filter_bar.setVisible(snap.enabled and (show_search or show_sources))

    def _day_header(self, day: QDate, today: QDate, first: bool) -> QLabel:
        if day == today:
            text = self.tr("Today")
        elif day == today.addDays(-1):
            text = self.tr("Yesterday")
        else:
            text = self._locale.toString(day, QLocale.FormatType.LongFormat)
        label = QLabel(text)
        font = QFont(label.font())
        font.setBold(True)
        label.setFont(font)
        _set_color(label, _muted(self.palette()))
        label.setContentsMargins(10, 4 if first else 14, 10, 4)
        return label

    def _meta_width(self, snap: HistorySnapshot) -> int:
        """One width for every row's right column, so the times line up."""
        fm = self.fontMetrics()
        bold = QFont(self.font())
        bold.setBold(True)
        bold_fm = type(fm)(bold)
        widths = [bold_fm.horizontalAdvance(self.tr("Now playing")) + 20]
        for entry in snap.entries:
            widths.append(fm.horizontalAdvance(_source_text(entry)) + 14 + 5 + 2)
            widths.append(fm.horizontalAdvance("00:00 AM") + 20)
        return min(_META_MAX_WIDTH, max(widths))

    def _stored_cover(self, url: str, dpr: float) -> QPixmap | None:
        """The song's cover from the cover cache, or None while it has none."""
        cached = self._covers.get((url, dpr))
        if cached is not None:
            return cached
        path = image_path_for_url(url)
        try:
            present = path.exists() and path.stat().st_size > 0
        except OSError:
            present = False
        if not present:
            return None
        image = _read_scaled(str(path), round(_COVER_PX * dpr))
        if image.isNull():
            return None
        rounded = _round_cover(QPixmap.fromImage(image), dpr)
        self._covers[(url, dpr)] = rounded
        return rounded

    def _placeholder(self, dpr: float) -> QPixmap:
        key = ("", dpr)
        if key not in self._covers:
            self._covers[key] = _placeholder_cover(self.palette(), dpr)
        return self._covers[key]

    def _forget_old_covers(self) -> None:
        dpr = self.devicePixelRatioF()
        keep = {(e.cover_url, dpr) for e in self._snapshot.entries}
        keep.add(("", dpr))
        self._covers = {k: v for k, v in self._covers.items() if k in keep}

    # ------------------------------------------------------------ actions

    def _on_clear_clicked(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Clear history"))
        box.setText(self.tr("Remove every song from the history?"))
        box.setInformativeText(self.tr("This cannot be undone."))
        clear = box.addButton(self.tr("Clear history"), QMessageBox.ButtonRole.DestructiveRole)
        # Qt's own Cancel button stays English unless Qt's translations
        # happen to be installed; our string is translated with the rest.
        cancel = box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        # QMessageBox sizes itself to the text and wraps short sentences
        # into a narrow column; a spacer across the grid keeps it wider.
        grid = box.layout()
        grid.addItem(
            QSpacerItem(_CONFIRM_MIN_WIDTH, 0, QSizePolicy.Policy.Minimum),
            grid.rowCount(),
            0,
            1,
            grid.columnCount(),
        )
        box.exec()
        if box.clickedButton() is clear:
            self.clearRequested.emit()
