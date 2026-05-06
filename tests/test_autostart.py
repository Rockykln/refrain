"""Autostart toggle — writes and removes the XDG autostart desktop file."""

from __future__ import annotations

import importlib


def test_enable_then_disable(xdg_tmp):
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.autostart as autostart

    importlib.reload(autostart)

    autostart_file = xdg_tmp["config"] / "autostart" / "refrain.desktop"

    assert autostart.is_enabled() is False

    autostart.enable()
    assert autostart_file.is_file()
    assert autostart.is_enabled() is True

    contents = autostart_file.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in contents
    # Exec= must point at *something* with --silent appended; the exact path
    # depends on how the test runner is invoked (pytest binary, venv, etc).
    assert "\nExec=" in contents
    assert "--silent" in contents
    assert "Name=Refrain" in contents

    autostart.disable()
    assert not autostart_file.exists()
    assert autostart.is_enabled() is False


def test_disable_is_idempotent(xdg_tmp):
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.autostart as autostart

    importlib.reload(autostart)

    # Already absent; calling disable() must not raise.
    autostart.disable()
    autostart.disable()
    assert autostart.is_enabled() is False


def test_enable_overwrites_existing(xdg_tmp):
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.autostart as autostart

    importlib.reload(autostart)

    autostart.enable()
    autostart_file = xdg_tmp["config"] / "autostart" / "refrain.desktop"
    autostart_file.write_text("garbage", encoding="utf-8")

    autostart.enable()
    contents = autostart_file.read_text(encoding="utf-8")
    assert "\nExec=" in contents
    assert "--silent" in contents
    assert "garbage" not in contents
