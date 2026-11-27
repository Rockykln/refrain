"""Icon-name resolution with a freedesktop fallback chain.

Several KDE (Breeze) icon names Refrain uses have no equivalent in the
freedesktop icon naming spec, so they stay empty outside Plasma (e.g. GNOME,
Cinnamon on Linux Mint): "configure", "view-list-text", "tools-report-bug",
"chronometer", "view-media-track", "view-media-artist", "edit-clear-history",
"network-disconnect", "network-connect". `themed_icon` tries the requested
name first, so Plasma users keep today's icons, then walks a list of
standard names, then the "-symbolic" spelling of each, then a bundled glyph
for the one name with no good standard equivalent.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette

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
    "internet-web-browser": ("web-browser",),
    "applications-development": ("applications-engineering", "system-run"),
}

# Last resort for names with no widely available standard equivalent.
_BUNDLED: dict[str, str] = {
    "chronometer": "chronometer.svg",
}


# Sizes a recoloured glyph is baked at; the call sites ask for 16 to 24,
# the larger ones keep it sharp on a scaled screen.
_PIXMAP_SIZES = (16, 20, 22, 24, 32, 48, 64)


# Recolouring costs about a millisecond, and the history asks for one icon
# per row. Theme and colour are part of the key, so a theme or palette
# change starts over instead of handing out stale glyphs.
_RECOLORED: dict[tuple[str, str, int], QIcon] = {}


def _symbolic_icon(name: str) -> QIcon:
    """The "-symbolic" spelling of `name`, painted in the current text colour.

    Adwaita 48 dropped the plain names and Qt does not append the suffix by
    itself. The symbolic files carry a hard-coded dark fill that Qt leaves
    alone, so they would be all but invisible on a dark palette.
    """
    tint = QGuiApplication.palette().color(
        QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText
    )
    key = (QIcon.themeName(), name, tint.rgba())
    cached = _RECOLORED.get(key)
    if cached is not None:
        return cached
    icon = QIcon.fromTheme(f"{name}-symbolic")
    if not icon.isNull():
        icon = _recolored(icon, tint)
    _RECOLORED[key] = icon
    return icon


def _recolored(icon: QIcon, tint: QColor) -> QIcon:
    out = QIcon()
    for size in _PIXMAP_SIZES:
        pixmap = icon.pixmap(QSize(size, size))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), tint)
        painter.end()
        out.addPixmap(pixmap)
    return out


def themed_icon(name: str, *extra_fallbacks: str) -> QIcon:
    """Resolve `name` through its fallback chain.

    `extra_fallbacks` lets a call site pass its own ordered alternatives
    (e.g. history entries picking between source-specific names) in
    addition to the built-in KDE-name table.
    """
    candidates = (name, *extra_fallbacks, *_FALLBACKS.get(name, ()))
    for candidate in candidates:
        icon = QIcon.fromTheme(candidate)
        if not icon.isNull():
            return icon
    # Only once no plain name hit: a theme that has both spellings keeps
    # giving Plasma and Breeze users exactly today's icons.
    for candidate in candidates:
        icon = _symbolic_icon(candidate)
        if not icon.isNull():
            return icon
    bundled = _BUNDLED.get(name)
    if bundled:
        return QIcon(str(assets_dir() / "icons" / bundled))
    return QIcon()
