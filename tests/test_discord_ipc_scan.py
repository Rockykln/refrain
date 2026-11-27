"""A stale Discord IPC socket must not hide a running client.
pypresence's scan stops at the first dead ``discord-ipc-N``; ``_scan_ipc_pipes`` skips it."""

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
    """Two Discord apps are listening, and we say so."""
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


# What the debug log says about the slots, so a report weeks later can tell
# "no Discord at all" from "a client that left no socket".


def test_the_slot_line_names_every_file_and_its_state(runtime_dir):
    from refrain.discord_rpc import _describe_ipc_slots, _scan_ipc_pipes

    keep = []
    try:
        _dead_socket(runtime_dir / "discord-ipc-0")
        _live_socket(runtime_dir / "discord-ipc-1", keep)
        (runtime_dir / "discord-ipc-2").write_text("not a socket")

        line = _describe_ipc_slots(*_scan_ipc_pipes())

        assert "0: dead" in line
        assert "1: answers" in line
        assert "2: not a socket" in line
        assert "made 2" in line
    finally:
        for s in keep:
            s.close()


def test_the_slot_line_says_so_when_there_is_nothing(runtime_dir):
    from refrain.discord_rpc import _describe_ipc_slots

    assert _describe_ipc_slots([], []) == "none in XDG_RUNTIME_DIR"


def test_the_slot_line_marks_a_bridged_socket(runtime_dir, tmp_path):
    """A Snap/Flatpak socket reaches the standard path as a symlink."""
    from refrain.discord_rpc import _describe_ipc_slots, _scan_ipc_pipes

    keep = []
    try:
        real = tmp_path / "sandbox-ipc"
        real.parent.mkdir(parents=True, exist_ok=True)
        _live_socket(real, keep)
        (runtime_dir / "discord-ipc-0").symlink_to(real)

        line = _describe_ipc_slots(*_scan_ipc_pipes())

        assert "0: symlink, answers" in line
    finally:
        for s in keep:
            s.close()


def test_the_slot_line_survives_a_missing_runtime_dir(monkeypatch):
    from refrain import discord_rpc

    monkeypatch.setattr(discord_rpc, "_runtime_dir", lambda: None)
    assert discord_rpc._describe_ipc_slots([], []) == "no XDG_RUNTIME_DIR"
