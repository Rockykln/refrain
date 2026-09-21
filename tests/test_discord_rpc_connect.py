"""DiscordRPC connecting, backing off and recovering, plus the Snap/Flatpak socket bridge."""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import refrain.discord_rpc as drpc

CLIENT_ID = "123456789012345678"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(drpc, "time", SimpleNamespace(monotonic=c.monotonic))
    return c


@pytest.fixture
def discord(monkeypatch, clock):
    """Fake pipes and Presence objects; `failures[pipe]` is raised by that pipe's connect()."""
    state = SimpleNamespace(live=[0], stale=[], failures={}, made=[], bridge_error=None)

    def bridge():
        if state.bridge_error:
            raise state.bridge_error

    def presence(client_id, pipe="auto"):
        p = MagicMock(name=f"presence-{pipe}")
        p.client_id = client_id
        p.pipe = pipe
        if pipe in state.failures:
            p.connect.side_effect = state.failures[pipe]
        state.made.append(p)
        return p

    monkeypatch.setattr(drpc, "_bridge_sandboxed_ipc_socket", bridge)
    monkeypatch.setattr(drpc, "_scan_ipc_pipes", lambda: (list(state.live), list(state.stale)))
    monkeypatch.setattr(drpc, "Presence", presence)
    return state


def test_without_a_client_id_nothing_is_dialled(discord):
    rpc = drpc.DiscordRPC("  ")
    rpc.update(details="Glass Tides", state="Neon Harbor")
    assert rpc.status == "disabled"
    assert discord.made == []


def test_a_broken_bridge_does_not_stop_the_connection(discord):
    discord.bridge_error = PermissionError("sandbox dir locked")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc._ensure_connected() is True
    assert rpc.status == "connected"


def test_stale_sockets_are_skipped_for_the_live_one(discord, caplog):
    discord.live, discord.stale = [1], [0]
    rpc = drpc.DiscordRPC(CLIENT_ID)
    with caplog.at_level(logging.DEBUG, logger="refrain.discord_rpc"):
        assert rpc._ensure_connected() is True
    assert [p.pipe for p in discord.made] == [1]
    assert rpc.status_detail == "discord-ipc-1"
    assert "stale socket(s) discord-ipc-0" in caplog.text


def test_with_no_visible_socket_pypresence_searches_itself(discord):
    discord.live = []
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc._ensure_connected() is True
    assert discord.made[0].pipe == "auto"
    assert rpc.status_detail == "auto"


def test_a_failed_connect_reports_no_client_and_waits(discord, clock):
    discord.failures[0] = ConnectionRefusedError("connection refused")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc._ensure_connected() is False
    assert rpc.status == "no_client"
    assert "connection refused" in rpc.status_detail
    assert rpc._next_retry_ts == clock.now + 2.0

    clock.now += 1.0
    assert rpc._ensure_connected() is False
    assert len(discord.made) == 1


def test_the_retry_delay_doubles_up_to_fifteen_seconds(discord, clock):
    discord.failures[0] = ConnectionRefusedError("connection refused")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    waits = []
    for _ in range(6):
        rpc._ensure_connected()
        waits.append(rpc._next_retry_ts - clock.now)
        clock.now = rpc._next_retry_ts
    assert waits == [2.0, 4.0, 8.0, 15.0, 15.0, 15.0]


def test_a_successful_connect_resets_the_backoff(discord, clock):
    discord.failures[0] = ConnectionRefusedError("connection refused")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    for _ in range(3):
        rpc._ensure_connected()
        clock.now = rpc._next_retry_ts
    del discord.failures[0]
    assert rpc._ensure_connected() is True
    assert rpc._backoff_s == 2.0


def test_a_rejected_handshake_waits_the_longest(discord, clock):
    discord.failures[0] = drpc.ppx.DiscordError(4000, "Invalid Client ID")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc._ensure_connected() is False
    assert rpc.status == "rejected"
    assert "Invalid Client ID" in rpc.status_detail
    assert rpc._next_retry_ts == clock.now + 15.0


def test_a_signed_out_discord_is_not_treated_as_rejected(discord, clock):
    discord.failures[0] = drpc.ppx.DiscordError(1000, "User logged out")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc._ensure_connected() is False
    assert rpc.status == "not_logged_in"
    assert rpc.state is drpc.RPCState.NOT_LOGGED_IN
    assert "User logged out" in rpc.status_detail
    # Ordinary backoff, not the 15 s a permanent rejection gets — logging
    # in later must be noticed quickly, without a restart.
    assert rpc._next_retry_ts == clock.now + 2.0


