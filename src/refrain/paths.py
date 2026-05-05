"""XDG-compliant runtime paths for Refrain."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env_var: str, default_subpath: str) -> Path:
    custom = os.environ.get(env_var)
    if custom:
        return Path(custom)
    return Path.home() / default_subpath


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "refrain"


def config_path() -> Path:
    return config_dir() / "config.toml"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "refrain"


def log_path() -> Path:
    return state_dir() / "refrain.log"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "refrain"


def cover_cache_dir() -> Path:
    return cache_dir() / "covers"


def autostart_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "refrain.desktop"


def assets_dir() -> Path:
    """Bundled assets directory (lives inside the installed package)."""
    return Path(__file__).parent / "assets"
