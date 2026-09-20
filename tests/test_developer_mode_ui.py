"""Unlocking developer mode, the Developer tab in the live log and the tray hint."""

from __future__ import annotations

import json
import logging
import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

import refrain.ui.log_window as lw  # noqa: E402
import refrain.ui.settings_window as sw  # noqa: E402
from refrain import dev_metrics  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.ui.tray import TrayIcon  # noqa: E402


@pytest.fixture(autouse=True)
def _app():
    QApplication.instance() or QApplication(sys.argv)
    yield
    dev_metrics.set_enabled(False)


@pytest.fixture
def win(monkeypatch):
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    window = sw.SettingsWindow(Config())
    sent = []
    window.applied.connect(sent.append)
    return window, sent


def _answer(monkeypatch, role):
    asked = []

    def fake_exec(box):
        asked.append(box.text())
        for button in box.buttons():
            if box.buttonRole(button) == role:
                button.click()

    monkeypatch.setattr(sw.QMessageBox, "exec", fake_exec)
    return asked


def _click_version(window, times):
    for _ in range(times):
        QTest.mouseClick(window.version_label, Qt.MouseButton.LeftButton)


def test_the_developer_switch_is_hidden_at_first(win):
    window, _ = win
    assert window.developer_group.isHidden()


def test_five_clicks_do_nothing(win, monkeypatch):
    window, sent = win
    asked = _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)
    _click_version(window, 5)
    QTest.mouseClick(window.version_label, Qt.MouseButton.RightButton)
    assert asked == [] and sent == []


def test_six_clicks_and_turn_on(win, monkeypatch):
    window, sent = win
    asked = _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)
    _click_version(window, 6)
    assert len(asked) == 1
    assert len(sent) == 1 and sent[0].advanced.developer_mode is True
    assert not window.developer_group.isHidden()
    assert window.developer_mode_box.isChecked()
    assert Config.load().advanced.developer_mode is True


def test_six_clicks_and_cancel(win, monkeypatch):
    window, sent = win
    asked = _answer(monkeypatch, QMessageBox.ButtonRole.RejectRole)
    _click_version(window, 6)
    assert len(asked) == 1 and sent == []
    assert window.developer_group.isHidden()
    assert Config.load().advanced.developer_mode is False


def test_slow_clicks_start_counting_again(win, monkeypatch):
    window, _ = win
    asked = _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)
    now = [100.0]
    monkeypatch.setattr(sw.time, "monotonic", lambda: now[0])
    for _ in range(4):
        window._on_version_clicked()
    now[0] += sw.DEVELOPER_CLICK_GAP_S + 1
    for _ in range(4):
        window._on_version_clicked()
    assert asked == []


def test_no_dialog_when_already_on(win, monkeypatch):
    window, sent = win
    window._config.advanced.developer_mode = True
    asked = _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)
    _click_version(window, 6)
    assert asked == [] and sent == []


def test_a_failed_save_still_turns_it_on(win, monkeypatch):
    window, sent = win
    _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)

    def refuse(self, path=None):
        raise OSError("read-only")

    monkeypatch.setattr(Config, "save", refuse)
    _click_version(window, 6)
    assert sent[0].advanced.developer_mode is True


def test_the_switch_turns_it_off_on_apply(win, monkeypatch):
    window, sent = win
    _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)
    _click_version(window, 6)
    window.developer_mode_box.setChecked(False)
    window._on_apply_clicked()
    assert sent[-1].advanced.developer_mode is False
    assert not window.developer_group.isHidden()


def test_a_config_with_developer_mode_shows_the_switch(monkeypatch):
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    config = Config()
    config.advanced.developer_mode = True
    window = sw.SettingsWindow(config)
    assert not window.developer_group.isHidden()
    assert window.developer_mode_box.isChecked()


# Live log -------------------------------------------------------------------


class _Bridge(QObject):
    log_record = Signal(str, int)


def test_the_developer_tab_comes_and_goes():
    window = lw.LogWindow(_Bridge())
    assert window.tabs.count() == 1 and window.developer_panel is None
    window.show_developer_tab()
    window.set_developer_mode(True)
    window.set_developer_mode(True)
    assert window.tabs.count() == 2
    window.show_developer_tab()
    assert window.tabs.currentWidget() is window.developer_panel
    window.set_developer_mode(False)
    window.set_developer_mode(False)
    assert window.tabs.count() == 1 and window.developer_panel is None


def test_the_panel_shows_what_was_measured():
    dev_metrics.set_enabled(True)
    dev_metrics.mark("config_loaded")
    clock = dev_metrics.poll_clock()
    clock.lap("source")
    clock.lap("custom")
    clock.done()
    with dev_metrics.network("itunes"):
        pass
    dev_metrics.interaction("click", target="tray/settings_action")
    dev_metrics.recorder().layout_findings(
        "SettingsWindow", ["UI: Settings › ok_btn needs 12 px more (text)"]
    )
    panel = lw.DeveloperPanel()
    panel.show()
    assert panel._timer.isActive()
    cells = {row[0]: row[5] for row in panel.stages.rows}
    assert cells == {"source": "1", "total": "1", "custom": "1"}
    assert panel.stages.rows[0][0] == "source"
    assert panel.network.rows[0][0] == "itunes"
    assert panel.interactions.rows[0][0] == "click tray/settings_action"
    assert panel.startup.rows[0][0] == "process_start"
    assert panel.layout_issues.rows[0][0].endswith("needs 12 px more (text)")
    assert panel.layout_issues.rows[0][1] == "1"
    # The columns line up without a table widget.
    assert panel.stages.text().splitlines()[0].startswith("Stage")
    assert "MB" in panel.memory.text()
    panel.hide()
    assert not panel._timer.isActive()


