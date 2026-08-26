"""Refrain settings window — opens on launch, hides on Apply."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import (
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
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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

from refrain import __version__
from refrain.config import Config
from refrain.discord_app import (
    FOUND,
    UNKNOWN_ID,
    cached_name_is_fresh,
    fetch_application_name,
    looks_like_application_id,
    remember_application_name,
)
from refrain.paths import assets_dir, config_path, state_dir
from refrain.scrobble import API_ACCOUNT_URL, LastfmClient, LastfmError
from refrain.sources.bluetooth import BluetoothSource
from refrain.ui.cursors import apply_interactive_cursors
from refrain.updater import ReleaseInfo, prepare_release_notes

GITHUB_URL = "https://github.com/Rockykln/refrain"

log = logging.getLogger(__name__)


def application_name_status(client_id: str, status: str, name: str) -> tuple[str, bool]:
    """What the line under the Client ID should say, and whether it's good news.

    Returns ``(text, ok)``. ``ok`` drives nothing but the colour: a green
    line means Discord confirmed the ID and told us what it is called.

    The four states a user can actually be in:

    - No ID at all. Say nothing — an empty field is not an error, it is
      the default, and Refrain runs perfectly well without Discord.
    - An ID that cannot be a snowflake. Local knowledge, so say so
      without asking Discord.
    - Discord has no such application. This is the one the feature
      exists for: a mistyped ID used to fail completely silently, with
      the status simply never appearing and nothing to point at.
    - We could not ask. Distinct from the above, because the user can do
      nothing about it and should not go re-checking a correct ID.

    Pure, and separate from the widget, so all four can be tested
    without a Qt event loop.
    """
    client_id = client_id.strip()
    if not client_id:
        return "", False
    if not looks_like_application_id(client_id):
        return QCoreApplication.translate("SettingsWindow", "Expected 17-20 digits"), False
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
      start, which is exactly what it used to do.
    """
    fresh = Config()
    fresh.discord = current.discord
    fresh.lastfm = current.lastfm
    fresh.behavior.first_run_complete = current.behavior.first_run_complete
    return fresh


