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

    @classmethod
    def empty(cls) -> TrackInfo:
        return cls(source="none")

    @property
    def has_track(self) -> bool:
        return bool(self.title)

    def fingerprint(self) -> str:
        return f"{self.source}|{self.title}|{self.artist}|{self.album}|{self.status.value}"
