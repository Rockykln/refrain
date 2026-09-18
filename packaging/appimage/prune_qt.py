"""Strip the PySide6 wheel down to the Qt modules Refrain uses.

PySide6-Essentials still carries Qt tools and a few GPL-only libraries
(Qt Wayland Compositor, Qt Lottie). Keep only the bindings below and the Qt
libraries they and the runtime plugins actually link against.

Usage: prune_qt.py <dist-packages dir>
"""

import shutil
import subprocess
import sys
from pathlib import Path

BINDINGS = {"QtCore", "QtGui", "QtWidgets", "QtSvg", "QtDBus", "QtNetwork"}
PLUGIN_DIRS = {
    "egldeviceintegrations",
    "generic",
    "iconengines",
    "imageformats",
    "networkinformation",
    "platforminputcontexts",
    "platforms",
    "platformthemes",
    "tls",
    "wayland-decoration-client",
    "wayland-graphics-integration-client",
    "wayland-shell-integration",
    "xcbglintegrations",
}


def needed(path):
    out = subprocess.run(
        ["readelf", "-d", str(path)], capture_output=True, text=True, check=True
    ).stdout
    return {
        line.split("[", 1)[1].split("]", 1)[0] for line in out.splitlines() if "(NEEDED)" in line
    }


def is_elf(path):
    with path.open("rb") as f:
        return f.read(4) == b"\x7fELF"


def main():
    pyside = Path(sys.argv[1]) / "PySide6"
    qt = pyside / "Qt"

    for entry in pyside.iterdir():
        stem = entry.name.split(".", 1)[0]
        if entry.suffix == ".so" and entry.name.startswith("Qt") and stem not in BINDINGS:
            entry.unlink()
        elif entry.suffix == ".pyi" and stem.startswith("Qt") and stem not in BINDINGS:
            entry.unlink()
        elif (
            entry.is_file()
            and not entry.name.endswith(".so")
            and ".so." not in entry.name
            and is_elf(entry)
        ):
            entry.unlink()
    for lib in pyside.glob("libpyside6qml*"):
        lib.unlink()
    for directory in (qt / "libexec", qt / "metatypes", qt / "qml"):
        shutil.rmtree(directory, ignore_errors=True)
    for group in (qt / "plugins").iterdir():
        if group.name not in PLUGIN_DIRS:
            shutil.rmtree(group)

    libs = {p.name: p for p in (qt / "lib").iterdir() if ".so" in p.name}
    plugins = []
    for plugin in (qt / "plugins").rglob("*.so"):
        # Plugins for Addons modules (virtual keyboard, PDF) come with Essentials
        # but cannot load without them.
        if any(n.startswith("libQt6") and n not in libs for n in needed(plugin)):
            plugin.unlink()
        else:
            plugins.append(plugin)
    todo = list(pyside.glob("*.so*")) + plugins
    todo += list((pyside.parent / "shiboken6").glob("*.so*"))
    keep = set()
    while todo:
        for name in needed(todo.pop()):
            if name in libs and name not in keep:
                keep.add(name)
                todo.append(libs[name])
    for name, path in libs.items():
        if name not in keep:
            path.unlink()


if __name__ == "__main__":
    main()
