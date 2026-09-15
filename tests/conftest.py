"""Shared pytest fixtures.

Tests run hermetically — XDG paths are redirected to a tmp dir per test, and
network-using modules are stubbed at the urllib level. The suite does not
require Qt, D-Bus, Discord, or BlueZ to be available.
"""

from __future__ import annotations

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
    """No test reaches the internet — not even from a background thread.

    Tests that exercise an HTTP path stub ``urlopen`` themselves, and
    their monkeypatch wins over this one. Anything else gets the error an
    offline machine would. Without this, a Scrobbler test that rebuilt a
    real Last.fm client sent "now playing" to Last.fm from its executor
    thread — the thread that was mid-request when a 3.12 CI run died
    with a segfault.
    """
    import urllib.error
    import urllib.request

    def _refuse(*_args, **_kwargs):
        raise urllib.error.URLError("network access is disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)


@pytest.fixture(autouse=True)
def _private_state(tmp_path_factory, monkeypatch):
    """No test reads or writes the real ``~/.local/state/refrain``.

    The history, the scrobble queue and the play in progress all default
    to it, and a test building a DaemonWorker or a Scrobbler without a
    path of its own would otherwise pick up — and overwrite — the
    user's. Tests that want the full tree use ``xdg_tmp``, which wins.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))


@pytest.fixture
def xdg_tmp(tmp_path, monkeypatch):
    """Redirect every XDG_* env var Refrain reads to an isolated tmp tree.

    Each test gets its own clean config / state / cache directories so the
    real user dotfiles are never touched.
    """
    config = tmp_path / "config"
    state = tmp_path / "state"
    cache = tmp_path / "cache"
    for d in (config, state, cache):
        d.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return {"config": config, "state": state, "cache": cache}
