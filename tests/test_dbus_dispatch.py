"""dbus-python dispatch setup must not cost us the MPRIS server — or the process.

Two regressions are guarded here, both from 0.4.3.

1. **The MPRIS server never registered on distro installs.**
   ``dbus.SessionBus()`` is a process-wide singleton: the first caller
   decides whether that connection carries a main loop, and everyone
   after gets the same object back. ``app.main`` read the Last.fm
   secrets out of the Secret Service — opening the bus — *before*
   wiring dbus into GLib, so ``dbus.service.Object`` could never export
   ("D-Bus connections must be attached to a main loop"). Plasma's media
   controls silently never worked, even with PyGObject installed.

2. **Starting our own GLib loop segfaulted Qt.**
   The naive fix (just moving the init earlier) let the worker thread
   acquire the default GMainContext before ``QApplication`` existed. Qt's
   own glib dispatcher then tripped ``g_main_context_push_thread_default:
   assertion 'acquired_context' failed`` and the process died with
   SIGSEGV once timers began crossing threads. Qt's dispatcher already
   pumps the default context, so the extra loop is only a fallback for a
   Qt that isn't glib-backed, and it may only start once Qt is up.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


# ---------------------------------------------------------------------------
# Ordering invariant (regression 1)
# ---------------------------------------------------------------------------
def test_dbus_loop_is_wired_before_anything_opens_the_bus():
    """``_ensure_dbus_glib_loop()`` must precede the keyring lookup.

    Asserted against the source of ``main`` because the failure is one of
    *ordering*: both calls succeed on their own, and the damage only shows
    up later as an MPRIS server that cannot export.
    """
    import refrain.app as app

    src = Path(app.__file__).read_text()
    body = src[src.index("def main(") :]

    loop_at = body.index("_ensure_dbus_glib_loop()")
    keyring_at = body.index("_load_lastfm_secrets(")
    assert loop_at < keyring_at, (
        "_ensure_dbus_glib_loop() must run before the Secret Service lookup — "
        "dbus.SessionBus() is a singleton and the first caller wins"
    )


def test_dispatch_pump_starts_only_after_qapplication():
    """The pump call must sit after ``QApplication`` is constructed."""
    import refrain.app as app

    src = Path(app.__file__).read_text()
    body = src[src.index("def main(") :]

    qapp_at = body.index("QApplication(sys.argv)")
    pump_at = body.index("ensure_dbus_dispatch_pump()")
    assert qapp_at < pump_at, (
        "ensure_dbus_dispatch_pump() must run after QApplication — starting a "
        "GLib loop first steals the default GMainContext and segfaults Qt"
    )


def test_ensure_dbus_glib_loop_starts_no_thread_of_its_own():
    """Wiring the dispatch must not spin a loop; that is the pump's job."""
    import refrain.sources.mpris_server as M

    src = Path(M.__file__).read_text()
    fn = src[src.index("def _ensure_dbus_glib_loop(") : src.index("def _qt_pumps_glib_context(")]
    assert "Thread(" not in fn, (
        "_ensure_dbus_glib_loop() must not start a GLib thread — it runs before QApplication exists"
    )
    assert "DBusGMainLoop(set_as_default=True)" in fn


# ---------------------------------------------------------------------------
# Dispatcher classification (regression 2)
# ---------------------------------------------------------------------------
class _FakeMeta:
    def __init__(self, name: str) -> None:
        self._name = name

    def className(self) -> str:
        return self._name


class _FakeDispatcher:
    def __init__(self, name: str) -> None:
        self._meta = _FakeMeta(name)

    def metaObject(self) -> _FakeMeta:
        return self._meta


@pytest.mark.parametrize(
    ("class_name", "pumps"),
    [
        ("QPAEventDispatcherGlib", True),
        ("QEventDispatcherGlib", True),
        ("QEventDispatcherUNIX", False),
    ],
)
def test_qt_pumps_glib_context_classification(monkeypatch, class_name, pumps):
    """className() is a ``str`` — comparing it against ``bytes`` raised a
    TypeError that the defensive except swallowed, so every install took
    the fallback path."""
    import PySide6.QtCore as QtCore

    import refrain.sources.mpris_server as M

    monkeypatch.setattr(
        QtCore.QCoreApplication, "instance", staticmethod(lambda: object()), raising=False
    )
    monkeypatch.setattr(
        QtCore.QAbstractEventDispatcher,
        "instance",
        staticmethod(lambda: _FakeDispatcher(class_name)),
        raising=False,
    )
    assert M._qt_pumps_glib_context() is pumps


def test_qt_pumps_glib_context_false_without_qapplication(monkeypatch):
    import PySide6.QtCore as QtCore

    import refrain.sources.mpris_server as M

    monkeypatch.setattr(
        QtCore.QCoreApplication, "instance", staticmethod(lambda: None), raising=False
    )
    assert M._qt_pumps_glib_context() is False


# ---------------------------------------------------------------------------
# Pump behaviour
# ---------------------------------------------------------------------------
def test_pump_is_a_noop_when_qt_already_pumps(monkeypatch):
    import refrain.sources.mpris_server as M

    monkeypatch.setattr(M, "_DBUS_LOOP_INITIALIZED", True)
    monkeypatch.setattr(M, "_GLIB_THREAD", None)
    monkeypatch.setattr(M, "_qt_pumps_glib_context", lambda: True)

    M.ensure_dbus_dispatch_pump()

    assert M._GLIB_THREAD is None


def test_pump_does_nothing_when_dispatch_was_never_wired(monkeypatch):
    """No PyGObject means no dispatch and no loop — just graceful degradation."""
    import refrain.sources.mpris_server as M

    monkeypatch.setattr(M, "_DBUS_LOOP_INITIALIZED", False)
    monkeypatch.setattr(M, "_GLIB_THREAD", None)

    def _boom():  # pragma: no cover - must not be reached
        raise AssertionError("must not classify the dispatcher without dbus wired up")

    monkeypatch.setattr(M, "_qt_pumps_glib_context", _boom)

    M.ensure_dbus_dispatch_pump()

    assert M._GLIB_THREAD is None


def test_pump_is_idempotent(monkeypatch):
    import refrain.sources.mpris_server as M

    sentinel = object()
    monkeypatch.setattr(M, "_DBUS_LOOP_INITIALIZED", True)
    monkeypatch.setattr(M, "_GLIB_THREAD", sentinel)

    def _boom():  # pragma: no cover - must not be reached
        raise AssertionError("must not re-classify once a loop is running")

    monkeypatch.setattr(M, "_qt_pumps_glib_context", _boom)

    M.ensure_dbus_dispatch_pump()

    assert M._GLIB_THREAD is sentinel


def test_warning_names_the_distro_packages():
    """The PyGObject hint is the only thing a user sees when controls are
    missing — keep it actionable."""
    import refrain.sources.mpris_server as M

    src = Path(M.__file__).read_text()
    fn = src[src.index("def _ensure_dbus_glib_loop(") : src.index("def _qt_pumps_glib_context(")]
    for pkg in ("python-gobject", "python3-gi"):
        assert pkg in fn
    assert re.search(r"PyGObject not installed", fn)
