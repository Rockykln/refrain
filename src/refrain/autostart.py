"""Toggle XDG autostart entry for Refrain.

The Exec= value has to point at *something the desktop session can actually
launch* — not just `refrain`, because for venv / pip --user / pipx installs
the bare name isn't on $PATH at session-startup time.

Resolution order:
  1. $APPIMAGE — the .AppImage path, if we were launched from one.
  2. shutil.which("refrain") — picks up venv shims, /usr/bin, ~/.local/bin.
  3. sys.argv[0] — whatever launched the current process, as an absolute path.
  4. `<sys.executable> -m refrain` — last resort if argv[0] isn't a file.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from refrain.paths import autostart_path

log = logging.getLogger(__name__)

_DESKTOP_ENTRY_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=Refrain
GenericName=Apple Music Discord RPC
Comment=Discord Rich Presence for Apple Music
Exec={exec_line}
Icon=refrain
Terminal=false
Categories=Audio;Music;Network;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def _quote(path: str) -> str:
    # Per the Desktop Entry spec, paths with spaces or special chars must be
    # double-quoted in Exec=. Escape inner double quotes and backslashes.
    if any(c in path for c in ' \t\n"\\$`'):
        escaped = path.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return path


def resolve_exec_line(extra_args: str = "") -> str:
    """Return the Desktop Entry ``Exec=`` line for the running install.

    Picks the first viable launcher in this order: ``$APPIMAGE`` →
    ``which refrain`` → ``argv[0]`` resolved to an absolute path →
    ``<python> -m refrain`` as a last resort. Properly quotes paths
    that need it.

    ``extra_args`` is appended after the launcher (e.g. ``"--silent"``
    for autostart, empty for the menu entry).
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).is_file():
        base = _quote(appimage)
    else:
        on_path = shutil.which("refrain")
        if on_path:
            base = _quote(on_path)
        else:
            argv0 = sys.argv[0] if sys.argv else ""
            argv0_abs = str(Path(argv0).resolve()) if argv0 else ""
            if argv0_abs and Path(argv0_abs).is_file():
                base = _quote(argv0_abs)
            else:
                base = f"{_quote(sys.executable)} -m refrain"
    return f"{base} {extra_args}".rstrip()


def _desktop_entry() -> str:
    return _DESKTOP_ENTRY_TEMPLATE.format(exec_line=resolve_exec_line("--silent"))


def is_enabled() -> bool:
    return autostart_path().exists()


def enable() -> None:
    p = autostart_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_desktop_entry(), encoding="utf-8")
    log.info("Autostart enabled at %s", p)


def disable() -> None:
    p = autostart_path()
    if p.exists():
        p.unlink()
        log.info("Autostart disabled (removed %s)", p)
