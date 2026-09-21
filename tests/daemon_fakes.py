"""Stand-ins for everything DaemonWorker talks to, and a player that drives them."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from refrain import daemon  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.history import HistorySnapshot  # noqa: E402
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402

CLIENT_ID = "123456789012345678"
CLIENT_ID_MPRIS = "123450000000000001"
CLIENT_ID_BT = "123450000000000002"


class Clock:
    """Replaces the ``time`` module inside refrain.daemon."""

    def __init__(self, mono: float = 1000.0, wall: float = 1_700_000_000.0):
        self.mono = mono
        self.wall = wall

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds


class FakeRPC:
    instances: list[FakeRPC] = []

    def __init__(self, client_id: str, all_clients: bool = False):
        self.client_id = client_id
        self.all_clients = all_clients
        self.calls: list[tuple[str, dict]] = []
        self.connected = True
        self.closed = False
        self.ensure_calls = 0
        self.pumps = 0
        self.forced_state = None
        self.detail = ""
        FakeRPC.instances.append(self)

    def update(self, **payload) -> None:
        self.calls.append(("update", payload))

    def clear(self, force: bool = False) -> None:
        self.calls.append(("clear", {"force": force} if force else {}))

    def close(self) -> None:
        self.closed = True

    def is_connected(self) -> bool:
        return self.connected

    def _ensure_connected(self) -> None:
        self.ensure_calls += 1

    def pump(self) -> None:
        self.pumps += 1

    @property
    def state(self):
        from refrain.discord_rpc import RPCState

        if self.forced_state is not None:
            return self.forced_state
        if not self.client_id:
            return RPCState.DISABLED
        if not self.connected:
            return RPCState.NO_CLIENT
        if self.calls and self.calls[-1][0] == "update":
            return RPCState.SHOWING
        return RPCState.CONNECTED_IDLE

    @property
    def updates(self) -> list[dict]:
        return [p for kind, p in self.calls if kind == "update"]

    @property
    def last(self) -> tuple[str, dict]:
        return self.calls[-1]


class FakeSource:
    def __init__(self, *args):
        self.track = TrackInfo.empty()
        self.reads = 0
        self.controls: list[str] = []
        self.accepts = True
        self.hints = None
        self.device = None

    def read(self) -> TrackInfo:
        self.reads += 1
        return self.track

    def set_device(self, mac: str) -> None:
        self.device = mac

    def set_browser_hints(self, hints: list[str]) -> None:
        self.hints = hints

    def _control(self, name: str) -> bool:
        self.controls.append(name)
        return self.accepts

    def play_pause(self) -> bool:
        return self._control("play_pause")

    def play(self) -> bool:
        return self._control("play")

    def pause(self) -> bool:
        return self._control("pause")

    def next(self) -> bool:
        return self._control("next")

    def previous(self) -> bool:
        return self._control("previous")


class FakeCovers:
    def __init__(self, *args, **kwargs):
        self.urls: dict[tuple[str, str], str] = {}
        self.durations: dict[tuple[str, str], int] = {}
        self.song_urls: dict[tuple[str, str], str] = {}
        self.albums: dict[tuple[str, str], str] = {}
        self.local: dict[tuple[str, str], Path] = {}
        self.requested: list[tuple[str, str]] = []
        self.kept: list[list[str]] = []
        self.temp_kept: list[str] = []  # the title whose temporary image was spared
        self.shut = False

    def get(self, artist: str, title: str, album: str = "") -> str | None:
        self.requested.append((artist, title))
        return self.urls.get((artist, title))

    def get_duration_ms(self, artist: str, title: str, album: str = "") -> int:
        return self.durations.get((artist, title), 0)

    def get_song_url(self, artist: str, title: str, album: str = "") -> str | None:
        return self.song_urls.get((artist, title))

    def get_album(self, artist: str, title: str, album: str = "") -> str:
        return self.albums.get((artist, title), "")

    def get_local_path(self, artist: str, title: str, album: str = "") -> Path | None:
        return self.local.get((artist, title))

    def keep_covers(self, urls) -> None:
        self.kept.append(list(urls))

    def drop_temp_covers(self, artist: str = "", title: str = "", album: str = "") -> None:
        self.temp_kept.append(title)

    def shutdown(self) -> None:
        self.shut = True


class FakeMprisServer:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.updates: list[tuple[TrackInfo, str | None, int]] = []
        self.started = False
        self.stopped = False

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.stopped = True

    def update(self, track, cover_url, effective_duration_ms=None) -> None:
        self.updates.append((track, cover_url, effective_duration_ms))


class FakeScrobbler:
    looks_up_lengths = False

    def __init__(self, cfg, on_queued=None, **kwargs):
        self.cfg = cfg
        self.on_queued = on_queued
        self.calls: list[dict] = []
        self.reconfigured: list = []
        self.shut = False
        self.invalid = False
        self.waiting = 0

    def health(self):
        return self.invalid, self.waiting

    def update(self, track, effective_duration_ms, privacy_off, **kwargs) -> None:
        self.calls.append(
            {"track": track, "duration_ms": effective_duration_ms, "privacy_off": privacy_off}
            | kwargs
        )

    def reconfigure(self, cfg) -> None:
        self.reconfigured.append(cfg)

    def shutdown(self) -> None:
        self.shut = True


class FakeHistory:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls: list[dict] = []
        self.changes = False
        self.reconfigure_result = False
        self.scrobbled: list[tuple[str, str]] = []
        self.removed: list[tuple] = []
        self.cleared = False
        self.shut = False

    def update(self, track, duration_ms, **kwargs) -> bool:
        self.calls.append({"track": track, "duration_ms": duration_ms} | kwargs)
        return self.changes

    def reconfigure(self, cfg) -> bool:
        self.cfg = cfg
        return self.reconfigure_result

    def mark_scrobbled(self, artist: str, title: str) -> bool:
        self.scrobbled.append((artist, title))
        return True

    def remove(self, started_at, title, artist) -> bool:
        self.removed.append((started_at, title, artist))
        return True

    def clear(self) -> bool:
        self.cleared = True
        return True

    def shutdown(self) -> None:
        self.shut = True

    def snapshot(self) -> HistorySnapshot:
        return HistorySnapshot(enabled=self.cfg.enabled, limit=self.cfg.max_entries)


class Notifier:
    """Replaces ``subprocess`` inside refrain.daemon; records every notify-send argv."""

    DEVNULL = -3

    def __init__(self):
        self.popen: list[list[str]] = []
        self.run_calls: list[list[str]] = []
        self.next_id = "4242\n"

    def Popen(self, argv, **kwargs):  # noqa: N802
        self.popen.append(argv)

    def run(self, argv, **kwargs):
        self.run_calls.append(argv)
        return SimpleNamespace(stdout=self.next_id, returncode=0)


def install(monkeypatch, notify_bin: str | None = None) -> tuple[Clock, Notifier]:
    """Swap every collaborator in refrain.daemon for a fake."""
    FakeRPC.instances = []
    clock = Clock()
    notifier = Notifier()
    monkeypatch.setattr(daemon, "time", clock)
    monkeypatch.setattr(daemon, "DiscordRPC", FakeRPC)
    monkeypatch.setattr(daemon, "MPRISSource", FakeSource)
    monkeypatch.setattr(daemon, "BluetoothSource", FakeSource)
    monkeypatch.setattr(daemon, "CoverFetcher", FakeCovers)
    monkeypatch.setattr(daemon, "MPRISServer", FakeMprisServer)
    monkeypatch.setattr(daemon, "Scrobbler", FakeScrobbler)
    monkeypatch.setattr(daemon, "PlayHistory", FakeHistory)
    monkeypatch.setattr(daemon, "subprocess", notifier)
    monkeypatch.setattr(daemon, "shutil", SimpleNamespace(which=lambda name: notify_bin))
    monkeypatch.setattr(daemon, "_NOTIFY_BIN", notify_bin)
    return clock, notifier


def make_config(**sections) -> Config:
    config = Config()
    config.discord.client_id = CLIENT_ID
    for section, values in sections.items():
        for key, value in values.items():
            setattr(getattr(config, section), key, value)
    return config


def song(
    title="Paper Satellites",
    artist="Mara Keel",
    album="Tidal",
    *,
    source="mpris",
    status=PlaybackStatus.PLAYING,
    position_ms=0,
    duration_ms=200_000,
    url="",
    loop_track=False,
) -> TrackInfo:
    return TrackInfo(
        source=source,
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        position_ms=position_ms,
        status=status,
        url=url,
        loop_track=loop_track,
    )


class Player:
    """Drives a fake source like a real player: the position follows the clock while playing."""

    def __init__(self, worker, clock: Clock, source: str = "mpris"):
        self.worker = worker
        self.clock = clock
        self.src = worker._mpris if source == "mpris" else worker._bluetooth
        self.source = source
        # A dangling tab keeps saying "playing" with a position that no longer moves.
        self.frozen = False

    def play(self, position_ms: int = 0, **fields) -> None:
        self.src.track = song(source=self.source, position_ms=position_ms, **fields)

    def pause(self) -> None:
        self.src.track = _replace(self.src.track, status=PlaybackStatus.PAUSED)

    def resume(self) -> None:
        self.src.track = _replace(self.src.track, status=PlaybackStatus.PLAYING)

    def stop(self) -> None:
        self.src.track = TrackInfo.empty()

    def tick(self, seconds: float = 0.5, n: int = 1) -> None:
        for _ in range(n):
            self.clock.advance(seconds)
            t = self.src.track
            if t.status == PlaybackStatus.PLAYING and t.has_track and not self.frozen:
                self.src.track = _replace(t, position_ms=t.position_ms + int(seconds * 1000))
            self.worker._tick()


def _replace(track: TrackInfo, **changes) -> TrackInfo:
    return dataclasses.replace(track, **changes)
