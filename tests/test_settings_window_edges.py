"""settings_window.py edge cases: guards on half-built widgets, stale worker answers,
the Discord app-name cache short-circuits, and a real (network-less) Last.fm auth round-trip."""

from __future__ import annotations

import os
import sys
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import refrain.ui.settings_window as sw  # noqa: E402
from refrain.config import Config  # noqa: E402

CLIENT_ID = "12345678901234567"

# The `win` fixture stubs this out (construction can trigger a lookup via
# resolve_app_name_box.setChecked); tests of the real method restore it.
_REAL_LOOKUP_APPLICATION_NAME = sw.SettingsWindow._lookup_application_name


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
    config.discord.client_id = CLIENT_ID
    window = sw.SettingsWindow(config)
    yield window
    window._mark_clean()
    window._lastfm_auth_thread = None  # a test may have left a fake one behind
    window.close()
    window.deleteLater()
    app.processEvents()


# ------------------------------------------------------------------------- #
# LastfmApprovalDialog: a poll already in flight is not asked again          #
# ------------------------------------------------------------------------- #


class _FakeLastfmClient:
    def get_session(self, token):
        return "SK", "alice"


class _NoRunThread:
    """A stand-in for threading.Thread that never actually runs its target."""

    def __init__(self, target=None, name=None, daemon=None):
        pass

    def start(self):
        pass


def test_a_poll_still_waiting_on_an_answer_is_not_asked_again(monkeypatch):
    QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(sw.threading, "Thread", _NoRunThread)
    dialog = sw.LastfmApprovalDialog(
        _FakeLastfmClient(), "token", lambda: None, poll_ms=999_999, give_up_ms=999_999
    )
    dialog._poll()
    first_worker = dialog._worker
    assert first_worker is not None

    dialog._poll()

    assert dialog._worker is first_worker, "a question still in flight must not be asked twice"
    dialog._poll_timer.stop()
    dialog._give_up.stop()


# ------------------------------------------------------------------------- #
# Guards on widgets that may not exist yet (theme change mid-construction)   #
# ------------------------------------------------------------------------- #


def test_a_theme_change_before_the_github_button_exists_is_ignored(win):
    from PySide6.QtCore import QEvent

    del win._github_btn
    win.changeEvent(QEvent(QEvent.Type.PaletteChange))  # must not raise


def test_toggling_overrides_before_the_form_exists_does_nothing(win):
    win._discord_form = None
    win.client_id_mpris_input.setVisible(False)
    before = win.client_id_mpris_input.isHidden()

    win._set_discord_overrides_visible(True)  # would normally reveal the field

    assert win.client_id_mpris_input.isHidden() == before


def test_a_form_without_row_visibility_support_still_shows_the_fields(win):
    class _NoRowVisible:
        def setRowVisible(self, _widget, _visible):
            raise TypeError("setRowVisible missing on this Qt version")

    win._discord_form = _NoRowVisible()
    win.client_id_mpris_input.setVisible(False)
    win.client_id_bluetooth_input.setVisible(False)

    win._set_discord_overrides_visible(True)

    assert not win.client_id_mpris_input.isHidden()
    assert not win.client_id_bluetooth_input.isHidden()


# ------------------------------------------------------------------------- #
# Bluetooth device refresh: stale answers and a device that vanished        #
# ------------------------------------------------------------------------- #


def test_a_stale_bluetooth_refresh_answer_is_ignored(win):
    win._bluetooth_worker = object()  # a newer refresh is (as far as we know) still in flight
    before = win.bluetooth_device.count()

    win._fill_bluetooth_devices([{"name": "Headphones", "address": "AA:BB:CC:00:00:01"}])

    assert win.bluetooth_device.count() == before


def test_a_device_no_longer_listed_stays_selected_instead_of_reverting_to_auto(win):
    win._bluetooth_worker = None
    win._fill_bluetooth_devices([{"name": "Headphones", "address": "AA:BB:CC:00:00:01"}])
    idx = win.bluetooth_device.findData("AA:BB:CC:00:00:01")
    win.bluetooth_device.setCurrentIndex(idx)

    win._bluetooth_worker = None
    win._fill_bluetooth_devices([])  # no longer paired or in range

    assert win.bluetooth_device.currentData() == "AA:BB:CC:00:00:01"


