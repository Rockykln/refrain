"""Logging: rotating file handler in XDG state dir + console + Qt bridge."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from refrain.paths import log_path, state_dir

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_qt_bridge = None  # set by attach_qt_log_bridge(), read by the live-log window


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
        file_handler = logging.handlers.RotatingFileHandler(
            log_path(),
            maxBytes=1_048_576,
            backupCount=3,
            encoding="utf-8",
        )
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

    bridge = _Bridge()

    class _QtLogHandler(logging.Handler):
        def __init__(self, owner: _Bridge):
            super().__init__()
            self._owner = owner

        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = self.format(record)
                self._owner.log_record.emit(msg, record.levelno)
            except Exception:
                self.handleError(record)

    handler = _QtLogHandler(bridge)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    handler.setLevel(logging.DEBUG)  # window does its own filtering
    logging.getLogger().addHandler(handler)

    _qt_bridge = bridge
    return bridge
