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


class _Proxy:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def Activate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


class _ActivatingBus(_Bus):
    def __init__(self, answers, proxy):
        super().__init__(answers)
        self.proxy = proxy
        self.objects = []

    def get_object(self, name, path, introspect=True):
        self.objects.append((name, path, introspect))
        return self.proxy


def test_a_second_start_asks_the_running_one_to_show_itself(monkeypatch):
    proxy = _Proxy()
    bus = _ActivatingBus([EXISTS], proxy)
    _with_bus(monkeypatch, bus)
    with pytest.raises(si.AlreadyRunning) as caught:
        si.acquire(wait_s=5.0, step_s=0.01, activate=True)
    assert caught.value.activated is True
    assert bus.asked == 1
    assert bus.objects == [(si.BUS_NAME, si.OBJECT_PATH, False)]
    assert proxy.calls == [{"dbus_interface": si.INTERFACE, "timeout": si._ACTIVATE_TIMEOUT_S}]


def test_a_running_one_that_does_not_answer_is_waited_for_as_before(monkeypatch):
    proxy = _Proxy(si.dbus.DBusException("org.freedesktop.DBus.Error.NoReply"))
    bus = _ActivatingBus([EXISTS, EXISTS, 1], proxy)
    _with_bus(monkeypatch, bus)
    assert si.acquire(wait_s=1.0, step_s=0.01, activate=True) is bus
    assert len(proxy.calls) == 1


def test_without_an_answer_it_is_still_already_running(monkeypatch):
    proxy = _Proxy(si.dbus.DBusException("org.freedesktop.DBus.Error.ServiceUnknown"))
    bus = _ActivatingBus([EXISTS], proxy)
    _with_bus(monkeypatch, bus)
    with pytest.raises(si.AlreadyRunning) as caught:
        si.acquire(wait_s=0.05, step_s=0.01, activate=True)
    assert caught.value.activated is False


def test_activate_runs_the_callback(monkeypatch):
    made = []

    def fake_init(self, conn, path):
        made.append((conn, path))

    monkeypatch.setattr(si.dbus.service.Object, "__init__", fake_init)
    shown = []
    activator = si.listen_for_activation("bus", lambda: shown.append(True))
    assert made == [("bus", si.OBJECT_PATH)]
    activator.Activate()
    assert shown == [True]


def test_a_bus_without_a_main_loop_only_costs_the_second_start_its_window(monkeypatch, caplog):
    def refuse(self, conn, path):
        raise RuntimeError("To make asynchronous calls, receive signals or export objects...")

    monkeypatch.setattr(si.dbus.service.Object, "__init__", refuse)
    with caplog.at_level("INFO", logger="refrain.single_instance"):
        assert si.listen_for_activation("bus", lambda: None) is None
    assert "cannot bring up the window" in caplog.text
