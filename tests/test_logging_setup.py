"""setup_logging: handlers, levels, rotation, and the live-log bridge."""

from __future__ import annotations

import logging
import logging.handlers
import sys

import pytest

import refrain.logging_setup as ls
from refrain.paths import log_path

_QUIET = ("dbus.proxies", "dbus.connection")


@pytest.fixture(autouse=True)
def restore_logging(monkeypatch, xdg_tmp):
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    quiet = {name: logging.getLogger(name).level for name in _QUIET}
    monkeypatch.setattr(ls, "_qt_bridge", None)
    yield
    for h in root.handlers:
        if h not in handlers:
            h.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, lvl in quiet.items():
        logging.getLogger(name).setLevel(lvl)


def _file_handler():
    [h] = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
    return h


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("warning", logging.WARNING),
        ("nonsense", logging.INFO),
        (None, logging.INFO),
        (5, logging.INFO),
        (False, logging.INFO),
    ],
)
def test_the_configured_level_applies_and_junk_falls_back_to_info(level, expected):
    ls.setup_logging(level)
    assert logging.getLogger().level == expected


def test_console_and_rotating_file_handlers_are_installed():
    ls.setup_logging("INFO")
    handlers = logging.getLogger().handlers
    [console] = [h for h in handlers if type(h) is logging.StreamHandler]
    assert console.stream is sys.stderr
    fh = _file_handler()
    assert isinstance(fh, logging.handlers.RotatingFileHandler)
    assert fh.baseFilename == str(log_path())
    assert (fh.maxBytes, fh.backupCount) == (1_048_576, 3)
    assert len(handlers) == 2


def test_a_second_setup_replaces_the_handlers():
    ls.setup_logging("INFO")
    ls.setup_logging("DEBUG")
    assert len(logging.getLogger().handlers) == 2


def test_messages_reach_the_log_file_formatted():
    ls.setup_logging("INFO")
    logging.getLogger("refrain.test").info("Now playing: %s", "Paper Satellites")
    logging.getLogger("refrain.test").debug("below the level")
    _file_handler().flush()
    lines = log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].endswith("[INFO] refrain.test: Now playing: Paper Satellites")


def test_a_full_log_rotates_into_a_backup():
    ls.setup_logging("INFO")
    logger = logging.getLogger("refrain.test")
    logger.info("x" * 1_100_000)
    logger.info("after rotation")
    _file_handler().flush()
    assert log_path().with_name("refrain.log.1").stat().st_size > 1_000_000
    assert "after rotation" in log_path().read_text(encoding="utf-8")


def test_dbus_python_loggers_are_silenced():
    ls.setup_logging("DEBUG")
    for name in _QUIET:
        assert logging.getLogger(name).level == logging.CRITICAL


def test_an_unwritable_state_dir_leaves_console_logging(monkeypatch, capsys):
    class _Dir:
        def mkdir(self, **_kw):
            raise PermissionError("read-only file system")

    monkeypatch.setattr(ls, "state_dir", lambda: _Dir())
    ls.setup_logging("INFO")
    handlers = logging.getLogger().handlers
    assert [type(h) for h in handlers] == [logging.StreamHandler]
    assert "console only" in capsys.readouterr().err


def test_the_qt_bridge_forwards_every_record():
    pytest.importorskip("PySide6")
    logging.getLogger().setLevel(logging.DEBUG)
    bridge = ls.attach_qt_log_bridge()
    got = []
    bridge.log_record.connect(lambda msg, level: got.append((msg, level)))
    logging.getLogger("refrain.test").debug("Cover found for %s", "Low Tide")
    [(msg, level)] = got
    assert level == logging.DEBUG
    assert msg.endswith("[DEBUG] refrain.test: Cover found for Low Tide")
    assert list(bridge.backlog)[-1] == (msg, level)


def test_the_qt_bridge_is_installed_only_once():
    pytest.importorskip("PySide6")
    before = len(logging.getLogger().handlers)
    bridge = ls.attach_qt_log_bridge()
    assert ls.attach_qt_log_bridge() is bridge
    assert len(logging.getLogger().handlers) == before + 1
