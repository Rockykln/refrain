"""Last.fm connection status: usable-triple logic + UI rendering.

Regression for the 0.4.0 report: status showed "Connected as
(connected)" (empty username) and claimed "Connected" when only the
keyring session survived but the api_key/secret were missing
(scrobble-inert, misleading).
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.config import Config  # noqa: E402
from refrain.ui.settings_window import (  # noqa: E402
    SettingsWindow,
    lastfm_connection_state,
)

# --------------------------------------------------------------------------- #
# pure logic (no QApplication needed)                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sk,ak,ss,expected",
    [
        ("S", "A", "K", "connected"),
        ("S", "A", "", "incomplete"),  # secret missing
        ("S", "", "K", "incomplete"),  # api_key missing (the report)
        ("S", "", "", "incomplete"),
        ("", "A", "K", "disconnected"),  # no session
        ("", "", "", "disconnected"),
        ("  ", "A", "K", "disconnected"),  # whitespace == empty
        ("S", "  ", "K", "incomplete"),
    ],
)
def test_connection_state(sk, ak, ss, expected):
    assert lastfm_connection_state(sk, ak, ss) == expected


# --------------------------------------------------------------------------- #
# UI rendering                                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def _win(app, *, api_key="", secret="", session="", username=""):
    c = Config()
    c.lastfm.api_key = api_key
    c.lastfm.shared_secret = secret
    c.lastfm.session_key = session
    c.lastfm.username = username
    return SettingsWindow(c)


def test_status_fully_connected_with_username(app):
    w = _win(app, api_key="A", secret="K", session="S", username="Rockykln")
    assert w.lastfm_status_label.text() == "Connected as Rockykln"
    assert w.lastfm_connect_btn.text() == "Disconnect"


def test_status_connected_without_username_is_not_doubled(app):
    # The "Connected as Connected"/"(connected)" bug: no username but a
    # fully usable connection → just "Connected", never "… as …".
    w = _win(app, api_key="A", secret="K", session="S", username="")
    assert w.lastfm_status_label.text() == "Connected"
    assert "as (" not in w.lastfm_status_label.text()
    assert w.lastfm_connect_btn.text() == "Disconnect"


def test_status_incomplete_session_without_apikey(app):
    # The reported scenario: keyring kept session+secret, config has no
    # api_key → must NOT claim "Connected".
    w = _win(app, api_key="", secret="K", session="S", username="Rockykln")
    txt = w.lastfm_status_label.text()
    assert "Not connected" in txt and "re-enter" in txt
    assert w.lastfm_connect_btn.text() == "Connect…"


def test_status_disconnected(app):
    w = _win(app)
    assert w.lastfm_status_label.text() == "Not connected"
    assert w.lastfm_connect_btn.text() == "Connect…"


def test_incomplete_connect_button_runs_connect_not_disconnect(app):
    # In the "incomplete" state the button must NOT silently wipe the
    # leftover session as a "Disconnect"; with empty api_key/secret it
    # should warn the user to enter them (connect path).
    w = _win(app, api_key="", secret="", session="S", username="")
    w._lastfm_session_key = "S"
    # Patch the warning dialog so the test stays headless/non-blocking.
    seen = {}
    import refrain.ui.settings_window as m

    orig = m.QMessageBox.warning
    m.QMessageBox.warning = lambda *a, **k: seen.setdefault("warned", True)
    try:
        w._on_lastfm_connect()
    finally:
        m.QMessageBox.warning = orig
    # Connect path was taken (it warned about missing key/secret), the
    # leftover session was NOT cleared as a disconnect.
    assert seen.get("warned") is True
    assert w._lastfm_session_key == "S"
