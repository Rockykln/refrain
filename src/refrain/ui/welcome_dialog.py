"""First-run welcome wizard.

Runs once on a brand-new install (or whenever ``behavior.first_run_complete``
is False AND no Discord client_id is configured). Walks the user through:

1. Tray-icon orientation — where Refrain lives once dismissed.
2. A live Discord IPC probe so they know whether the desktop client is
   reachable from this session.
3. A live iTunes Search lookup so they know cover-art will work.
4. A field to paste their Discord Application ID, with a direct link
   to the Developer Portal.

The dialog is intentionally non-blocking on the daemon: it runs in the
main thread, and "Apply + close" hands the new ``client_id`` back via a
signal — same wire-up as ``SettingsWindow``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from refrain.paths import assets_dir

log = logging.getLogger(__name__)


_DISCORD_DEVELOPER_PORTAL = "https://discord.com/developers/applications"
_ITUNES_TEST_URL = (
    "https://itunes.apple.com/search?term=test&entity=song&limit=1&country=us"
)


class _DiagnosticsWorker(QObject):
    """Runs the two probes off the GUI thread.

    Emits ``finished(discord_ok, discord_msg, itunes_ok, itunes_msg)``.
    """

    finished = Signal(bool, str, bool, str)

    def run(self) -> None:
        discord_ok, discord_msg = _probe_discord_ipc()
        itunes_ok, itunes_msg = _probe_itunes()
        self.finished.emit(discord_ok, discord_msg, itunes_ok, itunes_msg)


def _probe_discord_ipc() -> tuple[bool, str]:
    """Try to reach a Discord IPC socket without using pypresence (avoids
    a half-open connection that would race with the daemon).

    Discord publishes its IPC socket under ``$XDG_RUNTIME_DIR`` on
    properly-configured Linux desktops. Fallback search paths cover
    legacy installs where the env var isn't set.
    """
    candidate_roots: list[Path] = []
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidate_roots.append(Path(xdg_runtime))
    # Some Flatpak'd Discord builds expose the socket under the user
    # cache directory instead.
    candidate_roots.append(Path.home() / ".cache")
    for candidate_root in candidate_roots:
        for n in range(10):
            path = candidate_root / f"discord-ipc-{n}"
            if path.exists():
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect(str(path))
                    s.close()
                    return True, f"Found Discord IPC at {path}"
                except OSError as e:
                    log.debug("Discord IPC probe %s failed: %s", path, e)
    return False, "No Discord IPC socket found — start the Discord desktop app."


def _probe_itunes() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(_ITUNES_TEST_URL, timeout=5) as resp:  # noqa: S310
            data = json.load(resp)
        if isinstance(data, dict) and "resultCount" in data:
            return True, "iTunes Search API reachable."
        return False, "iTunes responded but the payload looked off."
    except urllib.error.URLError as e:
        return False, f"iTunes Search unreachable: {e.reason}"
    except Exception as e:
        return False, f"iTunes probe failed: {e}"


class WelcomeDialog(QDialog):
    """First-run welcome wizard. Owns no daemon state — emits ``applied``
    when the user confirms a Discord client_id."""

    applied = Signal(str)  # client_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Welcome"))
        self.setMinimumWidth(560)

        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout(self)

        title = QLabel(self.tr("<h2>Welcome to Refrain</h2>"))
        layout.addWidget(title)

        intro = QLabel(
            self.tr(
                "Refrain shows what you're listening to on Apple Music as your "
                "Discord status. It lives in the system tray. Right-click the tray "
                "icon for player controls; click the icon to open Settings."
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._diag_label = QLabel(self.tr("Running diagnostics…"))
        self._diag_label.setWordWrap(True)
        layout.addWidget(self._diag_label)

        layout.addSpacing(8)

        client_label = QLabel(
            self.tr(
                'Paste your <a href="{url}">Discord Application ID</a> below. '
                "You can register a free Discord Application in 30 seconds — "
                'the name you choose appears as "Listening to &lt;name&gt;" '
                "in your Discord status."
            ).format(url=_DISCORD_DEVELOPER_PORTAL)
        )
        client_label.setOpenExternalLinks(True)
        client_label.setWordWrap(True)
        layout.addWidget(client_label)

        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText(self.tr("e.g. 1234567890123456789"))
        layout.addWidget(self.client_id_edit)

        layout.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._skip_btn = QPushButton(self.tr("Skip for now"))
        self._skip_btn.clicked.connect(self._on_skip)
        buttons.addWidget(self._skip_btn)
        self._apply_btn = QPushButton(self.tr("Apply"))
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self._on_apply)
        buttons.addWidget(self._apply_btn)
        layout.addLayout(buttons)

        self._diag_thread: QThread | None = None
        self._diag_worker: _DiagnosticsWorker | None = None

    def start_diagnostics(self) -> None:
        """Kick off Discord + iTunes probes on a background thread."""
        self._diag_thread = QThread(self)
        self._diag_worker = _DiagnosticsWorker()
        self._diag_worker.moveToThread(self._diag_thread)
        self._diag_thread.started.connect(self._diag_worker.run)
        self._diag_worker.finished.connect(self._on_diag_finished)
        self._diag_thread.start()

    def _on_diag_finished(self, d_ok: bool, d_msg: str, i_ok: bool, i_msg: str) -> None:
        d_mark = "✅" if d_ok else "⚠️"
        i_mark = "✅" if i_ok else "⚠️"
        self._diag_label.setText(
            self.tr(
                "<b>Diagnostics</b><br/>"
                "{d_mark} <b>Discord</b>: {d_msg}<br/>"
                "{i_mark} <b>Cover-art lookup</b>: {i_msg}"
            ).format(d_mark=d_mark, d_msg=d_msg, i_mark=i_mark, i_msg=i_msg)
        )
        if self._diag_thread is not None:
            self._diag_thread.quit()
            with contextlib.suppress(Exception):
                self._diag_thread.wait(1500)
            self._diag_thread = None
            self._diag_worker = None

    def _on_skip(self) -> None:
        self.applied.emit("")  # empty = no client_id, but mark first_run_complete anyway
        self.accept()

    def _on_apply(self) -> None:
        client_id = self.client_id_edit.text().strip()
        if client_id and not client_id.isdigit():
            QMessageBox.warning(
                self,
                self.tr("Invalid Application ID"),
                self.tr(
                    "The Discord Application ID is a numeric snowflake (17–19 digits). "
                    "Double-check the value you copied from the Developer Portal."
                ),
            )
            return
        self.applied.emit(client_id)
        self.accept()
