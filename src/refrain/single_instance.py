"""Single-instance lock via D-Bus name acquisition.

More idiomatic than a /tmp/.lock file: we claim a well-known bus name on
the session bus. If another Refrain instance already owns it, we bail.
The returned bus reference must be kept alive for the duration of the
process so that the name does not get released.
"""

from __future__ import annotations

import logging

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


def acquire() -> dbus.SessionBus:
    try:
        bus = dbus.SessionBus()
    except dbus.DBusException as e:
        log.error("Session bus unreachable: %s", e)
        raise SessionBusUnavailable(str(e)) from e
    try:
        result = bus.request_name(BUS_NAME, _DO_NOT_QUEUE)
    except dbus.DBusException as e:
        log.error("Could not request bus name %s: %s", BUS_NAME, e)
        raise SessionBusUnavailable(str(e)) from e

    if result != _REPLY_PRIMARY_OWNER:
        raise AlreadyRunning(f"{BUS_NAME} is already in use")
    log.debug("Bus name acquired: %s", BUS_NAME)
    return bus
