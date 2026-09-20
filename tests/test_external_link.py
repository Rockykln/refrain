"""Leaving Refrain for the browser is always a question first."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from refrain.ui import external_link  # noqa: E402

LINK = "https://github.com/Rockykln/refrain"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _answer(monkeypatch, role):
    seen = {}

    def exec_(self):
        seen["text"] = self.text() + self.informativeText()
        for button in self.buttons():
            if self.buttonRole(button) == role:
                self.setProperty("chosen", True)
                monkeypatch.setattr(type(self), "clickedButton", lambda _self, b=button: b)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return seen


def test_the_question_names_the_page_and_opens_it_on_yes(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(
        external_link, "open_url", lambda url, player="": opened.append(url) or True
    )
    seen = _answer(monkeypatch, QMessageBox.ButtonRole.AcceptRole)
    assert external_link.confirm_and_open(None, LINK) is True
    assert LINK in seen["text"]
    assert opened == [LINK]


def test_cancel_opens_nothing(qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(
        external_link, "open_url", lambda url, player="": opened.append(url) or True
    )
    _answer(monkeypatch, QMessageBox.ButtonRole.RejectRole)
    assert external_link.confirm_and_open(None, LINK) is False
    assert opened == []
