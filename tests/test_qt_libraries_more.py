"""Missing-library check: real plugin loading, platform choice and the printed message."""

from __future__ import annotations

import ctypes.util

import pytest

from refrain import qt_libraries
from tests.test_qt_libraries import _cannot_open, _errors, _plugins


def test_a_file_that_is_no_library_reports_a_load_error(tmp_path):
    fake = tmp_path / "libqxcb.so"
    fake.write_bytes(b"not an ELF file")
    error = qt_libraries._load_error(fake)
    assert error
    assert str(fake) in error


def test_a_library_that_loads_reports_no_error():
    libc = ctypes.util.find_library("c")
    if libc is None:
        pytest.skip("no C library to load")
    assert qt_libraries._load_error(libc) is None


def test_a_broken_plugin_file_is_left_to_qt(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    plugins = _plugins(tmp_path, "xcb")
    assert qt_libraries.missing_libraries(plugins) == []


@pytest.mark.parametrize(
    "env,platforms",
    [
        ({"QT_QPA_PLATFORM": "wayland:scale=2;xcb"}, ["wayland", "xcb"]),
        ({"QT_QPA_PLATFORM": "xcb;"}, ["xcb"]),
        ({"WAYLAND_DISPLAY": "wayland-0"}, ["wayland", "xcb"]),
        ({}, ["xcb"]),
    ],
)
def test_the_platforms_checked_follow_the_session(monkeypatch, env, platforms):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert qt_libraries._platforms() == platforms


def test_a_library_both_platforms_lack_is_named_once(tmp_path, monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    plugins = _plugins(tmp_path, "wayland", "xcb")
    both = _cannot_open("libEGL.so.1") + "\n" + _cannot_open("libfontconfig.so.1")
    load = _errors(wayland=both, xcb=_cannot_open("libfontconfig.so.1"))
    assert qt_libraries.missing_libraries(plugins, load) == ["libEGL.so.1", "libfontconfig.so.1"]


def test_an_unreadable_os_release_gives_no_command(tmp_path):
    missing = tmp_path / "no-os-release"
    assert qt_libraries.install_hint(["libGL.so.1"], missing) == ""
    text = qt_libraries.message(["libGL.so.1"], missing)
    assert "Install with" not in text
    assert text.startswith("Refrain can't open a window: Qt needs libGL.so.1")


def test_the_message_ends_with_the_command_to_copy(tmp_path):
    path = tmp_path / "os-release"
    path.write_text('NAME="Debian"\nID=debian\n', encoding="utf-8")
    libs = ["libEGL.so.1", "libGL.so.1", "libxcb-shape.so.0"]
    text = qt_libraries.message(libs, path)
    assert text.splitlines() == [
        "Refrain can't open a window: Qt needs libEGL.so.1, libGL.so.1, libxcb-shape.so.0, "
        "which this system doesn't have.",
        "Install with:",
        "  sudo apt install libegl1 libgl1 libxcb-shape0",
    ]


def test_packages_shared_by_two_libraries_are_listed_once(tmp_path):
    path = tmp_path / "os-release"
    path.write_text("ID=manjaro\nID_LIKE=arch\n", encoding="utf-8")
    hint = qt_libraries.install_hint(["libEGL.so.1", "libGL.so.1"], path)
    assert hint == "sudo pacman -S libglvnd"
