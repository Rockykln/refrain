"""The message Refrain gives instead of Qt's abort when a system library is missing."""

from __future__ import annotations

import pytest

from refrain import qt_libraries


def _plugins(tmp_path, *names):
    platforms = tmp_path / "platforms"
    platforms.mkdir()
    for name in names:
        (platforms / f"libq{name}.so").write_bytes(b"")
    return tmp_path


def _errors(**by_platform):
    def load_error(plugin):
        return by_platform.get(plugin.name.removeprefix("libq").removesuffix(".so"))

    return load_error


def _cannot_open(lib):
    return f"{lib}: cannot open shared object file: No such file or directory"


@pytest.fixture(autouse=True)
def _wayland_session(monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")


def test_nothing_is_missing_when_one_platform_loads(tmp_path):
    plugins = _plugins(tmp_path, "wayland", "xcb")
    load = _errors(wayland=_cannot_open("libwayland-cursor.so.0"))
    assert qt_libraries.missing_libraries(plugins, load) == []


def test_what_every_platform_lacks_is_named(tmp_path):
    plugins = _plugins(tmp_path, "wayland", "xcb")
    load = _errors(
        wayland=_cannot_open("libwayland-cursor.so.0"),
        xcb=_cannot_open("libxcb-cursor.so.0"),
    )
    assert qt_libraries.missing_libraries(plugins, load) == [
        "libwayland-cursor.so.0",
        "libxcb-cursor.so.0",
    ]


def test_the_requested_platform_is_the_one_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    plugins = _plugins(tmp_path, "wayland", "xcb")
    load = _errors(wayland=_cannot_open("libwayland-cursor.so.0"))
    assert qt_libraries.missing_libraries(plugins, load) == []


def test_any_other_load_error_is_left_to_qt(tmp_path):
    plugins = _plugins(tmp_path, "wayland")
    load = _errors(wayland="undefined symbol: _ZN7QWidget")
    assert qt_libraries.missing_libraries(plugins, load) == []


def test_without_plugins_there_is_nothing_to_say(tmp_path):
    (tmp_path / "platforms").mkdir()
    assert qt_libraries.missing_libraries(tmp_path, _errors()) == []


@pytest.mark.parametrize(
    "os_release, command",
    [
        ("ID=ubuntu\nID_LIKE=debian\n", "sudo apt install libxcb-cursor0 libegl1"),
        ("ID=fedora\n", "sudo dnf install xcb-util-cursor libglvnd-egl"),
        (
            'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
            "sudo zypper install libxcb-cursor0 libglvnd",
        ),
        ("ID=cachyos\nID_LIKE=arch\n", "sudo pacman -S xcb-util-cursor libglvnd"),
    ],
)
def test_the_hint_uses_the_distros_own_package_names(tmp_path, os_release, command):
    path = tmp_path / "os-release"
    path.write_text(os_release, encoding="utf-8")
    assert qt_libraries.install_hint(["libxcb-cursor.so.0", "libEGL.so.1"], path) == command


def test_an_unknown_distro_or_library_gets_no_guessed_command(tmp_path):
    path = tmp_path / "os-release"
    path.write_text("ID=gentoo\n", encoding="utf-8")
    assert qt_libraries.install_hint(["libxcb-cursor.so.0"], path) == ""
    path.write_text("ID=fedora\n", encoding="utf-8")
    assert qt_libraries.install_hint(["libfoo.so.1"], path) == ""
    assert "libxcb-cursor.so.0" in qt_libraries.message(["libxcb-cursor.so.0"], path)
