"""Update dialog: closing cancels the download, and only https links open."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture
def qapp():
    yield QApplication.instance() or QApplication([])


def _release():
    from refrain.updater import ReleaseInfo

    return ReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Refrain 9.9.9",
        body="See [notes](https://example.com/12345) and [file](file:///etc/passwd).",
        html_url="https://github.com/Rockykln/refrain/releases/tag/v9.9.9",
    )


@pytest.fixture
def dialog(qapp, xdg_tmp, monkeypatch):
    from refrain.ui import update_dialog

    monkeypatch.setattr(update_dialog, "detect_install_type", lambda: "appimage")
    shown = []
    monkeypatch.setattr(update_dialog.QMessageBox, "information", lambda *a, **kw: shown.append(a))
    monkeypatch.setattr(update_dialog.QMessageBox, "warning", lambda *a, **kw: shown.append(a))
    opened = []
    monkeypatch.setattr(
        update_dialog.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    dlg = update_dialog.UpdateDialog(_release())
    dlg.shown = shown
    dlg.opened = opened
    return update_dialog, dlg


def _pump(qapp, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_rejecting_during_the_download_cancels_it(qapp, dialog, monkeypatch):
    update_dialog, dlg = dialog
    started = threading.Event()
    seen = {}

    def fake_apply(release, install_type, cancelled=None):
        started.set()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if cancelled():
                seen["cancelled"] = True
                return update_dialog.UpdateResult(False, "Update cancelled.", cancelled=True)
            time.sleep(0.01)
        return update_dialog.UpdateResult(True, "Downloaded.", needs_restart=True)

    monkeypatch.setattr(update_dialog, "apply_update", fake_apply)
    dlg._on_update_clicked()
    assert started.wait(2)

    dlg.reject()
    _pump(qapp, 2.0)

    assert seen.get("cancelled") is True
    assert dlg.shown == []
    assert not dlg._runner.isRunning()


def test_a_pip_upgrade_keeps_the_dialog_open(qapp, dialog, monkeypatch):
    update_dialog, dlg = dialog
    dlg._install_type = "pip"
    release_run = threading.Event()

    def fake_apply(release, install_type, cancelled=None):
        release_run.wait(2)
        return update_dialog.UpdateResult(False, "pip failed")

    monkeypatch.setattr(update_dialog, "apply_update", fake_apply)
    dlg.show()
    dlg._on_update_clicked()
    dlg.reject()
    assert dlg.isVisible()
    release_run.set()
    dlg._runner.wait(2000)
    _pump(qapp, 0.2)
    dlg.close()


def test_release_notes_do_not_open_links_themselves(dialog):
    _, dlg = dialog
    assert dlg.notes.openLinks() is False
    assert dlg.notes.openExternalLinks() is False


@pytest.mark.parametrize(
    "url,opens",
    [
        ("https://example.com/12345", True),
        ("http://example.com/12345", False),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
    ],
)
def test_only_https_links_open(dialog, url, opens):
    _, dlg = dialog
    dlg.notes.anchorClicked.emit(QUrl(url))
    assert dlg.opened == ([url] if opens else [])


def test_release_page_must_be_https(dialog):
    _, dlg = dialog
    dlg._release.html_url = "file:///etc/passwd"
    dlg._open_release_page()
    dlg._release.html_url = "https://github.com/Rockykln/refrain/releases/tag/v9.9.9"
    dlg._open_release_page()
    assert dlg.opened == ["https://github.com/Rockykln/refrain/releases/tag/v9.9.9"]
