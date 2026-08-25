"""Update-available dialog.

Shows the current vs. available version, the release notes, and an action
button whose behavior is install-type-specific (download AppImage, run pip
upgrade, or surface the distro upgrade command).
"""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from refrain import __version__
from refrain.paths import assets_dir
from refrain.ui.cursors import apply_interactive_cursors
from refrain.updater import (
    ReleaseInfo,
    UpdateResult,
    apply_update,
    detect_install_type,
    prepare_release_notes,
)

log = logging.getLogger(__name__)


class _UpdateRunner(QThread):
    """Runs the (potentially slow) network update on a background thread.

    Forwards Qt's ``isInterruptionRequested`` to ``apply_update`` so the
    chunked AppImage download can break out promptly when the user hits
    Cancel.
    """

    finished_with_result = Signal(object)  # UpdateResult

    def __init__(self, release: ReleaseInfo, install_type: str, parent=None):
        super().__init__(parent)
        self._release = release
        self._install_type = install_type

    def run(self) -> None:
        result = apply_update(
            self._release,
            self._install_type,
            cancelled=self.isInterruptionRequested,
        )
        self.finished_with_result.emit(result)


class UpdateDialog(QDialog):
    def __init__(self, release: ReleaseInfo, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Update available"))
        self.setMinimumSize(560, 480)
        icon_path = assets_dir() / "icons" / "refrain.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._release = release
        self._install_type = detect_install_type()
        self._runner: _UpdateRunner | None = None

        layout = QVBoxLayout(self)

        header_text = self.tr(
            "<h2>Refrain {version} is available</h2>"
            "<p>You're running <b>v{current}</b>. "
            "Detected install type: <b>{install_type}</b>.</p>"
        ).format(version=release.version, current=__version__, install_type=self._install_type)
        header = QLabel(header_text)
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        notes_label = QLabel(self.tr("<b>Release notes</b>"))
        layout.addWidget(notes_label)

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(prepare_release_notes(release.body))
        layout.addWidget(self.notes, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Action row
        button_row = QHBoxLayout()
        button_row.addStretch()

        self.open_release_btn = QPushButton(self.tr("Open release page"))
        self.open_release_btn.clicked.connect(self._open_release_page)
        button_row.addWidget(self.open_release_btn)

        self.update_btn = QPushButton(self._update_button_label())
        self.update_btn.setDefault(True)
        self.update_btn.clicked.connect(self._on_update_clicked)
        button_row.addWidget(self.update_btn)

        self.close_btn = QPushButton(self.tr("Later"))
        self.close_btn.clicked.connect(self.reject)
        button_row.addWidget(self.close_btn)

        layout.addLayout(button_row)

        # Every clickable child gets the pointing hand, in one place —
        # see refrain.ui.cursors.
        apply_interactive_cursors(self)

    # ----------------------------------------------------------- helpers

    def _update_button_label(self) -> str:
        if self._install_type == "appimage":
            return self.tr("Download && replace")
        if self._install_type == "pip":
            return self.tr("Run pip upgrade")
        if self._install_type == "pipx":
            return self.tr("Run pipx upgrade")
        if self._install_type in ("aur", "flatpak"):
            return self.tr("Run update in terminal")
        return self.tr("Show update command")

    def _open_release_page(self) -> None:
        if self._release.html_url:
            QDesktopServices.openUrl(QUrl(self._release.html_url))

    # --------------------------------------------------------- handlers

    def _on_update_clicked(self) -> None:
        # For install types we can't auto-update, just show the command.
        if self._install_type not in ("appimage", "pip", "pipx"):
            result = apply_update(self._release, self._install_type)
            self._show_result(result)
            return

        # Network/subprocess work on a background thread.
        self.update_btn.setEnabled(False)
        self.progress.setVisible(True)
        if self._install_type == "appimage":
            busy = self.tr("Downloading…")
        elif self._install_type == "pipx":
            busy = self.tr("Running pipx…")
        else:
            busy = self.tr("Running pip…")
        self.status_label.setText(busy)
        # Repurpose the "Later" button as Cancel while the runner is alive.
        # Only the AppImage path actually polls the cancel flag — pip is a
        # subprocess we don't try to interrupt mid-flight.
        if self._install_type == "appimage":
            self.close_btn.setText(self.tr("Cancel"))
            # `disconnect()` raises TypeError when the signal has no
            # connections — defensive in case _on_update_clicked is
            # ever re-entered between runner-start and runner-finish.
            with contextlib.suppress(TypeError):
                self.close_btn.clicked.disconnect()
            self.close_btn.clicked.connect(self._on_cancel_clicked)
        else:
            self.close_btn.setEnabled(False)

        self._runner = _UpdateRunner(self._release, self._install_type, self)
        self._runner.finished_with_result.connect(self._on_runner_finished)
        self._runner.start()

    def _on_cancel_clicked(self) -> None:
        if self._runner is None or not self._runner.isRunning():
            return
        log.info("User requested update cancel")
        self.status_label.setText(self.tr("Cancelling…"))
        self.close_btn.setEnabled(False)
        self._runner.requestInterruption()

    def _on_runner_finished(self, result: UpdateResult) -> None:
        self.progress.setVisible(False)
        self.update_btn.setEnabled(True)
        # Restore the original "Later" wiring whether or not we showed Cancel.
        self.close_btn.setEnabled(True)
        self.close_btn.setText(self.tr("Later"))
        with contextlib.suppress(TypeError):
            self.close_btn.clicked.disconnect()
        self.close_btn.clicked.connect(self.reject)
        self._show_result(result)

    def _show_result(self, result: UpdateResult) -> None:
        if result.cancelled:
            self.status_label.setText(self.tr("Update cancelled."))
            return
        if result.success:
            QMessageBox.information(self, self.tr("Update complete"), result.message)
            if result.needs_restart:
                self.accept()
        else:
            QMessageBox.warning(self, self.tr("Update"), result.message)

    def closeEvent(self, event) -> None:
        # If the user closes the window while a download is running, treat
        # it as a cancel: ask the runner to stop and wait briefly so the
        # tmp file is cleaned up before we vanish. The runner's
        # finished_with_result will not fire after we close, so we accept
        # the close once the thread has joined.
        if self._runner is not None and self._runner.isRunning():
            log.info("Update dialog closed during download — cancelling")
            self._runner.requestInterruption()
            self._runner.wait(3000)
        super().closeEvent(event)
