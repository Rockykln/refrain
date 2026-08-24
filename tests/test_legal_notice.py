"""The legal notice must say the same thing in the repo and in the app.

``LEGAL.md`` is not shipped in the wheel — the packaged build installs
``src/refrain`` only — so ``ui/legal_dialog.py`` carries its own copy of
the text. Two copies drift. These tests pin the statements that must
appear in both, so dropping one from either side fails the suite rather
than quietly shipping an app whose legal notice no longer matches the
repository's.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    """Same pattern as test_tray_init.py — reuse any running instance."""
    yield QApplication.instance() or QApplication(sys.argv)


REPO = Path(__file__).resolve().parent.parent
LEGAL_MD = REPO / "LEGAL.md"


def _dialog_plain_text() -> str:
    """Every string the dialog renders, with the markup stripped."""
    from refrain.ui.legal_dialog import LEGAL_URL, LICENSE_URL, _sections

    parts = [f"{heading}\n{body}" for heading, body in _sections()]
    parts.append(f"{LEGAL_URL} {LICENSE_URL}")
    return re.sub(r"<[^>]+>", " ", "\n".join(parts))


def test_legal_md_exists():
    assert LEGAL_MD.is_file(), "LEGAL.md is the canonical notice; it must exist"


@pytest.mark.parametrize(
    "claim",
    [
        # The three things Rocky specifically asked to be stated.
        "not a registered trademark",
        "Refrain License (Use-Only)",
        "not affiliated with",
        # Nominative-fair-use framing for the marks we lean on.
        "Apple Inc.",
        "Discord Inc.",
        "Last.fm Ltd.",
        "KDE e.V.",
        # Liability.
        "without warranty of any kind",
    ],
)
def test_claim_appears_in_both_copies(claim):
    md = LEGAL_MD.read_text()
    dialog = _dialog_plain_text()
    assert claim in md, f"{claim!r} missing from LEGAL.md"
    assert claim in dialog, f"{claim!r} missing from the in-app legal dialog"


def test_trademark_owners_are_all_named_in_the_markdown():
    """The dialog builds its owner list from data — keep LEGAL.md in step."""
    from refrain.ui.legal_dialog import TRADEMARK_OWNERS

    md = LEGAL_MD.read_text()
    for owner, marks in TRADEMARK_OWNERS:
        assert owner in md, f"{owner} named in the dialog but not in LEGAL.md"
        for mark in marks.split(" and "):
            assert mark in md, f"{mark} named in the dialog but not in LEGAL.md"


def test_owner_list_reads_grammatically():
    """Singular/plural is derived from the mark string; check both shapes."""
    from refrain.ui.legal_dialog import _sections

    body = dict(_sections())["No affiliation, no endorsement"]
    assert "Apple and Apple Music are trademarks of Apple Inc." in re.sub(r"<[^>]+>", "", body)
    assert "Discord is a trademark of Discord Inc." in re.sub(r"<[^>]+>", "", body)


def test_third_party_licences_match():
    """Dependency licences are a factual claim — keep the two lists equal."""
    from refrain.ui.legal_dialog import _sections

    md = LEGAL_MD.read_text()
    body = re.sub(r"<[^>]+>", "", dict(_sections())["Licence"])
    for component, licence in (
        ("PySide6", "LGPL v3"),
        ("pypresence", "MIT"),
        ("dbus-python", "MIT"),
        ("PyGObject", "LGPL v2.1"),
    ):
        assert component in md and component in body, f"{component} missing"
        assert licence in md and licence in body, f"{licence} missing"


def test_dialog_does_not_promise_translation(monkeypatch):
    """The section text is assembled at runtime, so pylupdate can't extract
    it. Wrapping it in tr() would only pretend it is translatable."""
    src = (REPO / "src" / "refrain" / "ui" / "legal_dialog.py").read_text()
    body = src[src.index("for heading, body in _sections():") :]
    assert "self.tr(heading)" not in body
    assert "self.tr(body)" not in body


def test_dialog_constructs_and_renders(qt_app):
    from PySide6.QtWidgets import QLabel

    from refrain.ui.legal_dialog import LegalDialog

    dlg = LegalDialog()
    rendered = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    plain = re.sub(r"<[^>]+>", " ", rendered)

    assert dlg.windowTitle()
    for claim in ("not a registered trademark", "Use-Only", "no telemetry"):
        assert claim in plain
    dlg.deleteLater()


def test_links_are_not_opened_by_qt_itself(qt_app):
    """Links go through QDesktopServices so they land in the system browser
    rather than Qt's default handler."""
    from PySide6.QtWidgets import QLabel

    from refrain.ui.legal_dialog import LegalDialog

    dlg = LegalDialog()
    for lbl in dlg.findChildren(QLabel):
        if "href" in lbl.text():
            assert lbl.openExternalLinks() is False
    dlg.deleteLater()
