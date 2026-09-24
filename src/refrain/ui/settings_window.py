"""Refrain settings window: OK, Apply and Cancel, with a question before unsaved changes go."""

from __future__ import annotations

import copy
import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QCoreApplication,
    QDateTime,
    QLocale,
    QObject,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from refrain import __version__, dev_metrics
from refrain.config import HISTORY_LIMIT_CHOICES, HISTORY_LIMIT_MAX, Config
from refrain.discord_app import (
    FOUND,
    UNKNOWN_ID,
    cached_name_is_fresh,
    fetch_application_name,
    looks_like_application_id,
    remember_application_name,
)
from refrain.paths import assets_dir, config_path, state_dir
from refrain.scrobble import (
    API_ACCOUNT_URL,
    ERR_INVALID_TOKEN,
    ERR_TOKEN_EXPIRED,
    ERR_TOKEN_NOT_AUTHORISED,
    LastfmClient,
    LastfmError,
)
from refrain.sources.bluetooth import BluetoothSource
from refrain.ui.cursors import apply_interactive_cursors
from refrain.ui.external_link import confirm_and_open
from refrain.ui.update_dialog import _open_https_link
from refrain.updater import ReleaseInfo, prepare_release_notes

GITHUB_URL = "https://github.com/Rockykln/refrain"
DEVELOPER_UNLOCK_CLICKS = 6
# Clicks further apart than this start the count again.
DEVELOPER_CLICK_GAP_S = 2.0
_LASTFM_ACCOUNT_KEYS = (("lastfm", "session_key"), ("lastfm", "username"))

log = logging.getLogger(__name__)


class _VersionLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def application_name_status(client_id: str, status: str, name: str) -> tuple[str, bool]:
    """What the line beside the Application ID should say, and whether it's good news.

    Returns ``(text, ok)``. ``ok`` drives nothing but the colour: a green
    line means Discord confirmed the ID and told us what it is called.

    The four states a user can actually be in:

    - No ID at all. Say nothing — an empty field is not an error, it is
      the default, and Refrain runs perfectly well without Discord.
    - An ID that cannot be a snowflake. Local knowledge, so say so
      without asking Discord.
    - Discord has no such application. The case this exists for: a
      mistyped ID otherwise fails silently, the status simply never
      appearing and nothing to point at.
    - We could not ask. Distinct from the above, because the user can do
      nothing about it and should not go re-checking a correct ID.

    Pure, and separate from the widget, so all four can be tested
    without a Qt event loop.
    """
    client_id = client_id.strip()
    if not client_id:
        return "", False
    if not looks_like_application_id(client_id):
        return QCoreApplication.translate("SettingsWindow", "Expected 17–20 digits"), False
    if status == FOUND:
        return (
            QCoreApplication.translate("SettingsWindow", "Application name: “{name}”").format(
                name=name
            ),
            True,
        )
    if status == UNKNOWN_ID:
        return QCoreApplication.translate("SettingsWindow", "No such Discord application"), False
    return QCoreApplication.translate("SettingsWindow", "Could not reach Discord"), False


def reset_to_defaults(current: Config) -> Config:
    """Shipped defaults for every setting — but not for what isn't one.

    Three things survive, and the Reset dialog names the first two:

    - Every Discord client_id, the per-source overrides included. They
      are the user's Discord identity, not a preference.
    - The Last.fm credentials and the connected session, for the same
      reason: a settings reset must not silently disconnect an account.
    - Whether the welcome wizard has already run. That is a record of
      something that happened, and the dialog offers no undo for the
      setup — so a reset must not make the wizard reappear on the next
      start.
    """
    fresh = Config()
    fresh.discord = current.discord
    fresh.lastfm = current.lastfm
    fresh.behavior.first_run_complete = current.behavior.first_run_complete
    fresh.update.last_check_ts = current.update.last_check_ts
    fresh.advanced.developer_unlocked = current.advanced.developer_unlocked
    return fresh


def lastfm_connection_state(session_key: str, api_key: str, shared_secret: str) -> str:
    """Whether the Last.fm connection is actually *usable*.

    Pure (no Qt) so it's unit-testable. A Last.fm call needs all three
    of api_key + shared_secret + session_key — the secret/session live
    in the keyring, the api_key in config.toml, so they can desync
    (fresh config but a surviving keyring entry, or vice-versa). Status
    keyed on session_key alone would show "Connected" for an
    unusable, scrobble-inert state.

    Returns:
      * ``"connected"``    — all three present; scrobbling works.
      * ``"incomplete"``   — a leftover session but no usable
        api_key/secret; the user must re-enter them and reconnect.
      * ``"disconnected"`` — no session.
    """
    sk = (session_key or "").strip()
    ak = (api_key or "").strip()
    ss = (shared_secret or "").strip()
    if sk and ak and ss:
        return "connected"
    if sk:
        return "incomplete"
    return "disconnected"


# ---------------------------------------------------------------------------
# Layout helpers — keep every tab visually consistent.
# ---------------------------------------------------------------------------

# Outer padding inside each tab page.
_TAB_MARGINS = (16, 16, 16, 16)
# Vertical gap between QGroupBox sections.
_TAB_SPACING = 10
# Padding inside each QGroupBox content area.
_GROUP_MARGINS = (14, 16, 14, 10)
# Horizontal / vertical gap between form rows inside a group.
_FORM_HSPACING = 12
_FORM_VSPACING = 6
# Fixed width for inputs (combos, line edits, spinboxes). Combined with
# FieldsStayAtSizeHint the form layout will not grow them past this — on
# Plasma Breeze, AllNonFixedFieldsGrow ignores maxWidth caps and stretches
# widgets to ~440 px even with setFixedWidth set. FieldsStayAtSizeHint +
# explicit per-widget setFixedWidth is the only combo that holds across
# Fusion (offscreen tests) and Breeze (Plasma).
_INPUT_MAX_WIDTH = 220
# Wider variant for inputs whose placeholder / longest item text doesn't
# fit in 220 — the Application ID fields and the privacy modes, whose
# longest label needs ~340 px. Keep the inputs of a group at the same
# width so they line up vertically.
_INPUT_WIDE_WIDTH = 360
# Room for twenty digits, and for the application's name beside them.
_INPUT_ID_WIDTH = 260


# Han, kana and hangul fonts have no italic; Qt slants them artificially.
_NO_ITALIC_SCRIPTS = {
    QLocale.Script.HanScript,
    QLocale.Script.SimplifiedHanScript,
    QLocale.Script.TraditionalHanScript,
    QLocale.Script.JapaneseScript,
    QLocale.Script.KoreanScript,
    QLocale.Script.HangulScript,
}


def _hint_style(color: str = "palette(text)") -> str:
    # palette(mid) all but vanishes on Plasma Breeze Dark.
    italic = QLocale().script() not in _NO_ITALIC_SCRIPTS
    return f"color: {color};" + (" font-style: italic;" if italic else "")


def _hint(text: str) -> QLabel:
    """Wrapped helper text under form rows."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(_hint_style())
    return lbl


class _ElidedHint(QLabel):
    """One line that ends in "…" instead of widening the page."""

    def __init__(self) -> None:
        super().__init__()
        self._full = ""
        # Shows names that come from Discord and Last.fm; one with a "<" in
        # it must not turn into markup the width measurement can't see.
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("refrainElides", True)
        self.setStyleSheet(_hint_style())

    def text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        width = self.fontMetrics().horizontalAdvance(self._full) + 2 * self.margin() + 4
        return QSize(width, hint.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(
            self.fontMetrics().horizontalAdvance("…") * 3, super().minimumSizeHint().height()
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width())
        )
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


def _row_with_buttons(*buttons: QPushButton) -> QHBoxLayout:
    """A horizontal layout that keeps buttons at their natural width
    and pushes them all to the left with a trailing stretch."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    for b in buttons:
        b.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        row.addWidget(b)
    row.addStretch(1)
    return row


def _tab_layout(parent: QWidget) -> QVBoxLayout:
    """Common chrome for every tab — vertical stack with consistent
    margins and a trailing stretch so groups anchor at the top."""
    v = QVBoxLayout(parent)
    v.setContentsMargins(*_TAB_MARGINS)
    v.setSpacing(_TAB_SPACING)
    return v


def _scroll_wrap(page: QWidget) -> QScrollArea:
    """Put a tab page in a vertically-scrolling viewport.

    Every tab stacks fixed-height QGroupBoxes; with enough groups — and
    especially with the ~30 %-longer German strings — the content is
    taller than the dialog. Without a scroll area Qt crushes every group
    below its sizeHint and the form rows overlap. ``setWidgetResizable(True)`` keeps
    the page at the viewport width (inputs stay laid out; only a
    vertical scrollbar appears, and only when actually needed), so this
    is inert on tabs that already fit.
    """
    sa = QScrollArea()
    # Long tabs are meant to scroll; the layout watcher must not call that a fault.
    sa.setProperty("refrainScrolls", True)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(page)
    return sa


# Stylesheet applied to every QGroupBox so the title sits flush left
# instead of centered. Plasma Breeze centers QGroupBox titles by default;
# left alignment matches the label/input rows below and reads better.
_GROUPBOX_STYLE = """
QGroupBox {
    font-weight: 600;
    margin-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 8px;
}
"""


