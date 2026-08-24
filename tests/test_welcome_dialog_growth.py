"""The welcome dialog must not clip its live diagnostics.

The two diagnostics rows word-wrap and are filled at runtime with
whatever the probes report. The dialog used to be ``setFixedSize`` — Qt
never grows an already-shown window on its own, so a long enough failure
message ("no IPC socket answered …", and the same text is longer again
in a wordier locale) lost its last lines with no scrollbar to reach them.

It now grows in height when, and only when, the labels genuinely need
more room than the layout can absorb.

Runs against ``QT_QPA_PLATFORM=offscreen`` so it works in headless CI.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

BASE_H = 470
SHORT = "reachable"
LONG = (
    "could not be reached: none of the IPC sockets "
    "/run/user/1000/discord-ipc-0 … -9 answered (Connection refused). "
    "Is the Discord desktop client running in this session?"
)
VERY_LONG = LONG + " " + LONG


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication(sys.argv)


def _settled(dlg, discord_msg, itunes_msg, app):
    """Deliver a probe result and let the layout finish reflowing."""
    dlg.show()
    app.processEvents()
    dlg._on_diag_finished(False, discord_msg, False, itunes_msg)
    # The nested diagnostics box reflows a turn later, and _grow_to_fit
    # is queued behind it.
    for _ in range(6):
        app.processEvents()
    return dlg


def _clipped(dlg):
    return [
        lbl.text()[:40]
        for lbl in dlg.findChildren(QLabel)
        if lbl.wordWrap() and lbl.text() and lbl.heightForWidth(lbl.width()) > lbl.height() + 1
    ]


def _lowest_button(dlg):
    return max(b.y() + b.height() for b in dlg.findChildren(QPushButton))


@pytest.mark.parametrize(
    ("name", "message"),
    [("short", SHORT), ("realistic failure", LONG), ("very long", VERY_LONG)],
)
def test_diagnostics_are_never_clipped(app, name, message):
    from refrain.ui.welcome_dialog import WelcomeDialog

    dlg = _settled(WelcomeDialog(), message, message, app)
    try:
        assert _clipped(dlg) == [], f"{name}: diagnostics text is cut off"
        assert _lowest_button(dlg) <= dlg.height(), f"{name}: buttons pushed off"
    finally:
        dlg.close()
        dlg.deleteLater()


def test_short_and_realistic_messages_keep_the_designed_size(app):
    """The layout absorbs these on its own; the dialog must not jump."""
    from refrain.ui.welcome_dialog import WelcomeDialog

    for message in (SHORT, LONG):
        dlg = _settled(WelcomeDialog(), message, message, app)
        try:
            assert dlg.height() == BASE_H, (
                f"dialog resized to {dlg.height()} for a message the layout had room for"
            )
        finally:
            dlg.close()
            dlg.deleteLater()


def test_a_very_long_message_grows_the_dialog(app):
    from refrain.ui.welcome_dialog import WelcomeDialog

    dlg = _settled(WelcomeDialog(), VERY_LONG, VERY_LONG, app)
    try:
        assert dlg.height() > BASE_H, (
            "dialog stayed at its base height for a message that does not fit"
        )
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_is_not_pinned_to_a_fixed_size():
    """setFixedSize is what made the clipping unfixable — keep it gone."""
    from pathlib import Path

    import refrain.ui.welcome_dialog as mod

    # Match a call, not the word — the code comment explains why it is gone.
    src = Path(mod.__file__).read_text()
    assert not re.search(r"\bsetFixedSize\s*\(", src)


def test_growth_never_shrinks_the_dialog(app):
    """A later, shorter message must not pull the window back in."""
    from refrain.ui.welcome_dialog import WelcomeDialog

    dlg = _settled(WelcomeDialog(), VERY_LONG, VERY_LONG, app)
    try:
        grown = dlg.height()
        assert grown > BASE_H
        dlg._on_diag_finished(True, SHORT, True, SHORT)
        for _ in range(6):
            app.processEvents()
        assert dlg.height() >= grown
    finally:
        dlg.close()
        dlg.deleteLater()
