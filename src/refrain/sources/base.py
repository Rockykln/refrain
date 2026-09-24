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


def content_key(source: str, title: str, artist: str, album: str) -> str:
    """Identify a song by its metadata, as one string.

    The parts are joined with "|" and the album is cut off again by
    timing._only_album_differs, so a pipe inside a title or album would move
    that boundary and let two different songs share a key.
    """
    return "|".join(_escape(part) for part in (source, title, artist, album))


def _escape(part: str) -> str:
    return part.replace("%", "%25").replace("|", "%7C")