def _new_group(title: str) -> tuple[QGroupBox, QFormLayout]:
    """Create a QGroupBox + a QFormLayout configured to match every
    other group on the page (margins, spacing, label alignment, field
    growth policy). Returns the box + the form so callers populate it."""
    box = QGroupBox(title)
    box.setStyleSheet(_GROUPBOX_STYLE)
    form = QFormLayout(box)
    form.setContentsMargins(*_GROUP_MARGINS)
    form.setHorizontalSpacing(_FORM_HSPACING)
    form.setVerticalSpacing(_FORM_VSPACING)
    # Left-aligned labels read better with German text — right-aligned
    # detaches long labels from their inputs and feels off-balance.
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    # FieldsStayAtSizeHint keeps every input at its sizeHint (or
    # explicit setFixedWidth) and refuses to grow it. This is critical
    # on Plasma Breeze: AllNonFixedFieldsGrow ignores fixed-width caps
    # there and stretches spinboxes/combos to ~440 px, leaving huge
    # empty space between the value and the chevron chrome. With
    # FieldsStayAtSizeHint + per-widget setFixedWidth(_INPUT_MAX_WIDTH),
    # every input renders at exactly 220 logical px on every Qt style.
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    return box, form


def _align_labels(*forms: QFormLayout) -> None:
    """One label column across groups, so their fields line up."""
    labels = []
    for form in forms:
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            label = item.widget() if item is not None else None
            if label is not None:
                labels.append(label)
    width = max((label.sizeHint().width() for label in labels), default=0)
    for label in labels:
        label.setMinimumWidth(width)


class _AppNameWorker(QObject):
    """Resolves one Discord Application ID off the GUI thread.

    A settings dialog that freezes for five seconds because the network
    is slow is worse than one that never showed the name at all, so the
    lookup never runs on the GUI thread. One worker per lookup, torn
    down when it finishes — the same shape as the Last.fm auth worker
    below, and for the same reason.
    """

    resolved = Signal(str, str, str)  # (client_id, status, name)

    def __init__(self, client_id: str) -> None:
        super().__init__()
        self._client_id = client_id

    def run(self) -> None:
        status, name = fetch_application_name(self._client_id)
        # The id goes back with the answer: the user may have typed on
        # while this was in flight, and a stale answer must not overwrite
        # the label for an id they have since changed.
        self.resolved.emit(self._client_id, status, name)


class _BluetoothDevicesWorker(QObject):
    """Lists the paired Bluetooth devices off the GUI thread; BlueZ can take
    up to the D-Bus timeout to answer."""

    listed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._list = BluetoothSource.list_paired_devices

    def run(self) -> None:
        self.listed.emit(self._list())


class _LastfmAuthWorker(QObject):
    """Runs one Last.fm auth network call off the GUI thread.

    The desktop flow is two calls with a browser round-trip between
    them, so a worker handles a single ``phase`` ("token" or "session")
    and is torn down before the next one — no thread lives across the
    user's browser interaction.
    """

    tokenReady = Signal(str)  # request token
    sessionReady = Signal(str, str)  # (session_key, username)
    failed = Signal(str, int)  # human-readable error, Last.fm's code or -1

    def __init__(self, client: LastfmClient, phase: str, token: str = "") -> None:
        super().__init__()
        self._client = client
        self._phase = phase
        self._token = token

    def run(self) -> None:
        try:
            if self._phase == "token":
                self.tokenReady.emit(self._client.get_token())
            else:
                key, name = self._client.get_session(self._token)
                self.sessionReady.emit(key, name)
        except LastfmError as e:
            self.failed.emit(str(e), -1 if e.code is None else e.code)
        except Exception as e:  # never let a worker exception escape the thread
            self.failed.emit(f"Unexpected Last.fm error: {e}", -1)


