"""DiscordRPC against a difficult Discord: refused payloads, rate limits, hung clients."""

from __future__ import annotations

import asyncio
import gc
import inspect
import json
import logging
import socket
import struct
import threading
import time
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import refrain.discord_rpc as drpc
from refrain.discord_rpc import RPCState, sanitize_activity

CLIENT_ID = "123456789012345678"
PAD = "\N{BRAILLE PATTERN BLANK}"


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
    state = SimpleNamespace(live=[0], failures={}, made=[])

    def presence(client_id, pipe="auto"):
        p = MagicMock(name=f"presence-{pipe}")
        p.pipe = pipe
        if pipe in state.failures:
            p.connect.side_effect = state.failures[pipe]
        state.made.append(p)
        return p

    monkeypatch.setattr(drpc, "_bridge_sandboxed_ipc_socket", lambda: None)
    monkeypatch.setattr(drpc, "_scan_ipc_pipes", lambda: (list(state.live), []))
    monkeypatch.setattr(drpc, "Presence", presence)
    return state


def refused(message="child details fails because details length must be at least 2"):
    return drpc.ppx.ServerError(message)


# Payload limits


def test_a_one_letter_artist_is_padded_to_two_characters():
    sent = sanitize_activity({"details": "7", "state": "M"})
    assert sent == {"details": "7" + PAD, "state": "M" + PAD}
    assert sent["state"].strip() == sent["state"]


def test_whitespace_is_collapsed_and_blank_fields_are_dropped():
    sent = sanitize_activity({"details": "  Glass \t\n Tides ", "state": " \n ", "large_text": ""})
    assert sent == {"details": "Glass Tides"}


def test_text_that_is_not_a_string_is_dropped():
    assert sanitize_activity({"details": 7, "state": None}) == {}


def test_long_text_is_shortened_with_an_ellipsis():
    sent = sanitize_activity(
        {"details": "x" * 200, "state": "Northbound " * 20, "large_text": "y" * 128}
    )
    assert sent["details"] == "x" * 127 + "…"
    assert sent["state"] == ("Northbound " * 12)[:127].rstrip() + "…"
    assert sent["large_text"] == "y" * 128


def test_text_is_measured_the_way_discord_counts_it():
    sent = sanitize_activity({"details": "\U0001f3b5" * 100})["details"]
    assert len(sent.encode("utf-16-le")) // 2 <= 128
    assert sent == "\U0001f3b5" * 63 + "…"


def test_images_keep_asset_keys_and_https_links_within_the_limit():
    cover = "https://is1-ssl.example.com/image/thumb/600x600bb.jpg"
    assert sanitize_activity({"large_image": f" {cover} "}) == {"large_image": cover}
    assert sanitize_activity({"large_image": "refrain"}) == {"large_image": "refrain"}


@pytest.mark.parametrize(
    "image",
    [
        "https://example.com/" + "a" * 250,
        "k" * 257,
        "ftp://example.com/cover.jpg",
        "   ",
        42,
    ],
)
def test_an_image_discord_cannot_take_is_left_out(image):
    assert sanitize_activity({"details": "Salt Flats", "large_image": image}) == {
        "details": "Salt Flats"
    }


@pytest.mark.parametrize(
    "link",
    ["https://example.com/a b", "mailto:someone@example.com", "http://[::1", None, "https://"],
)
def test_broken_line_links_are_left_out(link):
    assert "details_url" not in sanitize_activity({"details_url": link})


def test_a_good_line_link_is_kept():
    link = "https://music.example.com/song/12345"
    assert sanitize_activity({"details_url": link}) == {"details_url": link}


