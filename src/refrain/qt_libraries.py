"""Name the system library Qt's platform plugin is missing, before Qt aborts on it."""

from __future__ import annotations

import ctypes
import os
import re
from pathlib import Path

_MISSING = re.compile(r"(lib[\w.+-]+\.so[\w.]*): cannot open shared object file")

# library -> package, per package manager
_PACKAGES = {
    "libxcb-cursor.so.0": (
        "libxcb-cursor0",
        "xcb-util-cursor",
        "libxcb-cursor0",
        "xcb-util-cursor",
    ),
    "libxcb-icccm.so.4": ("libxcb-icccm4", "xcb-util-wm", "libxcb-icccm4", "xcb-util-wm"),
    "libxcb-keysyms.so.1": (
        "libxcb-keysyms1",
        "xcb-util-keysyms",
        "libxcb-keysyms1",
        "xcb-util-keysyms",
    ),
    "libxcb-shape.so.0": ("libxcb-shape0", "libxcb", "libxcb-shape0", "libxcb"),
    "libxkbcommon-x11.so.0": (
        "libxkbcommon-x11-0",
        "libxkbcommon-x11",
        "libxkbcommon-x11-0",
        "libxkbcommon-x11",
    ),
    "libwayland-cursor.so.0": (
        "libwayland-cursor0",
        "libwayland-cursor",
        "libwayland-cursor0",
        "wayland",
    ),
    "libwayland-egl.so.1": ("libwayland-egl1", "libwayland-egl", "libwayland-egl1", "wayland"),
    "libEGL.so.1": ("libegl1", "libglvnd-egl", "libglvnd", "libglvnd"),
    "libGL.so.1": ("libgl1", "libglvnd-glx", "libglvnd", "libglvnd"),
    "libfontconfig.so.1": ("libfontconfig1", "fontconfig", "libfontconfig1", "fontconfig"),
}
_MANAGERS = (
    ({"debian", "ubuntu", "linuxmint", "pop"}, "sudo apt install"),
    ({"fedora", "rhel", "centos"}, "sudo dnf install"),
    ({"opensuse", "opensuse-tumbleweed", "opensuse-leap", "suse"}, "sudo zypper install"),
    ({"arch", "cachyos", "manjaro", "endeavouros"}, "sudo pacman -S"),
)


def _platforms() -> list[str]:
    requested = os.environ.get("QT_QPA_PLATFORM", "")
    if requested:
        return [p.split(":")[0] for p in requested.split(";") if p]
    return ["wayland", "xcb"] if os.environ.get("WAYLAND_DISPLAY") else ["xcb"]


def _load_error(plugin: Path) -> str | None:
    try:
        ctypes.CDLL(str(plugin))
    except OSError as e:
        return str(e)
    return None


def missing_libraries(plugins_dir: Path, load_error=_load_error) -> list[str]:
    """The libraries every candidate platform plugin lacks; empty when one loads."""
    missing: list[str] = []
    for name in _platforms():
        plugin = plugins_dir / "platforms" / f"libq{name}.so"
        if not plugin.exists():
            continue
        error = load_error(plugin)
        if error is None:
            return []
        found = _MISSING.findall(error)
        if not found:
            return []
        missing += [lib for lib in found if lib not in missing]
    return missing


def _distro_ids(os_release: Path) -> set[str]:
    try:
        text = os_release.read_text(encoding="utf-8")
    except OSError:
        return set()
    ids: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids.update(value.strip().strip('"').split())
    return ids


def install_hint(libraries: list[str], os_release: Path = Path("/etc/os-release")) -> str:
    ids = _distro_ids(os_release)
    for index, (distros, command) in enumerate(_MANAGERS):
        if ids & distros:
            packages = [_PACKAGES[lib][index] for lib in libraries if lib in _PACKAGES]
            if len(packages) == len(libraries):
                return f"{command} {' '.join(dict.fromkeys(packages))}"
    return ""


def message(libraries: list[str], os_release: Path = Path("/etc/os-release")) -> str:
    text = (
        "Refrain can't open a window: Qt needs "
        + ", ".join(libraries)
        + ", which this system doesn't have."
    )
    hint = install_hint(libraries, os_release)
    return f"{text}\nInstall with:\n  {hint}" if hint else text