class LastfmApprovalDialog(QDialog):
    """Asks Last.fm every few seconds until the user has allowed Refrain."""

    def __init__(
        self,
        client: LastfmClient,
        token: str,
        open_page,
        parent: QWidget | None = None,
        poll_ms: int = 3000,
        give_up_ms: int = 10 * 60 * 1000,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._token = token
        self._worker: _LastfmAuthWorker | None = None
        self.session_key = ""
        self.username = ""
        self.error = ""
        self.setWindowTitle(self.tr("Authorize Refrain"))
        text = QLabel(
            self.tr(
                "A Last.fm page opened in your browser. Click “Yes, allow access” "
                "there — Refrain connects by itself as soon as you have."
            )
        )
        text.setWordWrap(True)
        self._status = QLabel(self.tr("Waiting for you to allow access…"))
        again = QPushButton(self.tr("Open the page again"))
        again.clicked.connect(open_page)
        cancel = QPushButton(self.tr("Cancel"))
        cancel.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self._status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(again)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(poll_ms)
        self._give_up = QTimer(self)
        self._give_up.setSingleShot(True)
        self._give_up.timeout.connect(self._on_timed_out)
        self._give_up.start(give_up_ms)

    def _poll(self) -> None:
        if self._worker is not None:
            return  # the last question is still on its way
        # A plain daemon thread, like _AppNameWorker: a request still in
        # flight when the dialog closes is simply forgotten, and there is
        # no QThread to pile up per poll or to destroy while it runs.
        worker = _LastfmAuthWorker(self._client, "session", self._token)
        worker.sessionReady.connect(self._on_session)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        threading.Thread(target=worker.run, name="refrain-lastfm-approval", daemon=True).start()

    def _answer_is_current(self) -> bool:
        if self._worker is None or self.sender() is not self._worker:
            return False
        self._worker = None
        return True

    def _on_session(self, key: str, name: str) -> None:
        if not self._answer_is_current():
            return
        self.session_key, self.username = key, name
        self.accept()

    def _on_failed(self, message: str, code: int) -> None:
        if not self._answer_is_current():
            return
        if code == ERR_TOKEN_NOT_AUTHORISED:
            self._status.setText(self.tr("Waiting for you to allow access…"))
        elif code == -1:
            # Offline for a moment: the approval may already be given.
            self._status.setText(self.tr("Can't reach Last.fm right now — still trying…"))
        elif code in (ERR_TOKEN_EXPIRED, ERR_INVALID_TOKEN):
            self._stop(self.tr("The Last.fm page has expired. Click Connect to start again."))
        else:
            self._stop(message)

    def _on_timed_out(self) -> None:
        self._stop(self.tr("No approval arrived. Click Connect to start again."))

    def _stop(self, error: str) -> None:
        self.error = error
        self.reject()

    def done(self, result: int) -> None:
        self._poll_timer.stop()
        self._give_up.stop()
        self._worker = None
        super().done(result)


def themed_svg_icon(svg: bytes, color: QColor, size: int, scale: float = 2.0) -> QIcon:
    """An SVG icon with its ``currentColor`` drawn as ``color``.

    Qt's SVG renderer knows nothing of the widget's text colour and paints
    ``currentColor`` black, which all but vanishes on a dark theme.
    """
    renderer = QSvgRenderer(QByteArray(svg.replace(b"currentColor", color.name().encode())))
    px = round(size * scale)
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    image.setDevicePixelRatio(scale)
    return QIcon(QPixmap.fromImage(image))


class SettingsWindow(QDialog):
    """Tabbed settings dialog. Emits `applied(Config)` whenever it saves."""

    applied = Signal(object)
    checkUpdatesRequested = Signal()
    showLogRequested = Signal()
    showHistoryRequested = Signal()
    restartRequested = Signal()
    uninstallRequested = Signal()

    def __init__(self, config: Config, parent: QWidget | None = None):
        super().__init__(parent)
        # Qt's applicationDisplayName ("Refrain") is auto-appended to
        # this title by the window manager. Setting the manual prefix
        # too would duplicate it as "Refrain — Settings — Refrain".
        self.setWindowTitle(self.tr("Settings"))
        # Explicit per-window icon — without this, GNOME Shell's
        # window-to-desktop-entry matcher on Wayland sees a window
        # titled "Settings" and falls back to gnome-control-center
        # ("org.gnome.Settings"), so Refrain shows up in the dock /
        # task bar with a gear icon. Setting the icon directly bypasses
        # that heuristic.
        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        # Default size fits every tab without a scrollbar in all shipped
        # languages (Advanced is the tallest; the per-source ID rows are
        # opt-in and may scroll). The per-tab scroll area is the safety
        # net for small screens — the minimum stays lower so the window
        # can shrink without crushing the form rows.
        self.setMinimumSize(680, 600)
        self.resize(720, 720)
        self._config = config
        # What the form showed when it last matched the saved settings.
        self._baseline: dict[tuple[str, str], object] | None = None
        self._version_clicks = 0
        self._last_version_click = 0.0
        self._developer_unlocked = False

        # Last.fm session/username aren't form widgets — they're set by
        # the connect flow and persisted on Apply. Auth network calls run
        # on a short-lived worker thread; refs kept so it's joined before
        # the next phase / dialog close.
        self._lastfm_session_key = ""
        self._lastfm_username = ""
        self._lastfm_token = ""
        self._lastfm_client: LastfmClient | None = None
        self._lastfm_auth_thread: QThread | None = None
        self._lastfm_auth_worker: _LastfmAuthWorker | None = None

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # Page names other windows can jump to, independent of the translation.
        self.tab_pages = {"general": 0, "sources": 1, "lastfm": 2, "history": 3, "updates": 4}
        self.tabs.addTab(_scroll_wrap(self._build_general_tab()), self.tr("General"))
        self.tabs.addTab(_scroll_wrap(self._build_sources_tab()), self.tr("Sources"))
        self.tabs.addTab(_scroll_wrap(self._build_lastfm_tab()), self.tr("Last.fm"))
        self.tabs.addTab(_scroll_wrap(self._build_history_tab()), self.tr("Recently played"))
        self.tabs.addTab(_scroll_wrap(self._build_updates_tab()), self.tr("Updates"))
        self.tabs.addTab(_scroll_wrap(self._build_advanced_tab()), self.tr("Advanced"))

        self.ok_btn = QPushButton(self.tr("OK"))
        self.apply_btn = QPushButton(self.tr("Apply"))
        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.ok_btn.setDefault(True)
        button_box = QDialogButtonBox()
        button_box.addButton(self.ok_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(self.apply_btn, QDialogButtonBox.ButtonRole.ApplyRole)
        button_box.addButton(self.cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        self.ok_btn.clicked.connect(self._on_ok_clicked)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        self.cancel_btn.clicked.connect(self.reject)

        self.version_label = version_label = _VersionLabel(f"Refrain v{__version__}")
        # palette(text) follows the theme — `gray` is unreadable on
        # Plasma Breeze Dark.
        version_label.setStyleSheet("color: palette(text);")
        version_label.clicked.connect(self._on_version_clicked)

        self._github_btn = github_btn = QToolButton()
        self._apply_github_icon()
        github_btn.setIconSize(QSize(16, 16))
        github_btn.setAutoRaise(True)
        github_btn.setToolTip(self.tr("View Refrain on GitHub"))
        github_btn.clicked.connect(lambda: confirm_and_open(self, GITHUB_URL))

        legal_btn = QPushButton(self.tr("Legal"))
        legal_btn.setFlat(True)
        legal_btn.setToolTip(self.tr("License, trademark and affiliation notices"))
        legal_btn.clicked.connect(self._show_legal)

        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.addStretch()
        version_row.addWidget(legal_btn)
        version_row.addWidget(version_label)
        version_row.addWidget(github_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(button_box)
        layout.addLayout(version_row)

        self._load_into_form()
        self._watch_form()
        self._mark_clean()

        # Every clickable child gets the pointing hand, in one place —
        # see refrain.ui.cursors.
        apply_interactive_cursors(self)

    # ====================================================================
    # General tab
    # ====================================================================

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        v = _tab_layout(w)

        discord_group, df = _new_group(self.tr("Discord"))
        self._discord_form = df

        self.client_id_input = QLineEdit()
        self.client_id_input.setPlaceholderText(self.tr("17–20 digit number"))
        self.client_id_input.setFixedWidth(_INPUT_ID_WIDTH)

        # The ID is nineteen digits and a wrong one fails silently, so
        # the name Discord has on file is the only readable confirmation
        # there is — and it is also the word that ends up next to
        # "Listening to" on the card.
        #
        # Beside the field rather than under it: a row that appeared the
        # moment you started typing would push everything below it down
        # while you were still typing. Beside also means these texts
        # have to earn their width, which is why they are as short as
        # they are.
        self.app_name_label = _ElidedHint()
        self.app_name_label.setVisible(False)
        id_row = QHBoxLayout()
        id_row.setContentsMargins(0, 0, 0, 0)
        id_row.setSpacing(8)
        id_row.addWidget(self.client_id_input)
        id_row.addWidget(self.app_name_label, 1)
        df.addRow(self.tr("Application ID:"), id_row)

        # Typing nineteen digits would otherwise be nineteen requests.
        # The pause is long enough to mean "done typing" and short
        # enough that a paste feels immediate.
        self._app_name_timer = QTimer(self)
        self._app_name_timer.setSingleShot(True)
        self._app_name_timer.setInterval(600)
        self._app_name_timer.timeout.connect(self._lookup_application_name)
        self.client_id_input.textChanged.connect(self._on_client_id_edited)
        # Plain daemon threads: a lookup outliving the window or the app
        # must never be waited for, and a QThread destroyed mid-run aborts.
        self._app_name_workers: set[_AppNameWorker] = set()

        # Most users need exactly one Discord application, so the
        # per-source override fields are hidden behind this opt-in
        self.resolve_app_name_box = QCheckBox(self.tr("Look up the application's name on Discord"))
        self.resolve_app_name_box.setToolTip(
            self.tr(
                "Asks Discord what the Application ID is called, so a "
                "mistyped ID is visible instead of silently publishing "
                "nothing. This is the one request Refrain sends to "
                "Discord's servers rather than to your local Discord "
                "client; it carries the Application ID and nothing else."
            )
        )
        self.resolve_app_name_box.toggled.connect(self._on_resolve_app_name_toggled)
        df.addRow(self.resolve_app_name_box)

        # toggle (default off) instead of cluttering the tab. Ticking
        # it reveals separate Application IDs for Apple Music vs Bluetooth —
        # handy if you want each to render under its own Discord app
        # (different name + uploaded artwork). Refrain reconnects RPC
        # automatically when the active source flips.
        self.discord_per_source_box = QCheckBox(
            self.tr("Use a separate Discord application per source (advanced)")
        )
        self.discord_per_source_box.toggled.connect(self._set_discord_overrides_visible)
        df.addRow(self.discord_per_source_box)

        self.discord_all_clients_box = QCheckBox(
            self.tr("Send the status to every running Discord client")
        )
        self.discord_all_clients_box.setToolTip(
            self.tr(
                "Refrain normally sends your status to the first Discord app "
                "it finds. Turn this on if you run more than one at once."
            )
        )

        self.client_id_mpris_input = QLineEdit()
        self.client_id_mpris_input.setPlaceholderText(self.tr("(uses the main Application ID)"))
        self.client_id_mpris_input.setFixedWidth(_INPUT_ID_WIDTH)
        df.addRow(self.tr("Apple Music:"), self.client_id_mpris_input)

        self.client_id_bluetooth_input = QLineEdit()
        self.client_id_bluetooth_input.setPlaceholderText(self.tr("(uses the main Application ID)"))
        self.client_id_bluetooth_input.setFixedWidth(_INPUT_ID_WIDTH)
        df.addRow(self.tr("Bluetooth:"), self.client_id_bluetooth_input)
        df.addRow(self.discord_all_clients_box)
        # Hidden until the advanced toggle is ticked (or a saved
        # override is loaded — see _load_into_form).
        self._set_discord_overrides_visible(False)

        self.buttons_box = QCheckBox(self.tr("Show “Listen on Apple Music” button in Discord"))
        self.buttons_box.setToolTip(self.tr("Discord shows the button to others, not to you."))
        df.addRow(self.buttons_box)

        # Portal button right under the checkbox — keeps "open the
        # external page where you'd register an application" close to
        # the Application ID field it feeds. Hint sits below as helper text.
        portal_btn = QPushButton(self.tr("Open Discord Developer Portal"))
        portal_btn.clicked.connect(
            lambda: confirm_and_open(self, "https://discord.com/developers/applications")
        )
        df.addRow(_row_with_buttons(portal_btn))

        client_hint = _hint(
            self.tr(
                "Create a free application in the Discord Developer Portal "
                "and copy its Application ID. The application's name is what "
                "shows up next to “Listening to” in your Discord status."
            )
        )
        df.addRow(client_hint)

        v.addWidget(discord_group)

        # Outside the Discord group: Off pauses Last.fm too, and the
        # catalog lookup feeds Discord, notifications and the history.
        privacy_group, pf = _new_group(self.tr("Privacy"))
        self.privacy_combo = QComboBox()
        self.privacy_combo.setFixedWidth(_INPUT_WIDE_WIDTH)
        self.privacy_combo.addItem(self.tr("Full — title, artist, album, cover"), "full")
        self.privacy_combo.addItem(self.tr("Minimal — only “Listening to music”"), "minimal")
        self.privacy_combo.addItem(
            self.tr("Off — pause Discord status and Last.fm scrobbling"), "off"
        )
        self.privacy_combo.setToolTip(
            self.tr(
                "What Refrain shares while music plays. Off pauses both the "
                "Discord status and Last.fm scrobbling; the recently played "
                "list on this computer keeps working."
            )
        )
        pf.addRow(self.tr("Sharing:"), self.privacy_combo)
        self.cover_art_box = QCheckBox(self.tr("Look up songs in Apple's catalog"))
        pf.addRow(self.cover_art_box)
        pf.addRow(
            _hint(
                self.tr(
                    "Sends artist and title to Apple to get the cover, the song "
                    "link and the length. Without it, Discord shows no cover and "
                    "often no progress bar."
                )
            )
        )
        v.addWidget(privacy_group)
        _align_labels(df, pf)

        behavior_group, bf = _new_group(self.tr("Behavior"))
        self.autostart_box = QCheckBox(self.tr("Start Refrain automatically on login"))
        bf.addRow(self.autostart_box)
        self.notifications_box = QCheckBox(self.tr("Show desktop notification on track change"))
        bf.addRow(self.notifications_box)
        v.addWidget(behavior_group)

        v.addStretch(1)
        return w

    # ====================================================================
    # History tab
    # ====================================================================

    def _build_history_tab(self) -> QWidget:
        # Its own tab rather than a group under General: General was
        # already as tall as the window allows, and a fourth group would
        # push it into the scroll-area fallback in every language.
        w = QWidget()
        v = _tab_layout(w)

        history_group, hf = _new_group(self.tr("Recently played"))
        self.history_box = QCheckBox(self.tr("Keep a list of recently played songs"))
        self.history_box.toggled.connect(self._on_history_toggled)
        hf.addRow(self.history_box)

        # Plain numbers: "Songs to keep:" carries the noun, so no
        # language has to agree a plural with each value.
        self.history_limit_combo = QComboBox()
        self.history_limit_combo.setFixedWidth(_INPUT_MAX_WIDTH)
        for n in HISTORY_LIMIT_CHOICES:
            self.history_limit_combo.addItem(str(n), n)
        hf.addRow(self.tr("Songs to keep:"), self.history_limit_combo)

        self.history_show_btn = QPushButton(self.tr("Show recently played…"))
        self.history_show_btn.clicked.connect(self.showHistoryRequested.emit)
        hf.addRow(_row_with_buttons(self.history_show_btn))
        hf.addRow(
            _hint(
                self.tr(
                    "Stored only on this computer and never sent anywhere, so "
                    "privacy mode doesn't affect it. Turning it off deletes the "
                    "list; a lower number drops the oldest songs."
                )
            )
        )
        v.addWidget(history_group)

        v.addStretch(1)
        return w

    def _on_history_toggled(self, on: bool) -> None:
        self.history_limit_combo.setEnabled(on)
        self.history_show_btn.setEnabled(on)

    def _select_history_limit(self, value: int) -> None:
        """Select ``value`` in the combo, adding it if a hand-edit put an
        unlisted count in config.toml — rounding it would silently change
        a setting the user chose. Clamped the way the history itself
        applies it, so the combo never offers a count that isn't used."""
        value = max(1, min(HISTORY_LIMIT_MAX, int(value)))
        index = self.history_limit_combo.findData(value)
        if index < 0:
            index = sum(1 for n in HISTORY_LIMIT_CHOICES if n < value)
            self.history_limit_combo.insertItem(index, str(value), value)
        self.history_limit_combo.setCurrentIndex(index)

    def _confirm_history_off(self) -> bool:
        """Ask before an Apply that deletes the history — the one change
        in this window that destroys something with no way back."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Turn off history?"))
        box.setText(self.tr("Turning the history off deletes the list of recently played songs."))
        box.setInformativeText(self.tr("This cannot be undone."))
        turn_off = box.addButton(
            self.tr("Turn off and delete"), QMessageBox.ButtonRole.DestructiveRole
        )
        cancel = box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is turn_off

    def _confirm_lastfm_disconnect(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Disconnect from Last.fm?"))
        box.setText(self.tr("Nothing is scrobbled until you connect again."))
        disconnect = box.addButton(self.tr("Disconnect"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is disconnect

    def _confirm_lastfm_unconnected(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Last.fm isn't connected"))
        box.setText(
            self.tr(
                "Scrobbling is switched on, but Refrain has no connection to "
                "Last.fm — nothing will be scrobbled."
            )
        )
        box.setInformativeText(self.tr("Click Connect… on the Last.fm tab to connect."))
        apply = box.addButton(self.tr("Apply anyway"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(apply)
        box.exec()
        return box.clickedButton() is apply

    def _ask_lastfm_turn_on(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Last.fm"))
        box.setText(self.tr("Last.fm is connected, but scrobbling is off."))
        turn_on = box.addButton(self.tr("Turn on scrobbling"), QMessageBox.ButtonRole.AcceptRole)
        keep_off = box.addButton(self.tr("Keep off"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(turn_on)
        box.setEscapeButton(keep_off)
        box.exec()
        return box.clickedButton() is turn_on

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (event.Type.PaletteChange, event.Type.StyleChange):
            self._apply_github_icon()

    def _apply_github_icon(self) -> None:
        # The same colour as the version label beside it.
        button = getattr(self, "_github_btn", None)
        if button is None:
            return  # a theme change while the window is still being built
        svg = (assets_dir() / "icons" / "github-mark.svg").read_bytes()
        color = self.palette().color(QPalette.ColorRole.Text)
        button.setIcon(themed_svg_icon(svg, color, 16, max(2.0, self.devicePixelRatioF())))

    def _set_discord_overrides_visible(self, visible: bool) -> None:
        """Show/hide the per-source Application ID rows (label + field).

        Uses QFormLayout.setRowVisible (Qt 6.4+, we require ≥ 6.6) so
        the row's *label* hides too — not just the input. Degrades to
        hiding only the field on the off-chance the API is missing.
        """
        form = getattr(self, "_discord_form", None)
        if form is None:
            return
        for widget in (self.client_id_mpris_input, self.client_id_bluetooth_input):
            try:
                form.setRowVisible(widget, visible)
            except (AttributeError, TypeError):
                widget.setVisible(visible)

    # ====================================================================
    # Last.fm tab
    # ====================================================================

    def _build_lastfm_tab(self) -> QWidget:
        # Last.fm gets its own tab rather than crowding General: opt-in
        # scrobbling *alongside* the Discord RPC, same "bring your own
        # credentials" model (register a Last.fm API account, connect
        # via the desktop auth flow). Its own page also keeps every tab
        # short enough to never need a scrollbar.
        w = QWidget()
        v = _tab_layout(w)

        lastfm_group, lf = _new_group(self.tr("Last.fm scrobbling"))

        self.lastfm_enabled_box = QCheckBox(self.tr("Enable Last.fm scrobbling"))
        lf.addRow(self.lastfm_enabled_box)

        self.lastfm_api_key_input = QLineEdit()
        self.lastfm_api_key_input.setPlaceholderText(self.tr("Last.fm API key"))
        self.lastfm_api_key_input.setFixedWidth(_INPUT_WIDE_WIDTH)
        lf.addRow(self.tr("API key:"), self.lastfm_api_key_input)

        self.lastfm_secret_input = QLineEdit()
        self.lastfm_secret_input.setPlaceholderText(self.tr("Last.fm shared secret"))
        self.lastfm_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.lastfm_secret_input.setFixedWidth(_INPUT_WIDE_WIDTH)
        lf.addRow(self.tr("Shared secret:"), self.lastfm_secret_input)

        self.lastfm_status_label = QLabel(self.tr("Not connected"))
        lf.addRow(self.tr("Account:"), self.lastfm_status_label)

        self.lastfm_connect_btn = QPushButton(self.tr("Connect…"))
        self.lastfm_connect_btn.clicked.connect(self._on_lastfm_connect)
        account_btn = QPushButton(self.tr("Create API account"))
        account_btn.clicked.connect(lambda: confirm_and_open(self, API_ACCOUNT_URL))
        lf.addRow(_row_with_buttons(self.lastfm_connect_btn, account_btn))

        self.lastfm_nowplaying_box = QCheckBox(self.tr("Also send a “Now playing” update"))
        lf.addRow(self.lastfm_nowplaying_box)

        lf.addRow(
            _hint(
                self.tr(
                    "Register a free API account, paste the key + secret, then "
                    "Connect to authorize in your browser. Scrobbling runs "
                    "alongside Discord and never replaces it; it pauses "
                    "while Privacy is set to Off. The shared secret and the "
                    "session token are stored in your system keyring, never "
                    "in plain text."
                )
            )
        )
        # Last.fm's API terms ask for a visible link back to Last.fm.
        self.lastfm_attribution = QLabel(
            '<a href="https://www.last.fm">{}</a>'.format(self.tr("Scrobbling via Last.fm"))
        )
        self.lastfm_attribution.linkActivated.connect(lambda url: _open_https_link(QUrl(url)))
        lf.addRow(self.lastfm_attribution)
        v.addWidget(lastfm_group)

        v.addStretch(1)
        return w

    # ====================================================================
    # Sources tab
    # ====================================================================

    def _build_sources_tab(self) -> QWidget:
        w = QWidget()
        v = _tab_layout(w)

        # ---- Apple Music Web group ---------------------------------------
        mpris_group, mf = _new_group(self.tr("Apple Music Web (browser)"))
        self.mpris_box = QCheckBox(self.tr("Enable browser source"))
        mf.addRow(self.mpris_box)

        # User-friendly browser picker: a checkbox per known browser in
        # a 2-column grid + a free-text field below for less-common ones.
        browsers_label = QLabel(self.tr("Detected browsers:"))
        mf.addRow(browsers_label)

        self._browser_checkboxes: dict[str, QCheckBox] = {}
        browser_grid = QGridLayout()
        browser_grid.setHorizontalSpacing(18)
        browser_grid.setVerticalSpacing(4)
        browser_grid.setContentsMargins(0, 0, 0, 0)
        # Two-column grid; labels first, then code in alphabetical-ish
        # order grouped by family (Firefox, Chrome, KDE).
        known = [
            # Firefox family
            ("firefox", "Firefox"),
            ("zen", "Zen Browser"),
            ("librewolf", "LibreWolf"),
            ("floorp", "Floorp"),
            ("waterfox", "Waterfox"),
            ("mullvad-browser", "Mullvad Browser"),
            ("tor-browser", "Tor Browser"),
            # Chromium family
            ("chromium", "Chromium"),
            ("chrome", "Google Chrome"),
            ("brave", "Brave"),
            ("edge", "Microsoft Edge"),
            ("vivaldi", "Vivaldi"),
            ("opera", "Opera"),
            ("ungoogled-chromium", "ungoogled-chromium"),
            # Per-DE bridge
            ("plasma-browser-integration", "Plasma Browser Integration"),
        ]
        for idx, (token, label) in enumerate(known):
            cb = QCheckBox(label)
            self._browser_checkboxes[token] = cb
            browser_grid.addWidget(cb, idx // 2, idx % 2)
        browser_wrap = QWidget()
        browser_wrap.setLayout(browser_grid)
        mf.addRow(browser_wrap)

        self.browser_extra_input = QLineEdit()
        self.browser_extra_input.setFixedWidth(_INPUT_MAX_WIDTH)
        self.browser_extra_input.setPlaceholderText(self.tr("e.g. waterfox, palemoon"))
        mf.addRow(self.tr("Other (comma-separated):"), self.browser_extra_input)
        mf.addRow(
            _hint(
                self.tr(
                    "Refrain only picks up browsers whose process name or desktop "
                    "entry contains one of these substrings. Tick what you use."
                )
            )
        )
        v.addWidget(mpris_group)

        # ---- Bluetooth group ---------------------------------------------
        bt_group, bf = _new_group(self.tr("Bluetooth (AVRCP)"))
        self.bluetooth_box = QCheckBox(self.tr("Enable Bluetooth source"))
        bf.addRow(self.bluetooth_box)

        self.bluetooth_device = QComboBox()
        self.bluetooth_device.setEditable(True)
        self.bluetooth_device.setFixedWidth(_INPUT_MAX_WIDTH)
        refresh_btn = QPushButton(self.tr("Refresh"))
        refresh_btn.clicked.connect(self._populate_bluetooth_devices)
        refresh_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        device_row = QHBoxLayout()
        device_row.setContentsMargins(0, 0, 0, 0)
        device_row.setSpacing(8)
        device_row.addWidget(self.bluetooth_device, 1)
        device_row.addWidget(refresh_btn)
        bf.addRow(self.tr("Device:"), device_row)
        bf.addRow(
            _hint(
                self.tr(
                    "Pick a paired device, or leave on auto-detect to read whichever "
                    "AVRCP-capable source is currently connected."
                )
            )
        )
        self._populate_bluetooth_devices()
        v.addWidget(bt_group)

        v.addStretch(1)
        return w

    def _populate_bluetooth_devices(self) -> None:
        if not self.bluetooth_device.count():
            self.bluetooth_device.addItem(self.tr("(auto-detect)"), userData="")
        worker = _BluetoothDevicesWorker()
        worker.listed.connect(self._fill_bluetooth_devices)
        self._bluetooth_worker: _BluetoothDevicesWorker | None = worker
        threading.Thread(target=worker.run, name="refrain-bt-devices", daemon=True).start()

    def _fill_bluetooth_devices(self, devices: list[dict]) -> None:
        if self.sender() is not self._bluetooth_worker:
            return  # an earlier Refresh answering late
        self._bluetooth_worker = None
        previous = self.bluetooth_device.currentData() if self.bluetooth_device.count() else None
        self.bluetooth_device.clear()
        self.bluetooth_device.addItem(self.tr("(auto-detect)"), userData="")
        for d in devices:
            name = d.get("name") or self.tr("(unknown device)")
            label = f"{name} — {d.get('address', '')}"
            if d.get("connected"):
                label = self.tr("● {label} (connected)").format(label=label)
            self.bluetooth_device.addItem(label, userData=d.get("address", ""))
        if previous:
            for i in range(self.bluetooth_device.count()):
                if self.bluetooth_device.itemData(i) == previous:
                    self.bluetooth_device.setCurrentIndex(i)
                    return
            # Not paired or not reachable right now; keep it selectable so
            # Apply doesn't turn it into auto-detect.
            self.bluetooth_device.addItem(previous, userData=previous)
            self.bluetooth_device.setCurrentIndex(self.bluetooth_device.count() - 1)

    def _format_last_check(self, ts: int) -> str:
        """Render the 'Last checked' timestamp in the active UI locale.

        ``QLocale`` formats the date the way
        every other localised string in the window does, so a German /
        Japanese / etc. UI doesn't show a lone ISO timestamp. ``never``
        is a real translatable string.
        """
        if not ts:
            return self.tr("never")
        dt = QDateTime.fromSecsSinceEpoch(int(ts))
        return QLocale().toString(dt, QLocale.FormatType.ShortFormat)

    # ====================================================================
    # Updates tab
    # ====================================================================

    def _build_updates_tab(self) -> QWidget:
        w = QWidget()
        v = _tab_layout(w)

        update_group, uf = _new_group(self.tr("Update checking"))

        self.auto_check_box = QCheckBox(
            self.tr("Automatically check on startup (max once per day)")
        )
        uf.addRow(self.auto_check_box)

        self.current_version_label = QLabel(__version__)
        uf.addRow(self.tr("Current version:"), self.current_version_label)

        self.latest_version_label = QLabel(self.tr("—"))
        uf.addRow(self.tr("Latest known:"), self.latest_version_label)

        self.last_check_label = QLabel("—")
        uf.addRow(self.tr("Last checked:"), self.last_check_label)

        self._last_check_dt_format = self._format_last_check

        check_btn = QPushButton(self.tr("Check for updates now"))
        check_btn.clicked.connect(self.checkUpdatesRequested.emit)
        uf.addRow(_row_with_buttons(check_btn))

        uf.addRow(
            _hint(
                self.tr(
                    "Refrain queries the GitHub Releases API. Update behavior "
                    "depends on how Refrain was installed (AppImage / pip / "
                    "Flatpak / AUR)."
                )
            )
        )
        v.addWidget(update_group)

        # Inline release-notes pane — same Markdown source as the
        # update-available popup, but always visible here so users can
        # read what's in the latest version without having to click
        # through to GitHub. Populated lazily once the orchestrator's
        # check finishes (set_latest_release).
        # `_new_group` already installs a QFormLayout on the box. Calling
        # `notes_group.setLayout(QVBoxLayout())` on top of that is a no-op
        # — Qt logs "QLayout: Attempting to add QLayout to QGroupBox which
        # already has a layout" and the new layout is discarded, leaving
        # the QTextBrowser parentless. So we add the view to the existing
        # form layout via addRow with a single field instead.
        notes_group, nf = _new_group(self.tr("Latest release notes"))
        self.release_notes_view = QTextBrowser(notes_group)
        self.release_notes_view.setOpenLinks(False)
        self.release_notes_view.anchorClicked.connect(_open_https_link)
        self.release_notes_view.setMarkdown(
            self.tr(
                "_Click_ **Check for updates now** _to fetch the latest changelog from GitHub._"
            )
        )
        self.release_notes_view.setMinimumHeight(180)
        nf.addRow(self.release_notes_view)
        v.addWidget(notes_group, 1)

        return w

    # ====================================================================
    # External hooks for the update orchestrator
    # ====================================================================

    def set_latest_release(self, release: ReleaseInfo | None) -> None:
        """Update the in-tab release-notes pane + the latest-known label.

        Wired from ``app.py`` to ``UpdateOrchestrator.releaseInfoFetched``
        so each check refreshes the inline changelog without making the
        user click through the popup.
        """
        if release is None:
            self.latest_version_label.setText(self.tr("(check failed)"))
            self.release_notes_view.setMarkdown(
                self.tr("_Could not reach GitHub. Check your network and try again._")
            )
            return
        if release.is_newer_than_current:
            self.latest_version_label.setText(
                self.tr("{version} (update available)").format(version=release.version)
            )
        else:
            self.latest_version_label.setText(
                self.tr("{version} (up to date)").format(version=release.version)
            )
        body = release.body or self.tr("_No release notes provided._")
        self.release_notes_view.setMarkdown(prepare_release_notes(body))
        self.last_check_label.setText(self._last_check_dt_format(self._config.update.last_check_ts))

    # ====================================================================
    # Advanced tab
    # ====================================================================

    def _build_advanced_tab(self) -> QWidget:
        w = QWidget()
        v = _tab_layout(w)

        # ---- Performance group -------------------------------------------
        perf_group, pf = _new_group(self.tr("Performance"))
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(250, 10000)
        self.poll_spin.setSingleStep(250)
        self.poll_spin.setSuffix(" ms")
        self.poll_spin.setFixedWidth(_INPUT_MAX_WIDTH)
        pf.addRow(self.tr("Poll interval:"), self.poll_spin)

        self.notify_delay_spin = QSpinBox()
        self.notify_delay_spin.setRange(0, 10000)
        self.notify_delay_spin.setSingleStep(250)
        self.notify_delay_spin.setSuffix(" ms")
        self.notify_delay_spin.setFixedWidth(_INPUT_MAX_WIDTH)
        pf.addRow(self.tr("Notification delay:"), self.notify_delay_spin)
        pf.addRow(
            _hint(
                self.tr(
                    "The poll interval is how often Refrain asks the player what's "
                    "playing: lower reacts faster but uses a little more CPU. The "
                    "notification delay gives the cover time to load."
                )
            )
        )
        v.addWidget(perf_group)

        # ---- Localization group ------------------------------------------
        # Only languages with a complete translation ship in the dropdown —
        # picking a stub language would silently fall back to English source
        # strings. New languages get added here as their .ts files reach
        # full coverage; the .ts stubs live in i18n/ for translator PRs.
        lang_group, lf = _new_group(self.tr("Localization"))
        self.language_combo = QComboBox()
        self.language_combo.setFixedWidth(_INPUT_MAX_WIDTH)
        self.language_combo.addItem(self.tr("System default"), "system")
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Deutsch", "de")
        self.language_combo.addItem("Español", "es")
        self.language_combo.addItem("Français", "fr")
        self.language_combo.addItem("Português", "pt")
        self.language_combo.addItem("Italiano", "it")
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("Polski", "pl")
        self.language_combo.addItem("Nederlands", "nl")
        self.language_combo.addItem("Svenska", "sv")
        self.language_combo.addItem("Čeština", "cs")
        self.language_combo.addItem("Türkçe", "tr")
        self.language_combo.addItem("Українська", "uk")
        self.language_combo.addItem("日本語", "ja")
        self.language_combo.addItem("한국어", "ko")
        self.language_combo.addItem("简体中文", "zh_CN")
        lf.addRow(self.tr("Language:"), self.language_combo)
        lf.addRow(
            _hint(self.tr("A new language needs a restart; Refrain asks before it restarts."))
        )
        v.addWidget(lang_group)

        # ---- Logging group -----------------------------------------------
        log_group, lgf = _new_group(self.tr("Logging"))
        self.log_level_combo = QComboBox()
        self.log_level_combo.setFixedWidth(_INPUT_MAX_WIDTH)
        for text, lvl in (
            (self.tr("Detailed (DEBUG)"), "DEBUG"),
            (self.tr("Normal (INFO)"), "INFO"),
            (self.tr("Warnings only"), "WARNING"),
            (self.tr("Errors only"), "ERROR"),
        ):
            self.log_level_combo.addItem(text, lvl)
        self.log_level_combo.setToolTip(self.tr("Choose Detailed when you report a bug."))
        lgf.addRow(self.tr("Log level:"), self.log_level_combo)

        live_log_btn = QPushButton(self.tr("Open live-log window"))
        live_log_btn.clicked.connect(self.showLogRequested.emit)
        log_folder_btn = QPushButton(self.tr("Open log folder"))
        log_folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(state_dir())))
        )
        lgf.addRow(_row_with_buttons(live_log_btn, log_folder_btn))
        v.addWidget(log_group)
        _align_labels(pf, lf, lgf)

        # ---- Maintenance group -------------------------------------------
        maint_group, mf = _new_group(self.tr("Maintenance"))
        restart_btn = QPushButton(self.tr("Restart Refrain"))
        restart_btn.clicked.connect(self.restartRequested.emit)
        reset_btn = QPushButton(self.tr("Reset all settings to defaults"))
        reset_btn.clicked.connect(self._on_reset_clicked)
        mf.addRow(_row_with_buttons(restart_btn, reset_btn))
        uninstall_btn = QPushButton(self.tr("Uninstall Refrain…"))
        uninstall_btn.clicked.connect(self._on_uninstall_clicked)
        mf.addRow(_row_with_buttons(uninstall_btn))
        mf.addRow(
            _hint(
                self.tr(
                    "Uninstall deletes all Refrain data and the Last.fm "
                    "credentials, then shows the command that removes the "
                    "program. This cannot be undone."
                )
            )
        )
        v.addWidget(maint_group)

        # ---- Developer group (hidden until unlocked) -----------------------
        self.developer_group, df = _new_group(self.tr("Developer"))
        self.developer_mode_box = QCheckBox(self.tr("Developer mode"))
        df.addRow(self.developer_mode_box)
        df.addRow(
            _hint(
                self.tr(
                    "Measures timings and how you use Refrain, on this computer only. "
                    "Nothing is ever sent. The results are in the live-log window."
                )
            )
        )
        self.developer_group.setVisible(False)
        v.addWidget(self.developer_group)

        v.addStretch(1)
        return w

    def _on_version_clicked(self) -> None:
        now = time.monotonic()
        if now - self._last_version_click > DEVELOPER_CLICK_GAP_S:
            self._version_clicks = 0
        self._last_version_click = now
        self._version_clicks += 1
        if self._version_clicks < DEVELOPER_UNLOCK_CLICKS:
            return
        self._version_clicks = 0
        if (
            self._developer_unlocked
            or self._config.advanced.developer_mode
            or not self._confirm_developer_mode()
        ):
            return
        self._developer_unlocked = True
        self.developer_group.setVisible(True)
        self.developer_mode_box.setChecked(True)
        # Saved straight away: the rest of the form may hold edits the
        # user has not applied yet, and those must stay pending.
        c = copy.deepcopy(self._config)
        c.advanced.developer_mode = True
        c.advanced.developer_unlocked = True
        try:
            c.save()
        except OSError as e:
            log.warning("Could not save developer mode: %s", e)
        self._config = c
        self._settle(("advanced", "developer_mode"))
        self.applied.emit(c)

    def _confirm_developer_mode(self) -> bool:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(self.tr("Developer mode"))
        msg.setText(self.tr("Turn on developer mode?"))
        msg.setInformativeText(
            self.tr(
                "Refrain then measures its own timings and how you use it: which "
                "windows, tabs and buttons, and for how long. Never song titles or "
                "anything you type.\n\n"
                "Everything stays on this computer and is never sent anywhere. "
                "Measuring costs a little performance. You can turn it off again "
                "under Advanced."
            )
        )
        on_btn = msg.addButton(self.tr("Turn on"), QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(on_btn)
        msg.exec()
        return msg.clickedButton() is on_btn

    # ====================================================================
    # Discord application name
    # ====================================================================

    def _on_client_id_edited(self, _text: str) -> None:
        """Restart the debounce, and clear a name that no longer applies."""
        self._show_application_name("", "", "")
        self._app_name_timer.start()

    def _on_resolve_app_name_toggled(self, on: bool) -> None:
        if on:
            self._lookup_application_name()
        else:
            self._app_name_timer.stop()
            self._show_application_name("", "", "")

    def _lookup_application_name(self) -> None:
        client_id = self.client_id_input.text().strip()
        if not client_id or not looks_like_application_id(client_id):
            # Either nothing to check, or something we can rule out
            # locally — both answered without spending a request.
            self._show_application_name(client_id, "", "")
            return
        d = self._config.discord
        if d.app_name and cached_name_is_fresh(
            client_id, d.app_name_for_id, d.app_name_checked_ts, time.time()
        ):
            # Known, and known recently enough. Opening Settings should
            # not cost a round-trip for an answer that changes about
            # never — see refrain.discord_app.NAME_TTL_S.
            self._show_application_name(client_id, FOUND, d.app_name)
            return
        if not self.resolve_app_name_box.isChecked():
            # Read from the widget, not the config: the switch should
            # take effect while the user is looking at it, not after
            # Apply.
            self._show_application_name("", "", "")
            return
        if self.privacy_combo.currentData() == "off":
            # Privacy → Off is "do not talk to Discord". Checking a name
            # is a small request, but it is still a request to Discord,
            # and the switch would not mean much if it had exceptions.
            self._show_application_name("", "", "")
            return
        self._show_application_name(client_id, "checking", "")
        worker = _AppNameWorker(client_id)
        worker.resolved.connect(self._on_application_name)
        self._app_name_workers.add(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    def _on_application_name(self, client_id: str, status: str, name: str) -> None:
        self._app_name_workers.discard(self.sender())
        if status == FOUND:
            remember_application_name(self._config, client_id, name)
        if client_id != self.client_id_input.text().strip():
            return  # the field moved on while we were asking
        self._show_application_name(client_id, status, name)

    def _show_application_name(self, client_id: str, status: str, name: str) -> None:
        if status == "checking":
            text, ok = self.tr("Checking…"), False
        else:
            text, ok = application_name_status(client_id, status, name)
        self.app_name_label.setText(text)
        self.app_name_label.setVisible(bool(text))
        # Green only for a confirmed name; everything else keeps the
        # ordinary hint colour rather than shouting in red at someone
        # who is still typing.
        colour = "palette(link)" if ok else "palette(text)"
        self.app_name_label.setStyleSheet(_hint_style(colour))

    # ====================================================================
    # Reset
    # ====================================================================

    def _on_reset_clicked(self) -> None:
        fresh = reset_to_defaults(self._config)
        restart = self._restart_reason(fresh)
        # Build the dialog manually so the action button reads "Reset" /
        # "Zurücksetzen" instead of the generic "Yes" / "Ja".
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle(self.tr("Reset all settings"))
        msg.setText(
            self.tr(
                "Reset every setting to its default? All three Discord "
                "Application IDs (default + per-source), your connected "
                "Last.fm account and your Recently played list stay "
                "untouched — everything else (sources, "
                "privacy, autostart, advanced) goes back to the shipped "
                "defaults.\n\n"
                "The reset is saved right away, and changes you haven't "
                "saved yet are dropped."
            )
        )
        if restart:
            msg.setInformativeText(restart)
        reset_btn = msg.addButton(self.tr("Reset"), QMessageBox.ButtonRole.AcceptRole)
        cancel = msg.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        # Enter must not reset everything by accident.
        msg.setDefaultButton(cancel)
        msg.exec()
        if msg.clickedButton() is not reset_btn:
            return
        self._commit(fresh)
        self._load_into_form()
        self._mark_clean()
        if restart:
            self.restartRequested.emit()

    def reject(self) -> None:
        """Cancel, Esc and the window's close button all end up here."""
        if self.is_dirty():
            choice = self._ask_unsaved()
            dev_metrics.interaction("settings_unsaved", choice=choice)
            if choice == "keep" or (choice == "save" and not self._save_form()):
                return
        self._load_into_form()
        self._mark_clean()
        super().reject()

    def _ask_unsaved(self) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Unsaved changes"))
        box.setText(self.tr("You changed settings that aren't saved yet."))
        save = box.addButton(self.tr("Save"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(self.tr("Discard"), QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton(self.tr("Keep editing"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(keep)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return "save"
        return "discard" if clicked is discard else "keep"

    def use_config(self, config: Config) -> None:
        """Take over settings saved elsewhere, such as by the welcome wizard."""
        self._config = config
        self._load_into_form()
        self._mark_clean()

    def _on_uninstall_clicked(self) -> None:
        # Build the plan from the same core the CLI uses so the dialog
        # and `refrain --uninstall` can never disagree.
        import os

        from refrain.uninstall import collect_paths, removal_command
        from refrain.updater import detect_install_type

        paths = collect_paths()
        cmd = removal_command(detect_install_type(), os.environ.get("APPIMAGE"))
        home = str(Path.home())
        shown = [str(p).replace(home, "~", 1) if str(p).startswith(home) else str(p) for p in paths]
        listing = "\n".join(f"  • {p}" for p in shown) or "  • " + self.tr("(no data files found)")

        msg = QMessageBox(self)
        # Paths have no spaces to wrap at; keep the box wide enough to show them whole.
        msg.setStyleSheet("QLabel#qt_msgbox_label { min-width: 560px; }")
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle(self.tr("Uninstall Refrain"))
        msg.setText(
            self.tr(
                "This permanently deletes all Refrain data and the Last.fm "
                "credentials from your keyring:\n\n{listing}\n  • Last.fm "
                "credentials in the OS keyring\n\nIt does NOT remove the "
                "program itself — afterwards run:\n\n  {cmd}\n\n"
                "Refrain will close. This cannot be undone."
            ).format(listing=listing, cmd=cmd)
        )
        go = msg.addButton(self.tr("Uninstall"), QMessageBox.ButtonRole.DestructiveRole)
        cancel = msg.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(cancel)  # default = Cancel (safe)
        msg.exec()
        if msg.clickedButton() is go:
            self.uninstallRequested.emit()

    # ====================================================================
    # Last.fm connect flow
    # ====================================================================

    def _refresh_lastfm_status(self) -> None:
        state = lastfm_connection_state(
            self._lastfm_session_key,
            self.lastfm_api_key_input.text(),
            self.lastfm_secret_input.text(),
        )
        if state == "connected":
            if self._lastfm_username:
                self.lastfm_status_label.setText(
                    self.tr("Connected as {user}").format(user=self._lastfm_username)
                )
            else:
                self.lastfm_status_label.setText(self.tr("Connected"))
            self.lastfm_connect_btn.setText(self.tr("Disconnect"))
        elif state == "incomplete":
            # A session survived (keyring) but the api_key/secret are
            # missing — scrobbling is inert. Don't claim "Connected";
            # the button reconnects (re-entering the credentials).
            self.lastfm_status_label.setText(
                self.tr("Not connected — re-enter the API key + secret, then Connect")
            )
            self.lastfm_connect_btn.setText(self.tr("Connect…"))
        else:
            self.lastfm_status_label.setText(self.tr("Not connected"))
            self.lastfm_connect_btn.setText(self.tr("Connect…"))
        self.lastfm_connect_btn.setEnabled(True)
        self._update_dirty()

    def _on_lastfm_connect(self) -> None:
        # Fully connected → this button is "Disconnect", saved at once
        # like a connection. An "incomplete" leftover (session in keyring
        # but no usable api_key/secret) falls through to the connect
        # flow instead so the user can re-enter and reconnect.
        if (
            lastfm_connection_state(
                self._lastfm_session_key,
                self.lastfm_api_key_input.text(),
                self.lastfm_secret_input.text(),
            )
            == "connected"
        ):
            if not self._confirm_lastfm_disconnect():
                return
            self._lastfm_session_key = ""
            self._lastfm_username = ""
            self._refresh_lastfm_status()
            self._save_lastfm_now(_LASTFM_ACCOUNT_KEYS, clear=True)
            return
        if self._lastfm_auth_thread is not None:
            return  # an auth round-trip is already in flight
        api_key = self.lastfm_api_key_input.text().strip()
        secret = self.lastfm_secret_input.text().strip()
        if not api_key or not secret:
            QMessageBox.warning(
                self,
                self.tr("Last.fm"),
                self.tr(
                    "Enter your Last.fm API key and shared secret first. "
                    "Use “Create API account” to register one (free)."
                ),
            )
            return
        self._lastfm_client = LastfmClient(api_key, secret)
        self.lastfm_connect_btn.setEnabled(False)
        self.lastfm_status_label.setText(self.tr("Requesting authorization token…"))
        self._start_lastfm_auth("token")

    def _start_lastfm_auth(self, phase: str) -> None:
        assert self._lastfm_client is not None
        self._lastfm_auth_thread = QThread(self)
        self._lastfm_auth_worker = _LastfmAuthWorker(self._lastfm_client, phase, self._lastfm_token)
        self._lastfm_auth_worker.moveToThread(self._lastfm_auth_thread)
        self._lastfm_auth_thread.started.connect(self._lastfm_auth_worker.run)
        self._lastfm_auth_worker.tokenReady.connect(self._on_lastfm_token)
        self._lastfm_auth_worker.sessionReady.connect(self._on_lastfm_session)
        self._lastfm_auth_worker.failed.connect(self._on_lastfm_auth_failed)
        self._lastfm_auth_thread.start()

    def _finish_lastfm_thread(self) -> None:
        if self._lastfm_auth_thread is not None:
            self._lastfm_auth_thread.quit()
            self._lastfm_auth_thread.wait(2000)
            self._lastfm_auth_thread = None
            self._lastfm_auth_worker = None

    def _on_lastfm_token(self, token: str) -> None:
        self._finish_lastfm_thread()
        self._lastfm_token = token
        assert self._lastfm_client is not None
        url = QUrl(self._lastfm_client.authorize_url(token))
        QDesktopServices.openUrl(url)
        dialog = LastfmApprovalDialog(
            self._lastfm_client, token, lambda: QDesktopServices.openUrl(url), self
        )
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        session_key, username, error = dialog.session_key, dialog.username, dialog.error
        dialog.deleteLater()
        if accepted:
            self._on_lastfm_session(session_key, username)
            return
        self._lastfm_token = ""
        self._refresh_lastfm_status()
        if error:
            QMessageBox.warning(self, self.tr("Last.fm connection failed"), error)

    def _on_lastfm_session(self, key: str, name: str) -> None:
        self._finish_lastfm_thread()
        self._lastfm_token = ""
        self._lastfm_session_key = key
        self._lastfm_username = name
        # A connection is a result, not a draft: it is saved at once and
        # switches scrobbling on, so Cancel cannot lose it.
        self.lastfm_enabled_box.setChecked(True)
        self._refresh_lastfm_status()
        self._save_lastfm_now(
            (
                ("lastfm", "enabled"),
                ("lastfm", "api_key"),
                ("lastfm", "shared_secret"),
                *_LASTFM_ACCOUNT_KEYS,
            )
        )
        if name:
            done = self.tr("Connected as {user}. Scrobbling starts with the next song.").format(
                user=name
            )
        else:
            done = self.tr("Connected. Scrobbling starts with the next song.")
        if self._config.privacy.mode == "off":
            done += " " + self.tr("It stays paused while Privacy is set to Off.")
        QMessageBox.information(self, self.tr("Last.fm"), done)

    def _save_lastfm_now(self, keys, *, clear: bool = False) -> None:
        """Save these Last.fm fields at once; other edits stay pending."""
        values = self._form_values()
        c = copy.deepcopy(self._config)
        for section, key in keys:
            setattr(getattr(c, section), key, values[section, key])
        self._commit(c, clear_lastfm=clear)
        self._settle(*keys)

    def _on_lastfm_auth_failed(self, message: str, _code: int = -1) -> None:
        self._finish_lastfm_thread()
        self._lastfm_token = ""
        self._refresh_lastfm_status()
        QMessageBox.warning(
            self,
            self.tr("Last.fm connection failed"),
            self.tr("Could not connect to Last.fm:\n\n{error}").format(error=message),
        )

    # ====================================================================
    # Form load + save
    # ====================================================================

    def _show_legal(self) -> None:
        """Open the legal notice. Imported lazily — it is a rarely used
        dialog and there is no reason to build it on every settings open."""
        from refrain.ui.legal_dialog import LegalDialog

        dialog = LegalDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def _load_into_form(self) -> None:
        c = self._config
        # setText fires textChanged, which starts the debounce — so an
        # already-configured ID gets confirmed on open without a special
        # case, and a window opened on an empty field asks nothing.
        self.client_id_input.setText(c.discord.client_id)
        self.client_id_mpris_input.setText(c.discord.client_id_mpris)
        self.client_id_bluetooth_input.setText(c.discord.client_id_bluetooth)
        # Reveal the per-source override fields only if the user already
        # has one configured — otherwise keep the advanced toggle off
        # and the rows hidden so the tab stays uncluttered.
        has_overrides = bool(c.discord.client_id_mpris or c.discord.client_id_bluetooth)
        self.discord_per_source_box.setChecked(has_overrides)
        self.resolve_app_name_box.setChecked(c.discord.resolve_app_name)
        self.discord_all_clients_box.setChecked(c.discord.all_clients)
        self._set_discord_overrides_visible(has_overrides)
        self.autostart_box.setChecked(c.behavior.autostart)
        self.notifications_box.setChecked(c.behavior.notifications)
        self.cover_art_box.setChecked(c.behavior.cover_art)
        self.buttons_box.setChecked(c.behavior.show_buttons)
        self.history_box.setChecked(c.history.enabled)
        self._on_history_toggled(c.history.enabled)
        self._select_history_limit(c.history.max_entries)

        self.lastfm_enabled_box.setChecked(c.lastfm.enabled)
        self.lastfm_api_key_input.setText(c.lastfm.api_key)
        self.lastfm_secret_input.setText(c.lastfm.shared_secret)
        # Show the start: scrolled to the end, the first dot is cut in half.
        self.lastfm_secret_input.setCursorPosition(0)
        self.lastfm_nowplaying_box.setChecked(c.lastfm.scrobble_now_playing)
        self._lastfm_session_key = c.lastfm.session_key
        self._lastfm_username = c.lastfm.username
        self._refresh_lastfm_status()

        self.auto_check_box.setChecked(c.update.auto_check)
        self.last_check_label.setText(self._last_check_dt_format(c.update.last_check_ts))

        self.mpris_box.setChecked(c.sources.mpris_enabled)
        self.bluetooth_box.setChecked(c.sources.bluetooth_enabled)
        # Split persisted browser_hints (comma-sep string) into known
        # checkboxes + everything-else into the extra free-text field.
        existing = {h.strip().lower() for h in c.sources.browser_hints.split(",") if h.strip()}
        for token, cb in self._browser_checkboxes.items():
            cb.setChecked(token in existing)
        extras = sorted(existing - set(self._browser_checkboxes))
        self.browser_extra_input.setText(",".join(extras))

        if c.sources.bluetooth_device:
            matched = False
            for i in range(self.bluetooth_device.count()):
                if self.bluetooth_device.itemData(i) == c.sources.bluetooth_device:
                    self.bluetooth_device.setCurrentIndex(i)
                    matched = True
                    break
            if not matched:
                # Not connected right now; keep it selectable so Apply
                # doesn't turn it into auto-detect.
                self.bluetooth_device.addItem(
                    c.sources.bluetooth_device, userData=c.sources.bluetooth_device
                )
                self.bluetooth_device.setCurrentIndex(self.bluetooth_device.count() - 1)
        else:
            self.bluetooth_device.setCurrentIndex(0)

        for i in range(self.privacy_combo.count()):
            if self.privacy_combo.itemData(i) == c.privacy.mode:
                self.privacy_combo.setCurrentIndex(i)
                break

        self.poll_spin.setValue(c.advanced.poll_interval_ms)
        self.notify_delay_spin.setValue(c.behavior.notify_delay_ms)
        for i in range(self.log_level_combo.count()):
            if self.log_level_combo.itemData(i) == c.advanced.log_level:
                self.log_level_combo.setCurrentIndex(i)
                break

        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == c.advanced.language:
                self.language_combo.setCurrentIndex(i)
                break

        self._developer_unlocked = (
            self._developer_unlocked or c.advanced.developer_mode or c.advanced.developer_unlocked
        )
        self.developer_mode_box.setChecked(c.advanced.developer_mode)
        self.developer_group.setVisible(self._developer_unlocked)

    def _form_values(self) -> dict[tuple[str, str], object]:
        """Every setting the form edits, as a save would store it.

        ``None`` keeps the saved value. Also the yardstick for unsaved
        changes, so a hand-edited file that the form merely tidies does
        not count as a change.
        """
        # The advanced toggle is the per-source feature switch: when it's
        # off, the overrides are cleared so the single default ID is used
        # everywhere and the hidden field contents can't linger.
        per_source = self.discord_per_source_box.isChecked()
        # Known browsers in display order first, then the typed extras.
        picked = [t for t, cb in self._browser_checkboxes.items() if cb.isChecked()]
        extra_text = self.browser_extra_input.text().strip()
        if extra_text:
            extras = [e.strip().lower() for e in extra_text.split(",") if e.strip()]
            picked.extend(e for e in extras if e not in picked)

        index = self.bluetooth_device.currentIndex()
        text = self.bluetooth_device.currentText().strip()
        if index >= 0 and text == self.bluetooth_device.itemText(index).strip():
            bt_device = self.bluetooth_device.itemData(index) or ""
        else:
            bt_device = "" if text in ("", "(auto-detect)", self.tr("(auto-detect)")) else text

        return {
            # An empty field turns Discord off, so it must not fall back.
            ("discord", "client_id"): self.client_id_input.text().strip(),
            ("discord", "client_id_mpris"): (
                self.client_id_mpris_input.text().strip() if per_source else ""
            ),
            ("discord", "client_id_bluetooth"): (
                self.client_id_bluetooth_input.text().strip() if per_source else ""
            ),
            ("discord", "resolve_app_name"): self.resolve_app_name_box.isChecked(),
            ("discord", "all_clients"): self.discord_all_clients_box.isChecked(),
            ("behavior", "autostart"): self.autostart_box.isChecked(),
            ("behavior", "notifications"): self.notifications_box.isChecked(),
            ("behavior", "cover_art"): self.cover_art_box.isChecked(),
            ("behavior", "show_buttons"): self.buttons_box.isChecked(),
            ("behavior", "notify_delay_ms"): self.notify_delay_spin.value(),
            ("lastfm", "enabled"): self.lastfm_enabled_box.isChecked(),
            ("lastfm", "api_key"): self.lastfm_api_key_input.text().strip(),
            ("lastfm", "shared_secret"): self.lastfm_secret_input.text().strip(),
            ("lastfm", "scrobble_now_playing"): self.lastfm_nowplaying_box.isChecked(),
            # From the connect flow, not a widget.
            ("lastfm", "session_key"): self._lastfm_session_key,
            ("lastfm", "username"): self._lastfm_username,
            ("update", "auto_check"): self.auto_check_box.isChecked(),
            ("sources", "mpris_enabled"): self.mpris_box.isChecked(),
            ("sources", "bluetooth_enabled"): self.bluetooth_box.isChecked(),
            ("sources", "browser_hints"): ",".join(picked) if picked else None,
            ("sources", "bluetooth_device"): bt_device,
            ("privacy", "mode"): self.privacy_combo.currentData() or "full",
            ("advanced", "poll_interval_ms"): self.poll_spin.value(),
            ("advanced", "log_level"): self.log_level_combo.currentData() or "INFO",
            ("advanced", "language"): self.language_combo.currentData() or "system",
            ("advanced", "developer_mode"): self.developer_mode_box.isChecked(),
            ("history", "enabled"): self.history_box.isChecked(),
            ("history", "max_entries"): self.history_limit_combo.currentData() or None,
        }

    def _config_from_form(self) -> Config:
        # A new object, so everyone holding the old one can tell what changed.
        c = copy.deepcopy(self._config)
        for (section, key), value in self._form_values().items():
            if value is not None:
                setattr(getattr(c, section), key, value)
        c.advanced.developer_unlocked = c.advanced.developer_unlocked or self._developer_unlocked
        # What *Resume sharing* goes back to: whatever level was last chosen here.
        if c.privacy.mode != "off":
            c.privacy.resume_mode = c.privacy.mode
        return c

    def is_dirty(self) -> bool:
        return self._baseline is not None and self._form_values() != self._baseline

    def _mark_clean(self) -> None:
        self._baseline = self._form_values()
        self._update_dirty()

    def _settle(self, *keys: tuple[str, str]) -> None:
        """Count these fields as saved; the rest of the form stays as it is."""
        if self._baseline is not None:
            now = self._form_values()
            for key in keys:
                self._baseline[key] = now[key]
        self._update_dirty()

    def _update_dirty(self, *_args) -> None:
        if self._baseline is not None:
            self.apply_btn.setEnabled(self.is_dirty())

    def _watch_form(self) -> None:
        # Spin boxes and editable combos hold a QLineEdit, so their typing
        # is covered by the line-edit loop.
        for box in self.tabs.findChildren(QCheckBox):
            box.toggled.connect(self._update_dirty)
        for edit in self.tabs.findChildren(QLineEdit):
            edit.textChanged.connect(self._update_dirty)
        for combo in self.tabs.findChildren(QComboBox):
            combo.currentIndexChanged.connect(self._update_dirty)
        for spin in self.tabs.findChildren(QSpinBox):
            spin.valueChanged.connect(self._update_dirty)

    def _restart_reason(self, c: Config) -> str:
        # The translator is installed once at startup; a new Application ID
        # reconnects without one.
        if c.advanced.language != self._config.advanced.language:
            return self.tr("Refrain restarts to apply the new language.")
        return ""

    def _confirm_restart(self, reason: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self.tr("Restart Refrain"))
        box.setText(reason)
        go = box.addButton(self.tr("Save and restart"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        return box.clickedButton() is go

    def _on_apply_clicked(self) -> None:
        self._save_form()

    def _on_ok_clicked(self) -> None:
        if self._save_form():
            self.accept()

    def _save_form(self) -> bool:
        """Save what the form shows. False when the user backed out of a question."""
        # Asked before anything is built, so Cancel leaves both the config
        # and the open form exactly as they were.
        if (
            self._config.history.enabled
            and not self.history_box.isChecked()
            and not self._confirm_history_off()
        ):
            return False
        lastfm_state = lastfm_connection_state(
            self._lastfm_session_key,
            self.lastfm_api_key_input.text(),
            self.lastfm_secret_input.text(),
        )
        if self.lastfm_enabled_box.isChecked():
            if lastfm_state != "connected" and not self._confirm_lastfm_unconnected():
                return False
        elif (
            lastfm_state == "connected"
            # Asked once: a saved "connected but off" was a choice.
            and (self._config.lastfm.enabled or not self._config.lastfm.session_key)
            and self._ask_lastfm_turn_on()
        ):
            self.lastfm_enabled_box.setChecked(True)
        c = self._config_from_form()
        restart = self._restart_reason(c)
        if restart and not self._confirm_restart(restart):
            return False
        self._commit(c)
        self._mark_clean()
        if restart:
            log.info("Settings changed the language; restarting")
            self.restartRequested.emit()
        return True

    def _commit(self, c: Config, *, clear_lastfm: bool = False) -> None:
        try:
            c.save()
        except OSError as e:
            # The daemon still gets the new settings below; only the file
            # is stale, and the user has to know that.
            log.exception("Could not save config")
            QMessageBox.critical(
                self,
                self.tr("Could not save settings"),
                self.tr(
                    "Refrain could not write to {path}:\n\n{error}\n\n"
                    "The settings you just changed will apply for this "
                    "session but won't persist across a restart."
                ).format(path=config_path(), error=e),
            )
        # The Last.fm secrets go to the OS keyring, not config.toml, and
        # separately, so one failing does not take the other with it.
        from refrain.secrets_store import save_from as _save_lastfm_secrets

        if not _save_lastfm_secrets(c.lastfm, clear_missing=clear_lastfm):
            QMessageBox.warning(
                self,
                self.tr("Could not store Last.fm credentials"),
                self.tr(
                    "Refrain could not store your Last.fm credentials, neither in "
                    "the system keyring nor in {path}.\n\n"
                    "Scrobbling works for this session, but you will have to "
                    "connect Last.fm again after a restart."
                ).format(path=config_path().parent),
            )
        self._config = c
        self.applied.emit(c)

    def done(self, result: int) -> None:
        # Every way out lands here, unlike closeEvent, which OK, Cancel and
        # Esc skip. A Last.fm request left running opens a browser tab and a
        # dialog minutes after the window is gone.
        self._finish_lastfm_thread()
        self._app_name_timer.stop()
        super().done(result)
