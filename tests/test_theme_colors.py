"""Every log level stays readable on light and dark themes."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QPalette  # noqa: E402

from refrain.ui.log_window import level_colors  # noqa: E402

THEMES = {
    "breeze-light": ("#ffffff", "#232629"),
    "breeze-dark": ("#141618", "#fcfcfc"),
    "fusion": ("#ffffff", "#000000"),
    "dark": ("#2b2b2b", "#e0e0e0"),
}


def _luminance(color: QColor) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("theme", THEMES)
def test_every_level_meets_the_contrast_minimum(theme):
    base, text = THEMES[theme]
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Base, QColor(base))
    palette.setColor(QPalette.ColorRole.Text, QColor(text))
    colors = level_colors(palette)
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        ratio = _contrast(QColor(colors[level]), QColor(base))
        assert ratio >= 4.5, f"{logging.getLevelName(level)} on {theme}: {ratio:.2f}"


def _inactive_greyed(base: str, text: str) -> QPalette:
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Base, base), (QPalette.ColorRole.Window, base)):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Active, role, QColor(text))
        palette.setColor(QPalette.ColorGroup.Inactive, role, QColor("#babbbb"))
    palette.setCurrentColorGroup(QPalette.ColorGroup.Inactive)
    return palette


def test_a_window_built_before_it_is_focused_keeps_readable_colours():
    """Fusion greys out an unfocused window's text; that must not stick."""
    from refrain.ui.history_window import _muted

    palette = _inactive_greyed("#faf9f8", "#000000")
    assert _contrast(_muted(palette), QColor("#faf9f8")) >= 4.5
    assert level_colors(palette)[logging.INFO] == "#000000"
