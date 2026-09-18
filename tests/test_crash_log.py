"""crash.log: kept open for faulthandler while Refrain runs, closed after."""

from __future__ import annotations

import faulthandler
import os
import sys

import pytest

from refrain import app


@pytest.fixture
def crash_log(xdg_tmp):
    # pytest arms faulthandler for itself; hand it back afterwards.
    was_enabled = faulthandler.is_enabled()
    yield xdg_tmp["state"] / "refrain" / "crash.log"
    if was_enabled:
        faulthandler.enable(file=sys.stderr, all_threads=True)


def _descriptors_on(path) -> list[str]:
    return [
        fd
        for fd in os.listdir("/proc/self/fd")
        if os.path.realpath(f"/proc/self/fd/{fd}") == os.path.realpath(path)
    ]


def test_each_start_adds_a_line_and_arms_faulthandler(crash_log):
    for _ in range(2):
        with app._crash_log():
            assert faulthandler.is_enabled()
            assert len(_descriptors_on(crash_log)) == 1
    lines = crash_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(line.startswith("--- Refrain ") for line in lines)


def test_it_is_closed_and_disarmed_on_the_way_out(crash_log):
    with app._crash_log():
        pass
    assert not faulthandler.is_enabled()
    assert _descriptors_on(crash_log) == []


def test_the_log_is_owner_only_even_if_it_was_not(crash_log):
    # Stacks carry paths and song titles, so an existing world-readable log
    # is tightened too.
    crash_log.parent.mkdir(parents=True)
    crash_log.write_text("--- old\n", encoding="utf-8")
    crash_log.chmod(0o644)
    with app._crash_log():
        pass
    assert crash_log.stat().st_mode & 0o777 == 0o600


def test_a_log_past_its_cap_starts_afresh(crash_log):
    crash_log.parent.mkdir(parents=True)
    crash_log.write_text("x" * (app._CRASH_LOG_MAX_BYTES + 1), encoding="utf-8")
    with app._crash_log():
        pass
    assert crash_log.read_text(encoding="utf-8").startswith("--- Refrain ")
    assert crash_log.stat().st_size < 200


def test_an_unwritable_state_dir_just_runs_without_it(crash_log, monkeypatch):
    def _refuse(*_a, **_k):
        raise PermissionError("read-only")

    monkeypatch.setattr(app, "_open_crash_log", _refuse)
    ran = False
    with app._crash_log():
        ran = True
    assert ran
