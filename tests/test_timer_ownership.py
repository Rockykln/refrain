"""Every one-shot timer names an owner, so it dies with the object it calls into."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "src" / "refrain"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def _single_shot_calls():
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "singleShot"
            ):
                yield path.relative_to(SRC), node


def test_no_one_shot_timer_is_left_without_an_owner():
    ownerless = [
        f"{path}:{node.lineno}" for path, node in _single_shot_calls() if len(node.args) < 3
    ]
    assert ownerless == [], (
        "QTimer.singleShot(ms, callable) creates a timer Qt keeps out of the object "
        f"tree; pass the receiver as the second argument: {ownerless}"
    )


def test_the_scan_does_not_run_empty():
    assert len(list(_single_shot_calls())) >= 9


def test_a_dead_owner_keeps_its_timer_from_firing(qapp):
    fired = []
    alive = QObject()
    QTimer.singleShot(40, alive, lambda: fired.append("alive"))
    doomed = QObject()
    QTimer.singleShot(40, doomed, lambda: fired.append("doomed"))
    doomed.deleteLater()
    del doomed
    QTimer.singleShot(120, qapp.quit)
    qapp.exec()
    assert fired == ["alive"]


def test_a_zero_delay_timer_beats_a_pending_deleteLater(qapp):
    """deleteLater only takes effect on the next pass, so a 0 ms timer still fires."""
    fired = []
    doomed = QObject()
    QTimer.singleShot(0, doomed, lambda: fired.append("doomed"))
    doomed.deleteLater()
    QTimer.singleShot(60, qapp.quit)
    qapp.exec()
    assert fired == ["doomed"]
