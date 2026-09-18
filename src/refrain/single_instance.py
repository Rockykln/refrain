"""Single-instance lock via a well-known name on the D-Bus session bus.

The returned bus must stay referenced for the process lifetime, or the name is released."""

from __future__ import annotations

import logging
import time

import dbus

log = logging.getLogger(__name__)

BUS_NAME = "io.github.Rockykln.Refrain"

_DO_NOT_QUEUE = 4
_REPLY_PRIMARY_OWNER = 1


class AlreadyRunning(Exception):
    """Another Refrain process owns the well-known bus name."""


class SessionBusUnavailable(Exception):
    """The session bus itself isn't reachable — distinct from AlreadyRunning
    so the caller can show a different (and accurate) error message.

    This happens on headless / minimal systems without dbus-daemon, when
    DBUS_SESSION_BUS_ADDRESS isn't set, or when Polkit / AppArmor denies
    access. Callers shouldn't tell the user "Refrain is already
    running" — that's confusing and wrong.
    """


def acquire(wait_s: float = 3.0, step_s: float = 0.2) -> dbus.SessionBus:
    """Claim the bus name, waiting ``wait_s`` for a Refrain that is on its way out.

    On a restart the bus may still hold the old process's name for a moment.
    """
    try:
        bus = dbus.SessionBus()
    except dbus.DBusException as e:
        log.error("Session bus unreachable: %s", e)
        raise SessionBusUnavailable(str(e)) from e
    deadline = time.monotonic() + wait_s
    while True:
        try:
            result = bus.request_name(BUS_NAME, _DO_NOT_QUEUE)
        except dbus.DBusException as e:
            log.error("Could not request bus name %s: %s", BUS_NAME, e)
            raise SessionBusUnavailable(str(e)) from e
        if result == _REPLY_PRIMARY_OWNER:
            log.debug("Bus name acquired: %s", BUS_NAME)
            return bus
        if time.monotonic() >= deadline:
            log.info("Another Refrain owns %s — not starting a second one", BUS_NAME)
            raise AlreadyRunning(f"{BUS_NAME} is already in use")
        time.sleep(step_s)
