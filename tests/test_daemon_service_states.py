"""What the daemon tells the tray and the Status window about Discord and Last.fm."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.discord_rpc import RPCState  # noqa: E402
from tests.daemon_fakes import FakeRPC, Player, install, make_config  # noqa: E402

LASTFM = {
    "enabled": True,
    "api_key": "k" * 32,
    "shared_secret": "s" * 32,
    "session_key": "session",
    "username": "refrain_demo",
}


@pytest.fixture
def rig(monkeypatch):
    def build(**sections):
        clock, _ = install(monkeypatch)
        worker = DaemonWorker(make_config(**sections))
        discord, lastfm = [], []
        worker.discordStateChanged.connect(lambda *a: discord.append(a))
        worker.lastfmStateChanged.connect(lambda *a: lastfm.append(a))
        return worker, Player(worker, clock), discord, lastfm

    return build


def rpc() -> FakeRPC:
    return FakeRPC.instances[-1]


def test_nothing_playing_is_ready_not_disconnected(rig):
    worker, player, discord, _ = rig()
    player.tick(n=3)
    assert discord == [("ready", "")]
    assert rpc().ensure_calls == 3


def test_a_playing_song_is_showing_and_a_pause_says_so(rig):
    worker, player, discord, _ = rig(behavior={"cover_art": False})
    player.play()
    player.tick(n=2)
    player.pause()
    player.tick(n=2)
    assert discord == [("showing", ""), ("paused", "")]


def test_minimal_privacy_shows_only_listening_to_music(rig):
    worker, player, discord, _ = rig(privacy={"mode": "minimal"})
    player.play()
    player.tick()
    assert discord == [("showing_minimal", "")]


def test_a_song_waiting_for_its_cover_is_ready_until_it_shows(rig):
    worker, player, discord, _ = rig()
    player.play()
    player.tick(n=5)
    assert discord == [("ready", ""), ("showing", "")]


def test_privacy_off_wins_over_everything(rig):
    worker, player, discord, lastfm = rig(privacy={"mode": "off"}, lastfm=LASTFM)
    player.play()
    player.tick()
    assert discord == [("privacy_off", "")]
    assert lastfm == [("paused", "")]


def test_no_application_id_is_not_set_up(rig):
    worker, player, discord, _ = rig(discord={"client_id": ""})
    player.tick()
    assert discord == [("not_set_up", "")]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (RPCState.REJECTED, "rejected"),
        (RPCState.NO_CLIENT, "no_client"),
        (RPCState.ERROR, "error"),
    ],
)
def test_rpc_problems_pass_through_with_their_reason(rig, state, expected):
    worker, player, discord, _ = rig()
    rpc().forced_state = state
    rpc().detail = "Invalid Client ID"
    player.play()
    player.tick(n=2)
    assert discord == [(expected, "Invalid Client ID")]


def test_the_state_is_signalled_only_on_change(rig):
    worker, player, discord, lastfm = rig(behavior={"cover_art": False})
    player.play()
    player.tick(n=5)
    rpc().connected = False
    player.tick(n=5)
    assert discord == [("showing", ""), ("no_client", "")]
    assert lastfm == [("off", "")]


def test_the_rate_limited_status_is_pumped_every_tick(rig):
    worker, player, _, _ = rig()
    player.tick(n=4)
    player.play()
    player.tick(n=4)
    assert rpc().pumps == 8


@pytest.mark.parametrize(
    ("invalid", "waiting", "expected"),
    [
        (False, 0, ("scrobbling", "refrain_demo")),
        (False, 3, ("waiting", "3")),
        (True, 3, ("expired", "")),
    ],
)
def test_lastfm_state_follows_the_scrobbler(rig, invalid, waiting, expected):
    worker, player, _, lastfm = rig(lastfm=LASTFM)
    worker._scrobbler.invalid = invalid
    worker._scrobbler.waiting = waiting
    player.tick()
    assert lastfm == [expected]


def test_lastfm_switched_off_but_still_connected(rig):
    worker, player, _, lastfm = rig(lastfm=LASTFM | {"enabled": False})
    player.tick()
    assert lastfm == [("connected_off", "")]


def test_lastfm_on_without_a_session_is_not_connected(rig):
    worker, player, _, lastfm = rig(lastfm=LASTFM | {"session_key": ""})
    player.tick()
    assert lastfm == [("not_connected", "")]
