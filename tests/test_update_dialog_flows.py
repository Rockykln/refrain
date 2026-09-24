"""Update dialog: per-install-type buttons, results shown, cancel and detach."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _release():
    from refrain.updater import ReleaseInfo

    return ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Refrain 9.9.9",
        body="Salt Flats now scrobbles.",
        html_url="https://github.com/Rockykln/refrain/releases/tag/v9.9.9",
    )


@pytest.fixture
def make_dialog(qapp, xdg_tmp, monkeypatch):
    from refrain.ui import update_dialog

    boxes = []
    monkeypatch.setattr(
        update_dialog.QMessageBox,
        "information",
        lambda _parent, title, text: boxes.append(("info", title, text)),
    )
    monkeypatch.setattr(
        update_dialog.QMessageBox,
        "warning",
        lambda _parent, title, text: boxes.append(("warning", title, text)),
    )

    def make(install_type):
        monkeypatch.setattr(update_dialog, "detect_install_type", lambda: install_type)
        dlg = update_dialog.UpdateDialog(_release())
        dlg.boxes = boxes
        return dlg

    return update_dialog, make


def _pump(qapp, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize(
    "install_type,label",
    [
        ("appimage", "Download && replace"),
        ("pip", "Run pip upgrade"),
        ("pipx", "Run pipx upgrade"),
        ("aur", "Run update in terminal"),
        ("flatpak", "Run update in terminal"),
        ("system", "Show update command"),
    ],
)
def test_update_button_names_what_will_happen(make_dialog, install_type, label):
    _, make = make_dialog
    dlg = make(install_type)
    assert dlg.update_btn.text() == label
    labels = [w.text() for w in dlg.findChildren(type(dlg.status_label))]
    assert any(f"<b>{install_type}</b>" in text for text in labels)


def test_distro_install_shows_the_command_without_a_thread(make_dialog, monkeypatch):
    update_dialog, make = make_dialog
    dlg = make("aur")
    calls = []

    def fake_apply(release, install_type, cancelled=None):
        calls.append((release.version, install_type, cancelled))
        return update_dialog.UpdateResult(False, "Run: paru -Syu refrain")

    monkeypatch.setattr(update_dialog, "apply_update", fake_apply)
    dlg._on_update_clicked()

    assert calls == [("9.9.9", "aur", None)]
    assert dlg._runner is None
    assert dlg.update_btn.isEnabled()
    assert dlg.boxes == [("warning", "Update", "Run: paru -Syu refrain")]


def test_pipx_upgrade_says_so_and_locks_the_buttons_until_done(qapp, make_dialog, monkeypatch):
    update_dialog, make = make_dialog
    dlg = make("pipx")
    go = threading.Event()

    def fake_apply(release, install_type, cancelled=None):
        go.wait(2)
        return update_dialog.UpdateResult(True, "Upgraded to 9.9.9.", needs_restart=False)

    monkeypatch.setattr(update_dialog, "apply_update", fake_apply)
    dlg._on_update_clicked()
    # Says it takes a while and that the window stays, so it does not look frozen.
    assert dlg.status_label.text().startswith("Running pipx…")
    assert "stays open" in dlg.status_label.text()
    assert not dlg.update_btn.isEnabled()
    assert not dlg.close_btn.isEnabled()

    go.set()
    dlg._runner.wait(2000)
    _pump(qapp, 0.2)

    assert dlg.update_btn.isEnabled()
    assert dlg.close_btn.isEnabled()
    assert dlg.close_btn.text() == "Later"
    assert dlg.boxes == [("info", "Update complete", "Upgraded to 9.9.9.")]
    assert dlg.result() != dlg.DialogCode.Accepted


def test_success_that_needs_a_restart_closes_the_dialog(make_dialog):
    update_dialog, make = make_dialog
    dlg = make("appimage")
    dlg._show_result(update_dialog.UpdateResult(True, "Downloaded.", needs_restart=True))
    assert dlg.boxes == [("info", "Update complete", "Downloaded.")]
    assert dlg.result() == dlg.DialogCode.Accepted


def test_cancelled_result_only_updates_the_status_line(make_dialog):
    update_dialog, make = make_dialog
    dlg = make("appimage")
    dlg._show_result(update_dialog.UpdateResult(False, "ignored", cancelled=True))
    assert dlg.status_label.text() == "Update canceled."
    assert dlg.boxes == []


def test_cancel_without_a_running_download_does_nothing(make_dialog):
    _, make = make_dialog
    dlg = make("appimage")
    dlg._on_cancel_clicked()
    assert dlg.status_label.text() == ""
    assert dlg.close_btn.isEnabled()


def test_cancel_button_interrupts_the_download(qapp, make_dialog, monkeypatch):
    update_dialog, make = make_dialog
    dlg = make("appimage")
    started = threading.Event()

    def fake_apply(release, install_type, cancelled=None):
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not cancelled():
            time.sleep(0.01)
        return update_dialog.UpdateResult(False, "Update canceled.", cancelled=cancelled())

    monkeypatch.setattr(update_dialog, "apply_update", fake_apply)
    dlg._on_update_clicked()
    assert dlg.close_btn.text() == "Cancel"
    assert started.wait(2)

    dlg.close_btn.click()
    assert dlg.status_label.text() == "Canceling…"
    assert not dlg.close_btn.isEnabled()

    dlg._runner.wait(3000)
    _pump(qapp, 0.2)
    assert dlg.status_label.text() == "Update canceled."
    assert dlg.close_btn.text() == "Later"
    assert dlg.close_btn.isEnabled()
    assert dlg.boxes == []


def test_a_stalled_download_is_detached_instead_of_blocking_close(qapp, make_dialog, monkeypatch):
    update_dialog, make = make_dialog
    dlg = make("appimage")
    release_run = threading.Event()

    def fake_apply(release, install_type, cancelled=None):
        release_run.wait(5)
        return update_dialog.UpdateResult(False, "late")

    monkeypatch.setattr(update_dialog, "apply_update", fake_apply)
    dlg._on_update_clicked()
    runner = dlg._runner
    real_wait = runner.wait
    monkeypatch.setattr(runner, "wait", lambda _ms: False)

    dlg.reject()

    assert runner in update_dialog._detached_runners
    assert runner.parent() is None
    assert dlg.result() == dlg.DialogCode.Rejected

    release_run.set()
    assert real_wait(3000)
    _pump(qapp, 0.2)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _pump(qapp, 0.1)
    assert runner not in update_dialog._detached_runners
    assert dlg.boxes == []