def test_a_signed_out_discord_recovers_once_the_user_logs_in(discord, clock):
    discord.failures[0] = drpc.ppx.DiscordError(1000, "User logged out")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc._ensure_connected() is False
    assert rpc.status == "not_logged_in"

    clock.now = rpc._next_retry_ts
    del discord.failures[0]
    assert rpc._ensure_connected() is True
    assert rpc.status == "connected"
    assert rpc.state is drpc.RPCState.CONNECTED_IDLE


def test_a_rejection_stops_trying_the_remaining_clients(discord):
    discord.live = [0, 2]
    discord.failures[0] = drpc.ppx.DiscordError(4000, "Invalid Client ID")
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    rpc._ensure_connected()
    assert [p.pipe for p in discord.made] == [0]


def test_a_rejecting_second_client_does_not_hide_the_working_one(discord):
    discord.live = [0, 2]
    discord.failures[2] = drpc.ppx.DiscordError(4006, "Not authenticated")
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    assert rpc._ensure_connected() is True
    assert rpc.status == "connected"


def test_a_client_that_rejected_is_asked_again_later(discord, clock):
    discord.live = [0, 2]
    discord.failures[2] = drpc.ppx.DiscordError(4006, "Not authenticated")
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    rpc._ensure_connected()
    assert rpc._next_retry_ts == clock.now + 15.0

    clock.now += 5.0
    assert rpc._ensure_connected() is True
    assert len(discord.made) == 2

    del discord.failures[2]
    clock.now += 10.0
    assert rpc._ensure_connected() is True
    assert sorted(rpc._presences) == [0, 2]
    assert rpc.status_detail == "discord-ipc-0, discord-ipc-2"


