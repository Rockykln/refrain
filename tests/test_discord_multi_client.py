"""Publishing the status to more than one Discord client.

Discord and Vencord/Vesktop are separate programs with separate IPC
sockets. A status sent to one is invisible in the other, so running both
meant the status only ever showed up in whichever client Refrain happened
to reach first.

With ``discord.all_clients`` on, every live client gets the same payload,
and each connection is judged on its own — closing one client mid-song
must not drop the status from the rest.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from refrain.discord_rpc import DiscordRPC


@pytest.fixture
def rpc_factory(monkeypatch):
    """Build a DiscordRPC whose connections are mocks, one per live pipe."""

    def build(live_pipes, all_clients, fail_on=()):
        monkeypatch.setattr("refrain.discord_rpc._scan_ipc_pipes", lambda: (list(live_pipes), []))
        monkeypatch.setattr("refrain.discord_rpc._bridge_sandboxed_ipc_socket", lambda: None)
        made = {}

        def fake_presence(client_id, pipe=None):
            m = MagicMock(name=f"presence-{pipe}")
            if pipe in fail_on:
                m.connect.side_effect = ConnectionRefusedError("nope")
            made[pipe] = m
            return m

        monkeypatch.setattr("refrain.discord_rpc.Presence", fake_presence)
        return DiscordRPC("123456789", all_clients=all_clients), made

    return build


def test_off_by_default_only_one_connection(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=False)

    assert rpc._ensure_connected() is True
    assert len(rpc._presences) == 1, "one client should be served by default"
    assert list(rpc._presences) == [0], "the lowest live slot wins"


def test_all_clients_connects_to_every_live_socket(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=True)

    assert rpc._ensure_connected() is True
    assert sorted(rpc._presences) == [0, 2]


def test_update_reaches_every_client(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=True)
    rpc._ensure_connected()

    rpc.update(details="Song", state="Artist")

    for pipe in (0, 2):
        assert made[pipe].update.called, f"discord-ipc-{pipe} got no update"


def test_one_dead_client_does_not_silence_the_others(rpc_factory):
    """Closing one client mid-song must not take the status down."""
    rpc, made = rpc_factory([0, 2], all_clients=True)
    rpc._ensure_connected()
    made[0].update.side_effect = BrokenPipeError("client closed")

    rpc.update(details="Song", state="Artist")

    assert 0 not in rpc._presences, "the dead connection should be dropped"
    assert 2 in rpc._presences, "the healthy connection must survive"
    assert rpc.is_connected() is True
    assert made[2].update.called


def test_dedup_still_applies_across_clients(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=True)
    rpc._ensure_connected()

    rpc.update(details="Song")
    rpc.update(details="Song")

    for pipe in (0, 2):
        assert made[pipe].update.call_count == 1, "identical payload resent"


def test_a_client_that_refuses_is_skipped_not_fatal(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=True, fail_on=(0,))

    assert rpc._ensure_connected() is True
    assert sorted(rpc._presences) == [2]


def test_clear_reaches_every_client(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=True)
    rpc._ensure_connected()

    rpc.clear()

    for pipe in (0, 2):
        assert made[pipe].clear.called


def test_close_shuts_every_client_down(rpc_factory):
    rpc, made = rpc_factory([0, 2], all_clients=True)
    rpc._ensure_connected()

    rpc.close()

    assert rpc._presences == {}
    assert rpc.is_connected() is False
    for pipe in (0, 2):
        assert made[pipe].close.called


def test_a_client_started_later_is_picked_up(rpc_factory, monkeypatch):
    """Vencord opened after Refrain should still get the status."""
    rpc, made = rpc_factory([0], all_clients=True)
    rpc._ensure_connected()
    assert sorted(rpc._presences) == [0]

    monkeypatch.setattr("refrain.discord_rpc._scan_ipc_pipes", lambda: ([0, 2], []))
    rpc._next_retry_ts = 0.0  # the retry window has passed

    rpc._ensure_connected()

    assert sorted(rpc._presences) == [0, 2]


def test_single_client_mode_does_not_rescan_once_connected(rpc_factory, monkeypatch):
    """The default path must stay as cheap as it was."""
    rpc, made = rpc_factory([0], all_clients=False)
    rpc._ensure_connected()

    calls = []
    monkeypatch.setattr(
        "refrain.discord_rpc._scan_ipc_pipes", lambda: (calls.append(1), ([0], []))[1]
    )
    rpc._ensure_connected()

    assert calls == [], "connected single-client mode should not sweep sockets"


def test_backoff_window_does_not_freeze_an_established_status(rpc_factory):
    """A pending retry must not stop updates to clients already served.

    With all_clients on, _ensure_connected keeps looking for newcomers,
    so it no longer returns early when connected. The retry gate then has
    to answer "yes, we are connected" rather than "no" — otherwise every
    update() during the backoff window bailed out and the status froze on
    whatever was playing when the last client failed to appear.
    """
    import time

    rpc, made = rpc_factory([0], all_clients=True)
    rpc._ensure_connected()
    assert sorted(rpc._presences) == [0]

    rpc._next_retry_ts = time.monotonic() + 60  # a newcomer was not ready

    assert rpc._ensure_connected() is True
    rpc.update(details="Next song")
    assert made[0].update.called, "status stopped while the retry window was open"


def test_watching_for_newcomers_does_not_sweep_every_tick(rpc_factory, monkeypatch):
    """The daemon ticks twice a second; the sweep must not ride it.

    Each sweep is one connect() per occupied slot. Without its own
    cadence, all_clients turned "keep an eye out for a second client"
    into ten socket connects, twice a second, forever.
    """
    rpc, made = rpc_factory([0], all_clients=True)
    rpc._ensure_connected()

    calls = []
    monkeypatch.setattr(
        "refrain.discord_rpc._scan_ipc_pipes", lambda: (calls.append(1), ([0], []))[1]
    )
    for i in range(20):  # 20 ticks == 10 s at the default poll interval
        rpc.update(details=f"Song {i}")

    assert len(calls) <= 2, f"swept the sockets {len(calls)} times in 20 ticks"
    assert made[0].update.call_count == 20, "updates must still go out"


def test_the_multi_client_notice_is_logged_once_not_every_sweep(rpc_factory, caplog):
    """The sweep runs every few seconds; an unchanged set has no news.

    ``_last_live_pipes`` was recorded but never consulted, so a user with
    Discord and Vesktop both open got the same INFO line every five
    seconds for the whole session — the live log was unreadable.
    """
    import logging

    rpc, _made = rpc_factory([0, 2], all_clients=True)
    caplog.set_level(logging.INFO, logger="refrain.discord_rpc")

    def notices():
        return [r for r in caplog.records if "clients listening" in r.getMessage()]

    assert rpc._ensure_connected() is True
    assert len(notices()) == 1

    # Force the next sweep to run and find exactly the same clients.
    rpc._next_retry_ts = 0.0
    assert rpc._ensure_connected() is True
    assert len(notices()) == 1, "an unchanged client set must not log again"


def test_a_changed_client_set_is_worth_saying(rpc_factory, monkeypatch, caplog):
    import logging

    rpc, _made = rpc_factory([0, 2], all_clients=True)
    caplog.set_level(logging.INFO, logger="refrain.discord_rpc")
    assert rpc._ensure_connected() is True

    # A third client shows up.
    monkeypatch.setattr("refrain.discord_rpc._scan_ipc_pipes", lambda: ([0, 2, 3], []))
    rpc._next_retry_ts = 0.0
    assert rpc._ensure_connected() is True

    notices = [r for r in caplog.records if "clients listening" in r.getMessage()]
    assert len(notices) == 2
    assert "3 clients" in notices[-1].getMessage()
