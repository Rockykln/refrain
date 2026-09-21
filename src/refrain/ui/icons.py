"""Icon-name resolution with a freedesktop fallback chain.

Several KDE (Breeze) icon names Refrain uses have no equivalent in the
freedesktop icon naming spec, so they stay empty outside Plasma (e.g. GNOME,
Cinnamon on Linux Mint): "configure", "view-list-text", "tools-report-bug",
"chronometer", "view-media-track", "view-media-artist", "edit-clear-history",
"network-disconnect", "network-connect". `themed_icon` tries the requested
name first, so Plasma users keep today's icons, then walks a list of
standard names, then a bundled glyph for the one name with no good standard
equivalent.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon

from refrain.paths import assets_dir

# KDE-only name -> freedesktop icon-naming-spec names to try next.
_FALLBACKS: dict[str, tuple[str, ...]] = {
    "configure": ("preferences-system",),
    "view-list-text": ("view-list", "format-justify-fill"),
    "tools-report-bug": ("dialog-warning",),
    "chronometer": ("appointment-soon",),
    "view-media-track": ("audio-x-generic",),
    "view-media-artist": ("avatar-default",),
    "edit-clear-history": ("edit-clear",),
    "network-disconnect": ("network-offline",),
    "network-connect": ("network-transmit-receive", "network-idle"),
}

# Last resort for names with no widely available standard equivalent.
_BUNDLED: dict[str, str] = {
    "chronometer": "chronometer.svg",
}


def themed_icon(name: str, *extra_fallbacks: str) -> QIcon:
    """Resolve `name` through its fallback chain.

    `extra_fallbacks` lets a call site pass its own ordered alternatives
    (e.g. history entries picking between source-specific names) in
    addition to the built-in KDE-name table.
    """
    for candidate in (name, *extra_fallbacks, *_FALLBACKS.get(name, ())):
        icon = QIcon.fromTheme(candidate)
        if not icon.isNull():
            return icon
    bundled = _BUNDLED.get(name)
    if bundled:
        return QIcon(str(assets_dir() / "icons" / bundled))
    return QIcon()
