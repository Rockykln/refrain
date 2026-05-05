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
