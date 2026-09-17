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


def test_resolve_exec_line_appimage(monkeypatch, tmp_path):
    """When $APPIMAGE points at a real file, that's what wins — and
    extra_args is appended verbatim."""
    fake_appimage = tmp_path / "Refrain-x86_64.AppImage"
    fake_appimage.write_bytes(b"")
    monkeypatch.setenv("APPIMAGE", str(fake_appimage))

    import refrain.autostart as autostart

    assert autostart.resolve_exec_line() == str(fake_appimage)
    assert autostart.resolve_exec_line("--silent") == f"{fake_appimage} --silent"


def test_resolve_exec_line_quotes_paths_with_spaces(monkeypatch, tmp_path):
    """Spaces in the path require Desktop-Entry-spec double-quoting."""
    spaced_dir = tmp_path / "with space"
    spaced_dir.mkdir()
    fake_appimage = spaced_dir / "Refrain.AppImage"
    fake_appimage.write_bytes(b"")
    monkeypatch.setenv("APPIMAGE", str(fake_appimage))

    import refrain.autostart as autostart

    line = autostart.resolve_exec_line("--silent")
    assert line == f'"{fake_appimage}" --silent'
    assert line.startswith('"')


def test_refresh_brings_an_old_entry_up_to_date_but_keeps_its_exec(xdg_tmp):
    """An entry written by an older version carried invalid categories. The
    launcher in it stays: a dev checkout running now must not take over."""
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.autostart as autostart

    importlib.reload(autostart)

    autostart_file = xdg_tmp["config"] / "autostart" / "refrain.desktop"
    autostart_file.parent.mkdir(parents=True)
    autostart_file.write_text(
        "[Desktop Entry]\nType=Application\nName=Refrain\n"
        "Exec=/usr/bin/refrain --silent\nCategories=Audio;Music;Network;\n",
        encoding="utf-8",
    )

    assert autostart.refresh() is True
    contents = autostart_file.read_text(encoding="utf-8")
    assert "\nExec=/usr/bin/refrain --silent\n" in contents
    assert "\nCategories=AudioVideo;Audio;Music;\n" in contents


def test_refresh_leaves_a_current_entry_untouched(xdg_tmp):
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.autostart as autostart

    importlib.reload(autostart)

    autostart.enable()
    autostart_file = xdg_tmp["config"] / "autostart" / "refrain.desktop"
    before = autostart_file.stat().st_mtime_ns
    assert autostart.refresh() is True
    assert autostart_file.stat().st_mtime_ns == before


def test_refresh_keeps_an_entry_the_desktop_switched_off(xdg_tmp):
    """Session managers switch autostart off inside the file; refreshing the
    categories must not switch it back on or drop the desktop's own keys."""
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.autostart as autostart

    importlib.reload(autostart)

    autostart_file = xdg_tmp["config"] / "autostart" / "refrain.desktop"
    autostart_file.parent.mkdir(parents=True)
    autostart_file.write_text(
        "[Desktop Entry]\nType=Application\nName=Refrain\n"
        "Exec=/usr/bin/refrain --silent\nCategories=Audio;Music;Network;\n"
        "X-GNOME-Autostart-enabled=false\nHidden=true\nX-KDE-AutostartPhase=2\n",
        encoding="utf-8",
    )

    assert autostart.refresh() is True
    assert autostart_file.read_text(encoding="utf-8") == (
        "[Desktop Entry]\nType=Application\nName=Refrain\n"
        "Exec=/usr/bin/refrain --silent\nCategories=AudioVideo;Audio;Music;\n"
        "X-GNOME-Autostart-enabled=false\nHidden=true\nX-KDE-AutostartPhase=2\n"
    )
