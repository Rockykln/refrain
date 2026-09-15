"""Settings → History: the two settings, and the one Apply that deletes.

One window for the module with the Bluetooth probe stubbed — see the
note at the top of test_settings_lastfm.py for why.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.config import HISTORY_LIMIT_CHOICES, Config  # noqa: E402
from refrain.ui.settings_window import SettingsWindow  # noqa: E402


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication(sys.argv)
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


@pytest.fixture
def form(win, xdg_tmp, monkeypatch):
    """The window re-driven from a fresh default config, keyring stubbed."""
    # Apply also writes the Last.fm secrets to the OS keyring; a test
    # has no business touching the real one.
    monkeypatch.setattr("refrain.secrets_store.save_from", lambda *a, **k: None)
    win._config = Config()
    win._load_into_form()
    return win


def _tab_titles(win) -> list[str]:
    return [win.tabs.tabText(i) for i in range(win.tabs.count())]


def test_history_has_its_own_tab(form):
    assert _tab_titles(form) == [
        "General",
        "Sources",
        "Last.fm",
        "History",
        "Updates",
        "Advanced",
    ]


def test_defaults_on_and_thirty(form):
    assert form.history_box.isChecked()
    assert form.history_limit_combo.currentData() == 30
    assert [
        form.history_limit_combo.itemData(i) for i in range(form.history_limit_combo.count())
    ] == list(HISTORY_LIMIT_CHOICES)


def test_unticking_disables_the_rest(form):
    form.history_box.setChecked(False)
    assert not form.history_limit_combo.isEnabled()
    assert not form.history_show_btn.isEnabled()
    form.history_box.setChecked(True)
    assert form.history_limit_combo.isEnabled()


def test_a_hand_edited_count_is_kept_not_rounded(form):
    form._config.history.max_entries = 42
    form._load_into_form()
    assert form.history_limit_combo.currentData() == 42
    data = [form.history_limit_combo.itemData(i) for i in range(form.history_limit_combo.count())]
    assert data == sorted(data)


def test_a_count_past_the_maximum_shows_as_the_maximum(form):
    form._config.history.max_entries = 500
    form._load_into_form()
    assert form.history_limit_combo.currentData() == 100
    assert form.history_limit_combo.findData(500) < 0


def test_apply_saves_the_limit(form):
    form.history_limit_combo.setCurrentIndex(form.history_limit_combo.findData(75))
    form._on_apply_clicked()
    assert form._config.history.max_entries == 75
    assert Config.load().history.max_entries == 75


def test_turning_it_off_asks_first(form, monkeypatch):
    form.history_box.setChecked(False)
    applied = []
    form.applied.connect(applied.append)

    monkeypatch.setattr(form, "_confirm_history_off", lambda: False)
    form._on_apply_clicked()
    assert form._config.history.enabled  # cancelled — nothing written
    assert applied == []

    monkeypatch.setattr(form, "_confirm_history_off", lambda: True)
    form._on_apply_clicked()
    assert not form._config.history.enabled
    assert applied and not applied[-1].history.enabled
    form.applied.disconnect()


def test_no_question_when_it_was_already_off(form, monkeypatch):
    form._config.history.enabled = False
    form._load_into_form()

    def _must_not_ask():
        raise AssertionError("asked although nothing gets deleted")

    monkeypatch.setattr(form, "_confirm_history_off", _must_not_ask)
    form._on_apply_clicked()
    assert not form._config.history.enabled
