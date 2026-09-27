"""Every file Refrain writes: owner-only, atomic, and not redirectable."""

from __future__ import annotations

import os

import pytest

from refrain.paths import make_private_dir, write_private


def test_the_file_is_owner_only_from_the_first_byte(tmp_path):
    target = tmp_path / "config.toml"
    write_private(target, "a = 1\n")
    assert target.read_text() == "a = 1\n"
    assert target.stat().st_mode & 0o777 == 0o600


def test_bytes_go_through_unchanged(tmp_path):
    target = tmp_path / "cover.jpg"
    write_private(target, b"\xff\xd8\xff")
    assert target.read_bytes() == b"\xff\xd8\xff"


def test_a_symlink_left_as_the_temp_file_is_not_followed(tmp_path):
    """The temp name sits next to the target and is easy to guess."""
    target, victim = tmp_path / "history.json", tmp_path / "victim"
    victim.write_text("untouched")
    os.symlink(victim, tmp_path / "history.json.tmp")
    write_private(target, "new")
    assert victim.read_text() == "untouched"
    assert target.read_text() == "new"
    assert not target.is_symlink()


def test_a_symlink_as_the_target_is_replaced_not_written_through(tmp_path):
    target, victim = tmp_path / "history.json", tmp_path / "victim"
    victim.write_text("untouched")
    os.symlink(victim, target)
    write_private(target, "new")
    assert victim.read_text() == "untouched"
    assert not target.is_symlink()


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    import refrain.paths as paths_module

    def _boom(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(paths_module.os, "replace", _boom)
    with pytest.raises(OSError):
        write_private(tmp_path / "x.json", "data")
    assert list(tmp_path.iterdir()) == []


def test_the_directory_is_owner_only_whatever_the_umask(tmp_path):
    old = os.umask(0o000)
    try:
        d = make_private_dir(tmp_path / "deep" / "state")
    finally:
        os.umask(old)
    assert d.stat().st_mode & 0o777 == 0o700


def test_a_directory_an_older_version_left_open_is_narrowed(tmp_path):
    d = tmp_path / "state"
    d.mkdir(mode=0o755)
    assert make_private_dir(d).stat().st_mode & 0o777 == 0o700


def test_a_directory_locked_down_further_keeps_its_own_mode(tmp_path):
    """Narrowing only. Re-opening it would let a write land where the
    owner decided nothing may be written."""
    d = tmp_path / "state"
    d.mkdir(mode=0o500)
    try:
        assert make_private_dir(d).stat().st_mode & 0o777 == 0o500
    finally:
        d.chmod(0o700)


def test_a_write_that_dies_mid_file_leaves_nothing_behind(tmp_path, monkeypatch):
    """A half-written temp file inherits the target's name on the next
    attempt, so it must not survive the failure that produced it."""
    import refrain.paths as paths_module

    opened = []
    real_open, real_close = os.open, os.close

    def _remember(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def _boom(*_a, **_k):
        raise OSError("no space left on device")

    monkeypatch.setattr(paths_module.os, "open", _remember)

    monkeypatch.setattr(paths_module.os, "write", _boom)
    with pytest.raises(OSError, match="no space"):
        write_private(tmp_path / "history.json", "data")
    assert list(tmp_path.iterdir()) == []
    # The descriptor is closed on the way out, not leaked for the session.
    with pytest.raises(OSError):
        real_close(opened[0])
