"""The thread that owns the Discord connection, so the daemon never waits on it."""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import refrain.discord_rpc as drpc

CLIENT_ID = "1234567890123456789"


@pytest.fixture
def discord(monkeypatch):
    """Fake pipes and Presence objects, on the real clock: the serving thread
    waits on an Event, so a frozen monotonic() would never let it move."""
    state = SimpleNamespace(live=[0], failures={}, made=[])

    def presence(client_id, pipe="auto"):
        p = MagicMock(name=f"presence-{pipe}")
        p.client_id = client_id
        p.pipe = pipe
        if pipe in state.failures:
            p.connect.side_effect = state.failures[pipe]
        state.made.append(p)
        return p

    monkeypatch.setattr(drpc, "_bridge_sandboxed_ipc_socket", lambda: None)
    monkeypatch.setattr(drpc, "_scan_ipc_pipes", lambda: (list(state.live), []))
    monkeypatch.setattr(drpc, "Presence", presence)
    monkeypatch.setattr(drpc, "_SERVE_TICK_S", 0.02)
    monkeypatch.setattr(drpc, "_STOP_WAIT_S", 2.0)
    return state


def until(predicate, limit: float = 3.0) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def served(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.start()
    try:
        yield rpc, discord
    finally:
        rpc.close()


def test_the_thread_reaches_for_discord_without_being_asked(served):
    """Nothing is playing and nothing was posted — the connection still comes
    up, so a refused Application ID shows before the first song."""
    rpc, _ = served
    assert until(rpc.is_connected)
    assert rpc.state is drpc.RPCState.CONNECTED_IDLE


def test_a_posted_song_reaches_discord(served):
    rpc, discord = served
    rpc.post(details="Glass Tides", state="Neon Harbor")
    assert until(lambda: discord.made and discord.made[0].update.called)
    sent = discord.made[0].update.call_args.kwargs
    assert sent["details"] == "Glass Tides"
    assert until(lambda: rpc.state is drpc.RPCState.SHOWING)


def test_posting_does_not_wait_for_the_thread(served):
    """The whole point: handing over is a lock and an Event, nothing else."""
    rpc, _ = served
    began = time.monotonic()
    for _ in range(200):
        rpc.post(details="Glass Tides")
    assert time.monotonic() - began < 0.5


def test_the_thread_takes_the_connection_down_itself(discord):
    """pypresence builds its asyncio loop in whichever thread connects, and a
    loop is not thread-safe — so the same thread has to close it."""
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.start()
    assert until(rpc.is_connected)
    closer = discord.made[0].close
    rpc.close()
    assert closer.called
    assert rpc._thread is None
    assert rpc.is_connected() is False


def test_a_clear_posted_on_the_way_out_still_goes(discord):
    """Quitting posts a clear and closes at once; losing it would leave the
    last song sitting on the profile."""
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.start()
    assert until(rpc.is_connected)
    rpc.post_clear(force=True)
    rpc.close()
    assert discord.made[0].clear.called


def test_one_failure_does_not_end_the_thread(served, caplog, monkeypatch):
    """A thread that dies on its first bad cycle would leave Discord unserved
    for the rest of the session, and the log would not say why."""
    rpc, discord = served
    assert until(rpc.is_connected)
    blown = []
    real = rpc.update

    def once(**payload):
        if not blown:
            blown.append(payload)
            raise RuntimeError("boom")
        return real(**payload)

    monkeypatch.setattr(rpc, "update", once)
    rpc.post(details="Glass Tides")
    assert until(lambda: "serving thread hit an error" in caplog.text)
    assert blown
    rpc.post(details="Second Wind")
    assert until(lambda: discord.made[0].update.called)
    assert discord.made[0].update.call_args.kwargs["details"] == "Second Wind"


def test_starting_twice_keeps_one_thread(served):
    rpc, _ = served
    first = rpc._thread
    rpc.start()
    assert rpc._thread is first


def test_a_clear_is_handled_by_the_running_loop(served):
    """Privacy set to off posts a clear while the daemon keeps running — it
    must go out there, not only on the way to a shutdown."""
    rpc, discord = served
    assert until(rpc.is_connected)
    rpc.post_clear()
    assert until(lambda: discord.made[0].clear.called)
    assert rpc._thread.is_alive()


def test_a_thread_that_will_not_stop_is_reported(discord, monkeypatch, caplog):
    """Quitting cannot wait out a handshake, so a thread still dialling is
    left behind — but it is said out loud rather than passed over."""
    monkeypatch.setattr(drpc, "_STOP_WAIT_S", 0.05)
    held = threading.Event()
    inside = threading.Event()

    def stuck():
        inside.set()
        held.wait(3.0)

    rpc = drpc.DiscordRPC(CLIENT_ID)
    monkeypatch.setattr(rpc, "_ensure_connected", stuck)
    rpc.start()
    try:
        # Only once the thread is demonstrably stuck does closing have to
        # give up on it; asking earlier would catch it idle and pass for
        # the wrong reason.
        assert inside.wait(3.0)
        with caplog.at_level(logging.WARNING, logger="refrain.discord_rpc"):
            rpc.close()
        assert "did not stop in time" in caplog.text
        assert rpc._thread is None
    finally:
        held.set()