def test_a_device_still_listed_after_a_refresh_keeps_its_selection(win):
    win._bluetooth_worker = None
    win._fill_bluetooth_devices([{"name": "Headphones", "address": "AA:BB:CC:00:00:03"}])
    idx = win.bluetooth_device.findData("AA:BB:CC:00:00:03")
    win.bluetooth_device.setCurrentIndex(idx)

    win._bluetooth_worker = None
    win._fill_bluetooth_devices([{"name": "Headphones", "address": "AA:BB:CC:00:00:03"}])

    assert win.bluetooth_device.currentData() == "AA:BB:CC:00:00:03"


def test_a_bluetooth_device_no_longer_around_stays_selectable_after_reload(win):
    win._config.sources.bluetooth_device = "AA:BB:CC:00:00:02"

    win._load_into_form()

    assert win.bluetooth_device.currentData() == "AA:BB:CC:00:00:02"


def test_a_configured_bluetooth_device_already_listed_is_selected_on_load(win):
    win._bluetooth_worker = None
    win._fill_bluetooth_devices([{"name": "Headphones", "address": "AA:BB:CC:00:00:04"}])
    win._config.sources.bluetooth_device = "AA:BB:CC:00:00:04"

    win._load_into_form()

    assert win.bluetooth_device.currentData() == "AA:BB:CC:00:00:04"


# ------------------------------------------------------------------------- #
# Discord application-name lookup short-circuits                            #
# ------------------------------------------------------------------------- #


def test_turning_off_name_resolution_stops_pending_lookups_and_clears_the_name(win):
    win._show_application_name(CLIENT_ID, "found", "Glass Tides Radio")
    win._app_name_timer.start()
    assert win._app_name_timer.isActive()

    win._on_resolve_app_name_toggled(False)

    assert not win._app_name_timer.isActive()
    assert win.app_name_label.text() == ""


def test_a_recently_checked_name_is_shown_without_asking_discord_again(win, monkeypatch):
    monkeypatch.setattr(
        sw.SettingsWindow, "_lookup_application_name", _REAL_LOOKUP_APPLICATION_NAME
    )
    called = []
    monkeypatch.setattr(
        sw, "fetch_application_name", lambda cid: called.append(cid) or (sw.FOUND, "unused")
    )
    win._config.discord.app_name = "Glass Tides Radio"
    win._config.discord.app_name_for_id = CLIENT_ID
    win._config.discord.app_name_checked_ts = time.time()
    win.resolve_app_name_box.setChecked(True)
    win.client_id_input.setText(CLIENT_ID)

    win._lookup_application_name()

    assert "Glass Tides Radio" in win.app_name_label.text()
    assert called == []


def test_lookup_is_skipped_while_resolving_names_is_turned_off(win, monkeypatch):
    monkeypatch.setattr(
        sw.SettingsWindow, "_lookup_application_name", _REAL_LOOKUP_APPLICATION_NAME
    )
    win.resolve_app_name_box.setChecked(False)
    win.client_id_input.setText(CLIENT_ID)

    win._lookup_application_name()

    assert win.app_name_label.text() == ""


# ------------------------------------------------------------------------- #
# Last.fm connect: an auth round-trip already running, and a real one       #
# (offline, so it fails quickly) that starts and cleans up a real QThread   #
# ------------------------------------------------------------------------- #


def test_clicking_connect_again_mid_authorization_does_nothing(win):
    win._lastfm_session_key = ""
    win.lastfm_api_key_input.setText("key")
    win.lastfm_secret_input.setText("secret")
    win._lastfm_auth_thread = object()  # a round-trip already in flight
    win.lastfm_status_label.setText("Requesting authorization token…")

    win._on_lastfm_connect()

    assert win.lastfm_status_label.text() == "Requesting authorization token…"
    win._lastfm_auth_thread = None  # let teardown close the (fake) thread cleanly


def test_connecting_starts_a_real_worker_thread_and_cleans_up_after_a_failure(win, boxes):
    # No test reaches the network (see conftest._no_network), so this real
    # LastfmClient.get_token() call fails — the same path a user hits offline.
    win._lastfm_session_key = ""
    win.lastfm_api_key_input.setText("key")
    win.lastfm_secret_input.setText("secret")

    win._on_lastfm_connect()

    assert win._lastfm_auth_thread is not None
    assert win.lastfm_status_label.text() == "Requesting authorization token…"

    deadline = time.monotonic() + 5
    while win._lastfm_auth_thread is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)

    assert win._lastfm_auth_thread is None
    assert win._lastfm_auth_worker is None
    assert boxes and boxes[0][:2] == ("warning", "Last.fm connection failed")
