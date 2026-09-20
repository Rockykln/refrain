"""Settings: unsaved changes, OK/Apply/Cancel, restarts announced, Reset and Last.fm saved at once."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import refrain.ui.settings_window as sw  # noqa: E402
from refrain.config import Config  # noqa: E402

ID = "1234567890123456789"
OTHER_ID = "1234567890123456780"


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


class Answers:
    """Answers each QMessageBox.exec with the next button label, and keeps what was asked."""

    def __init__(self, monkeypatch):
        self.queue: list[str] = []
        self.asked: list[tuple[str, str, list[str]]] = []
        monkeypatch.setattr(QMessageBox, "exec", lambda box: self._exec(box))

    def __call__(self, *labels: str) -> Answers:
        self.queue.extend(labels)
        return self

    def _exec(self, box):
        labels = [b.text() for b in box.buttons()]
        self.asked.append((box.text(), box.informativeText(), labels))
        if not self.queue:
            raise AssertionError(f"unexpected question: {box.text()!r} {labels}")
        text = self.queue.pop(0)
        button = next(b for b in box.buttons() if b.text() == text)
        box.clickedButton = lambda: button
        return 0


@pytest.fixture
def answer(monkeypatch):
    return Answers(monkeypatch)


def _config() -> Config:
    c = Config()
    c.discord.client_id = ID
    c.advanced.poll_interval_ms = 1500
    return c


@pytest.fixture
def make(monkeypatch, boxes):
    app = QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    monkeypatch.setattr(BluetoothSource, "list_paired_devices", staticmethod(lambda: []))
    monkeypatch.setattr(sw.SettingsWindow, "_lookup_application_name", lambda self: None)
    made = []

    def build(config: Config | None = None):
        window = sw.SettingsWindow(config or _config())
        window.sent, window.restarts = [], []
        window.applied.connect(window.sent.append)
        window.restartRequested.connect(lambda: window.restarts.append(1))
        made.append(window)
        return window

    yield build
    for window in made:
        window._baseline = None
        window.hide()
        window.deleteLater()
    app.processEvents()


@pytest.fixture
def win(make):
    return make()


# ------------------------------------------------------------ dirty tracking


@pytest.mark.parametrize(
    "change",
    [
        lambda w: w.notifications_box.setChecked(not w.notifications_box.isChecked()),
        lambda w: w._browser_checkboxes["firefox"].setChecked(False),
        lambda w: w.client_id_input.setText(OTHER_ID),
        lambda w: w.lastfm_api_key_input.setText("12345apikey"),
        lambda w: w.browser_extra_input.setText("palemoon"),
        lambda w: w.privacy_combo.setCurrentIndex(w.privacy_combo.findData("off")),
        lambda w: w.log_level_combo.setCurrentIndex(w.log_level_combo.findData("DEBUG")),
        lambda w: w.history_limit_combo.setCurrentIndex(0),
        lambda w: w.poll_spin.setValue(3000),
        lambda w: w.notify_delay_spin.setValue(750),
        lambda w: w.bluetooth_device.setEditText("12:34:56:78:9A:BC"),
    ],
)
def test_every_kind_of_widget_counts_as_a_change(win, change):
    assert not win.is_dirty() and not win.apply_btn.isEnabled()
    change(win)
    assert win.is_dirty() and win.apply_btn.isEnabled()


def test_changing_a_value_back_is_no_change(win):
    win.poll_spin.setValue(3000)
    win.poll_spin.setValue(1500)
    assert not win.is_dirty() and not win.apply_btn.isEnabled()


def test_a_per_source_id_only_counts_while_the_switch_is_on(win):
    win.client_id_mpris_input.setText(OTHER_ID)
    assert not win.is_dirty()
    win.discord_per_source_box.setChecked(True)
    assert win.is_dirty()


def test_a_hand_edited_file_is_not_a_change_on_open(make):
    c = _config()
    c.sources.browser_hints = "Zen, firefox,palemoon"
    window = make(c)
    assert not window.is_dirty()


def test_a_connection_made_in_the_window_is_a_change(win):
    win._lastfm_session_key = "12345session"
    win._update_dirty()
    assert win.is_dirty()


# ------------------------------------------------------------ Apply and OK


def test_apply_saves_keeps_the_window_open_and_starts_a_new_baseline(win):
    win.show()
    win.poll_spin.setValue(3000)
    win.apply_btn.click()
    assert win.isVisible()
    assert win.sent[-1].advanced.poll_interval_ms == 3000
    assert Config.load().advanced.poll_interval_ms == 3000
    assert not win.is_dirty() and not win.apply_btn.isEnabled()


def test_ok_saves_and_closes(win):
    win.show()
    win.poll_spin.setValue(3000)
    win.ok_btn.click()
    assert not win.isVisible()
    assert win.sent[-1].advanced.poll_interval_ms == 3000


def test_ok_is_the_default_button_and_the_row_has_all_three(win):
    assert win.ok_btn.isDefault()
    assert [win.ok_btn.text(), win.apply_btn.text(), win.cancel_btn.text()] == [
        "OK",
        "Apply",
        "Cancel",
    ]


def test_ok_stays_open_when_a_question_is_turned_down(win, answer):
    win.show()
    win.history_box.setChecked(False)
    answer("Cancel")
    win.ok_btn.click()
    assert win.isVisible() and win.sent == []


# ------------------------------------------------------------ Cancel, X, Esc


def test_cancel_without_changes_closes_without_asking(win, answer):
    win.show()
    win.cancel_btn.click()
    assert not win.isVisible()
    assert answer.asked == []


def test_cancel_with_changes_asks_and_keep_editing_stays(win, answer):
    win.show()
    win.poll_spin.setValue(3000)
    answer("Keep editing")
    win.cancel_btn.click()
    assert win.isVisible()
    assert win.poll_spin.value() == 3000
    text, _info, labels = answer.asked[0]
    assert text == "You changed settings that aren't saved yet."
    assert sorted(labels) == ["Discard", "Keep editing", "Save"]


def test_discard_drops_the_changes_and_closes(win, answer):
    win.show()
    win.poll_spin.setValue(3000)
    answer("Discard")
    win.cancel_btn.click()
    assert not win.isVisible()
    assert win.poll_spin.value() == 1500
    assert win.sent == []


def test_save_from_the_question_saves_and_closes(win, answer):
    win.show()
    win.poll_spin.setValue(3000)
    answer("Save")
    win.cancel_btn.click()
    assert not win.isVisible()
    assert win.sent[-1].advanced.poll_interval_ms == 3000


def test_save_from_the_question_can_still_be_turned_down(win, answer):
    win.show()
    win.language_combo.setCurrentIndex(win.language_combo.findData("de"))
    answer("Save", "Cancel")
    win.cancel_btn.click()
    assert win.isVisible() and win.sent == [] and win.restarts == []


def test_escape_asks_too(win, answer):
    win.show()
    win.poll_spin.setValue(3000)
    answer("Keep editing")
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert win.isVisible() and len(answer.asked) == 1


def test_the_close_button_asks_and_can_be_refused(win, answer):
    win.show()
    win.poll_spin.setValue(3000)
    answer("Keep editing")
    event = QCloseEvent()
    win.closeEvent(event)
    assert not event.isAccepted() and win.isVisible()


def test_the_close_button_without_changes_closes(win, monkeypatch):
    win.show()
    joined = []
    monkeypatch.setattr(win, "_finish_lastfm_thread", lambda: joined.append(1))
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() and joined == [1]


def test_the_choice_is_measured_in_developer_mode(win, answer, monkeypatch):
    seen = []
    monkeypatch.setattr(sw.dev_metrics, "interaction", lambda kind, **f: seen.append((kind, f)))
    win.show()
    win.poll_spin.setValue(3000)
    answer("Keep editing")
    win.reject()
    assert seen == [("settings_unsaved", {"choice": "keep"})]


# ------------------------------------------------------------ restarts


def test_a_new_language_is_announced_before_the_restart(win, answer):
    win.language_combo.setCurrentIndex(win.language_combo.findData("de"))
    answer("Save and restart")
    win._on_apply_clicked()
    assert answer.asked[0][0] == "Refrain restarts to apply the new language."
    assert win.restarts == [1]
    assert win.sent[-1].advanced.language == "de"


def test_a_new_application_id_applies_without_a_restart(win, answer):
    win.client_id_input.setText(OTHER_ID)
    win._on_apply_clicked()
    assert answer.asked == []
    assert win.restarts == []
    assert win.sent[-1].discord.client_id == OTHER_ID


def test_turning_down_the_restart_saves_nothing(win, answer):
    win.language_combo.setCurrentIndex(win.language_combo.findData("de"))
    answer("Cancel")
    win._on_apply_clicked()
    assert win.sent == [] and win.restarts == []
    assert win.is_dirty()


def test_other_changes_never_restart(win):
    win.poll_spin.setValue(3000)
    win._on_apply_clicked()
    assert win.restarts == []


# ------------------------------------------------------------ Reset


def test_reset_saves_at_once_and_says_so(win, answer):
    win.poll_spin.setValue(3000)
    answer("Reset")
    win._on_reset_clicked()
    assert "saved right away" in answer.asked[0][0]
    assert answer.asked[0][1] == ""
    assert win.sent[-1].advanced.poll_interval_ms == Config().advanced.poll_interval_ms
    assert Config.load().advanced.poll_interval_ms == Config().advanced.poll_interval_ms
    assert win.sent[-1].discord.client_id == ID
    assert not win.is_dirty() and win.restarts == []


def test_reset_cancelled_changes_nothing(win, answer):
    answer("Cancel")
    win._on_reset_clicked()
    assert win.sent == []


def test_reset_that_changes_the_language_says_it_restarts(make, answer):
    c = _config()
    c.advanced.language = "de"
    window = make(c)
    answer("Reset")
    window._on_reset_clicked()
    assert answer.asked[0][1] == "Refrain restarts to apply the new language."
    assert window.restarts == [1]
    assert window.sent[-1].advanced.language == "system"


# ------------------------------------------------------------ Last.fm


def _with_keys(win):
    win.lastfm_api_key_input.setText("12345apikey")
    win.lastfm_secret_input.setText("12345secret")


def test_connecting_turns_scrobbling_on_and_saves_at_once(win, boxes):
    _with_keys(win)
    win.poll_spin.setValue(3000)
    win._on_lastfm_session("12345session", "marlowvance")
    assert win.lastfm_enabled_box.isChecked()
    saved = win.sent[-1]
    assert (saved.lastfm.enabled, saved.lastfm.username) == (True, "marlowvance")
    assert saved.lastfm.session_key == "12345session"
    assert saved.lastfm.api_key == "12345apikey"
    assert saved.advanced.poll_interval_ms == 1500
    loaded = Config.load()
    assert loaded.lastfm.enabled is True and loaded.lastfm.username == "marlowvance"
    # The unrelated edit is still waiting for Apply.
    assert win.is_dirty()
    win.poll_spin.setValue(1500)
    assert not win.is_dirty()
    assert boxes[-1][2] == "Connected as marlowvance. Scrobbling starts with the next song."


def test_a_fresh_connection_survives_cancel(win, answer):
    _with_keys(win)
    win._on_lastfm_session("12345session", "marlowvance")
    win.show()
    win.cancel_btn.click()
    assert answer.asked == []
    assert Config.load().lastfm.username == "marlowvance"


def test_connecting_while_privacy_is_off_says_it_stays_paused(make, boxes):
    c = _config()
    c.privacy.mode = "off"
    window = make(c)
    _with_keys(window)
    window._on_lastfm_session("12345session", "")
    assert boxes[-1][2] == (
        "Connected. Scrobbling starts with the next song. "
        "It stays paused while Privacy is set to Off."
    )


def _connected() -> Config:
    c = _config()
    c.lastfm.enabled = True
    c.lastfm.api_key = "12345apikey"
    c.lastfm.shared_secret = "12345secret"
    c.lastfm.session_key = "12345session"
    c.lastfm.username = "marlowvance"
    return c


def test_disconnect_is_saved_at_once(make, answer):
    window = make(_connected())
    window.poll_spin.setValue(3000)
    answer("Disconnect")
    window._on_lastfm_connect()
    assert answer.asked[0][1] == ""
    assert window.sent[-1].lastfm.session_key == ""
    assert window.sent[-1].lastfm.username == ""
    assert window.sent[-1].advanced.poll_interval_ms == 1500
    assert Config.load().lastfm.username == ""


def test_connected_but_off_asks_once(make, answer):
    window = make(_connected())
    window.lastfm_enabled_box.setChecked(False)
    answer("Keep off")
    window._on_apply_clicked()
    assert answer.asked[0][0] == "Last.fm is connected, but scrobbling is off."
    assert window.sent[-1].lastfm.enabled is False
    window.poll_spin.setValue(3000)
    window._on_apply_clicked()
    assert len(answer.asked) == 1


def test_connected_but_off_can_be_turned_on(make, answer):
    window = make(_connected())
    window.lastfm_enabled_box.setChecked(False)
    answer("Turn on scrobbling")
    window._on_apply_clicked()
    assert window.sent[-1].lastfm.enabled is True
    assert window.lastfm_enabled_box.isChecked()


# ------------------------------------------------------------ developer switch


def test_an_unlocked_switch_stays_after_a_restart_with_it_off(make):
    c = _config()
    c.advanced.developer_unlocked = True
    window = make(c)
    assert not window.developer_group.isHidden()
    assert not window.developer_mode_box.isChecked()


def test_unlocking_is_remembered(win, answer):
    answer("Turn on")
    for _ in range(sw.DEVELOPER_UNLOCK_CLICKS):
        win._on_version_clicked()
    assert Config.load().advanced.developer_unlocked is True
    assert not win.is_dirty()
    win.developer_mode_box.setChecked(False)
    win._on_apply_clicked()
    loaded = Config.load()
    assert loaded.advanced.developer_mode is False and loaded.advanced.developer_unlocked is True


def test_seven_clicks_once_unlocked_ask_nothing(make, answer):
    c = _config()
    c.advanced.developer_unlocked = True
    window = make(c)
    for _ in range(sw.DEVELOPER_UNLOCK_CLICKS):
        window._on_version_clicked()
    assert answer.asked == [] and window.sent == []


def test_a_switch_shown_by_developer_mode_is_remembered_on_apply(make):
    c = _config()
    c.advanced.developer_mode = True
    window = make(c)
    window._on_apply_clicked()
    assert window.sent[-1].advanced.developer_unlocked is True


def test_reset_keeps_the_unlocked_switch():
    c = _config()
    c.advanced.developer_unlocked = True
    c.advanced.developer_mode = True
    out = sw.reset_to_defaults(c)
    assert out.advanced.developer_unlocked is True
    assert out.advanced.developer_mode is False


# ------------------------------------------------------------ wording and layout


def test_privacy_off_names_both_services(win):
    off = win.privacy_combo.itemText(win.privacy_combo.findData("off"))
    assert "Discord" in off and "Last.fm" in off
    assert "Last.fm" in win.privacy_combo.toolTip()


def test_the_catalog_switch_sits_with_privacy_not_notifications(win):
    group = win.cover_art_box.parentWidget()
    assert group.title() == "Privacy"
    assert win.privacy_combo.parentWidget() is group


def test_the_terms_follow_the_discord_portal(win):
    texts = [w.text() for w in win.findChildren(sw.QLabel)]
    texts += [w.placeholderText() for w in win.findChildren(sw.QLineEdit)]
    assert "Application ID:" in texts
    assert not any("Client ID" in t for t in texts)


def test_the_history_tab_is_called_recently_played(win):
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert "Recently played" in titles and "History" not in titles


def test_log_levels_read_as_words(win):
    items = {
        win.log_level_combo.itemData(i): win.log_level_combo.itemText(i)
        for i in range(win.log_level_combo.count())
    }
    assert items == {
        "DEBUG": "Detailed (DEBUG)",
        "INFO": "Normal (INFO)",
        "WARNING": "Warnings only",
        "ERROR": "Errors only",
    }


def test_history_off_uses_a_translatable_cancel(win, answer):
    answer("Cancel")
    assert win._confirm_history_off() is False
    assert sorted(answer.asked[0][2]) == ["Cancel", "Turn off and delete"]


def test_the_secret_field_shows_its_start(make):
    window = make(_connected())
    assert window.lastfm_secret_input.cursorPosition() == 0


def test_advanced_fields_line_up(win):
    labels = [
        win.poll_spin,
        win.notify_delay_spin,
        win.language_combo,
        win.log_level_combo,
    ]
    widths = set()
    for field in labels:
        form = field.parentWidget().layout()
        widths.add(form.labelForField(field).minimumWidth())
    assert len(widths) == 1 and widths.pop() > 0


def test_hints_are_not_italic_in_cjk():
    QApplication.instance() or QApplication(sys.argv)
    saved = QLocale()
    try:
        QLocale.setDefault(QLocale("ja"))
        assert "italic" not in sw._hint_style()
        QLocale.setDefault(QLocale("de"))
        assert "italic" in sw._hint_style()
    finally:
        QLocale.setDefault(saved)


def test_the_application_name_ends_in_an_ellipsis_when_narrow():
    QApplication.instance() or QApplication(sys.argv)
    label = sw._ElidedHint()
    name = "Application name: “A really rather long Discord application name”"
    label.setText(name)
    assert label.text() == name
    assert label.sizeHint().width() > label.minimumSizeHint().width()
    label.show()
    label.resize(60, 20)
    shown = sw.QLabel.text(label)
    assert shown.endswith("…") and label.toolTip() == name
    label.resize(label.sizeHint().width() + 20, 20)
    assert sw.QLabel.text(label) == name and label.toolTip() == ""
    label.hide()
    label.deleteLater()


def test_use_config_takes_the_new_settings_as_saved(win):
    win.poll_spin.setValue(3000)
    c = _config()
    c.advanced.poll_interval_ms = 2000
    win.use_config(c)
    assert win.poll_spin.value() == 2000 and not win.is_dirty()
