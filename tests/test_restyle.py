"""A stylesheet colour taken from the palette has to move with the palette."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from refrain.ui.restyle import repolish  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def paint(app):
    """Set the application's text colour; always restored."""
    before = QPalette(app.palette())

    def _set(colour: str) -> None:
        changed = QPalette(app.palette())
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            changed.setColor(group, QPalette.ColorRole.Text, QColor(colour))
            changed.setColor(group, QPalette.ColorRole.WindowText, QColor(colour))
        app.setPalette(changed)
        app.processEvents()

    yield _set
    app.setPalette(before)
    app.processEvents()


def _darkest(widget) -> str:
    image = widget.grab().toImage()
    best, score = "#ffffff", 10**6
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            value = colour.red() + colour.green() + colour.blue()
            if value < score:
                best, score = colour.name(), value
    return best


def test_a_palette_colour_in_a_stylesheet_is_stale_until_repolished(app, paint):
    """Qt writes the colour once, while polishing, and never revisits it."""
    paint("#ff0000")
    label = QLabel("XXXXXXXX")
    label.setStyleSheet("color: palette(text); background: #ffffff;")
    label.resize(200, 50)
    label.show()
    app.processEvents()
    try:
        assert _darkest(label) == "#ff0000"
        paint("#00aa00")
        assert _darkest(label) == "#ff0000", "Qt started following the palette on its own"
        repolish(label)
        app.processEvents()
        assert _darkest(label) == "#00aa00"
    finally:
        label.close()
        label.deleteLater()


def test_repolish_reaches_the_children_too(app, paint):
    paint("#ff0000")
    box = QWidget()
    label = QLabel("XXXXXXXX", box)
    label.setStyleSheet("color: palette(text); background: #ffffff;")
    label.resize(200, 50)
    box.resize(220, 70)
    box.show()
    app.processEvents()
    try:
        paint("#00aa00")
        repolish(box)
        app.processEvents()
        assert _darkest(label) == "#00aa00"
    finally:
        box.close()
        box.deleteLater()


def test_a_widget_without_a_stylesheet_is_left_alone(app):
    plain = QLabel("nothing to restyle")
    try:
        repolish(plain)
        assert plain.styleSheet() == ""
    finally:
        plain.deleteLater()


def test_the_settings_window_repolishes_on_a_palette_change(app, paint):
    from refrain.config import Config
    from refrain.ui.settings_window import SettingsWindow

    paint("#ff0000")
    config = Config()
    config.discord.client_id = "1234567890123456789"
    window = SettingsWindow(config)
    try:
        hint = next(
            lbl
            for lbl in window.findChildren(QLabel)
            if "palette(text)" in lbl.styleSheet() and lbl.text()
        )
        hint.resize(400, 40)
        hint.show()
        app.processEvents()
        before = _darkest(hint)
        paint("#00aa00")
        window.changeEvent(QEvent(QEvent.Type.PaletteChange))
        app.processEvents()
        assert _darkest(hint) != before
    finally:
        window.close()
        window.deleteLater()
