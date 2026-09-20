"""Apply hands out a new Config, so everyone holding the old one sees what changed."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import refrain.ui.settings_window as sw  # noqa: E402
from refrain.config import Config  # noqa: E402


@pytest.fixture
def win(monkeypatch):
    QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    config = Config()
    config.discord.client_id = "1234567890123456789"
    config.advanced.poll_interval_ms = 1500
    config.history.window_width = 900
    config.update.last_check_ts = 1_700_000_000
    window = sw.SettingsWindow(config)
    sent = []
    window.applied.connect(sent.append)
    return window, config, sent


def test_apply_sends_a_new_object_and_leaves_the_old_one_alone(win):
    window, config, sent = win
    window.poll_spin.setValue(3000)
    window._on_apply_clicked()
    assert len(sent) == 1 and sent[0] is not config
    assert sent[0].advanced.poll_interval_ms == 3000
    assert config.advanced.poll_interval_ms == 1500


def test_cancel_and_discard_forgets_the_form(win, monkeypatch):
    window, config, sent = win
    window.poll_spin.setValue(3000)
    _answer(monkeypatch, "Discard")
    window.reject()
    assert window.poll_spin.value() == 1500
    assert sent == []


def test_reset_saves_at_once(win):
    window, config, sent = win
    window.poll_spin.setValue(3000)
    _confirm_reset(window)
    assert len(sent) == 1
    assert Config.load().advanced.poll_interval_ms == Config().advanced.poll_interval_ms
    assert window.poll_spin.value() == Config().advanced.poll_interval_ms
    assert not window.is_dirty()


def test_reset_resets_what_the_form_does_not_show(win):
    window, config, sent = win
    _confirm_reset(window)
    out = sent[-1]
    assert out.advanced.poll_interval_ms == Config().advanced.poll_interval_ms
    assert out.history.window_width == Config().history.window_width
    assert out.discord.client_id == "1234567890123456789"
    assert out.update.last_check_ts == 1_700_000_000


def _answer(monkeypatch, text):
    def pick(box):
        button = next(b for b in box.buttons() if b.text() == text)
        box.clickedButton = lambda: button
        return 0

    monkeypatch.setattr(QMessageBox, "exec", pick)


def _confirm_reset(window):
    real_exec = QMessageBox.exec

    def pick_reset(box):
        reset = next(b for b in box.buttons() if b.text() == "Reset")
        box.clickedButton = lambda: reset
        return 0

    QMessageBox.exec = pick_reset
    try:
        window._on_reset_clicked()
    finally:
        QMessageBox.exec = real_exec


def test_the_daemon_notices_what_apply_changed(monkeypatch):
    import copy

    import refrain.daemon as daemon

    made = []

    class FakeRPC:
        def __init__(self, client_id, all_clients):
            made.append((client_id, all_clients))

        def close(self):
            pass

        def _ensure_connected(self):
            return False

    monkeypatch.setattr(daemon, "DiscordRPC", FakeRPC)
    monkeypatch.setattr(daemon.QTimer, "singleShot", lambda *_a: None)
    config = Config()
    config.discord.client_id = "1234567890123456789"
    worker = daemon.DaemonWorker(config)
    new = copy.deepcopy(config)
    new.discord.all_clients = not config.discord.all_clients
    worker.update_config(new)
    assert made[-1] == ("1234567890123456789", new.discord.all_clients)
    assert len(made) == 2


def test_the_updater_follows_new_settings():
    from refrain.app import UpdateOrchestrator

    updater = UpdateOrchestrator(Config())
    new = Config()
    updater.use_config(new)
    assert updater._config is new
    assert updater.latest is None


def test_a_device_that_is_not_connected_survives_apply(monkeypatch):
    QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    config = Config()
    config.sources.bluetooth_device = "12:34:56:78:9A:BC"
    window = sw.SettingsWindow(config)
    sent = []
    window.applied.connect(sent.append)
    window._on_apply_clicked()
    assert sent[-1].sources.bluetooth_device == "12:34:56:78:9A:BC"
    window.bluetooth_device.setCurrentIndex(0)
    window._on_apply_clicked()
    assert sent[-1].sources.bluetooth_device == ""


def _wait_for(condition, ms=2000):
    import time

    deadline = time.monotonic() + ms / 1000
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    return condition()


def test_a_slow_bluetooth_stack_does_not_freeze_the_window(monkeypatch):
    import threading
    import time

    QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    answer = threading.Event()
    devices = [{"name": "Headphones", "address": "12:34:56:78:9A:BC", "connected": True}]

    def slow_bluez():
        answer.wait(2)
        return devices

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(slow_bluez))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    config = Config()
    config.sources.bluetooth_device = "AA:BB:CC:DD:EE:FF"
    started = time.monotonic()
    window = sw.SettingsWindow(config)
    assert time.monotonic() - started < 1
    answer.set()
    assert _wait_for(lambda: window.bluetooth_device.count() == 3)
    assert window.bluetooth_device.itemData(1) == "12:34:56:78:9A:BC"
    assert window.bluetooth_device.currentData() == "AA:BB:CC:DD:EE:FF"


def test_refresh_lists_devices_paired_since(monkeypatch):
    QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    paired = []
    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: list(paired)))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    window = sw.SettingsWindow(Config())
    assert _wait_for(lambda: window._bluetooth_worker is None)
    paired.append({"name": "Speaker", "address": "12:34:56:78:9A:BC", "connected": False})
    window._populate_bluetooth_devices()
    assert _wait_for(lambda: window.bluetooth_device.count() == 2)
    assert window.bluetooth_device.currentData() == ""


def test_legal_and_approval_dialogs_are_freed_after_closing(win, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QDialog

    from refrain.ui.legal_dialog import LegalDialog

    window, _, _ = win
    monkeypatch.setattr(LegalDialog, "exec", lambda self: 0)
    monkeypatch.setattr(sw.LastfmApprovalDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(sw.QDesktopServices, "openUrl", lambda url: True)
    window._lastfm_client = sw.LastfmClient("key", "secret")
    window._show_legal()
    window._on_lastfm_token("T")
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChildren(LegalDialog) == []
    assert window.findChildren(sw.LastfmApprovalDialog) == []


def test_last_checked_follows_a_new_check(win):
    from refrain.updater import ReleaseInfo

    window, config, _ = win
    before = window.last_check_label.text()
    config.update.last_check_ts = 1_800_000_000
    window.set_latest_release(
        ReleaseInfo(tag="v0.5.3", version="0.5.3", name="", body="", html_url="")
    )
    assert window.last_check_label.text() != before


def test_privacy_off_in_the_form_already_stops_the_name_lookup(monkeypatch):
    QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    asked = []
    monkeypatch.setattr(sw, "fetch_application_name", lambda cid: asked.append(cid))
    config = Config()
    config.discord.resolve_app_name = True
    window = sw.SettingsWindow(config)
    window.privacy_combo.setCurrentIndex(window.privacy_combo.findData("off"))
    window.client_id_input.setText("12345678901234567")
    window._lookup_application_name()
    assert asked == []


def test_release_note_links_open_https_only(win, monkeypatch):
    from PySide6.QtCore import QUrl

    opened = []
    monkeypatch.setattr(
        "refrain.ui.external_link.confirm_and_open",
        lambda parent, url, player="": opened.append(url) or True,
    )
    window, _, _ = win
    assert window.release_notes_view.openLinks() is False
    window.release_notes_view.anchorClicked.emit(QUrl("file:///etc/passwd"))
    window.release_notes_view.anchorClicked.emit(QUrl("https://github.com/Rockykln/refrain"))
    assert opened == ["https://github.com/Rockykln/refrain"]


def test_the_last_fm_tab_links_back_to_last_fm(win, monkeypatch):

    opened = []
    monkeypatch.setattr(
        "refrain.ui.external_link.confirm_and_open",
        lambda parent, url, player="": opened.append(url) or True,
    )
    window, _, _ = win
    assert "https://www.last.fm" in window.lastfm_attribution.text()
    window.lastfm_attribution.linkActivated.emit("https://www.last.fm")
    assert opened == ["https://www.last.fm"]


def _record_boxes(monkeypatch):
    shown = []
    for kind in ("warning", "critical"):
        monkeypatch.setattr(
            sw.QMessageBox,
            kind,
            lambda _parent, title, text, *a, _kind=kind, **k: shown.append((_kind, title, text)),
        )
    return shown


def test_apply_warns_when_the_last_fm_credentials_cannot_be_stored(win, xdg_tmp, monkeypatch):
    from refrain import secrets_store

    window, _config, sent = win
    locked = xdg_tmp["config"] / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setattr(secrets_store, "_fallback_path", lambda: locked / "secrets.json")
    shown = _record_boxes(monkeypatch)
    window._lastfm_session_key = "sk-12345"
    window.lastfm_secret_input.setText("shared-12345")
    try:
        window._on_apply_clicked()
    finally:
        locked.chmod(0o700)
    assert [(k, t) for k, t, _ in shown] == [("warning", "Could not store Last.fm credentials")]
    assert "could not store your Last.fm credentials" in shown[0][2]
    assert len(sent) == 1


def test_apply_stays_quiet_when_the_credentials_are_stored(win, xdg_tmp, monkeypatch):
    window, _config, sent = win
    shown = _record_boxes(monkeypatch)
    window._lastfm_session_key = "sk-12345"
    window.lastfm_secret_input.setText("shared-12345")
    window._on_apply_clicked()
    assert shown == []
    assert len(sent) == 1
    assert (xdg_tmp["config"] / "refrain" / "secrets.json").exists()
