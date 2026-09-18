"""dbus-python dispatch setup must neither cost the MPRIS server nor crash Qt.
The first SessionBus() decides the main loop for everyone; an own GLib loop only after Qt is up."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


# ---------------------------------------------------------------------------
# Ordering invariant
# ---------------------------------------------------------------------------
def test_dbus_loop_is_wired_before_anything_opens_the_bus():
    """``_ensure_dbus_glib_loop()`` must precede the keyring lookup.
    Checked on the source of ``main``: both calls succeed alone, only the order matters."""
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
# Dispatcher classification
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
    """className() is a ``str``; comparing it with ``bytes`` would always take the fallback."""
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
    """No PyGObject means no dispatch and no loop, and no error."""
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
    """The PyGObject hint is all a user sees when controls are missing."""
    import refrain.sources.mpris_server as M

    src = Path(M.__file__).read_text()
    fn = src[src.index("def _ensure_dbus_glib_loop(") : src.index("def _qt_pumps_glib_context(")]
    for pkg in ("python-gobject", "python3-gi"):
        assert pkg in fn
    assert re.search(r"PyGObject not installed", fn)
