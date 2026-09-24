"""DiscordRPC: payload dedup and sandboxed-IPC bridge, with pypresence mocked."""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pypresence import exceptions as real_exceptions


@pytest.fixture
def fake_pypresence(monkeypatch):
    """Replace `pypresence.Presence` with a MagicMock that records calls."""
    fake_module = MagicMock()
    fake_module.Presence = MagicMock(return_value=MagicMock())
    fake_module.ActivityType = MagicMock()
    fake_module.ActivityType.LISTENING = "listening"
    fake_module.exceptions = real_exceptions
    monkeypatch.setitem(sys.modules, "pypresence", fake_module)
    # Re-imported against the fake, and put back afterwards for the tests
    # that imported the real one.
    import refrain

    monkeypatch.delitem(sys.modules, "refrain.discord_rpc", raising=False)
    monkeypatch.delattr(refrain, "discord_rpc", raising=False)
    yield fake_module


@pytest.fixture
def clock(fake_pypresence, monkeypatch):
    """A controllable monotonic clock for the freshly imported module."""
    import refrain.discord_rpc as discord_rpc

    now = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(discord_rpc, "time", SimpleNamespace(monotonic=lambda: now.t))
    return now


def _later(clock):
    clock.t += 5.0


# ---------------------------------------------------------------------------
# Payload-dedup behavior.
# ---------------------------------------------------------------------------


def test_identical_consecutive_payloads_only_send_once(fake_pypresence):
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    # Force "connected" without going through the real connect path.
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    payload = {"details": "Track A", "state": "Artist", "large_image": "refrain"}
    rpc.update(**payload)
    rpc.update(**payload)
    rpc.update(**payload)

    assert presence_mock.update.call_count == 1


def test_different_payload_pushes_again(fake_pypresence, clock):
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    rpc.update(details="Track A", state="Artist")
    _later(clock)
    rpc.update(details="Track B", state="Artist")  # title changed
    rpc.update(details="Track B", state="Artist")  # same again — dedup

    assert presence_mock.update.call_count == 2


def test_clear_resets_dedup_cache(fake_pypresence, clock):
    """After clear() even an identical update pushes again."""
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    payload = {"details": "Track A"}
    rpc.update(**payload)
    _later(clock)
    rpc.clear()
    _later(clock)
    rpc.update(**payload)

    assert presence_mock.update.call_count == 2


def test_an_unchanged_status_is_sent_again_after_a_while(fake_pypresence, monkeypatch):
    """Only a write notices a Discord that restarted mid-song."""
    import refrain.discord_rpc as discord_rpc

    now = [1000.0]
    monkeypatch.setattr(discord_rpc.time, "monotonic", lambda: now[0])
    rpc = discord_rpc.DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    rpc.update(details="Track A")
    now[0] += 10
    rpc.update(details="Track A")
    assert presence_mock.update.call_count == 1
    now[0] += discord_rpc._RESEND_S
    rpc.update(details="Track A")
    assert presence_mock.update.call_count == 2


def test_a_paused_song_clears_once(fake_pypresence, clock):
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    rpc.update(details="Track A")
    for _ in range(5):
        _later(clock)
        rpc.clear()
    assert presence_mock.clear.call_count == 1
    _later(clock)
    rpc.update(details="Track A")
    _later(clock)
    rpc.clear()
    assert presence_mock.clear.call_count == 2


def test_update_failure_invalidates_cache(fake_pypresence):
    """A failed update clears the cache so the next attempt pushes."""
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence
    presence_mock.update.side_effect = OSError("pipe broke")

    rpc.update(details="Track A")
    # The failure path nulled _presence, so the next update goes
    # through _ensure_connected, which we'll let succeed on a fresh
    # mock. The dedup cache must NOT make us skip.
    presence_mock.update.side_effect = None
    rpc._presence = presence_mock  # reconnect simulation
    rpc._next_retry_ts = 0  # bypass backoff
    rpc.update(details="Track A")

    # First call raised, so it counts as one attempt.
    # Second call must have actually invoked update.
    assert presence_mock.update.call_count == 2


# ---------------------------------------------------------------------------
# Sandboxed-IPC bridge.
# ---------------------------------------------------------------------------


def test_bridge_no_op_when_standard_path_already_has_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    standard = tmp_path / "discord-ipc-0"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(standard))
    listener.listen(1)

    from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

    try:
        _bridge_sandboxed_ipc_socket()
        # Standard socket untouched, no symlink created.
        assert standard.exists()
        assert not standard.is_symlink()
    finally:
        listener.close()


def test_a_dead_socket_does_not_hide_a_sandboxed_discord(tmp_path, monkeypatch):
    """A crashed Discord leaves its socket file behind; it must not block the bridge."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    dead = tmp_path / "discord-ipc-0"
    corpse = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    corpse.bind(str(dead))
    corpse.close()  # the file stays, nothing listens on it any more
    assert dead.is_socket()

    flatpak_dir = tmp_path / "app" / "com.discordapp.Discord"
    flatpak_dir.mkdir(parents=True)
    real = flatpak_dir / "discord-ipc-1"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(real))
    listener.listen(1)

    from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

    try:
        _bridge_sandboxed_ipc_socket()
        bridged = tmp_path / "discord-ipc-1"
        assert bridged.is_symlink()
        assert bridged.resolve() == real.resolve()
    finally:
        listener.close()


def test_bridge_symlinks_flatpak_socket(tmp_path, monkeypatch):
    """A Flatpak socket with an empty standard path gets a symlink there."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    flatpak_dir = tmp_path / "app" / "com.discordapp.Discord"
    flatpak_dir.mkdir(parents=True)

    sandbox_socket_path = flatpak_dir / "discord-ipc-0"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(str(sandbox_socket_path))

        from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

        _bridge_sandboxed_ipc_socket()

        bridged = tmp_path / "discord-ipc-0"
        assert bridged.is_symlink()
        assert bridged.resolve() == sandbox_socket_path.resolve()
    finally:
        s.close()
        if sandbox_socket_path.exists():
            sandbox_socket_path.unlink()


def test_bridge_sweeps_stale_symlink(tmp_path, monkeypatch):
    """A dangling symlink from an earlier run is removed."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    stale_target = tmp_path / "gone" / "discord-ipc-0"
    stale_link = tmp_path / "discord-ipc-0"
    stale_link.symlink_to(stale_target)

    # Sanity: we've made an actually-broken symlink.
    assert stale_link.is_symlink()
    assert not stale_link.exists()

    from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

    _bridge_sandboxed_ipc_socket()

    assert not stale_link.exists()
    assert not stale_link.is_symlink()


def test_bridge_without_xdg_runtime_dir_links_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    snap_dir = tmp_path / "snap" / "discord" / "current" / ".config" / "discord"
    snap_dir.mkdir(parents=True)
    linked = []
    monkeypatch.setattr(Path, "symlink_to", lambda self, target: linked.append(self))
    monkeypatch.chdir(snap_dir)  # the full path is too long for a socket name
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind("discord-ipc-0")

        from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

        _bridge_sandboxed_ipc_socket()
    finally:
        s.close()
    assert linked == []
