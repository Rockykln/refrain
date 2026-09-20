"""A broken log record must not crash the live-log bridge or silence later messages."""

from __future__ import annotations

import logging

import pytest

import refrain.logging_setup as ls


@pytest.fixture(autouse=True)
def _restore_bridge(monkeypatch):
    monkeypatch.setattr(ls, "_qt_bridge", None)
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if h.__class__.__name__ == "_QtLogHandler":
            root.removeHandler(h)
            h.close()


def test_a_malformed_log_record_does_not_crash_the_bridge_or_lose_later_messages():
    pytest.importorskip("PySide6")
    logging.getLogger().setLevel(logging.DEBUG)
    bridge = ls.attach_qt_log_bridge()
    got = []
    bridge.log_record.connect(lambda msg, level: got.append((msg, level)))
    handler = next(
        h for h in logging.getLogger().handlers if h.__class__.__name__ == "_QtLogHandler"
    )

    # Handed to the handler directly: pytest's own handler re-raises such a record.
    broken = logging.LogRecord(
        "refrain.test", logging.DEBUG, __file__, 1, "broken: %d", ("x",), None
    )
    handler.emit(broken)
    logging.getLogger("refrain.test").debug("still logging afterwards")

    assert not any("broken" in msg for msg, _level in got), "the broken record never reached it"
    assert any(msg.endswith("still logging afterwards") for msg, _level in got)
    assert any(msg.endswith("still logging afterwards") for msg, _level in bridge.backlog)


def test_a_log_file_that_cannot_be_locked_down_leaves_no_descriptor_open(tmp_path, monkeypatch):
    handler = ls._PrivateRotatingFileHandler.__new__(ls._PrivateRotatingFileHandler)
    handler.baseFilename = str(tmp_path / "refrain.log")
    handler.mode = "a"
    handler.encoding = "utf-8"
    handler.errors = None
    closed = []
    monkeypatch.setattr(ls.os, "fchmod", lambda *_: (_ for _ in ()).throw(OSError("nope")))
    real_close = ls.os.close
    monkeypatch.setattr(ls.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])

    with pytest.raises(OSError):
        handler._open()

    assert len(closed) == 1
