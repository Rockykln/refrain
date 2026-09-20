"""Logging: rotating file handler in XDG state dir + console + Qt bridge."""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
from collections import deque

from refrain.paths import log_path, state_dir

_BACKLOG_LINES = 500
_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_qt_bridge = None  # set by attach_qt_log_bridge(), read by the live-log window


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Keeps the log owner-only: it holds file paths and the listening history."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        return open(fd, self.mode, encoding=self.encoding, errors=self.errors)


def setup_logging(level: str = "INFO") -> None:
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    # Console handler attaches unconditionally — even if the file
    # handler can't be created (read-only home, permission issue), the
    # user still gets log output on stderr instead of a hard crash at
    # startup.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    # Coerce to str before .upper() — a hand-edited config with
    # `log_level = false` (boolean) or `log_level = 5` (int) would
    # otherwise AttributeError on .upper() and crash the whole startup
    # before we can log anything about it.
    level_str = str(level) if level is not None else "INFO"
    root.setLevel(getattr(logging, level_str.upper(), logging.INFO))
    root.addHandler(console_handler)

    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        file_handler = _PrivateRotatingFileHandler(
            log_path(),
            maxBytes=1_048_576,
            backupCount=3,
            encoding="utf-8",
        )
        for n in range(1, file_handler.backupCount + 1):
            with contextlib.suppress(OSError):
                os.chmod(f"{file_handler.baseFilename}.{n}", 0o600)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as e:
        # No file log this session — the error itself reaches stderr via
        # the console handler we already attached.
        root.warning("Could not open log file %s: %s — console only", log_path(), e)

    # Silence dbus-python's own loggers — `dbus.proxies` in particular
    # logs every introspection timeout against unrelated MPRIS players
    # (Apple Music's plasma-browser-integration drops to ~25 s replies
    # under load), drowning the live log in tracebacks that have nothing
    # to do with refrain. CRITICAL = effectively off.
    logging.getLogger("dbus.proxies").setLevel(logging.CRITICAL)
    logging.getLogger("dbus.connection").setLevel(logging.CRITICAL)


def attach_qt_log_bridge():
    """Install a Qt-aware log handler. Returns a QObject whose ``log_record``
    signal fires for every log message (queued cross-thread automatically)."""
    global _qt_bridge
    if _qt_bridge is not None:
        return _qt_bridge

    # Imported lazily so non-GUI contexts (tests) don't need PySide6.
    from PySide6.QtCore import QObject, Signal

    class _Bridge(QObject):
        log_record = Signal(str, int)  # formatted_message, level

        def __init__(self):
            super().__init__()
            # The live log is built after startup has already logged; it replays these.
            self.backlog: deque[tuple[str, int]] = deque(maxlen=_BACKLOG_LINES)

    bridge = _Bridge()

    class _QtLogHandler(logging.Handler):
        def __init__(self, owner: _Bridge):
            super().__init__()
            self._owner = owner

        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = self.format(record)
                self._owner.backlog.append((msg, record.levelno))
                self._owner.log_record.emit(msg, record.levelno)
            except Exception:
                self.handleError(record)

    handler = _QtLogHandler(bridge)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    handler.setLevel(logging.DEBUG)  # window does its own filtering
    logging.getLogger().addHandler(handler)

    _qt_bridge = bridge
    return bridge
