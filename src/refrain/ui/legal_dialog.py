"""Legal notice, reachable from the Settings window.

Mirrors the repository's ``LEGAL.md`` so the same statements are visible
to someone who only ever sees the installed application: that Refrain is
an independent project with no affiliation to Apple, Discord, Last.fm or
KDE, that "Refrain" is not a registered trademark, which licence applies,
and what leaves the machine.

The text is kept inline rather than read from ``LEGAL.md`` at runtime —
the packaged wheel ships ``src/refrain`` only, so the Markdown file isn't
installed, and a legal notice that silently disappears in the packaged
build is worse than a little duplication. ``tests/test_legal_notice.py``
guards the two copies against drifting apart.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from refrain.ui.cursors import apply_interactive_cursors

GITHUB_URL = "https://github.com/Rockykln/refrain"
LEGAL_URL = f"{GITHUB_URL}/blob/main/LEGAL.md"
LICENSE_URL = f"{GITHUB_URL}/blob/main/LICENSE"

# Trademark owners Refrain interoperates with. Kept as data so the test
# that compares this dialog against LEGAL.md can check the list directly.
TRADEMARK_OWNERS = (
    ("Apple Inc.", "Apple and Apple Music"),
    ("Discord Inc.", "Discord"),
    ("Last.fm Ltd.", "Last.fm"),
    ("KDE e.V.", "KDE and Plasma"),
)


def _sections() -> list[tuple[str, str]]:
    """(heading, rich-text body) pairs, in display order."""
    owners = "".join(
        f"<li><b>{owner}</b> — {marks} "
        f"{'are' if ' and ' in marks else 'is a'} "
        f"{'trademarks' if ' and ' in marks else 'trademark'} of {owner}</li>"
        for owner, marks in TRADEMARK_OWNERS
    )
    return [
        (
            "No affiliation, no endorsement",
            "Refrain is an independent hobby project. It is <b>not affiliated "
            "with, sponsored by, endorsed by, or in any way officially "
            "connected to</b> any of the companies, products or services it "
            "interoperates with:"
            f"<ul>{owners}</ul>"
            "All product names, logos and trademarks are the property of their "
            "respective owners and are used only to describe what Refrain "
            "interoperates with — never to suggest a partnership, "
            "certification or origin. Refrain does not redistribute, modify or "
            "circumvent any of these products; it talks to interfaces they "
            "expose on your own machine.",
        ),
        (
            "Trademark status of “Refrain”",
            "<b>“Refrain” is not a registered trademark.</b> The name and logo "
            "are used by this project without any trademark registration or "
            "claim to exclusive rights in any jurisdiction. No trademark "
            "rights are asserted, and none should be inferred.",
        ),
        (
            "Licence",
            "Refrain is distributed under the <b>Refrain License "
            f"(Use-Only)</b> — see <a href='{LICENSE_URL}'>LICENSE</a>. It is "
            "<b>source-available, not open source</b>: the unmodified software "
            "may be run, copied and redistributed free of charge, and the "
            "source may be read and studied, but modified versions and "
            "derivative works may not be redistributed."
            "<br><br>"
            "Third-party components keep their own licences: PySide6 / Qt for "
            "Python (LGPL v3), pypresence (MIT), dbus-python (MIT) and, "
            "optionally, PyGObject (LGPL v2.1).",
        ),
        (
            "Data",
            "Refrain runs entirely on your machine. There is no backend, no "
            "account system and no telemetry. Data leaves your machine only "
            "where your own configuration requires it: track metadata goes to "
            "your local Discord client over its Rich Presence socket; track "
            "and album names go to Apple's iTunes Search API for cover art; "
            "Last.fm receives scrobbles only if you enable them with your own "
            "credentials; and GitHub's releases API is queried for updates."
            "<br><br>"
            "Last.fm credentials are stored in your operating system's keyring "
            "where one is available, and otherwise in a 0600-mode file in your "
            "own configuration directory.",
        ),
        (
            "No warranty",
            "The software is provided <b>“as is”, without warranty of any "
            "kind</b>, express or implied, including the warranties of "
            "merchantability, fitness for a particular purpose and "
            "non-infringement. Refrain is not certified, audited or supported "
            "by any third party, and you are responsible for complying with "
            "the terms of service of any platform you connect it to. See "
            f"<a href='{LICENSE_URL}'>LICENSE</a> for the binding disclaimer "
            "and limitation of liability.",
        ),
    ]


class LegalDialog(QDialog):
    """Read-only legal notice. Links open in the system browser."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Legal Notice"))
        self.setMinimumSize(560, 520)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 12, 4)

        # The notice itself is deliberately *not* run through self.tr():
        # the section bodies are assembled at runtime, so pylupdate could
        # never extract them, and wrapping them would only pretend they are
        # translatable. Legal wording stays in one language on purpose —
        # only the chrome (title, Close) follows the UI locale.
        intro = QLabel(
            "Refrain is an independent project by Rockykln. "
            f"The full text is kept in <a href='{LEGAL_URL}'>LEGAL.md</a>."
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(False)
        intro.linkActivated.connect(self._open_link)
        inner_layout.addWidget(intro)

        for heading, body in _sections():
            title = QLabel(f"<b>{heading}</b>")
            title.setContentsMargins(0, 12, 0, 0)
            inner_layout.addWidget(title)

            text = QLabel(body)
            text.setWordWrap(True)
            text.setTextFormat(Qt.RichText)
            # Selectable so a user can copy a clause; links go through
            # QDesktopServices rather than Qt's own handler.
            text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            text.setOpenExternalLinks(False)
            text.linkActivated.connect(self._open_link)
            inner_layout.addWidget(text)

        inner_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        # Qt fills a standard button's text from the platform theme, and
        # KDE's plugin takes it from KDE's own catalogs keyed to the
        # process locale — it never consults the translator we install.
        # So a German "Schließen" sat in an otherwise English window and
        # no language setting could reach it. Our own text can.
        buttons.button(QDialogButtonBox.Close).setText(self.tr("Close"))
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)

        # Every clickable child gets the pointing hand, in one place —
        # see refrain.ui.cursors.
        apply_interactive_cursors(self)

    @staticmethod
    def _open_link(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))
