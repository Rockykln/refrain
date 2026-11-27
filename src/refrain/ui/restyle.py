"""Make stylesheets read the palette again after a theme change."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


def repolish(root: QWidget) -> None:
    """Re-apply every stylesheet under ``root``, ``root`` included.

    Qt resolves ``palette(role)`` in a stylesheet once, while the widget
    is polished, and writes the colour it found at that moment. A later
    palette change repaints everything else but leaves that colour
    standing, so a label styled ``color: palette(text)`` keeps the shade
    it was born with until it is polished again.
    """
    for widget in (root, *root.findChildren(QWidget)):
        if widget.styleSheet():
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
