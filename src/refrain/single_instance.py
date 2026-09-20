"""Single-instance lock via a well-known name on the D-Bus session bus, and a way for
a second start to ask the running Refrain to show itself.

The returned bus must stay referenced for the process lifetime, or the name is released."""

from __future__ import annotations

import logging
import time

import dbus
import dbus.service

log = logging.getLogger(__name__)

BUS_NAME = "io.github.Rockykln.Refrain"
OBJECT_PATH = "/io/github/Rockykln/Refrain"
INTERFACE = "io.github.Rockykln.Refrain"
# A running Refrain answers at once; a frozen one must not keep the
# second start waiting.
_ACTIVATE_TIMEOUT_S = 2.0

_DO_NOT_QUEUE = 4
_REPLY_PRIMARY_OWNER = 1


class AlreadyRunning(Exception):
    """Another Refrain process owns the well-known bus name.

    ``activated`` says it answered and brought up its window."""

    def __init__(self, message: str = "", activated: bool = False) -> None:
        super().__init__(message)
        self.activated = activated


class SessionBusUnavailable(Exception):
    """The session bus itself isn't reachable — distinct from AlreadyRunning
    so the caller can show a different (and accurate) error message.

    This happens on headless / minimal systems without dbus-daemon, when
    DBUS_SESSION_BUS_ADDRESS isn't set, or when Polkit / AppArmor denies
    access. Callers shouldn't tell the user "Refrain is already
    running" — that's confusing and wrong.
    """


def acquire(wait_s: float = 3.0, step_s: float = 0.2, activate: bool = False) -> dbus.SessionBus:
    """Claim the bus name, waiting ``wait_s`` for a Refrain that is on its way out.

    On a restart the bus may still hold the old process's name for a moment.
    With ``activate``, a Refrain that holds the name and answers is asked
    to show its window instead, and nobody waits.
    """
    try:
        bus = dbus.SessionBus()
    except dbus.DBusException as e:
        log.error("Session bus unreachable: %s", e)
        raise SessionBusUnavailable(str(e)) from e
    deadline = time.monotonic() + wait_s
    asked = False
    while True:
        try:
            result = bus.request_name(BUS_NAME, _DO_NOT_QUEUE)
        except dbus.DBusException as e:
            log.error("Could not request bus name %s: %s", BUS_NAME, e)
            raise SessionBusUnavailable(str(e)) from e
        if result == _REPLY_PRIMARY_OWNER:
            log.debug("Bus name acquired: %s", BUS_NAME)
            return bus
        if activate and not asked:
            asked = True
            if activate_running(bus):
                raise AlreadyRunning(f"{BUS_NAME} is already in use", activated=True)
        if time.monotonic() >= deadline:
            log.info("Another Refrain owns %s — not starting a second one", BUS_NAME)
            raise AlreadyRunning(f"{BUS_NAME} is already in use")
        time.sleep(step_s)


def activate_running(bus) -> bool:
    """Ask the Refrain that owns the bus name to show its Status window."""
    try:
        proxy = bus.get_object(BUS_NAME, OBJECT_PATH, introspect=False)
        proxy.Activate(dbus_interface=INTERFACE, timeout=_ACTIVATE_TIMEOUT_S)
    except dbus.DBusException as e:
        log.info("The running Refrain did not answer Activate: %s", e)
        return False
    log.info("Asked the running Refrain to show its window")
    return True


class _Activator(dbus.service.Object):
    def __init__(self, bus, on_activate) -> None:
        self._on_activate = on_activate
        super().__init__(bus, OBJECT_PATH)

    @dbus.service.method(INTERFACE, in_signature="", out_signature="")
    def Activate(self) -> None:
        self._on_activate()


def listen_for_activation(bus, on_activate) -> object | None:
    """Export ``Activate`` on ``bus``; keep the result referenced.

    ``on_activate`` runs on the thread that dispatches the bus — the GUI
    thread once Qt's GLib loop drives it. Without a main loop on the bus
    nothing can be exported, and a second start falls back to a message.
    """
    try:
        return _Activator(bus, on_activate)
    except Exception as e:
        log.info("Second starts cannot bring up the window: %s", e)
        return None
