"""Last.fm connection status: usable-triple logic + UI rendering.

Regression for the 0.4.0 report: status showed "Connected as
(connected)" (empty username) and claimed "Connected" when only the
keyring session survived but the api_key/secret were missing
(scrobble-inert, misleading).

NOTE: an earlier version of this file constructed a fresh
``SettingsWindow`` per test (6+ heavy QDialogs, each with QThread
refs + a D-Bus Bluetooth probe, no teardown). On the offscreen QPA
that intermittently SIGSEGV'd in Qt teardown — flaky CI red (tests
#63, py3.12). This version builds **one** window for the module,
stubs the Bluetooth D-Bus probe, and re-drives ``_load_into_form``
per case.
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
# pure logic — no QApplication, fully deterministic                            #
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
# UI rendering — ONE window, stubbed Bluetooth (no D-Bus), reused per case      #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication(sys.argv)
    # The Sources tab probes BlueZ over D-Bus in __init__; stub it so
    # the test never touches a system bus and construction stays cheap.
    from refrain.sources.bluetooth import BluetoothSource

    orig = BluetoothSource.list_paired_devices
    BluetoothSource.list_paired_devices = staticmethod(lambda: [])
    w = SettingsWindow(Config())
    try:
        yield w
    finally:
        BluetoothSource.list_paired_devices = orig
        w.close()
        w.deleteLater()
        app.processEvents()


def _load(win, *, api_key="", secret="", session="", username=""):
    """Re-drive the form for one credential combination."""
    c = win._config
    c.lastfm.api_key = api_key
    c.lastfm.shared_secret = secret
    c.lastfm.session_key = session
    c.lastfm.username = username
    win._load_into_form()
    return win


def test_status_fully_connected_with_username(win):
    _load(win, api_key="A", secret="K", session="S", username="Rockykln")
    assert win.lastfm_status_label.text() == "Connected as Rockykln"
    assert win.lastfm_connect_btn.text() == "Disconnect"


def test_status_connected_without_username_is_not_doubled(win):
    # The "Connected as Connected"/"(connected)" bug: no username but a
    # fully usable connection → just "Connected", never "… as …".
    _load(win, api_key="A", secret="K", session="S", username="")
    assert win.lastfm_status_label.text() == "Connected"
    assert "as (" not in win.lastfm_status_label.text()
    assert win.lastfm_connect_btn.text() == "Disconnect"


def test_status_incomplete_session_without_apikey(win):
    # The reported scenario: keyring kept session+secret, config has no
    # api_key → must NOT claim "Connected".
    _load(win, api_key="", secret="K", session="S", username="Rockykln")
    txt = win.lastfm_status_label.text()
    assert "Not connected" in txt and "re-enter" in txt
    assert win.lastfm_connect_btn.text() == "Connect…"


def test_status_disconnected(win):
    _load(win)
    assert win.lastfm_status_label.text() == "Not connected"
    assert win.lastfm_connect_btn.text() == "Connect…"


def test_incomplete_connect_button_runs_connect_not_disconnect(win, monkeypatch):
    # In the "incomplete" state the button must NOT silently wipe the
    # leftover session as a "Disconnect"; with empty api_key/secret it
    # warns the user to enter them (the connect path).
    _load(win, api_key="", secret="", session="S", username="")
    seen = {}
    import refrain.ui.settings_window as m

    monkeypatch.setattr(m.QMessageBox, "warning", lambda *a, **k: seen.setdefault("warned", True))
    win._on_lastfm_connect()
    assert seen.get("warned") is True
    assert win._lastfm_session_key == "S"  # leftover NOT cleared as disconnect


# --------------------------------------------------------------------------- #
# connecting: wait for the approval, confirm a disconnect, warn when unusable   #
# --------------------------------------------------------------------------- #


def test_disconnect_asks_first(win, monkeypatch):
    _load(win, api_key="A", secret="K", session="S", username="Rockykln")
    monkeypatch.setattr(win, "_confirm_lastfm_disconnect", lambda: False)
    win._on_lastfm_connect()
    assert win._lastfm_session_key == "S", "one click no longer disconnects"
    monkeypatch.setattr(win, "_confirm_lastfm_disconnect", lambda: True)
    win._on_lastfm_connect()
    assert win._lastfm_session_key == ""


def test_apply_warns_when_scrobbling_has_no_connection(win, monkeypatch):
    _load(win)
    win.lastfm_enabled_box.setChecked(True)
    asked = []
    monkeypatch.setattr(win, "_confirm_lastfm_unconnected", lambda: asked.append(1) or False)
    applied = []
    win.applied.connect(applied.append)
    win._on_apply_clicked()
    assert asked == [1]
    assert applied == [], "Cancel leaves everything as it was"
    win.lastfm_enabled_box.setChecked(False)


class _SlowToApprove:
    """Last.fm answering "not authorised yet" until the user has clicked."""

    def __init__(self, answers):
        self._answers = list(answers)

    def get_session(self, token):
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _wait(dialog, ms=3000):
    import time

    from PySide6.QtWidgets import QDialog

    dialog.open()
    deadline = time.monotonic() + ms / 1000
    while dialog.isVisible() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    return dialog.result() == QDialog.DialogCode.Accepted


def test_the_approval_is_waited_for_not_clicked_for(win):
    """Refrain asks Last.fm until the approval is there."""
    from refrain.scrobble import LastfmError
    from refrain.ui.settings_window import LastfmApprovalDialog

    not_yet = LastfmError("Unauthorized Token", code=14)
    client = _SlowToApprove([not_yet, not_yet, ("SK", "alice")])
    dialog = LastfmApprovalDialog(client, "T", lambda: None, win, poll_ms=10)
    assert _wait(dialog)
    assert (dialog.session_key, dialog.username) == ("SK", "alice")


def test_being_offline_for_a_moment_keeps_waiting(win):
    from refrain.scrobble import LastfmError
    from refrain.ui.settings_window import LastfmApprovalDialog

    client = _SlowToApprove([LastfmError("timed out"), ("SK", "alice")])
    dialog = LastfmApprovalDialog(client, "T", lambda: None, win, poll_ms=10)
    assert _wait(dialog)


def test_an_expired_page_stops_with_a_reason(win):
    from refrain.scrobble import LastfmError
    from refrain.ui.settings_window import LastfmApprovalDialog

    client = _SlowToApprove([LastfmError("Token expired", code=15)])
    dialog = LastfmApprovalDialog(client, "T", lambda: None, win, poll_ms=10)
    assert not _wait(dialog)
    assert "expired" in dialog.error


def test_it_gives_up_eventually(win):
    from refrain.scrobble import LastfmError
    from refrain.ui.settings_window import LastfmApprovalDialog

    client = _SlowToApprove([LastfmError("Unauthorized Token", code=14)])
    dialog = LastfmApprovalDialog(client, "T", lambda: None, win, poll_ms=10, give_up_ms=100)
    assert not _wait(dialog)
    assert "No approval" in dialog.error
