"""The clock follows the desktop unless config.toml says otherwise."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime, QLocale, QTimeZone  # noqa: E402

from refrain.ui import clock  # noqa: E402

# 2026-09-20 18:05:00 UTC
STAMP = 1_789_927_500  # 2026-09-20 18:05:00 UTC


@pytest.fixture(autouse=True)
def _back_to_system():
    yield
    clock.configure("system", "")


def test_the_system_format_is_the_default():
    clock.configure("system", "UTC")
    german = QLocale("de_DE")
    assert clock.when(german, STAMP) == german.toString(
        QDateTime.fromSecsSinceEpoch(STAMP, QTimeZone(b"UTC")).time(),
        QLocale.FormatType.ShortFormat,
    )


def test_twelve_and_twentyfour_hours_are_a_config_choice():
    clock.configure("24h", "UTC")
    assert clock.when(QLocale("en_US"), STAMP) == "18:05"
    clock.configure("12h", "UTC")
    assert clock.when(QLocale("en_US"), STAMP) == "6:05 PM"


def test_the_zone_moves_the_clock():
    clock.configure("24h", "Europe/Berlin")
    assert clock.when(QLocale("de_DE"), STAMP) == "20:05"
    clock.configure("24h", "Asia/Tokyo")
    assert clock.when(QLocale("de_DE"), STAMP) == "03:05"


def test_an_unknown_zone_falls_back_to_the_desktop():
    clock.configure("24h", "Nowhere/City")
    shown = clock.when(QLocale("de_DE"), STAMP)
    assert shown == QLocale("de_DE").toString(QDateTime.fromSecsSinceEpoch(STAMP).time(), "HH:mm")


def test_the_day_heading_uses_the_same_zone():
    clock.configure("system", "Asia/Tokyo")
    assert clock.date(QLocale("de_DE"), STAMP).date().day() == 21