def lastfm_connection_state(session_key: str, api_key: str, shared_secret: str) -> str:
    """Whether the Last.fm connection is actually *usable*.

    Pure (no Qt) so it's unit-testable. A Last.fm call needs all three
    of api_key + shared_secret + session_key — the secret/session live
    in the keyring, the api_key in config.toml, so they can desync
    (fresh config but a surviving keyring entry, or vice-versa). Status
    keyed on session_key alone used to show "Connected" for an
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
_TAB_SPACING = 14
# Padding inside each QGroupBox content area.
_GROUP_MARGINS = (14, 18, 14, 12)
# Horizontal / vertical gap between form rows inside a group.
_FORM_HSPACING = 12
_FORM_VSPACING = 8
# Fixed width for inputs (combos, line edits, spinboxes). Combined with
# FieldsStayAtSizeHint the form layout will not grow them past this — on
# Plasma Breeze, AllNonFixedFieldsGrow ignored maxWidth caps and stretched
# widgets to ~440 px even with setFixedWidth set. FieldsStayAtSizeHint +
# explicit per-widget setFixedWidth is the only combo that holds across
# Fusion (offscreen tests) and Breeze (Plasma).
_INPUT_MAX_WIDTH = 220
# Wider variant for inputs whose placeholder / longest item text doesn't
# fit in 220 — used for the Discord group (Client ID placeholder + the
# longest privacy-mode label "Full — title, artist, album, cover" both
# need ~340 px to render without truncation). Keep both inputs in a
# group at the same width so they line up vertically.
_INPUT_WIDE_WIDTH = 360


def _hint(text: str) -> QLabel:
    """Italic, wrapped helper text under form rows.

    Uses ``palette(text)`` rather than ``palette(mid)`` because the
    latter renders almost-invisibly on Plasma Breeze Dark — the user
    couldn't read hint lines like the "Last checked" timestamp under
    Updates → Check for updates.
    """
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: palette(text); font-style: italic;")
    return lbl


def _row_with_buttons(*buttons: QPushButton) -> QHBoxLayout:
    """A horizontal layout that keeps buttons at their natural width
    and pushes them all to the left with a trailing stretch."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    for b in buttons:
        b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
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

    Every tab stacks fixed-height QGroupBoxes; with enough groups (the
    General tab now carries Discord + Last.fm + Notifications +
    Behavior) — and especially with the ~30 %-longer German strings —
    the content is taller than the dialog. Without a scroll area Qt
    crushes every group below its sizeHint and the form rows overlap
    ("the first page is all broken"). ``setWidgetResizable(True)`` keeps
    the page at the viewport width (inputs stay laid out; only a
    vertical scrollbar appears, and only when actually needed), so this
    is inert on tabs that already fit.
    """
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
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
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    # FieldsStayAtSizeHint keeps every input at its sizeHint (or
    # explicit setFixedWidth) and refuses to grow it. This is critical
    # on Plasma Breeze: AllNonFixedFieldsGrow ignored fixed-width caps
    # there and stretched spinboxes/combos to ~440 px, leaving huge
    # empty space between the value and the chevron chrome. With
    # FieldsStayAtSizeHint + per-widget setFixedWidth(_INPUT_MAX_WIDTH),
    # every input renders at exactly 220 logical px on every Qt style.
    form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return box, form


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


class _LastfmAuthWorker(QObject):
    """Runs one Last.fm auth network call off the GUI thread.

    The desktop flow is two calls with a browser round-trip between
    them, so a worker handles a single ``phase`` ("token" or "session")
    and is torn down before the next one — no thread lives across the
    user's browser interaction.
    """

    tokenReady = Signal(str)  # request token
    sessionReady = Signal(str, str)  # (session_key, username)
    failed = Signal(str)  # human-readable error

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
            self.failed.emit(str(e))
        except Exception as e:  # never let a worker exception escape the thread
            self.failed.emit(f"Unexpected Last.fm error: {e}")


class SettingsWindow(QDialog):
    """Tabbed settings dialog. Emits `applied(Config)` when the user hits Apply."""

    applied = Signal(object)
    checkUpdatesRequested = Signal()
    showLogRequested = Signal()
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
        # Default size is tuned so the tallest tab (Sources) fits with
        # no visible scrollbar in both English *and* German (DE strings
        # run ~30% longer; verified the Sources page needs ≤ 680 px of
        # window height in DE). The per-tab scroll area is only a silent
        # safety net for even-longer locales / very small screens — it
        # shows no bar at this size. Min stays lower so the window is
        # still resizable (the safety net then engages instead of
        # crushing the form rows).
        self.setMinimumSize(680, 600)
        self.resize(720, 700)
        self._config = config

        # Last.fm session/username aren't form widgets — they're set by
        # the connect flow and persisted on Apply. Auth network calls run
        # on a short-lived worker thread; refs kept so it's joined before
        # the next phase / dialog close.
        self._lastfm_session_key = ""
        self._lastfm_username = ""
        self._lastfm_token = ""
        # True only after the user actually pressed Disconnect. An empty
        # session key otherwise means "we never managed to load it", and
        # must not wipe the stored credentials — see secrets_store.save_from.
        self._lastfm_disconnect_requested = False
        self._lastfm_client: LastfmClient | None = None
        self._lastfm_auth_thread: QThread | None = None
        self._lastfm_auth_worker: _LastfmAuthWorker | None = None

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(_scroll_wrap(self._build_general_tab()), self.tr("General"))
        self.tabs.addTab(_scroll_wrap(self._build_sources_tab()), self.tr("Sources"))
        self.tabs.addTab(_scroll_wrap(self._build_lastfm_tab()), self.tr("Last.fm"))
        self.tabs.addTab(_scroll_wrap(self._build_updates_tab()), self.tr("Updates"))
        self.tabs.addTab(_scroll_wrap(self._build_advanced_tab()), self.tr("Advanced"))

        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.apply_btn = QPushButton(self.tr("Apply"))
        self.apply_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        self.apply_btn.clicked.connect(self._on_apply_clicked)

        button_row = QHBoxLayout()
        button_row.addStretch()
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.apply_btn)

        version_label = QLabel(f"Refrain v{__version__}")
        # palette(text) follows the theme — `gray` was unreadable on
        # Plasma Breeze Dark.
        version_label.setStyleSheet("color: palette(text);")

        github_btn = QToolButton()
        github_btn.setIcon(QIcon(str(assets_dir() / "icons" / "github-mark.svg")))
        github_btn.setIconSize(QSize(16, 16))
        github_btn.setAutoRaise(True)
        github_btn.setToolTip(self.tr("View Refrain on GitHub"))
        github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))

        legal_btn = QPushButton(self.tr("Legal"))
        legal_btn.setFlat(True)
        legal_btn.setToolTip(self.tr("Licence, trademark and affiliation notices"))
        legal_btn.clicked.connect(self._show_legal)

        version_row = QHBoxLayout()
        version_row.setContentsMargins(0, 0, 0, 0)
        version_row.addStretch()
        version_row.addWidget(legal_btn)
        version_row.addWidget(version_label)
        version_row.addWidget(github_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addLayout(button_row)
        layout.addLayout(version_row)

        self._load_into_form()

        # Every clickable child gets the pointing hand, in one place —
        # see refrain.ui.cursors.
        apply_interactive_cursors(self)

    # ====================================================================
    # General tab
    # ====================================================================

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        v = _tab_layout(w)

        # ---- Discord group ------------------------------------------------
        # Discord inputs use the wider variant: the Client ID placeholder
        # ("Discord Application Client ID") and the longest privacy label
        # ("Full — title, artist, album, cover") both need ~340 px to
        # render without truncation. Both at the same width so they
        # line up vertically.
        discord_group, df = _new_group(self.tr("Discord"))
        self._discord_form = df

        self.client_id_input = QLineEdit()
        self.client_id_input.setPlaceholderText(self.tr("Discord Application Client ID"))
        self.client_id_input.setFixedWidth(_INPUT_WIDE_WIDTH)

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
        self.app_name_label = _hint("")
        self.app_name_label.setWordWrap(False)
        self.app_name_label.setVisible(False)
        id_row = QHBoxLayout()
        id_row.setContentsMargins(0, 0, 0, 0)
        id_row.setSpacing(8)
        id_row.addWidget(self.client_id_input)
        id_row.addWidget(self.app_name_label, 1)
        df.addRow(self.tr("Client ID:"), id_row)

        # Typing nineteen digits would otherwise be nineteen requests.
        # The pause is long enough to mean "done typing" and short
        # enough that a paste feels immediate.
        self._app_name_timer = QTimer(self)
        self._app_name_timer.setSingleShot(True)
        self._app_name_timer.setInterval(600)
        self._app_name_timer.timeout.connect(self._lookup_application_name)
        self.client_id_input.textChanged.connect(self._on_client_id_edited)
        self._app_name_thread: QThread | None = None
        self._app_name_worker: _AppNameWorker | None = None

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
        # it reveals separate Client IDs for Apple Music vs Bluetooth —
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
                "Discord and Vencord/Vesktop are separate programs with "
                "separate connections, so a status sent to one does not show "
                "in the other. With this on, Refrain publishes to all of them."
            )
        )
        df.addRow(self.discord_all_clients_box)

        self.client_id_mpris_input = QLineEdit()
        self.client_id_mpris_input.setPlaceholderText(self.tr("(uses default Client ID)"))
        self.client_id_mpris_input.setFixedWidth(_INPUT_WIDE_WIDTH)
        df.addRow(self.tr("Apple Music Client ID:"), self.client_id_mpris_input)

        self.client_id_bluetooth_input = QLineEdit()
        self.client_id_bluetooth_input.setPlaceholderText(self.tr("(uses default Client ID)"))
        self.client_id_bluetooth_input.setFixedWidth(_INPUT_WIDE_WIDTH)
        df.addRow(self.tr("Bluetooth Client ID:"), self.client_id_bluetooth_input)
        # Hidden until the advanced toggle is ticked (or a saved
        # override is loaded — see _load_into_form).
        self._set_discord_overrides_visible(False)

        self.privacy_combo = QComboBox()
        self.privacy_combo.setFixedWidth(_INPUT_WIDE_WIDTH)
        self.privacy_combo.addItem(self.tr("Full — title, artist, album, cover"), "full")
        self.privacy_combo.addItem(self.tr("Minimal — only 'Listening to music'"), "minimal")
        self.privacy_combo.addItem(self.tr("Off — disable Discord status entirely"), "off")
        df.addRow(self.tr("Privacy:"), self.privacy_combo)

        self.buttons_box = QCheckBox(self.tr("Show 'Listen on Apple Music' button in Discord"))
        df.addRow(self.buttons_box)

        # Portal button right under the checkbox — keeps "open the
        # external page where you'd register an application" close to
        # the Client ID field it feeds. Hint sits below as helper text.
        portal_btn = QPushButton(self.tr("Open Discord Developer Portal"))
        portal_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://discord.com/developers/applications"))
        )
        df.addRow(_row_with_buttons(portal_btn))

        client_hint = _hint(
            self.tr(
                "Register a free Discord Application to get a Client ID. "
                "The application's name is what shows up next to "
                '"Listening to" in your Discord status.'
            )
        )
        df.addRow(client_hint)

        v.addWidget(discord_group)

        # ---- Notifications group -----------------------------------------
        notif_group, nf = _new_group(self.tr("Notifications"))
        self.notifications_box = QCheckBox(self.tr("Show desktop notification on track change"))
        nf.addRow(self.notifications_box)
        self.cover_art_box = QCheckBox(self.tr("Fetch album cover art from iTunes"))
        nf.addRow(self.cover_art_box)
        v.addWidget(notif_group)

        # ---- Behavior group ----------------------------------------------
        behavior_group, bf = _new_group(self.tr("Behavior"))
        self.autostart_box = QCheckBox(self.tr("Start Refrain automatically on login"))
        bf.addRow(self.autostart_box)
        v.addWidget(behavior_group)

        v.addStretch(1)
        return w

    def _set_discord_overrides_visible(self, visible: bool) -> None:
        """Show/hide the per-source Client ID rows (label + field).

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
        account_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_ACCOUNT_URL)))
        lf.addRow(_row_with_buttons(self.lastfm_connect_btn, account_btn))

        self.lastfm_nowplaying_box = QCheckBox(self.tr("Also send a “Now playing” update"))
        lf.addRow(self.lastfm_nowplaying_box)

        lf.addRow(
            _hint(
                self.tr(
                    "Register a free API account, paste the key + secret, then "
                    "Connect to authorise in your browser. Scrobbling runs "
                    "alongside Discord and never replaces it; it's silenced "
                    "while Privacy is set to Off. The shared secret and the "
                    "session token are stored in your system keyring, never "
                    "in plain text."
                )
            )
        )
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
        # Replaces the old comma-separated text input which was opaque.
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
        refresh_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
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
        previous = self.bluetooth_device.currentData() if self.bluetooth_device.count() else None
        self.bluetooth_device.clear()
        self.bluetooth_device.addItem(self.tr("(auto-detect)"), userData="")
        for d in BluetoothSource.list_paired_devices():
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

    def _format_last_check(self, ts: int) -> str:
        """Render the 'Last checked' timestamp in the active UI locale.

        Was a hard-coded ``%Y-%m-%d %H:%M:%S`` strftime, which ignored
        the user's chosen language. ``QLocale`` formats the date the way
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
        self.release_notes_view.setOpenExternalLinks(True)
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

        self.cover_cache_spin = QSpinBox()
        self.cover_cache_spin.setRange(10, 5000)
        self.cover_cache_spin.setSingleStep(50)
        self.cover_cache_spin.setSuffix(self.tr(" covers"))
        self.cover_cache_spin.setFixedWidth(_INPUT_MAX_WIDTH)
        pf.addRow(self.tr("Cover cache size:"), self.cover_cache_spin)
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
        self.language_combo.addItem("日本語", "ja")
        self.language_combo.addItem("简体中文", "zh_CN")
        lf.addRow(self.tr("Language:"), self.language_combo)
        lf.addRow(_hint(self.tr("Refrain restarts automatically after changing the language.")))
        v.addWidget(lang_group)

        # ---- Logging group -----------------------------------------------
        log_group, lgf = _new_group(self.tr("Logging"))
        self.log_level_combo = QComboBox()
        self.log_level_combo.setFixedWidth(_INPUT_MAX_WIDTH)
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(lvl, lvl)
        lgf.addRow(self.tr("Log level:"), self.log_level_combo)

        live_log_btn = QPushButton(self.tr("Open live-log window"))
        live_log_btn.clicked.connect(self.showLogRequested.emit)
        log_folder_btn = QPushButton(self.tr("Open log folder"))
        log_folder_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(state_dir())))
        )
        lgf.addRow(_row_with_buttons(live_log_btn, log_folder_btn))
        v.addWidget(log_group)

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
                    "Deletes all Refrain data (config, logs, cache, autostart, "
                    "menu entry) and the Last.fm credentials from your keyring, "
                    "then tells you the one command to remove the program "
                    "itself. This cannot be undone."
                )
            )
        )
        v.addWidget(maint_group)

        v.addStretch(1)
        return w

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
        if self._config.privacy.mode == "off":
            # Privacy → Off is "do not talk to Discord". Checking a name
            # is a small request, but it is still a request to Discord,
            # and the switch would not mean much if it had exceptions.
            self._show_application_name("", "", "")
            return
        self._show_application_name(client_id, "checking", "")
        self._finish_app_name_thread()
        self._app_name_thread = QThread(self)
        self._app_name_worker = _AppNameWorker(client_id)
        self._app_name_worker.moveToThread(self._app_name_thread)
        self._app_name_thread.started.connect(self._app_name_worker.run)
        self._app_name_worker.resolved.connect(self._on_application_name)
        self._app_name_thread.start()

    def _on_application_name(self, client_id: str, status: str, name: str) -> None:
        self._finish_app_name_thread()
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
        self.app_name_label.setStyleSheet(f"color: {colour}; font-style: italic;")

    def _finish_app_name_thread(self) -> None:
        if self._app_name_thread is not None:
            self._app_name_thread.quit()
            self._app_name_thread.wait(2000)
            self._app_name_thread.deleteLater()
        self._app_name_thread = None
        self._app_name_worker = None

    # ====================================================================
    # Reset
    # ====================================================================

    def _on_reset_clicked(self) -> None:
        # Build the dialog manually so the action button reads "Reset" /
        # "Zurücksetzen" instead of the generic "Yes" / "Ja". The
        # standard Yes/No buttons confused the body text — it tells
        # the user to confirm a *reset* but the button labels said yes.
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle(self.tr("Reset all settings"))
        msg.setText(
            self.tr(
                "Reset every setting to its default? All three Discord "
                "Application IDs (default + per-source) and your connected "
                "Last.fm account stay untouched — everything else (sources, "
                "privacy, autostart, advanced) goes back to the shipped "
                "defaults.\n\n"
                "After confirming, click Apply at the bottom of the "
                "Settings window to save the reset."
            )
        )
        reset_btn = msg.addButton(self.tr("Reset"), QMessageBox.AcceptRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
        msg.setDefaultButton(reset_btn)
        msg.exec()
        if msg.clickedButton() is not reset_btn:
            return
        self._config = reset_to_defaults(self._config)
        self._load_into_form()

    def _on_uninstall_clicked(self) -> None:
        # Build the plan from the same core the CLI uses so the dialog
        # and `refrain --uninstall` can never disagree.
        import os

        from refrain.uninstall import collect_paths, removal_command
        from refrain.updater import detect_install_type

        paths = collect_paths()
        cmd = removal_command(detect_install_type(), os.environ.get("APPIMAGE"))
        listing = "\n".join(f"  • {p}" for p in paths) or "  • (no data files found)"

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
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
        go = msg.addButton(self.tr("Uninstall"), QMessageBox.DestructiveRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
        msg.setDefaultButton(msg.buttons()[-1])  # default = Cancel (safe)
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

    def _on_lastfm_connect(self) -> None:
        # Fully connected → this button is "Disconnect". Clearing is
        # local; it persists when the user hits Apply (same as every
        # other field). An "incomplete" leftover (session in keyring
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
            self._lastfm_session_key = ""
            self._lastfm_username = ""
            self._lastfm_disconnect_requested = True
            self._refresh_lastfm_status()
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
        self.lastfm_status_label.setText(self.tr("Requesting authorisation token…"))
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
        QDesktopServices.openUrl(QUrl(self._lastfm_client.authorize_url(token)))
        # Hand-built for the same reason as the Reset dialog below: the
        # stock Ok/Cancel take their text from the platform theme, which
        # reads the process locale rather than `advanced.language`, so an
        # English window offered a German "Abbrechen".
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle(self.tr("Authorise Refrain"))
        msg.setText(
            self.tr(
                "A Last.fm page opened in your browser. Approve access "
                "for Refrain there, then click OK to finish connecting."
            )
        )
        ok_btn = msg.addButton(self.tr("OK"), QMessageBox.AcceptRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.RejectRole)
        msg.setDefaultButton(ok_btn)
        msg.exec()
        if msg.clickedButton() is not ok_btn:
            self._lastfm_token = ""
            self._refresh_lastfm_status()
            return
        self.lastfm_status_label.setText(self.tr("Completing sign-in…"))
        self._start_lastfm_auth("session")

    def _on_lastfm_session(self, key: str, name: str) -> None:
        self._finish_lastfm_thread()
        self._lastfm_token = ""
        self._lastfm_session_key = key
        self._lastfm_disconnect_requested = False
        self._lastfm_username = name
        self._refresh_lastfm_status()
        if name:
            done = self.tr(
                "Connected as {user}. Click Apply to save — scrobbling starts on the next track."
            ).format(user=name)
        else:
            done = self.tr("Connected. Click Apply to save — scrobbling starts on the next track.")
        QMessageBox.information(self, self.tr("Last.fm"), done)

    def _on_lastfm_auth_failed(self, message: str) -> None:
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

        LegalDialog(self).exec()

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

        self.lastfm_enabled_box.setChecked(c.lastfm.enabled)
        self.lastfm_api_key_input.setText(c.lastfm.api_key)
        self.lastfm_secret_input.setText(c.lastfm.shared_secret)
        self.lastfm_nowplaying_box.setChecked(c.lastfm.scrobble_now_playing)
        self._lastfm_session_key = c.lastfm.session_key
        self._lastfm_disconnect_requested = False
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
                self.bluetooth_device.setEditText(c.sources.bluetooth_device)
        else:
            self.bluetooth_device.setCurrentIndex(0)

        for i in range(self.privacy_combo.count()):
            if self.privacy_combo.itemData(i) == c.privacy.mode:
                self.privacy_combo.setCurrentIndex(i)
                break

        self.poll_spin.setValue(c.advanced.poll_interval_ms)
        self.notify_delay_spin.setValue(c.behavior.notify_delay_ms)
        self.cover_cache_spin.setValue(c.advanced.cover_cache_size)
        for i in range(self.log_level_combo.count()):
            if self.log_level_combo.itemData(i) == c.advanced.log_level:
                self.log_level_combo.setCurrentIndex(i)
                break

        for i in range(self.language_combo.count()):
            if self.language_combo.itemData(i) == c.advanced.language:
                self.language_combo.setCurrentIndex(i)
                break

    def _on_apply_clicked(self) -> None:
        c = self._config
        # Snapshot the language + client_id *before* we overwrite them so
        # we can detect a change and trigger an automatic restart. Both
        # require restart to take effect: the QTranslator is installed
        # once at app startup, and pypresence is bound to the original
        # client_id at connect time — a fresh process is the simplest
        # way to re-init both cleanly.
        previous_language = c.advanced.language
        previous_client_id = c.discord.client_id
        # Empty input is meaningful: it disables Discord RPC entirely.
        # The previous `or c.discord.client_id` fallback meant the user
        # could never *clear* a Client ID — emptying the field was a
        # no-op, surprising anyone trying to disable the integration.
        c.discord.client_id = self.client_id_input.text().strip()
        # The advanced toggle is the per-source feature switch: when
        # it's off, the overrides are cleared so the single default
        # Client ID is used everywhere (and the hidden field contents
        # can't linger). When on, persist what's in the fields.
        if self.discord_per_source_box.isChecked():
            c.discord.client_id_mpris = self.client_id_mpris_input.text().strip()
            c.discord.client_id_bluetooth = self.client_id_bluetooth_input.text().strip()
        else:
            c.discord.client_id_mpris = ""
            c.discord.client_id_bluetooth = ""
        c.discord.resolve_app_name = self.resolve_app_name_box.isChecked()
        c.discord.all_clients = self.discord_all_clients_box.isChecked()
        c.behavior.autostart = self.autostart_box.isChecked()
        c.behavior.notifications = self.notifications_box.isChecked()
        c.behavior.cover_art = self.cover_art_box.isChecked()
        c.behavior.show_buttons = self.buttons_box.isChecked()
        c.behavior.notify_delay_ms = self.notify_delay_spin.value()

        c.lastfm.enabled = self.lastfm_enabled_box.isChecked()
        c.lastfm.api_key = self.lastfm_api_key_input.text().strip()
        c.lastfm.shared_secret = self.lastfm_secret_input.text().strip()
        c.lastfm.scrobble_now_playing = self.lastfm_nowplaying_box.isChecked()
        # session_key / username come from the connect flow, not a
        # widget. The daemon's Scrobbler rebinds in place via
        # update_config — no process restart needed (unlike Discord).
        c.lastfm.session_key = self._lastfm_session_key
        c.lastfm.username = self._lastfm_username

        c.update.auto_check = self.auto_check_box.isChecked()

        c.sources.mpris_enabled = self.mpris_box.isChecked()
        c.sources.bluetooth_enabled = self.bluetooth_box.isChecked()
        # Recombine the checkbox-picks + the extras field into the
        # persisted comma-separated string. Order: known browsers in the
        # display order first, then any extras the user typed.
        picked = [t for t, cb in self._browser_checkboxes.items() if cb.isChecked()]
        extra_text = self.browser_extra_input.text().strip()
        if extra_text:
            extras = [e.strip().lower() for e in extra_text.split(",") if e.strip()]
            picked.extend(e for e in extras if e not in picked)
        c.sources.browser_hints = ",".join(picked) if picked else c.sources.browser_hints

        bt_data = self.bluetooth_device.currentData()
        if bt_data is None:
            text = self.bluetooth_device.currentText().strip()
            bt_data = "" if text in ("", "(auto-detect)", self.tr("(auto-detect)")) else text
        c.sources.bluetooth_device = bt_data

        c.privacy.mode = self.privacy_combo.currentData() or "full"
        c.advanced.poll_interval_ms = self.poll_spin.value()
        c.advanced.cover_cache_size = self.cover_cache_spin.value()
        c.advanced.log_level = self.log_level_combo.currentData() or "INFO"
        c.advanced.language = self.language_combo.currentData() or "system"

        try:
            c.save()
        except OSError as e:
            # Disk full / read-only / permission denied: we can't
            # silently swallow this — the user just clicked Apply and
            # would otherwise see no feedback while their settings
            # actually didn't get persisted (in-memory daemon state
            # would update but reload a stale config on next launch).
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
            # Continue with applied.emit anyway — the in-memory
            # daemon state should still be consistent for this
            # session even if the file write failed.
        # Persist the Last.fm credentials to the OS keyring (NOT
        # config.toml — to_dict() blanks them). Separate from c.save()
        # so a config-write failure doesn't also lose the secrets, and
        # vice-versa.
        from refrain.secrets_store import save_from as _save_lastfm_secrets

        _save_lastfm_secrets(c.lastfm, clear_missing=self._lastfm_disconnect_requested)
        self.applied.emit(c)
        # Apply triggers a restart automatically when the user changed
        # the UI language or the Discord client_id. Both need a fresh
        # process to re-init cleanly (QTranslator is installed once at
        # startup; pypresence binds to the client_id at connect time).
        if c.advanced.language != previous_language:
            log.info(
                "Language changed (%s → %s); requesting restart",
                previous_language,
                c.advanced.language,
            )
            self.restartRequested.emit()
            return
        if c.discord.client_id != previous_client_id:
            log.info("Discord client_id changed; requesting restart")
            self.restartRequested.emit()
            return
        self.hide()

    def closeEvent(self, event) -> None:
        # Join any in-flight worker so app teardown doesn't hit
        # "QThread: Destroyed while thread is still running". Brief
        # bounded wait, mirroring the welcome dialog's diagnostics thread.
        self._finish_lastfm_thread()
        self._app_name_timer.stop()
        self._finish_app_name_thread()
        super().closeEvent(event)
