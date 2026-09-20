"""The real ``_session_bus`` helper: normally forbidden in tests (conftest replaces it so no
test ever touches a real Secret Service), exercised here against a stubbed dbus.SessionBus."""

from __future__ import annotations

import importlib.util

from refrain import secrets_store


def _load_fresh_module():
    """A second, independent load of secrets_store.py, so patching its
    ``_session_bus`` back to the original code doesn't touch the shared
    module every other test relies on being safely stubbed out."""
    spec = importlib.util.spec_from_file_location(
        "refrain._secrets_store_probe", secrets_store.__file__
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_real_session_bus_helper_asks_dbus_for_a_session_bus(monkeypatch):
    import dbus

    fresh = _load_fresh_module()
    fake_bus = object()
    monkeypatch.setattr(dbus, "SessionBus", lambda: fake_bus)
    # Swap in the untouched implementation just for this call — everywhere
    # else keeps the conftest stub that refuses the real bus.
    monkeypatch.setattr(secrets_store, "_session_bus", fresh._session_bus)
    assert secrets_store._session_bus() is fake_bus
