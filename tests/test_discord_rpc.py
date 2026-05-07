"""DiscordRPC: payload-dedup + sandboxed-IPC bridge.

The pypresence library is mocked at the import boundary so the suite
runs without a Discord client and without a real IPC socket.
"""

from __future__ import annotations

import socket
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_pypresence(monkeypatch):
    """Replace `pypresence.Presence` with a MagicMock that records calls."""
    fake_module = MagicMock()
    fake_module.Presence = MagicMock(return_value=MagicMock())
    fake_module.ActivityType = MagicMock()
    fake_module.ActivityType.LISTENING = "listening"
    fake_module.exceptions = MagicMock()
    monkeypatch.setitem(sys.modules, "pypresence", fake_module)
    monkeypatch.setitem(sys.modules, "pypresence.exceptions", fake_module.exceptions)
    if "refrain.discord_rpc" in sys.modules:
        del sys.modules["refrain.discord_rpc"]
    yield fake_module


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


def test_different_payload_pushes_again(fake_pypresence):
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    rpc.update(details="Track A", state="Artist")
    rpc.update(details="Track B", state="Artist")  # title changed
    rpc.update(details="Track B", state="Artist")  # same again — dedup

    assert presence_mock.update.call_count == 2


def test_clear_resets_dedup_cache(fake_pypresence):
    """After a clear() the next update — even if identical to the
    pre-clear one — must push, because clear() invalidates Discord's
    side of the activity."""
    from refrain.discord_rpc import DiscordRPC

    rpc = DiscordRPC("123456789012345678")
    rpc._presence = fake_pypresence.Presence.return_value
    presence_mock = rpc._presence

    payload = {"details": "Track A"}
    rpc.update(**payload)
    rpc.clear()
    rpc.update(**payload)

    assert presence_mock.update.call_count == 2


def test_update_failure_invalidates_cache(fake_pypresence):
    """If pypresence's update raises, the cache must be cleared so the
    next attempt (after reconnect) actually pushes."""
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
    # Simulate an already-present working socket.
    standard = tmp_path / "discord-ipc-0"
    standard.touch()

    from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

    _bridge_sandboxed_ipc_socket()

    # Standard socket untouched, no symlink created.
    assert standard.exists()
    assert not standard.is_symlink()


def test_bridge_symlinks_flatpak_socket(tmp_path, monkeypatch):
    """Standard path empty + Flatpak instance dir holds a real unix
    socket → bridge creates a symlink at the standard path."""
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
    """A symlink left behind by a previous Refrain run whose target
    has since been removed must be cleaned up so the next connect
    attempt isn't pointing at nothing."""
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


def test_bridge_no_xdg_runtime_dir_is_safe(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    from refrain.discord_rpc import _bridge_sandboxed_ipc_socket

    # Must not raise.
    _bridge_sandboxed_ipc_socket()
