"""The welcome wizard's Discord and iTunes probes, its diagnostics rows and its Apply flow."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTranslator  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import refrain.ui.welcome_dialog as wd  # noqa: E402

CLIENT_ID = "1234567890123456789"


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def runtime(monkeypatch):
    """Empty XDG_RUNTIME_DIR and HOME; short paths so AF_UNIX addresses fit."""
    with tempfile.TemporaryDirectory(prefix="rf-", dir="/tmp") as root:
        run = Path(root) / "run"
        home = Path(root) / "home"
        run.mkdir()
        home.mkdir()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run))
        monkeypatch.setenv("HOME", str(home))
        yield run, home


@pytest.fixture
def listening():
    """Bind a real AF_UNIX socket; listen() makes it answer, else it refuses."""
    socks = []

    def _bind(path: Path, listen: bool = True) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(path))
        if listen:
            s.listen(1)
        socks.append(s)
        return path

    yield _bind
    for s in socks:
        s.close()


def test_a_listening_discord_socket_is_found(app, runtime, listening):
    run, _ = runtime
    path = listening(run / "discord-ipc-0")
    ok, template, kw = wd._probe_discord_ipc()
    assert ok is True
    assert str(path) in template.format(**kw)


def test_a_stale_socket_file_is_not_discord(app, runtime, listening):
    run, _ = runtime
    listening(run / "discord-ipc-0", listen=False)
    ok, template, kw = wd._probe_discord_ipc()
    assert ok is False
    assert "No Discord IPC socket found" in template.format(**kw)


def test_a_later_socket_number_is_tried_after_a_stale_one(app, runtime, listening):
    run, _ = runtime
    listening(run / "discord-ipc-0", listen=False)
    path = listening(run / "discord-ipc-3")
    ok, template, kw = wd._probe_discord_ipc()
    assert ok is True
    assert str(path) in template.format(**kw)


def test_the_flatpak_socket_counts(app, runtime, listening):
    run, _ = runtime
    path = listening(run / "app" / "com.discordapp.Discord" / "discord-ipc-0")
    assert wd._probe_discord_ipc() == (True, "Found Discord IPC at {path}", {"path": path})


def test_the_snap_socket_counts_without_a_runtime_dir(app, runtime, listening, monkeypatch):
    _, home = runtime
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    path = listening(
        home / "snap" / "discord" / "current" / ".config" / "discord" / "discord-ipc-1"
    )
    ok, template, kw = wd._probe_discord_ipc()
    assert ok is True
    assert str(path) in template.format(**kw)


def test_no_socket_means_no_discord(app, runtime):
    ok, template, kw = wd._probe_discord_ipc()
    assert ok is False
    assert "start the Discord desktop app" in template.format(**kw)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _stub_urlopen(monkeypatch, result):
    seen = []

    def urlopen(request, timeout):
        # Refrain names itself on every request; the probe is no exception.
        seen.append((request.full_url, request.get_header("User-agent"), timeout))
        if isinstance(result, Exception):
            raise result
        return _Response(result)

    monkeypatch.setattr(wd.urllib.request, "urlopen", urlopen)
    return seen


def test_an_itunes_answer_means_reachable(app, monkeypatch):
    seen = _stub_urlopen(monkeypatch, json.dumps({"resultCount": 1, "results": []}).encode())
    assert wd._probe_itunes() == (True, "iTunes Search API reachable.", {})
    assert seen == [(wd._ITUNES_TEST_URL, wd.USER_AGENT, 5)]


def test_an_odd_itunes_payload_is_reported(app, monkeypatch):
    _stub_urlopen(monkeypatch, b"[]")
    ok, template, kw = wd._probe_itunes()
    assert ok is False
    assert "payload looked off" in template.format(**kw)


def test_an_unreachable_itunes_names_the_reason(app, monkeypatch):
    _stub_urlopen(monkeypatch, urllib.error.URLError("Name or service not known"))
    ok, template, kw = wd._probe_itunes()
    assert ok is False
    assert template.format(**kw) == "iTunes Search unreachable: Name or service not known"


def test_a_broken_itunes_reply_is_reported(app, monkeypatch):
    _stub_urlopen(monkeypatch, b"<html>maintenance</html>")
    ok, template, kw = wd._probe_itunes()
    assert ok is False
    assert template.format(**kw).startswith("iTunes probe failed:")


def test_the_worker_reports_both_probes(app, monkeypatch):
    monkeypatch.setattr(wd, "_probe_discord_ipc", lambda: (True, "discord fine", {}))
    monkeypatch.setattr(wd, "_probe_itunes", lambda: (False, "itunes down", {}))
    worker = wd._DiagnosticsWorker()
    got = []
    worker.finished.connect(lambda *args: got.append(args))
    worker.run()
    assert got == [(True, "discord fine", False, "itunes down")]


class _FakeTranslator(QTranslator):
    """Translates every WelcomeDialog string it sees, like a real German .qm would."""

    def translate(self, context, source_text, disambiguation=None, n=-1):
        return f"[translated] {source_text}" if context == "WelcomeDialog" else ""


def test_the_log_gets_the_english_source_text_even_when_the_ui_is_translated(app, monkeypatch):
    """An installed translator must change the UI text but never the log line."""
    monkeypatch.setattr(
        wd, "_probe_discord_ipc", lambda: (True, "Found Discord IPC at {path}", {"path": "/x"})
    )
    monkeypatch.setattr(
        wd,
        "_probe_itunes",
        lambda: (False, "iTunes Search unreachable: {reason}", {"reason": "timeout"}),
    )
    logged = []
    monkeypatch.setattr(wd.log, "info", lambda *args: logged.append(args))

    translator = _FakeTranslator()
    app.installTranslator(translator)
    try:
        worker = wd._DiagnosticsWorker()
        got = []
        worker.finished.connect(lambda *args: got.append(args))
        worker.run()
    finally:
        app.removeTranslator(translator)

    log_text = " ".join(str(a) for call in logged for a in call)
    assert "Found Discord IPC at /x" in log_text
    assert "iTunes Search unreachable: timeout" in log_text
    assert "[translated]" not in log_text

    d_ok, d_msg, i_ok, i_msg = got[0]
    assert d_msg == "[translated] Found Discord IPC at /x"
    assert i_msg == "[translated] iTunes Search unreachable: timeout"


def _dialog(app):
    dlg = wd.WelcomeDialog()
    applied = []
    dlg.applied.connect(applied.append)
    return dlg, applied


def test_diagnostics_run_in_the_background_and_fill_the_rows(app, monkeypatch):
    monkeypatch.setattr(wd, "_probe_discord_ipc", lambda: (True, "socket found", {}))
    monkeypatch.setattr(wd, "_probe_itunes", lambda: (False, "offline", {}))
    dlg, _ = _dialog(app)
    try:
        dlg.start_diagnostics()
        deadline = time.monotonic() + 5
        while dlg._diag_thread is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert dlg._diag_thread is None and dlg._diag_worker is None
        assert dlg._diag_discord.text() == "✅ <b>Discord:</b> socket found"
        assert dlg._diag_itunes.text() == "⚠️ <b>Cover-art lookup:</b> offline"
    finally:
        dlg.deleteLater()


def test_a_valid_id_is_applied(app):
    dlg, applied = _dialog(app)
    dlg.client_id_edit.setText(f"  {CLIENT_ID} ")
    dlg._on_apply()
    assert applied == [CLIENT_ID]
    assert dlg.result() == QDialog.Accepted


@pytest.mark.parametrize("bad", ["abc", "12345", "123456789012345678901", "1234-5678-9012-3456"])
def test_an_invalid_id_is_refused(app, monkeypatch, bad):
    warned = []
    monkeypatch.setattr(wd.QMessageBox, "warning", lambda *a: warned.append(a[1]))
    dlg, applied = _dialog(app)
    dlg.client_id_edit.setText(bad)
    dlg._on_apply()
    assert warned == ["Invalid Application ID"]
    assert applied == []
    assert dlg.result() != QDialog.Accepted


class _Confirm:
    """QMessageBox stand-in that clicks the button at ``pick``."""

    Question = QMessageBox.Question
    AcceptRole = QMessageBox.AcceptRole
    RejectRole = QMessageBox.RejectRole
    pick = 0
    shown: list[str] = []

    def __init__(self, parent):
        self._buttons = []

    def setIcon(self, icon): ...  # noqa: N802

    def setWindowTitle(self, title):  # noqa: N802
        type(self).shown.append(title)

    def setText(self, text): ...  # noqa: N802

    def setDefaultButton(self, button):  # noqa: N802
        self.default = button

    def addButton(self, text, role):  # noqa: N802
        self._buttons.append((text, role))
        return self._buttons[-1]

    def exec(self):
        return 0

    def clickedButton(self):  # noqa: N802
        return self._buttons[self.pick]


@pytest.mark.parametrize(("pick", "expected"), [(0, [""]), (1, [])])
def test_an_empty_id_asks_before_going_without_discord(app, monkeypatch, pick, expected):
    confirm = type("Confirm", (_Confirm,), {"pick": pick, "shown": []})
    monkeypatch.setattr(wd, "QMessageBox", confirm)
    dlg, applied = _dialog(app)
    dlg._on_apply()
    assert confirm.shown == ["Skip Discord setup?"]
    assert applied == expected


def test_the_empty_id_question_defaults_to_cancel(app, monkeypatch):
    boxes = []

    class _Remember(_Confirm):
        def __init__(self, parent):
            super().__init__(parent)
            boxes.append(self)

    _Remember.pick = 1
    _Remember.shown = []
    monkeypatch.setattr(wd, "QMessageBox", _Remember)
    dlg, _ = _dialog(app)
    dlg._on_apply()
    assert boxes[0].default == ("Cancel", QMessageBox.RejectRole)


def test_skip_finishes_the_wizard_without_an_id(app):
    dlg, applied = _dialog(app)
    dlg._on_skip()
    assert applied == [""]
    assert dlg.result() == QDialog.Accepted


def test_closing_the_window_counts_as_skip_and_stops_the_probes(app, monkeypatch):
    stopped = []

    class _Thread:
        def quit(self):
            stopped.append("quit")

        def wait(self, ms):
            stopped.append(ms)
            raise RuntimeError("already deleted")

    dlg, applied = _dialog(app)
    dlg._diag_thread = _Thread()
    dlg._diag_worker = object()
    dlg.reject()
    assert applied == [""]
    assert stopped == ["quit", 500]
    assert dlg._diag_thread is None and dlg._diag_worker is None
    assert dlg.result() == QDialog.Rejected
