"""The Discord and Last.fm states behind the tray lines and the Status window."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from refrain.config import LastfmConfig  # noqa: E402
from refrain.discord_rpc import RPCState  # noqa: E402
from refrain.service_status import (  # noqa: E402
    DiscordStatus,
    LastfmStatus,
    ServiceStatus,
    StatusSnapshot,
    discord_status,
    lastfm_status,
)
from refrain.sources.base import PlaybackStatus, TrackInfo  # noqa: E402
from refrain.startup_check import INVALID, OK, UNREACHABLE, CheckResult  # noqa: E402

PLAYING = TrackInfo(source="mpris", title="Glass Tides", status=PlaybackStatus.PLAYING)
PAUSED = TrackInfo(source="mpris", title="Glass Tides", status=PlaybackStatus.PAUSED)
BLANK = TrackInfo(source="mpris", title="  ", status=PlaybackStatus.PLAYING)
NOTHING = TrackInfo.empty()


@pytest.mark.parametrize(
    ("rpc", "privacy", "track", "expected"),
    [
        (RPCState.SHOWING, "off", PLAYING, DiscordStatus.PRIVACY_OFF),
        (RPCState.DISABLED, "full", PLAYING, DiscordStatus.NOT_SET_UP),
        (RPCState.REJECTED, "full", PLAYING, DiscordStatus.REJECTED),
        (RPCState.ERROR, "full", PLAYING, DiscordStatus.ERROR),
        (RPCState.NO_CLIENT, "full", NOTHING, DiscordStatus.NO_CLIENT),
        (RPCState.SHOWING, "full", PLAYING, DiscordStatus.SHOWING),
        (RPCState.SHOWING, "minimal", PLAYING, DiscordStatus.SHOWING_MINIMAL),
        (RPCState.CONNECTED_IDLE, "full", PLAYING, DiscordStatus.READY),
        (RPCState.SHOWING, "full", BLANK, DiscordStatus.READY),
        (RPCState.CONNECTED_IDLE, "full", PAUSED, DiscordStatus.PAUSED),
        (RPCState.CONNECTED_IDLE, "full", NOTHING, DiscordStatus.READY),
    ],
)
def test_discord_status(rpc, privacy, track, expected):
    assert discord_status(rpc, privacy, track) is expected


def _lastfm(**kw) -> LastfmConfig:
    base = {
        "enabled": True,
        "api_key": "k",
        "shared_secret": "s",
        "session_key": "sk",
        "username": "refrain_demo",
    }
    return LastfmConfig(**(base | kw))


@pytest.mark.parametrize(
    ("cfg", "privacy", "invalid", "waiting", "expected"),
    [
        (_lastfm(enabled=False, session_key=""), "full", False, 0, (LastfmStatus.OFF, "")),
        (_lastfm(enabled=False), "full", False, 0, (LastfmStatus.CONNECTED_OFF, "")),
        (_lastfm(), "off", False, 0, (LastfmStatus.PAUSED, "")),
        (_lastfm(session_key=""), "full", False, 0, (LastfmStatus.NOT_CONNECTED, "")),
        (_lastfm(), "full", True, 2, (LastfmStatus.EXPIRED, "")),
        (_lastfm(), "full", False, 2, (LastfmStatus.WAITING, "2")),
        (_lastfm(), "minimal", False, 0, (LastfmStatus.SCROBBLING, "refrain_demo")),
    ],
)
def test_lastfm_status(cfg, privacy, invalid, waiting, expected):
    assert lastfm_status(cfg, privacy, invalid, waiting) == expected


def test_needs_attention_names_only_what_the_user_must_fix():
    assert StatusSnapshot(DiscordStatus.READY).needs_attention == frozenset()
    assert StatusSnapshot(DiscordStatus.NO_CLIENT).needs_attention == frozenset()
    snap = StatusSnapshot(DiscordStatus.REJECTED, "", LastfmStatus.EXPIRED)
    assert snap.needs_attention == {"discord:rejected", "lastfm:expired"}
    assert StatusSnapshot(DiscordStatus.NOT_SET_UP).needs_attention == {"discord:not_set_up"}


@pytest.fixture
def hub():
    hub = ServiceStatus()
    hub.seen = []
    hub.changed.connect(hub.seen.append)
    return hub


def test_hub_publishes_only_changes(hub):
    hub.set_discord("ready", "")
    hub.set_discord("ready", "")
    hub.set_lastfm("scrobbling", "refrain_demo")
    assert [(s.discord, s.lastfm) for s in hub.seen] == [
        ("ready", "off"),
        ("ready", "scrobbling"),
    ]
    assert hub.snapshot is hub.seen[-1]


def test_a_rejected_session_at_startup_shows_as_expired_until_new_credentials(hub):
    hub.set_lastfm("scrobbling", "refrain_demo")
    hub.set_startup_check(CheckResult(INVALID, "Invalid session key"), CheckResult(OK))
    assert hub.snapshot.lastfm is LastfmStatus.EXPIRED
    assert hub.snapshot.lastfm_detail == ""
    hub.forget_lastfm_check()
    assert hub.snapshot.lastfm is LastfmStatus.SCROBBLING


def test_an_unreachable_check_leaves_the_live_state(hub):
    hub.set_lastfm("waiting", "4")
    hub.set_startup_check(CheckResult(UNREACHABLE, "timed out"))
    assert (hub.snapshot.lastfm, hub.snapshot.lastfm_detail) == (LastfmStatus.WAITING, "4")


def test_the_startup_verdict_does_not_override_lastfm_being_off(hub):
    hub.set_startup_check(CheckResult(INVALID))
    hub.set_lastfm("off", "")
    assert hub.snapshot.lastfm is LastfmStatus.OFF
