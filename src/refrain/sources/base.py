"""Common types shared by all playback sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlaybackStatus(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class TrackInfo:
    source: str  # "mpris" | "bluetooth" | "none"
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    position_ms: int = 0
    status: PlaybackStatus = PlaybackStatus.STOPPED
    url: str = ""
    # Who is playing it, when the source says: the browser's MPRIS
    # Identity ("Chromium"), or the Bluetooth device's name. Display
    # only — deliberately not part of `fingerprint()`, so a browser
    # that renames its player mid-song isn't a track change.
    player: str = ""
    # The player repeats this one track (AVRCP "singletrack"). A phone may
    # then count its position on across loops — see resolve_position.
    loop_track: bool = False

    @classmethod
    def empty(cls) -> TrackInfo:
        return cls(source="none")

    @property
    def has_track(self) -> bool:
        return bool(self.title)

    def content_key(self) -> str:
        return content_key(self.source, self.title, self.artist, self.album)

    def fingerprint(self) -> str:
        return f"{self.content_key()}|{self.status.value}"


# Control characters and the bidi overrides, which no song title needs. A
# newline in a title forges a second line in the log; an override reverses
# what a reader sees without changing the text. Taken out once here, so the
# log, Discord, the notification and the history all get the same string.
_STRIP = {
    *range(0x00, 0x20),
    0x7F,
    *range(0x80, 0xA0),
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}


def clean_field(text: str) -> str:
    """Metadata from a player, with anything unprintable taken out."""
    return text.translate(dict.fromkeys(_STRIP)) if text else text


def content_key(source: str, title: str, artist: str, album: str) -> str:
    """Identify a song by its metadata, as one string.

    The parts are joined with "|" and the album is cut off again by
    timing._only_album_differs, so a pipe inside a title or album would move
    that boundary and let two different songs share a key.
    """
    return "|".join(_escape(part) for part in (source, title, artist, album))


def _escape(part: str) -> str:
    return part.replace("%", "%25").replace("|", "%7C")
