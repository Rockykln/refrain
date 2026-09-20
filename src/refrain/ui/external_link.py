"""One question before every link that leaves Refrain for the browser."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox, QWidget

from refrain.browser import open_url

_REFERENCE_LINK = "https://github.com/Rockykln/refrain"


def confirm_and_open(parent: QWidget | None, url: str, player: str = "") -> bool:
    """Say where the click leads, then open it in the browser on a yes."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(QCoreApplication.translate("ExternalLink", "Leave Refrain?"))
    box.setText(QCoreApplication.translate("ExternalLink", "This opens a page in your browser:"))
    box.setInformativeText(url)
    # Exactly as wide as the project's own link; longer song URLs wrap.
    needed = box.fontMetrics().horizontalAdvance(_REFERENCE_LINK)
    box.setStyleSheet(f"QLabel#qt_msgbox_informativelabel {{ min-width: {needed}px; }}")
    go = box.addButton(
        QCoreApplication.translate("ExternalLink", "Open"), QMessageBox.ButtonRole.AcceptRole
    )
    box.addButton(
        QCoreApplication.translate("ExternalLink", "Cancel"), QMessageBox.ButtonRole.RejectRole
    )
    box.setDefaultButton(go)
    box.exec()
    if box.clickedButton() is not go:
        return False
    return open_url(url, player)
