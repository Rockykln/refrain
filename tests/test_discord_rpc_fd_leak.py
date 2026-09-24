"""The connect path must not leak the event loop pypresence throws away.

``Presence.__init__`` opens one asyncio loop and ``Presence.connect()``
replaces it with a second one, so every attempt orphans an epoll fd and a
self-pipe pair until the cyclic collector happens to reach it.
"""

from __future__ import annotations

import asyncio
import gc
import os
from types import SimpleNamespace

import pytest

import refrain.discord_rpc as drpc

CLIENT_ID = "123456789012345678"
_FD_DIR = "/proc/self/fd"


class _FakePresence:
    """Same loop handling as pypresence: one in __init__, another in connect()."""

    def __init__(self, client_id, pipe="auto", fail=False):
        self.client_id = client_id
        self.pipe = pipe
        self.sock_writer = None
        self._fail = fail
        self.loops = [asyncio.new_event_loop()]
        self.loop = self.loops[0]

    def handshake(self):
        raise AssertionError("_open wraps the handshake; it must not run here")

    def connect(self):
        self.loop = asyncio.new_event_loop()
        self.loops.append(self.loop)
        if self._fail:
            raise OSError("Could not find Discord installed and running on this machine.")


@pytest.fixture
def presences(monkeypatch):
    """Hands out _FakePresence objects; `state.fail` makes their connect() raise."""
    state = SimpleNamespace(fail=False, made=[])

    def factory(client_id, pipe="auto"):
        p = _FakePresence(client_id, pipe, fail=state.fail)
        state.made.append(p)
        return p

    monkeypatch.setattr(drpc, "Presence", factory)
    yield state
    for p in state.made:
        for loop in p.loops:
            loop.close()


def test_open_closes_the_loop_pypresence_throws_away(presences):
    presence = drpc.DiscordRPC(CLIENT_ID)._open(0)
    throwaway, live = presence.loops
    assert throwaway.is_closed()
    assert not live.is_closed()


def test_failed_connect_leaves_no_loop_open(presences):
    presences.fail = True
    with pytest.raises(OSError):
        drpc.DiscordRPC(CLIENT_ID)._open(0)
    assert all(loop.is_closed() for loop in presences.made[0].loops)


@pytest.mark.skipif(not os.path.isdir(_FD_DIR), reason="needs /proc/<pid>/fd")
def test_failed_connects_do_not_grow_the_fd_table(presences):
    presences.fail = True
    rpc = drpc.DiscordRPC(CLIENT_ID)
    with pytest.raises(OSError):
        rpc._open(0)  # warm-up, so first-call allocations don't count
    gc.disable()  # the cyclic collector would hide the leak
    try:
        before = len(os.listdir(_FD_DIR))
        for _ in range(25):
            with pytest.raises(OSError):
                rpc._open(0)
        grown = len(os.listdir(_FD_DIR)) - before
    finally:
        gc.enable()
    assert grown <= 2, f"{grown} file descriptors left behind by 25 connect attempts"
