"""XDG path resolution."""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path


def test_xdg_overrides_take_effect(xdg_tmp):
    import refrain.paths

    importlib.reload(refrain.paths)
    p = refrain.paths

    assert p.config_dir() == xdg_tmp["config"] / "refrain"
    assert p.config_path() == xdg_tmp["config"] / "refrain" / "config.toml"
    assert p.state_dir() == xdg_tmp["state"] / "refrain"
    assert p.log_path() == xdg_tmp["state"] / "refrain" / "refrain.log"
    assert p.cache_dir() == xdg_tmp["cache"] / "refrain"
    assert p.cover_cache_dir() == xdg_tmp["cache"] / "refrain" / "covers"


def test_xdg_falls_back_to_home_subpaths(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)

    import refrain.paths

    importlib.reload(refrain.paths)
    p = refrain.paths

    assert p.config_dir() == home / ".config" / "refrain"
    assert p.state_dir() == home / ".local" / "state" / "refrain"
    assert p.cache_dir() == home / ".cache" / "refrain"


def test_assets_dir_is_inside_package():
    import refrain.paths

    importlib.reload(refrain.paths)
    a = refrain.paths.assets_dir()
    assert a.name == "assets"
    assert a.parent.name == "refrain"
    # Bundled icons must actually exist
    assert (a / "icons" / "refrain.svg").is_file()
    for state in ("playing", "paused", "stopped"):
        assert (a / "icons" / f"tray-{state}.svg").is_file()


def test_relative_xdg_values_are_ignored(monkeypatch, tmp_path):
    """The XDG spec calls relative values invalid; uninstall must never
    rmtree a path that resolves against the current directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    monkeypatch.setenv("XDG_STATE_HOME", ".")
    monkeypatch.setenv("XDG_CACHE_HOME", "cache")

    import refrain.paths

    importlib.reload(refrain.paths)
    p = refrain.paths

    assert p.config_dir() == home / ".config" / "refrain"
    assert p.state_dir() == home / ".local" / "state" / "refrain"
    assert p.cache_dir() == home / ".cache" / "refrain"
    assert p.autostart_path() == home / ".config" / "autostart" / "refrain.desktop"


def test_covers_in_flight_fall_back_to_the_system_temp_dir(monkeypatch, tmp_path):
    """Without a runtime dir they still need somewhere to go, or no cover
    is ever downloaded."""
    import refrain.paths as p

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert p.cover_temp_base() == Path(tempfile.gettempdir())

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    assert p.cover_temp_base() == runtime

    # A relative value is no more valid here than anywhere else.
    monkeypatch.setenv("XDG_RUNTIME_DIR", "relative/runtime")
    assert p.cover_temp_base() == Path(tempfile.gettempdir())
