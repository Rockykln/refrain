"""XDG path resolution."""

from __future__ import annotations

import importlib


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
