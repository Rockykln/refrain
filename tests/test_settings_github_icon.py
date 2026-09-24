"""Settings footer: the GitHub logo is drawn in the theme's text colour."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain.config import Config  # noqa: E402
from refrain.paths import assets_dir  # noqa: E402
from refrain.ui.settings_window import SettingsWindow, themed_svg_icon  # noqa: E402

SVG = (assets_dir() / "icons" / "github-mark.svg").read_bytes()


def _solid_colours(icon) -> set[str]:
    image = icon.pixmap(32, 32).toImage()
    return {
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    }


@pytest.fixture(scope="module")
def win():
    app = QApplication.instance() or QApplication(sys.argv)
    from refrain.sources.bluetooth import BluetoothSource

    orig = BluetoothSource.list_paired_devices
    BluetoothSource.list_paired_devices = staticmethod(lambda: [])
    w = SettingsWindow(Config())
    try:
        yield w
    finally:
        BluetoothSource.list_paired_devices = orig
        w.close()
        w.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("colour", ["#fcfcfc", "#232629"])
def test_the_logo_is_drawn_in_the_colour_asked_for(win, colour):
    # Unthemed, Qt paints the SVG's currentColor black on any theme.
    assert _solid_colours(themed_svg_icon(SVG, QColor(colour), 16)) == {colour}


def test_a_dark_theme_turns_the_logo_light(win):
    dark = QPalette(win.palette())
    dark.setColor(QPalette.ColorRole.Window, QColor("#1b1e20"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#eff0f1"))
    win.setPalette(dark)
    QApplication.processEvents()
    assert _solid_colours(win._github_btn.icon()) == {"#eff0f1"}


def test_the_status_window_logo_follows_the_theme_too(win):
    """The settings footer repainted on a theme change; the Status window did not."""
    from refrain.ui.status_window import StatusWindow

    status = StatusWindow()
    dark = QPalette(status.palette())
    dark.setColor(QPalette.ColorRole.Window, QColor("#1b1e20"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#eff0f1"))
    status.setPalette(dark)
    QApplication.processEvents()
    try:
        assert _solid_colours(status.github_btn.icon()) == {"#eff0f1"}
    finally:
        status.close()
        status.deleteLater()
        QApplication.processEvents()