def test_buttons_are_fitted_and_bad_ones_dropped():
    good = "https://music.example.com/song/12345"
    sent = sanitize_activity(
        {
            "buttons": [
                {"label": "  Listen   along on the web player today ", "url": good},
                {"label": "Plain http", "url": "http://music.example.com/song/12345"},
                {"label": "Too long", "url": "https://example.com/" + "a" * 500},
                {"label": " ", "url": good},
                "not a button",
                {"label": "Second", "url": good},
                {"label": "Third", "url": good},
            ]
        }
    )
    assert sent["buttons"] == [
        {"label": "Listen along on the web player…", "url": good},
        {"label": "Second", "url": good},
    ]


@pytest.mark.parametrize("buttons", [[], [{"label": "Only", "url": "nope"}], "https://x.example"])
def test_no_usable_button_means_no_buttons_field(buttons):
    assert sanitize_activity({"buttons": buttons}) == {}


def test_an_end_before_the_start_is_dropped():
    assert sanitize_activity({"start": 100, "end": 100}) == {"start": 100}
    assert sanitize_activity({"start": 100, "end": 280}) == {"start": 100, "end": 280}


def test_other_fields_pass_through_and_none_is_dropped():
    sent = sanitize_activity({"activity_type": drpc.ActivityType.LISTENING, "party_id": None})
    assert sent == {"activity_type": drpc.ActivityType.LISTENING}


def test_update_sends_the_fitted_payload(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details=" Ferrous ", state="M", large_image="x" * 300)
    sent = discord.made[0].update.call_args.kwargs
    assert sent["details"] == "Ferrous"
    assert sent["state"] == "M" + PAD
    assert "large_image" not in sent


# Refused payloads


def test_a_refused_payload_keeps_the_connection(discord, clock, caplog):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    presence = discord.made[0]
    presence.update.side_effect = refused()

    with caplog.at_level(logging.WARNING, logger="refrain.discord_rpc"):
        rpc.update(details="Late Train Home", state="Velvet Static")
        for _ in range(10):
            clock.now += 5.0
            rpc.update(details="Late Train Home", state="Velvet Static")
        clock.now += drpc._RESEND_S
        rpc.update(details="Late Train Home", state="Velvet Static")

    assert rpc.is_connected() is True
    # Once, then one retry — a refusal can also be a hiccup on Discord's side.
    assert presence.update.call_count == 2
    assert presence.close.called is False
    assert len(discord.made) == 1
    assert rpc.state is RPCState.ERROR
    assert "at least 2" in rpc.detail
    warnings = [r.getMessage() for r in caplog.records if "refused the status" in r.getMessage()]
    assert len(warnings) == 2
    assert "trying once more" in warnings[0]
    assert "not sending it again" in warnings[1]
    assert "Late Train Home" not in caplog.text