def test_a_newcomer_rejecting_later_keeps_the_status_connected(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    assert rpc._ensure_connected() is True

    discord.live = [0, 2]
    discord.failures[2] = drpc.ppx.DiscordError(4006, "Not authenticated")
    clock.now += 5.0
    assert rpc._ensure_connected() is True
    assert (rpc.status, rpc.status_detail) == ("connected", "discord-ipc-0")
    assert rpc._next_retry_ts == clock.now + 15.0


def test_a_newcomer_that_is_not_ready_keeps_the_existing_client(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    assert rpc._ensure_connected() is True

    discord.live = [0, 2]
    discord.failures[2] = ConnectionRefusedError("still starting")
    clock.now += 5.0
    assert rpc._ensure_connected() is True
    assert list(rpc._presences) == [0]
    assert rpc.status == "connected"
    assert rpc._next_retry_ts > clock.now


def test_the_status_is_sent_as_listening_unless_told_otherwise(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Northbound", state="The Quiet Hours")
    sent = discord.made[0].update.call_args.kwargs
    assert sent["activity_type"] == drpc.ActivityType.LISTENING

    clock.now += 5.0
    rpc.update(details="Northbound", activity_type=drpc.ActivityType.PLAYING)
    assert discord.made[0].update.call_args.kwargs["activity_type"] == drpc.ActivityType.PLAYING


def test_after_discord_restarts_the_same_status_is_sent_again(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Ferrous", state="Oskar Lind")
    first = discord.made[0]
    first.update.side_effect = BrokenPipeError("Discord closed")

    clock.now += 5.0
    rpc.update(details="Ferrous", state="Oskar Lind", large_text="Iron Garden")
    assert first.close.called
    assert rpc.is_connected() is False

    clock.now = rpc._next_retry_ts
    rpc.update(details="Ferrous", state="Oskar Lind", large_text="Iron Garden")
    second = discord.made[1]
    assert second.update.call_count == 1
    assert rpc.status == "connected"


def test_a_failing_close_after_a_failed_update_is_ignored(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    discord.made[0].update.side_effect = BrokenPipeError("gone")
    discord.made[0].close.side_effect = OSError("already closed")
    rpc.update(details="Late Train Home", state="Velvet Static")
    assert rpc._presences == {}
    assert rpc._last_payload is None


def test_a_failing_clear_drops_that_client_and_retries_later(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Salt Flats", state="Wren & Ash")
    discord.made[0].clear.side_effect = BrokenPipeError("gone")
    discord.made[0].close.side_effect = OSError("already closed")

    clock.now += 5.0
    rpc.clear()
    assert rpc.is_connected() is False
    assert rpc._cleared is False
    assert rpc._next_retry_ts == clock.now + 2.0


def test_clear_keeps_the_clients_that_answered(discord, clock):
    discord.live = [0, 2]
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    rpc.update(details="Silk Road Radio", state="Ilse Moreau")
    healthy, broken = discord.made
    broken.clear.side_effect = BrokenPipeError("gone")

    clock.now += 5.0
    rpc.clear()
    clock.now += 5.0
    rpc.clear()
    assert list(rpc._presences) == [0]
    assert healthy.clear.call_count == 1


def test_close_survives_a_client_that_fails_to_close(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Overexposed", state="Kite Theory")
    discord.made[0].close.side_effect = OSError("already closed")
    rpc.close()
    rpc.close()
    assert rpc.is_connected() is False
    assert discord.made[0].close.call_count == 1


def test_setting_the_primary_to_none_disconnects(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    rpc._presence = None
    assert rpc.is_connected() is False
    assert rpc._presence is None


def test_the_runtime_dir_is_optional(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(drpc.os, "getuid", lambda: 1234567)
    assert drpc._runtime_dir() is None
    assert drpc._scan_ipc_pipes() == ([], [])


def test_a_slot_that_cannot_be_inspected_is_skipped(monkeypatch):
    runtime = Path(drpc.os.environ["XDG_RUNTIME_DIR"])
    real_is_socket = Path.is_socket

    def is_socket(self):
        if self == runtime / "discord-ipc-0":
            raise PermissionError("no access")
        return real_is_socket(self)

    monkeypatch.setattr(Path, "is_socket", is_socket)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as srv:
        srv.bind(str(runtime / "discord-ipc-1"))
        srv.listen(1)
        assert drpc._scan_ipc_pipes() == ([1], [])


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A runtime dir with the Flatpak Discord directory, both empty."""
    runtime = tmp_path / "run"
    flatpak = runtime / "app" / "com.discordapp.Discord"
    flatpak.mkdir(parents=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return SimpleNamespace(runtime=runtime, flatpak=flatpak, home=tmp_path / "home")


def _socket_in(directory, monkeypatch, name="discord-ipc-0"):
    """Binds relative to the directory: the absolute path can be too long for a socket."""
    monkeypatch.chdir(directory)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(name)
    return srv


def test_bridge_does_nothing_when_the_runtime_dir_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    drpc._bridge_sandboxed_ipc_socket()
    assert not (tmp_path / "missing").exists()


def test_bridge_links_the_snap_socket_under_home(sandbox, monkeypatch):
    snap = sandbox.home / "snap" / "discord" / "current" / ".config" / "discord"
    snap.mkdir(parents=True)
    with _socket_in(snap, monkeypatch):
        drpc._bridge_sandboxed_ipc_socket()
    link = sandbox.runtime / "discord-ipc-0"
    assert link.is_symlink()
    assert link.readlink() == snap / "discord-ipc-0"


def test_bridge_ignores_files_that_are_not_discord_sockets(sandbox):
    (sandbox.flatpak / "discord-ipc-0").write_text("not a socket")
    (sandbox.flatpak / "SingletonSocket").write_text("")
    drpc._bridge_sandboxed_ipc_socket()
    assert sorted(p.name for p in sandbox.runtime.iterdir()) == ["app"]


def test_bridge_skips_an_entry_it_cannot_inspect(sandbox, monkeypatch):
    real_is_socket = Path.is_socket

    def is_socket(self):
        if self.parent == sandbox.flatpak:
            raise PermissionError("no access")
        return real_is_socket(self)

    with _socket_in(sandbox.flatpak, monkeypatch):
        monkeypatch.setattr(Path, "is_socket", is_socket)
        drpc._bridge_sandboxed_ipc_socket()
    assert not (sandbox.runtime / "discord-ipc-0").is_symlink()


@pytest.mark.parametrize("error", [FileExistsError("raced"), PermissionError("read-only")])
def test_bridge_gives_up_quietly_when_linking_fails(sandbox, monkeypatch, error):
    attempts = []

    def symlink_to(self, target):
        attempts.append(self.name)
        raise error

    with _socket_in(sandbox.flatpak, monkeypatch, "discord-ipc-3"):
        monkeypatch.setattr(Path, "symlink_to", symlink_to)
        drpc._bridge_sandboxed_ipc_socket()
    assert attempts == ["discord-ipc-3"]
