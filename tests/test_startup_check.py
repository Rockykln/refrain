"""Startup credential check: verdicts, log markers, and honesty.

Both credentials used to fail silently and late — a rejected Discord
Application ID only spoke up once the daemon had something to publish, a
revoked Last.fm session only at the first scrobble. This runs both checks
once at startup and says what it found.

The point of the tests is that each verdict is *accurate*: "not connected"
must not be reported as "Discord is missing" when the client is in fact
running and simply has not been dialled yet.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

import pytest

from refrain.startup_check import (
    DISABLED,
    INVALID,
    MARKER,
    OK,
    UNREACHABLE,
    check_discord,
    check_lastfm,
)


@dataclass
class FakeLastfmCfg:
    enabled: bool = True
    api_key: str = "key"
    shared_secret: str = "secret"
    session_key: str = "session"


class FakeRPC:
    def __init__(self, client_id="123", status="no_client", detail=""):
        self.client_id = client_id
        self.status = status
        self.status_detail = detail


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def _live_socket(path, keep):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    keep.append(srv)


# ---------------------------------------------------------------- Last.fm
def test_lastfm_disabled_is_not_a_problem(caplog):
    with caplog.at_level(logging.INFO):
        result = check_lastfm(FakeLastfmCfg(enabled=False))
    assert result.state == DISABLED
    assert not result.needs_attention
    assert MARKER in caplog.text


def test_lastfm_without_credentials_is_disabled_not_broken():
    assert check_lastfm(FakeLastfmCfg(session_key="")).state == DISABLED


def test_lastfm_valid_session_reports_the_user(monkeypatch, caplog):
    import refrain.scrobble as scrobble

    monkeypatch.setattr(
        scrobble.LastfmClient, "validate_session", lambda self: "rocky", raising=True
    )
    with caplog.at_level(logging.INFO):
        result = check_lastfm(FakeLastfmCfg())
    assert result.state == OK
    assert result.detail == "rocky"
    assert "OK" in caplog.text


def test_lastfm_revoked_session_demands_attention(monkeypatch, caplog):
    import refrain.scrobble as scrobble

    def _boom(self):
        raise scrobble.LastfmError("Invalid session key", code=9)

    monkeypatch.setattr(scrobble.LastfmClient, "validate_session", _boom, raising=True)
    with caplog.at_level(logging.WARNING):
        result = check_lastfm(FakeLastfmCfg())
    assert result.state == INVALID
    assert result.needs_attention
    assert "REJECTED" in caplog.text


def test_lastfm_network_failure_is_not_reported_as_invalid(monkeypatch):
    """Offline must not look like "your credentials are wrong"."""
    import refrain.scrobble as scrobble

    def _boom(self):
        raise scrobble.LastfmError("Last.fm request failed: timed out")

    monkeypatch.setattr(scrobble.LastfmClient, "validate_session", _boom, raising=True)
    result = check_lastfm(FakeLastfmCfg())
    assert result.state == UNREACHABLE
    assert not result.needs_attention


# ---------------------------------------------------------------- Discord
def test_discord_without_client_id_is_disabled():
    assert check_discord(FakeRPC(client_id="")).state == DISABLED


def test_discord_connected_is_ok(caplog):
    with caplog.at_level(logging.INFO):
        result = check_discord(FakeRPC(status="connected", detail="discord-ipc-0"))
    assert result.state == OK
    assert "OK" in caplog.text


def test_discord_rejected_handshake_demands_attention(caplog):
    with caplog.at_level(logging.WARNING):
        result = check_discord(FakeRPC(status="rejected", detail="Invalid Client ID"))
    assert result.state == INVALID
    assert result.needs_attention
    assert "REJECTED" in caplog.text


def test_running_client_is_not_reported_as_missing(runtime_dir, caplog):
    """The daemon only dials Discord once something plays.

    Reporting "no client reachable" while Discord is plainly running sent
    the user hunting for a problem they did not have.
    """
    keep = []
    try:
        _live_socket(runtime_dir / "discord-ipc-0", keep)
        with caplog.at_level(logging.INFO):
            result = check_discord(FakeRPC(status="no_client"))
        assert result.state == UNREACHABLE
        assert "client is running" in caplog.text
        assert "no client running" not in caplog.text
    finally:
        for s in keep:
            s.close()


def test_absent_client_says_so(runtime_dir, caplog):
    with caplog.at_level(logging.INFO):
        result = check_discord(FakeRPC(status="no_client"))
    assert result.state == UNREACHABLE
    assert "no client running" in caplog.text


def test_stale_sockets_are_named_but_not_counted_as_clients(runtime_dir, caplog):
    keep = []
    try:
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(str(runtime_dir / "discord-ipc-1"))
        dead.close()
        _live_socket(runtime_dir / "discord-ipc-0", keep)
        with caplog.at_level(logging.INFO):
            check_discord(FakeRPC(status="no_client"))
        assert "stale" in caplog.text
        assert "discord-ipc-0" in caplog.text
    finally:
        for s in keep:
            s.close()


def test_several_clients_are_reported(runtime_dir, caplog):
    """Discord + Vencord both listening."""
    keep = []
    try:
        _live_socket(runtime_dir / "discord-ipc-0", keep)
        _live_socket(runtime_dir / "discord-ipc-1", keep)
        with caplog.at_level(logging.INFO):
            check_discord(FakeRPC(status="no_client"))
        assert "2 clients listening" in caplog.text
    finally:
        for s in keep:
            s.close()


# ------------------------------------------------------------------ shape
def test_every_log_line_carries_the_marker(runtime_dir, caplog):
    """`grep '[startup-check]'` must find the whole verdict."""
    with caplog.at_level(logging.INFO):
        check_lastfm(FakeLastfmCfg(enabled=False))
        check_discord(FakeRPC(client_id=""))
    lines = [r.getMessage() for r in caplog.records if r.name == "refrain.startup_check"]
    assert lines
    assert all(MARKER in line for line in lines)


def test_worker_never_raises(monkeypatch):
    """A crashing check must not take startup down with it."""
    import refrain.startup_check as sc

    monkeypatch.setattr(
        sc, "check_lastfm", lambda cfg: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    got = []
    worker = sc.StartupCheckWorker(FakeLastfmCfg(), FakeRPC())
    worker.finished.connect(lambda a, b: got.append((a, b)))
    worker.run()
    assert got, "worker must still report after an internal failure"
    assert got[0][0].state == UNREACHABLE
