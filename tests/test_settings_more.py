"""Settings window: what Apply, the Last.fm connect flow and the confirm dialogs do for the user."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import refrain.ui.settings_window as sw  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.scrobble import LastfmError  # noqa: E402
from refrain.updater import ReleaseInfo  # noqa: E402


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(
            sw.QMessageBox,
            kind,
            lambda _parent, title, text, _kind=kind: shown.append((_kind, title, text)),
        )
    return shown


@pytest.fixture
def win(monkeypatch, boxes):
    app = QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    config = Config()
    config.discord.client_id = "1234567890123456789"
    window = sw.SettingsWindow(config)
    window.sent, window.restarts = [], []
    window.applied.connect(window.sent.append)
    window.restartRequested.connect(lambda: window.restarts.append(1))
    yield window
    window._mark_clean()
    window.close()
    window.deleteLater()
    app.processEvents()


def _click(monkeypatch, text):
    """Make the next QMessageBox.exec answer with the button labelled ``text``."""

    def pick(box):
        button = next(b for b in box.buttons() if b.text() == text)
        box.clickedButton = lambda: button
        return 0

    monkeypatch.setattr(QMessageBox, "exec", pick)


def _release(version):
    return ReleaseInfo(
        tag=f"v{version}",
        version=version,
        name=f"Refrain {version}",
        body="Glass Tides now shows its cover.",
        html_url=f"https://github.com/Rockykln/refrain/releases/tag/v{version}",
    )


# ------------------------------------------------------------------ Apply


def test_apply_saves_and_keeps_the_window_open(win):
    win.show()
    win.poll_spin.setValue(2500)
    win._on_apply_clicked()
    assert win.sent[-1].advanced.poll_interval_ms == 2500
    assert win.restarts == []
    assert win.isVisible()


def test_a_new_language_restarts_refrain_after_asking(win, monkeypatch):
    other = next(
        i for i in range(win.language_combo.count()) if win.language_combo.itemData(i) != "system"
    )
    win.language_combo.setCurrentIndex(other)
    _click(monkeypatch, "Save and restart")
    win._on_apply_clicked()
    assert win.sent[-1].advanced.language == win.language_combo.itemData(other)
    assert win.restarts == [1]


def test_a_new_discord_client_id_applies_without_a_restart(win):
    win.client_id_input.setText(" 1234567890123456780 ")
    win._on_apply_clicked()
    assert win.sent[-1].discord.client_id == "1234567890123456780"
    assert win.restarts == []


def test_per_source_ids_are_dropped_when_the_switch_is_off(win):
    win.discord_per_source_box.setChecked(True)
    win.client_id_mpris_input.setText("1234567890123456781")
    win.client_id_bluetooth_input.setText("1234567890123456782")
    win._on_apply_clicked()
    assert win.sent[-1].discord.client_id_mpris == "1234567890123456781"
    assert win.sent[-1].discord.client_id_bluetooth == "1234567890123456782"

    win.discord_per_source_box.setChecked(False)
    win._on_apply_clicked()
    assert win.sent[-1].discord.client_id_mpris == ""
    assert win.sent[-1].discord.client_id_bluetooth == ""


def test_a_config_that_cannot_be_saved_is_reported_but_still_applied(win, boxes, monkeypatch):
    def refuse(self, *a, **kw):
        raise OSError("No space left on device")

    monkeypatch.setattr(Config, "save", refuse)
    win.poll_spin.setValue(4000)
    win._on_apply_clicked()
    assert [kind for kind, _title, _text in boxes] == ["critical"]
    assert "No space left on device" in boxes[0][2]
    assert win.sent[-1].advanced.poll_interval_ms == 4000


def test_typed_browser_names_are_kept_after_the_ticked_ones(win):
    for cb in win._browser_checkboxes.values():
        cb.setChecked(False)
    first = next(iter(win._browser_checkboxes))
    win._browser_checkboxes[first].setChecked(True)
    win.browser_extra_input.setText(f" Vivaldi , {first.upper()}, ,vivaldi")
    win._on_apply_clicked()
    assert win.sent[-1].sources.browser_hints == f"{first},vivaldi"


# ------------------------------------------------------------- confirm boxes


@pytest.mark.parametrize(
    "method,button,expected",
    [
        ("_confirm_history_off", "Turn off and delete", True),
        ("_confirm_history_off", "Cancel", False),
        ("_confirm_lastfm_disconnect", "Disconnect", True),
        ("_confirm_lastfm_disconnect", "Cancel", False),
        ("_confirm_lastfm_unconnected", "Apply anyway", True),
        ("_confirm_lastfm_unconnected", "Cancel", False),
    ],
)
def test_confirm_boxes_only_agree_on_their_own_button(win, monkeypatch, method, button, expected):
    _click(monkeypatch, button)
    assert getattr(win, method)() is expected


def test_uninstall_goes_ahead_only_when_confirmed(win, monkeypatch, tmp_path):
    import refrain.uninstall as uninstall

    monkeypatch.setattr(uninstall, "collect_paths", lambda: [tmp_path / "refrain"])
    asked = []
    win.uninstallRequested.connect(lambda: asked.append(1))

    _click(monkeypatch, "Cancel")
    win._on_uninstall_clicked()
    assert asked == []

    _click(monkeypatch, "Uninstall")
    win._on_uninstall_clicked()
    assert asked == [1]


def test_uninstall_question_defaults_to_cancel(win, monkeypatch, tmp_path):
    import refrain.uninstall as uninstall

    monkeypatch.setattr(uninstall, "collect_paths", lambda: [tmp_path / "refrain"])
    defaults = []

    def record(box):
        defaults.append(box.defaultButton().text())
        box.clickedButton = lambda: None
        return 0

    monkeypatch.setattr(QMessageBox, "exec", record)
    win._on_uninstall_clicked()
    assert defaults == ["Cancel"]


def test_reset_question_defaults_to_cancel(win, monkeypatch):
    defaults = []

    def record(box):
        defaults.append(box.defaultButton().text())
        box.clickedButton = lambda: None
        return 0

    monkeypatch.setattr(QMessageBox, "exec", record)
    win._on_reset_clicked()
    assert defaults == ["Cancel"]


# ------------------------------------------------------------- update tab


def test_latest_release_label_follows_each_check(win):
    for version, text in (
        ("99.0.0", "99.0.0 (update available)"),
        ("0.0.1", "0.0.1 (up to date)"),
    ):
        win.set_latest_release(_release(version))
        assert win.latest_version_label.text() == text
        assert "Glass Tides" in win.release_notes_view.toPlainText()

    win.set_latest_release(None)
    assert win.latest_version_label.text() == "(check failed)"
    assert "Could not reach GitHub" in win.release_notes_view.toPlainText()


# ------------------------------------------------------------ Last.fm flow


def test_connect_without_api_credentials_asks_for_them_first(win, boxes):
    win.lastfm_api_key_input.setText("")
    win.lastfm_secret_input.setText("12345secret")
    win._on_lastfm_connect()
    assert boxes and boxes[0][0] == "warning"
    assert "API key and shared secret" in boxes[0][2]
    assert win._lastfm_auth_thread is None


class _Approval:
    """Stand-in for LastfmApprovalDialog with a fixed outcome."""

    outcome = (QDialog.DialogCode.Rejected, "", "", "")

    def __init__(self, client, token, open_page, parent=None):
        self.token = token
        _code, self.session_key, self.username, self.error = self.outcome

    def exec(self):
        return self.outcome[0]

    def deleteLater(self):
        pass


def _token_flow(win, monkeypatch, outcome):
    opened = []
    monkeypatch.setattr(sw.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    monkeypatch.setattr(_Approval, "outcome", outcome)
    monkeypatch.setattr(sw, "LastfmApprovalDialog", _Approval)
    win._lastfm_client = sw.LastfmClient("12345apikey", "12345secret")
    win._on_lastfm_token("12345token")
    return opened


def test_an_approved_token_connects_the_account(win, monkeypatch, boxes):
    opened = _token_flow(
        win,
        monkeypatch,
        (QDialog.DialogCode.Accepted, "12345session", "marlowvance", ""),
    )
    assert opened and "token=12345token" in opened[0]
    assert opened[0].startswith("https://www.last.fm/api/auth/")
    assert win._lastfm_session_key == "12345session"
    assert win._lastfm_username == "marlowvance"
    assert win._lastfm_token == ""
    assert boxes == [
        (
            "information",
            "Last.fm",
            "Connected as marlowvance. Scrobbling starts with the next song.",
        )
    ]


def test_an_abandoned_approval_reports_why(win, monkeypatch, boxes):
    _token_flow(
        win,
        monkeypatch,
        (QDialog.DialogCode.Rejected, "", "", "No approval arrived. Click Connect to start again."),
    )
    assert win._lastfm_session_key == ""
    assert win._lastfm_token == ""
    assert boxes == [
        (
            "warning",
            "Last.fm connection failed",
            "No approval arrived. Click Connect to start again.",
        )
    ]


def test_cancelling_the_approval_says_nothing(win, monkeypatch, boxes):
    _token_flow(win, monkeypatch, (QDialog.DialogCode.Rejected, "", "", ""))
    assert boxes == []
    assert win._lastfm_session_key == ""


def test_a_session_without_a_user_name_still_connects(win, boxes):
    win._on_lastfm_session("12345session", "")
    assert win._lastfm_session_key == "12345session"
    assert boxes[-1][2] == "Connected. Scrobbling starts with the next song."


def test_a_failed_token_request_is_shown(win, boxes):
    win._lastfm_token = "12345token"
    win._on_lastfm_auth_failed("Last.fm error 10: Invalid API key", 10)
    assert win._lastfm_token == ""
    assert boxes == [
        (
            "warning",
            "Last.fm connection failed",
            "Could not connect to Last.fm:\n\nLast.fm error 10: Invalid API key",
        )
    ]


class _Client:
    def __init__(self, token=None, session=None, error=None):
        self._token, self._session, self._error = token, session, error

    def get_token(self):
        if self._error:
            raise self._error
        return self._token

    def get_session(self, _token):
        if self._error:
            raise self._error
        return self._session


def _run_worker(client, phase):
    worker = sw._LastfmAuthWorker(client, phase, "12345token")
    got = []
    worker.tokenReady.connect(lambda t: got.append(("token", t)))
    worker.sessionReady.connect(lambda k, n: got.append(("session", k, n)))
    worker.failed.connect(lambda m, c: got.append(("failed", m, c)))
    worker.run()
    return got


@pytest.mark.parametrize(
    "client,phase,expected",
    [
        (_Client(token="12345token"), "token", [("token", "12345token")]),
        (
            _Client(session=("12345session", "marlowvance")),
            "session",
            [("session", "12345session", "marlowvance")],
        ),
        (
            _Client(error=LastfmError("Last.fm error 14: Unauthorized Token", code=14)),
            "session",
            [("failed", "Last.fm error 14: Unauthorized Token", 14)],
        ),
        (
            _Client(error=LastfmError("Last.fm request failed: timed out")),
            "token",
            [("failed", "Last.fm request failed: timed out", -1)],
        ),
        (
            _Client(error=KeyError("session")),
            "session",
            [("failed", "Unexpected Last.fm error: 'session'", -1)],
        ),
    ],
)
def test_the_auth_worker_reports_every_outcome_as_a_signal(win, client, phase, expected):
    assert _run_worker(client, phase) == expected


def test_the_approval_dialog_stops_on_a_rejected_key(win):
    dialog = sw.LastfmApprovalDialog(_Client(), "12345token", lambda: None, win, poll_ms=60_000)
    worker = sw._LastfmAuthWorker(_Client(), "session", "12345token")
    dialog._worker = worker
    worker.failed.connect(dialog._on_failed)
    worker.failed.emit("Last.fm error 10: Invalid API key", 10)
    assert dialog.error == "Last.fm error 10: Invalid API key"
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_the_approval_dialog_ignores_a_late_answer(win):
    dialog = sw.LastfmApprovalDialog(_Client(), "12345token", lambda: None, win, poll_ms=60_000)
    stale = sw._LastfmAuthWorker(_Client(), "session", "12345token")
    stale.sessionReady.connect(dialog._on_session)
    stale.failed.connect(dialog._on_failed)
    stale.sessionReady.emit("12345session", "marlowvance")
    stale.failed.emit("Last.fm error 10: Invalid API key", 10)
    assert dialog.session_key == ""
    assert dialog.error == ""
    dialog.reject()
