"""When a song was played, written the way the desktop writes times.

`config.toml` can override both the clock format and the time zone; the
settings window deliberately doesn't, because getting this wrong is rare and
the desktop is right the rest of the time.
"""

from __future__ import annotations

from PySide6.QtCore import QDateTime, QLocale, QTimeZone

_format = "system"
_zone = ""


def configure(time_format: str, time_zone: str) -> None:
    global _format, _zone
    _format = time_format
    _zone = time_zone


def when(locale: QLocale, started_at: int) -> str:
    """The time of day ``started_at``, in the configured zone and format."""
    stamp = QDateTime.fromSecsSinceEpoch(started_at)
    if _zone:
        zone = QTimeZone(_zone.encode())
        if zone.isValid():
            stamp = stamp.toTimeZone(zone)
    if _format == "12h":
        return locale.toString(stamp.time(), "h:mm AP")
    if _format == "24h":
        return locale.toString(stamp.time(), "HH:mm")
    return locale.toString(stamp.time(), QLocale.FormatType.ShortFormat)


def date(locale: QLocale, started_at: int) -> QDateTime:
    """The moment itself in the configured zone, for day headings."""
    stamp = QDateTime.fromSecsSinceEpoch(started_at)
    if _zone:
        zone = QTimeZone(_zone.encode())
        if zone.isValid():
            stamp = stamp.toTimeZone(zone)
    return stamp
