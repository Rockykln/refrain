"""Shared fixtures: per-test XDG dirs, no network, no real keyring."""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import pytest

# Make `src/` importable so `import refrain.<x>` works without installing.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test reaches the internet, not even from a background thread.
    Tests that stub ``urlopen`` themselves win over this."""
    import urllib.error
    import urllib.request

    def _refuse(*_args, **_kwargs):
        raise urllib.error.URLError("network access is disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)


@pytest.fixture(autouse=True, scope="session")
def _no_desktop_notifications(tmp_path_factory):
    """A stand-in notify-send first on PATH for the whole run.

    Session-wide, because a worker thread can still notify after its
    test's own fixtures are gone — and the real one pops up on the
    developer's desktop.
    """
    bin_dir = tmp_path_factory.mktemp("bin")
    stub = bin_dir / "notify-send"
    stub.write_text("#!/bin/sh\necho 1\n", encoding="utf-8")
    stub.chmod(0o755)
    saved = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{saved}"
    import refrain.daemon as daemon

    saved_bin, daemon._NOTIFY_BIN = daemon._NOTIFY_BIN, str(stub)
    yield
    daemon._NOTIFY_BIN = saved_bin
    os.environ["PATH"] = saved


@pytest.fixture(autouse=True)
def _private_state(tmp_path_factory, monkeypatch):
    """No test reads or writes the user's real config, state, cache or keyring.
    ``xdg_tmp`` wins over this; keyring tests pass a fake bus to ``SecretStore``."""
    root = tmp_path_factory.mktemp("xdg")
    for var, name in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "runtime"),
    ):
        (root / name).mkdir()
        monkeypatch.setenv(var, str(root / name))

    from refrain import secrets_store

    def _no_session_bus():
        raise RuntimeError("the real session bus is off limits in tests")

    monkeypatch.setattr(secrets_store, "_session_bus", _no_session_bus)


@pytest.fixture(autouse=True)
def free_dropped_qt_objects():
    """Free every Qt object a test let go of, before the next one starts.

    Python decides on its own when to free a widget or daemon, and Qt
    deletes the C++ side with it. Left to chance that lands in the middle
    of a later test driving the event loop, where a timer that was still
    queued fires into freed memory and takes the interpreter down. Doing
    it here means it happens with no event loop running.
    """
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
    except ImportError:
        gc.collect()
        return
    app = QCoreApplication.instance()
    # A test may have put a stand-in in its place.
    if isinstance(app, QCoreApplication):
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@pytest.fixture
def xdg_tmp(tmp_path, monkeypatch):
    """Redirect every XDG_* env var Refrain reads to an isolated tmp tree."""
    config = tmp_path / "config"
    state = tmp_path / "state"
    cache = tmp_path / "cache"
    for d in (config, state, cache):
        d.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return {"config": config, "state": state, "cache": cache}
