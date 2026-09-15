"""crash.log: a raw descriptor faulthandler can write to while dying."""

from __future__ import annotations

import faulthandler
import os

import pytest

from refrain import app


@pytest.fixture
def crash_log(xdg_tmp):
    yield xdg_tmp["state"] / "refrain" / "crash.log"
    fd = app._crash_log_fd
    faulthandler.disable()
    if fd is not None:
        os.close(fd)
    app._crash_log_fd = None


def test_each_start_adds_a_line_and_arms_faulthandler(crash_log):
    app._enable_crash_log()
    app._enable_crash_log()
    lines = crash_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(line.startswith("--- Refrain ") for line in lines)
    assert faulthandler.is_enabled()
    assert os.path.samefile(f"/proc/self/fd/{app._crash_log_fd}", crash_log)


def test_a_log_past_its_cap_starts_afresh(crash_log):
    crash_log.parent.mkdir(parents=True)
    crash_log.write_text("x" * (app._CRASH_LOG_MAX_BYTES + 1), encoding="utf-8")
    app._enable_crash_log()
    assert crash_log.read_text(encoding="utf-8").startswith("--- Refrain ")
    assert crash_log.stat().st_size < 200
