"""The single-instance lock on the session bus."""

from __future__ import annotations

import pytest

pytest.importorskip("dbus")

import refrain.single_instance as si  # noqa: E402


class _Bus:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = 0

    def request_name(self, name, flags):
        self.asked += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


EXISTS = 3


def _with_bus(monkeypatch, bus):
    monkeypatch.setattr(si.dbus, "SessionBus", lambda: bus)


def test_a_restart_waits_for_the_old_process_to_let_go(monkeypatch):
    """On a restart the old process can hold the name for a moment."""
    bus = _Bus([EXISTS, EXISTS, 1])
    _with_bus(monkeypatch, bus)
    assert si.acquire(wait_s=1.0, step_s=0.01) is bus
    assert bus.asked == 3


def test_a_second_refrain_still_gives_up(monkeypatch):
    bus = _Bus([EXISTS])
    _with_bus(monkeypatch, bus)
    with pytest.raises(si.AlreadyRunning):
        si.acquire(wait_s=0.05, step_s=0.01)
    assert bus.asked > 1
