"""The single-instance lock tells a missing session bus apart from a running Refrain."""

from __future__ import annotations

import pytest

pytest.importorskip("dbus")

import refrain.single_instance as si  # noqa: E402


def test_no_session_bus_is_not_already_running(monkeypatch):
    def no_bus():
        raise si.dbus.DBusException("DBUS_SESSION_BUS_ADDRESS not set")

    monkeypatch.setattr(si.dbus, "SessionBus", no_bus)
    with pytest.raises(si.SessionBusUnavailable, match="DBUS_SESSION_BUS_ADDRESS"):
        si.acquire(wait_s=0)


def test_a_refused_name_request_is_a_bus_problem(monkeypatch):
    class _Bus:
        def request_name(self, name, flags):
            raise si.dbus.DBusException("org.freedesktop.DBus.Error.AccessDenied")

    monkeypatch.setattr(si.dbus, "SessionBus", _Bus)
    with pytest.raises(si.SessionBusUnavailable, match="AccessDenied"):
        si.acquire(wait_s=0)


def test_the_name_is_requested_without_queueing(monkeypatch):
    asked = []

    class _Bus:
        def request_name(self, name, flags):
            asked.append((name, flags))
            return 1

    monkeypatch.setattr(si.dbus, "SessionBus", _Bus)
    si.acquire(wait_s=0)
    assert asked == [("io.github.Rockykln.Refrain", 4)]
