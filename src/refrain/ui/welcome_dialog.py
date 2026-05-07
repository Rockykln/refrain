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

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
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
_ITUNES_TEST_URL = "https://itunes.apple.com/search?term=test&entity=song&limit=1&country=us"


class _DiagnosticsWorker(QObject):
    """Runs the two probes off the GUI thread.

    Emits ``finished(discord_ok, discord_msg, itunes_ok, itunes_msg)``.
    """

    finished = Signal(bool, str, bool, str)

    def run(self) -> None:
        log.info("First-run wizard: starting diagnostics")
        discord_ok, discord_msg = _probe_discord_ipc()
        log.info("First-run wizard: Discord IPC probe → ok=%s (%s)", discord_ok, discord_msg)
        itunes_ok, itunes_msg = _probe_itunes()
        log.info("First-run wizard: iTunes probe → ok=%s (%s)", itunes_ok, itunes_msg)
        self.finished.emit(discord_ok, discord_msg, itunes_ok, itunes_msg)


def _probe_discord_ipc() -> tuple[bool, str]:
    """Try to reach a Discord IPC socket without using pypresence (avoids
    a half-open connection that would race with the daemon).

    Discord publishes its IPC socket under ``$XDG_RUNTIME_DIR`` on
    properly-configured Linux desktops. We also probe the Snap and
    Flatpak sandbox locations so users on those builds get a green
    diagnostic line — DiscordRPC._ensure_connected bridges those into
    ``$XDG_RUNTIME_DIR`` at connect time.
    """
    candidate_roots: list[Path] = []
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidate_roots.append(Path(xdg_runtime))
        candidate_roots.append(Path(xdg_runtime) / "app" / "com.discordapp.Discord")
    # Flatpak (older / config-dir layout)
    candidate_roots.append(
        Path.home() / ".var" / "app" / "com.discordapp.Discord" / "config" / "discord"
    )
    # Snap
    candidate_roots.append(Path.home() / "snap" / "discord" / "current" / ".config" / "discord")
    # Legacy fallback — some early Flatpak'd Discord builds put the socket here.
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
        # Compact-but-comfortable: tall enough for the two diagnostics
        # rows + intro + ID block without scrolling, narrow enough that
        # it doesn't feel like a settings window. The previous 580 px
        # height left a wide empty band between the input and the
        # action buttons.
        self.setFixedSize(560, 470)

        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(12)

        # Header — large icon + title block. Subtitle gets explicit
        # spacing from the title so they don't collide on Plasma's
        # tighter default line height.
        header = QHBoxLayout()
        header.setSpacing(14)
        if icon_path.exists():
            badge = QLabel()
            badge.setPixmap(QIcon(str(icon_path)).pixmap(56, 56))
            header.addWidget(badge, alignment=Qt.AlignTop)
        title_block = QVBoxLayout()
        title_block.setSpacing(4)
        title = QLabel(self.tr("Welcome to Refrain"))
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        subtitle = QLabel(
            self.tr("Discord Rich Presence for Apple Music on Linux.")
        )
        # palette(mid) renders almost-invisible on Plasma Breeze dark
        # themes — using palette(text) with a slight opacity reduction
        # via the alpha channel keeps the visual hierarchy without
        # making the line unreadable.
        subtitle.setStyleSheet("color: palette(text); font-size: 13px; font-style: italic;")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        title_block.addStretch(1)
        header.addLayout(title_block, 1)
        layout.addLayout(header)

        # Intro: short one-liner about how to drive refrain after this.
        intro = QLabel(
            self.tr(
                "Lives in the tray. <b>Right-click</b> for player controls; "
                "<b>click</b> to open Settings."
            )
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: palette(text); padding: 2px 0;")
        layout.addWidget(intro)

        # Diagnostics card. Two pre-built rows so it doesn't render as
        # an empty box while the probes are running.
        diag_box = QFrame()
        diag_box.setObjectName("diagBox")
        diag_box.setStyleSheet(
            "QFrame#diagBox { background: palette(alternate-base); "
            "border: 1px solid palette(mid); border-radius: 8px; }"
        )
        diag_layout = QVBoxLayout(diag_box)
        diag_layout.setContentsMargins(14, 10, 14, 10)
        diag_layout.setSpacing(6)
        diag_heading = QLabel(self.tr("Live diagnostics"))
        diag_heading.setStyleSheet("font-weight: 600; font-size: 12px;")
        diag_layout.addWidget(diag_heading)
        self._diag_discord = QLabel(self.tr("⏳ Discord — checking…"))
        self._diag_itunes = QLabel(self.tr("⏳ Cover-art lookup — checking…"))
        for lbl in (self._diag_discord, self._diag_itunes):
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: palette(text);")
            diag_layout.addWidget(lbl)
        layout.addWidget(diag_box)

        # Client-ID block.
        id_heading = QLabel(self.tr("Discord Application ID"))
        id_heading.setStyleSheet("font-weight: 600; padding-top: 6px;")
        layout.addWidget(id_heading)

        client_label = QLabel(
            self.tr(
                'Register a free app on the <a href="{url}">Discord Developer '
                "Portal</a> — the name you pick appears as "
                '"Listening to &lt;name&gt;" in your status.'
            ).format(url=_DISCORD_DEVELOPER_PORTAL)
        )
        client_label.setOpenExternalLinks(True)
        client_label.setWordWrap(True)
        # Default text color (was palette(mid) — illegible on dark
        # Breeze). Italic + slightly smaller keeps the helper-text feel
        # without sacrificing readability.
        client_label.setStyleSheet("color: palette(text); font-size: 12px; font-style: italic;")
        layout.addWidget(client_label)

        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText(self.tr("e.g. 1234567890123456789"))
        self.client_id_edit.setMinimumHeight(30)
        layout.addWidget(self.client_id_edit)

        layout.addStretch(1)

        # Footer row — skip ghost-style on the left, apply primary on
        # the right. setAutoDefault(False) on Skip so Enter doesn't
        # accidentally dismiss the wizard without saving the ID.
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._skip_btn = QPushButton(self.tr("Skip for now"))
        self._skip_btn.setAutoDefault(False)
        self._skip_btn.setFlat(True)
        self._skip_btn.clicked.connect(self._on_skip)
        buttons.addWidget(self._skip_btn)
        buttons.addStretch(1)
        self._apply_btn = QPushButton(self.tr("Apply"))
        self._apply_btn.setDefault(True)
        self._apply_btn.setMinimumWidth(110)
        self._apply_btn.setMinimumHeight(32)
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
        self._diag_discord.setText(
            self.tr("{mark} <b>Discord:</b> {msg}").format(mark=d_mark, msg=d_msg)
        )
        self._diag_itunes.setText(
            self.tr("{mark} <b>Cover-art lookup:</b> {msg}").format(mark=i_mark, msg=i_msg)
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

    def reject(self) -> None:
        # X / Escape behave the same as "Skip for now" — emit an empty
        # client_id so `first_run_complete=True` gets persisted and the
        # wizard doesn't re-appear on every launch. Without this, X
        # leaked back into the next session as another welcome popup.
        if self._diag_thread is not None:
            self._diag_thread.quit()
            with contextlib.suppress(Exception):
                self._diag_thread.wait(500)
            self._diag_thread = None
            self._diag_worker = None
        self.applied.emit("")
        super().reject()

    def _on_apply(self) -> None:
        client_id = self.client_id_edit.text().strip()
        # Empty Apply is suspiciously close to a misclick — confirm before
        # silently saving "no Discord". The user can also explicitly skip
        # via the Skip button if they really meant it.
        if not client_id:
            reply = QMessageBox.question(
                self,
                self.tr("Skip Discord setup?"),
                self.tr(
                    "No Application ID entered. Refrain will start without "
                    "Discord status (you can paste the ID later in "
                    "Settings → General).\n\nContinue without Discord status?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
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
        log.info("Welcome wizard: applying client_id (%d chars)", len(client_id))
        self.applied.emit(client_id)
        self.accept()