def test_the_panel_copes_with_nothing_measured():
    panel = lw.DeveloperPanel()
    panel.refresh()
    assert panel.stages.rows == []
    assert "nothing measured yet" in panel.stages.text()
    assert "—" in panel.memory.text()


def test_a_step_that_was_never_reached_shows_a_dash_instead_of_a_bogus_time():
    assert lw._ms(None) == "—"


def test_a_session_running_for_hours_reports_them_instead_of_only_minutes():
    assert lw._duration(2 * 3600 + 5 * 60 + 9) == "2 h 5 min"


def test_export_writes_where_the_user_chose(monkeypatch, tmp_path):
    dev_metrics.set_enabled(True)
    target = tmp_path / "metrics.json"
    monkeypatch.setattr(
        lw.QFileDialog, "getSaveFileName", staticmethod(lambda *a: (str(target), ""))
    )
    panel = lw.DeveloperPanel()
    panel.export_btn.click()
    assert json.loads(target.read_text(encoding="utf-8"))["version"]


def test_export_cancelled_writes_nothing(monkeypatch, tmp_path):
    dev_metrics.set_enabled(True)
    monkeypatch.setattr(lw.QFileDialog, "getSaveFileName", staticmethod(lambda *a: ("", "")))
    lw.DeveloperPanel()._export()
    assert dev_metrics.snapshot()["interactions"] == {"dialog_cancelled export": 1}


def test_export_failure_is_shown(monkeypatch, tmp_path):
    dev_metrics.set_enabled(True)
    target = tmp_path / "missing" / "metrics.json"
    monkeypatch.setattr(
        lw.QFileDialog, "getSaveFileName", staticmethod(lambda *a: (str(target), ""))
    )
    warned = []
    monkeypatch.setattr(lw.QMessageBox, "warning", staticmethod(lambda *a: warned.append(a)))
    lw.DeveloperPanel()._export()
    assert len(warned) == 1


def test_log_lines_still_work_with_tabs():
    bridge = _Bridge()
    window = lw.LogWindow(bridge)
    window.set_developer_mode(True)
    bridge.log_record.emit("hello", logging.INFO)
    assert "hello" in window.view.toPlainText()


# Tray -----------------------------------------------------------------------


def test_the_tray_hint_follows_developer_mode():
    tray = TrayIcon()
    assert not tray._developer_action.isVisible()
    tray.set_developer_mode(True)
    assert tray._developer_action.isVisible()
    got = []
    tray.developerRequested.connect(lambda: got.append(True))
    tray._developer_action.trigger()
    assert got == [True]
    tray.set_developer_mode(False)
    assert not tray._developer_action.isVisible()


def test_tray_clicks_are_counted_only_while_on():
    tray = TrayIcon()
    quit_action = next(a for a in tray._menu.actions() if a.text() == "Quit Refrain")
    tray._on_menu_triggered(tray._title_action)
    dev_metrics.set_enabled(True)
    tray._on_menu_triggered(tray._title_action)
    tray._on_menu_triggered(tray._log_action)
    tray._on_menu_triggered(quit_action)
    counts = dev_metrics.snapshot()["interactions"]
    index = tray._menu.actions().index(quit_action)
    assert counts == {
        "click tray/title_action": 1,
        "click tray/log_action": 1,
        f"click tray/action{index}": 1,
    }


def test_the_live_log_starts_at_the_level_refrain_logs_at():
    window = lw.LogWindow(_Bridge())
    window.set_log_level(logging.DEBUG)
    assert window.level_combo.currentData() == logging.DEBUG
    window.set_log_level(logging.WARNING)
    assert window.level_combo.currentData() == logging.WARNING


def test_the_system_report_can_be_read_and_copied(xdg_tmp, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QDialog, QPlainTextEdit

    from refrain.config import Config

    panel = lw.DeveloperPanel()
    config = Config()
    config.discord.client_id = "1234567890123456789"
    panel.use_config(config)

    shown = {}

    def grab(self):
        shown["text"] = self.findChild(QPlainTextEdit).toPlainText()
        shown["buttons"] = [b.text() for b in self.findChildren(QPushButton)]
        for button in self.findChildren(QPushButton):
            if button.text() == "Copy":
                button.click()
        return 0

    monkeypatch.setattr(QDialog, "exec", grab)
    panel._show_report()
    assert "Refrain " in shown["text"]
    assert "1234567890123456789" not in shown["text"]
    assert "Copy" in shown["buttons"] and "Close" in shown["buttons"]
    assert QGuiApplication.clipboard().text() == shown["text"]


def test_the_log_window_hands_a_new_config_to_the_developer_tab(xdg_tmp):
    from refrain.config import Config

    window = lw.LogWindow(_Bridge())
    window.set_developer_mode(True)
    config = Config()
    window.use_config(config)
    assert window.developer_panel._config is config
