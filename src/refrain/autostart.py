"""Toggle XDG autostart entry for Refrain."""

from __future__ import annotations

import logging

from refrain.paths import autostart_path

log = logging.getLogger(__name__)

_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Refrain
GenericName=Apple Music Discord RPC
Comment=Discord Rich Presence for Apple Music
Exec=refrain --silent
Icon=refrain
Terminal=false
Categories=Audio;Music;Network;
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""


def is_enabled() -> bool:
    return autostart_path().exists()


def enable() -> None:
    p = autostart_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_DESKTOP_ENTRY, encoding="utf-8")
    log.info("Autostart enabled at %s", p)


def disable() -> None:
    p = autostart_path()
    if p.exists():
        p.unlink()
        log.info("Autostart disabled (removed %s)", p)
