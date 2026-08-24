"""A stale Discord IPC socket must not hide a running client.

Reported symptom: Refrain sometimes failed to connect when Discord was
already running before it started, and misbehaved with Discord and
Vencord open at the same time.

Cause: pypresence's ``get_ipc_path`` probes candidates with
``test_ipc_path``, which calls ``socket.connect()`` with no exception
handling. The first dead ``discord-ipc-N`` it touches raises straight out
of the scan, so a live socket behind it is never tried — and the order
comes from ``os.scandir``, i.e. the filesystem. Discord leaves sockets
behind when it exits, and running two clients means more sockets to trip
over, which is why it failed only *sometimes*.

``_scan_ipc_pipes`` does the probing itself, skips what does not answer,
and hands the proven slot to ``Presence(pipe=...)``.
"""

from __future__ import annotations

import socket

import pytest


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def _dead_socket(path):
    """A socket file nobody is listening on — what Discord leaves behind."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.close()  # file stays, no listener
    assert path.is_socket()


def _live_socket(path, keep):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)
    keep.append(srv)
    return srv


def test_live_socket_is_found_behind_stale_ones(runtime_dir):
    from refrain.discord_rpc import _scan_ipc_pipes

    keep = []
    try:
        for n in (0, 1, 2):
            _dead_socket(runtime_dir / f"discord-ipc-{n}")
        _live_socket(runtime_dir / "discord-ipc-3", keep)

        live, stale = _scan_ipc_pipes()

        assert live == [3], "the running client behind three stale sockets was missed"
        assert stale == [0, 1, 2]
    finally:
        for s in keep:
            s.close()


def test_pypresence_alone_aborts_on_the_stale_socket(runtime_dir):
    """Why we do not simply let pypresence find the socket."""
    pytest.importorskip("pypresence")
    from pypresence.utils import get_ipc_path

    keep = []
    try:
        _dead_socket(runtime_dir / "discord-ipc-0")
        _live_socket(runtime_dir / "discord-ipc-1", keep)

        try:
            found = get_ipc_path()
        except OSError:
            return  # aborted on the stale socket — the bug we work around
        assert found is None or found.endswith("discord-ipc-1")
    finally:
        for s in keep:
            s.close()


def test_several_live_clients_are_all_reported(runtime_dir):
    """Discord + Vencord: both are listening, and we say so."""
    from refrain.discord_rpc import _scan_ipc_pipes

    keep = []
    try:
        _live_socket(runtime_dir / "discord-ipc-0", keep)
        _live_socket(runtime_dir / "discord-ipc-2", keep)
        _dead_socket(runtime_dir / "discord-ipc-1")

        live, stale = _scan_ipc_pipes()

        assert live == [0, 2]
        assert stale == [1]
    finally:
        for s in keep:
            s.close()


def test_slots_are_scanned_in_order(runtime_dir):
    """Lowest live slot wins, independent of filesystem order."""
    from refrain.discord_rpc import _scan_ipc_pipes

    keep = []
    try:
        for n in (7, 4, 9):
            _live_socket(runtime_dir / f"discord-ipc-{n}", keep)

        live, _ = _scan_ipc_pipes()

        assert live == [4, 7, 9]
    finally:
        for s in keep:
            s.close()


def test_no_sockets_is_not_an_error(runtime_dir):
    from refrain.discord_rpc import _scan_ipc_pipes

    assert _scan_ipc_pipes() == ([], [])


def test_non_socket_files_are_ignored(runtime_dir):
    """A regular file named like a socket must not be probed."""
    from refrain.discord_rpc import _scan_ipc_pipes

    (runtime_dir / "discord-ipc-0").write_text("not a socket")

    assert _scan_ipc_pipes() == ([], [])


def test_missing_runtime_dir_is_not_an_error(tmp_path, monkeypatch):
    from refrain.discord_rpc import _scan_ipc_pipes

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "does-not-exist"))

    assert _scan_ipc_pipes() == ([], [])