def test_after_a_refusal_the_next_different_status_goes_out(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    presence = discord.made[0]
    presence.update.side_effect = [refused(), None]

    rpc.update(details="Overexposed", state="Kite Theory")
    clock.now += 1.0
    rpc.update(details="Silk Road Radio", state="Ilse Moreau")
    assert presence.update.call_count == 2
    assert presence.update.call_args.kwargs["details"] == "Silk Road Radio"
    assert rpc.state is RPCState.SHOWING
    assert rpc.detail == "discord-ipc-0"


def test_refused_payloads_are_remembered_only_so_far(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    discord.made[0].update.side_effect = refused()
    for n in range(drpc._REFUSED_MEMORY + 5):
        clock.now += 5.0
        rpc.update(details=f"Song {n}")
    assert len(rpc._refused) == drpc._REFUSED_MEMORY


def test_one_client_refusing_does_not_hide_the_delivery_to_another(discord):
    discord.live = [0, 2]
    rpc = drpc.DiscordRPC(CLIENT_ID, all_clients=True)
    rpc._ensure_connected()
    discord.made[0].update.side_effect = refused()
    rpc.update(details="Northbound", state="The Quiet Hours")
    assert rpc.state is RPCState.SHOWING
    assert sorted(rpc._presences) == [0, 2]


def test_a_refused_clear_is_tried_again(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Northbound", state="The Quiet Hours")
    presence = discord.made[0]
    presence.clear.side_effect = [refused("busy"), None]

    clock.now += 5.0
    rpc.clear()
    assert rpc.is_connected() is True
    assert presence.clear.call_count == 1
    rpc.pump()
    assert presence.clear.call_count == 2
    assert rpc.state is RPCState.CONNECTED_IDLE


# Rate limit


def _spend_all_tokens(rpc, clock):
    for n in range(drpc._WRITE_TOKENS):
        rpc.update(details=f"Warm-up {n}", state="Neon Harbor")
        clock.now += 0.1


def test_changes_go_out_at_once_while_tokens_are_left(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    for n in range(drpc._WRITE_TOKENS):
        rpc.update(details=f"Song {n}", state="Neon Harbor")
        assert discord.made[0].update.call_args.kwargs["details"] == f"Song {n}"
        clock.now += 0.2
    assert discord.made[0].update.call_count == drpc._WRITE_TOKENS


def test_a_burst_of_track_changes_is_coalesced(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    first = clock.now
    for n in range(10):
        rpc.update(details=f"Song {n}", state="Neon Harbor")
        clock.now += 0.5
    presence = discord.made[0]
    assert presence.update.call_count == 5
    assert presence.update.call_args.kwargs["details"] == "Song 4"

    clock.now = first + drpc._WRITE_WINDOW_S - 0.1
    rpc.pump()
    assert presence.update.call_count == 5
    clock.now = first + drpc._WRITE_WINDOW_S
    rpc.pump()
    assert presence.update.call_count == 6
    assert presence.update.call_args.kwargs["details"] == "Song 9"
    rpc.pump()
    assert presence.update.call_count == 6


def test_never_more_than_five_writes_in_any_twenty_seconds(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    writes = []
    discord.made[0].update.side_effect = lambda **_: writes.append(clock.now)
    discord.made[0].clear.side_effect = lambda: writes.append(clock.now)
    for tick in range(200):
        if tick % 3:
            rpc.update(details=f"Song {tick}")
        else:
            rpc.clear()
        clock.now += 0.5
    assert len(writes) > 10
    for i, t in enumerate(writes):
        assert sum(1 for u in writes[i:] if u < t + 20.0) <= 5


def test_a_clear_held_back_by_the_limit_still_goes_out(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    first = clock.now
    _spend_all_tokens(rpc, clock)
    rpc.clear()
    presence = discord.made[0]
    assert presence.clear.call_count == 0
    assert rpc.state is RPCState.SHOWING

    clock.now = first + drpc._WRITE_WINDOW_S
    rpc.pump()
    assert presence.clear.call_count == 1
    assert rpc.state is RPCState.CONNECTED_IDLE


def test_the_newest_state_wins_while_the_tokens_are_spent(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    first = clock.now
    _spend_all_tokens(rpc, clock)
    rpc.update(details="Glass Tides", state="Neon Harbor")
    rpc.clear()
    rpc.update(details="Rooftop Weather", state="Juniper Lane")
    clock.now = first + drpc._WRITE_WINDOW_S
    rpc.pump()
    presence = discord.made[0]
    assert presence.clear.call_count == 0
    assert presence.update.call_count == drpc._WRITE_TOKENS + 1
    assert presence.update.call_args.kwargs["details"] == "Rooftop Weather"


def test_a_pause_and_resume_while_the_tokens_are_spent_sends_nothing(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    first = clock.now
    _spend_all_tokens(rpc, clock)
    last = {"details": "Warm-up 4", "state": "Neon Harbor"}
    rpc.clear()
    clock.now += 1.0
    rpc.update(**last)
    clock.now = first + drpc._WRITE_WINDOW_S
    rpc.pump()
    presence = discord.made[0]
    assert (presence.update.call_count, presence.clear.call_count) == (drpc._WRITE_TOKENS, 0)


def test_a_long_pause_repeats_the_clear_now_and_then(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    presence = discord.made[0]
    for _ in range(int(drpc._RESEND_S * 2) + 1):
        rpc.clear()
        clock.now += 1.0
    assert presence.clear.call_count == 3


def test_pump_without_a_connection_or_anything_pending_does_nothing(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.pump()
    rpc.clear()
    rpc.pump()
    assert discord.made == []
    rpc._ensure_connected()
    rpc.close()
    rpc.pump()
    assert discord.made[0].clear.called is False


# Connection state for the UI


def test_state_without_an_application_id_is_disabled():
    rpc = drpc.DiscordRPC("")
    assert rpc.state == "disabled"
    assert rpc.state is RPCState.DISABLED


def test_state_follows_the_connection(discord, clock):
    discord.failures[0] = ConnectionRefusedError("connection refused")
    rpc = drpc.DiscordRPC(CLIENT_ID)
    assert rpc.state is RPCState.NO_CLIENT
    rpc.update(details="Glass Tides")
    assert rpc.state is RPCState.NO_CLIENT
    assert "connection refused" in rpc.detail

    del discord.failures[0]
    clock.now = rpc._next_retry_ts
    rpc._ensure_connected()
    assert rpc.state is RPCState.CONNECTED_IDLE
    rpc.update(details="Glass Tides")
    assert rpc.state is RPCState.SHOWING
    clock.now += 5.0
    rpc.clear()
    assert rpc.state is RPCState.CONNECTED_IDLE
    rpc.close()
    assert rpc.state is RPCState.NO_CLIENT


def test_state_names_a_refused_application_id(discord):
    discord.failures[0] = drpc.ppx.InvalidID()
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Glass Tides")
    assert rpc.state is RPCState.REJECTED
    assert rpc.status == "rejected"
    assert "Invalid" in rpc.detail


def test_a_timeout_while_connecting_is_an_error_until_discord_is_simply_gone(discord, clock):
    discord.failures[0] = TimeoutError()
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Glass Tides")
    assert rpc.state is RPCState.ERROR
    assert rpc.detail == "Discord did not answer in time"
    assert rpc.status == "no_client"

    discord.failures[0] = drpc.ppx.InvalidPipe()
    clock.now = rpc._next_retry_ts
    rpc.update(details="Glass Tides")
    assert rpc.state is RPCState.NO_CLIENT


def test_a_timeout_on_update_drops_the_connection_as_an_error(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    discord.made[0].update.side_effect = drpc.ppx.ResponseTimeout()
    rpc.update(details="Glass Tides")
    assert rpc.is_connected() is False
    assert rpc.state is RPCState.ERROR

    clock.now = rpc._next_retry_ts
    rpc.update(details="Glass Tides")
    assert rpc.state is RPCState.SHOWING


# Timeouts


def test_connections_are_opened_with_short_timeouts(discord):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    presence = discord.made[0]
    assert presence.connection_timeout == drpc._CONNECT_TIMEOUT_S
    assert presence.response_timeout == drpc._RESPONSE_TIMEOUT_S
    assert inspect.iscoroutinefunction(presence.handshake)


def test_a_failed_connect_closes_its_socket(discord):
    discord.failures[0] = TimeoutError()
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    presence = discord.made[0]
    presence.sock_writer.close.assert_called_once()
    presence.loop.close.assert_called_once()


def test_discarding_a_half_built_connection_never_raises():
    drpc._discard(SimpleNamespace(loop=None, sock_writer=None))


def _frame(op, data):
    body = json.dumps(data).encode()
    return struct.pack("<II", op, len(body)) + body


def _read_frame(conn):
    head = b""
    while len(head) < 8:
        chunk = conn.recv(8 - len(head))
        if not chunk:
            return None
        head += chunk
    _op, length = struct.unpack("<II", head)
    body = b""
    while len(body) < length:
        body += conn.recv(length - len(body))
    return json.loads(body)


class FakeDiscord:
    """A discord-ipc-0 socket that answers the handshake and then follows a script.

    Each entry in `replies` answers one request: a dict is sent back, None
    means go silent. Connections that close without a request (probes) are
    ignored.
    """

    def __init__(self, path, replies):
        self.replies = list(replies)
        self.received = []
        self.stop = threading.Event()
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(8)
        self.srv.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.srv.accept()
            except TimeoutError:
                continue
            with conn:
                frame = _read_frame(conn)
                if frame is None:
                    continue
                conn.sendall(_frame(1, {"cmd": "DISPATCH", "evt": "READY", "data": {"v": 1}}))
                for reply in self.replies:
                    frame = _read_frame(conn)
                    if frame is None:
                        break
                    self.received.append(frame)
                    if reply is None:
                        self.stop.wait()
                        break
                    conn.sendall(_frame(1, reply))
                self.stop.wait()

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.srv.close()


@pytest.fixture
def real_ipc(monkeypatch):
    """The real Presence class against sockets in the test's own runtime dir."""
    monkeypatch.setattr(drpc, "_CONNECT_TIMEOUT_S", 0.3)
    monkeypatch.setattr(drpc, "_RESPONSE_TIMEOUT_S", 0.3)
    runtime = drpc._runtime_dir()
    assert runtime is not None and "runtime" in str(runtime)
    servers = []
    yield SimpleNamespace(path=runtime / "discord-ipc-0", servers=servers)
    for s in servers:
        s.close()
    asyncio.set_event_loop(None)
    # pypresence leaves a spare event loop and sockets for the GC; collect
    # them here so the warnings do not land on whichever test runs next.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()


# pypresence's socket search leaves a directory iterator open.
pypresence_leaks = pytest.mark.filterwarnings("ignore:unclosed scandir iterator:ResourceWarning")


@pypresence_leaks
def test_a_client_that_never_finishes_the_handshake_does_not_block(real_ipc):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as silent:
        silent.bind(str(real_ipc.path))
        silent.listen(8)
        rpc = drpc.DiscordRPC(CLIENT_ID)
        began = time.monotonic()
        rpc.update(details="Glass Tides", state="Neon Harbor")
        assert time.monotonic() - began < 2.0
    assert rpc.is_connected() is False
    assert rpc.state is RPCState.ERROR


@pypresence_leaks
def test_a_client_that_stops_answering_does_not_block_updates(real_ipc):
    server = FakeDiscord(real_ipc.path, [None])
    real_ipc.servers.append(server)
    rpc = drpc.DiscordRPC(CLIENT_ID)
    began = time.monotonic()
    rpc.update(details="Glass Tides", state="Neon Harbor")
    assert time.monotonic() - began < 2.0
    assert server.received[0]["cmd"] == "SET_ACTIVITY"
    assert rpc.is_connected() is False
    assert rpc.state is RPCState.ERROR


@pypresence_leaks
def test_a_real_refusal_from_discord_keeps_the_pipe(real_ipc):
    error = {
        "cmd": "SET_ACTIVITY",
        "evt": "ERROR",
        "data": {"code": 4000, "message": "child details fails because length must be at least 2"},
    }
    server = FakeDiscord(real_ipc.path, [error])
    real_ipc.servers.append(server)
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc.update(details="Glass Tides", state="Neon Harbor")
    assert rpc.is_connected() is True
    assert rpc.state is RPCState.ERROR
    assert "at least 2" in rpc.detail
    rpc.update(details="Glass Tides", state="Neon Harbor")
    assert len(server.received) == 1
    rpc.close()


def test_the_clear_on_the_way_out_ignores_the_rate_limit(discord, clock):
    rpc = drpc.DiscordRPC(CLIENT_ID)
    rpc._ensure_connected()
    presence = discord.made[0]
    for i in range(drpc._WRITE_TOKENS):
        rpc.update(details=f"Song {i}", state="Neon Harbor")
        clock.now += 0.1
    presence.clear.reset_mock()
    rpc.clear()  # no tokens left: held back
    assert presence.clear.called is False
    rpc.clear(force=True)
    assert presence.clear.called is True
