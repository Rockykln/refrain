"""Developer mode: timings, resource use and UI interaction counts, kept on this computer only.

Everything here is inert until ``set_enabled(True)``: the helpers return shared
no-op objects, and no timer, event filter or file exists.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import threading
import time
from collections import Counter, deque
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QMessageBox, QTabBar, QWidget

from refrain import __version__
from refrain.paths import state_dir

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 5 * 1024 * 1024
POLL_WINDOW = 600
RESOURCE_INTERVAL_MS = 60_000
UNUSED_START_WINDOW_S = 10.0
LAYOUT_CHECK_DELAY_MS = 300

STAGES = (
    "source",
    "position",
    "tray",
    "discord",
    "history",
    "scrobble",
    "covers",
    "mpris",
    "total",
)
STARTUP_STEPS = (
    "process_start",
    "config_loaded",
    "qapplication",
    "tray_visible",
    "daemon_started",
    "first_poll",
    "discord_connected",
)


def metrics_path() -> Path:
    return state_dir() / "dev-metrics.jsonl"


def _process_start() -> float:
    """perf_counter() value at the moment the kernel started this process."""
    try:
        with open("/proc/self/stat", encoding="ascii") as f:
            # The command name may contain spaces; fields after it are fixed.
            fields = f.read().rsplit(")", 1)[1].split()
        with open("/proc/uptime", encoding="ascii") as f:
            uptime = float(f.read().split()[0])
        age = uptime - int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return time.perf_counter()
    return time.perf_counter() - max(0.0, age)


def resource_usage() -> dict[str, int | None]:
    rss_kb = threads = None
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                elif line.startswith("Threads:"):
                    threads = int(line.split()[1])
    except (OSError, ValueError):
        pass
    try:
        open_files = len(os.listdir("/proc/self/fd"))
    except OSError:
        open_files = None
    return {"rss_kb": rss_kb, "threads": threads, "open_files": open_files}


def summarize(values) -> dict[str, float | int]:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return {"n": 0}
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    p95 = ordered[max(0, math.ceil(0.95 * n) - 1)]
    return {
        "n": n,
        "median_ms": round(median, 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(ordered[-1], 3),
    }


class _NullClock:
    __slots__ = ()

    def lap(self, stage: str) -> None:
        pass

    def done(self) -> None:
        pass


NULL_CLOCK = _NullClock()
_OFF = contextlib.nullcontext()


class _PollClock:
    """Splits one poll into stages; each lap is charged the time since the previous one."""

    __slots__ = ("_last", "_laps", "_rec", "_start")

    def __init__(self, rec: Recorder):
        self._rec = rec
        self._start = self._last = time.perf_counter()
        self._laps: dict[str, float] = {}

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        self._laps[stage] = self._laps.get(stage, 0.0) + (now - self._last) * 1000
        self._last = now

    def done(self) -> None:
        self._laps["total"] = (time.perf_counter() - self._start) * 1000
        self._rec.add_poll(self._laps)


class Recorder:
    def __init__(self, path: Path | None = None):
        self.path = path or metrics_path()
        self._lock = threading.Lock()
        self._origin = _process_start()
        self.startup: dict[str, float] = {"process_start": 0.0}
        self.polls: dict[str, deque[float]] = {}
        self.last_poll: dict[str, float] = {}
        self.network: dict[str, dict] = {}
        self.builds: dict[str, float] = {}
        self.counters: Counter[str] = Counter()
        self.layout: Counter[str] = Counter()
        self.resources: dict[str, int | None] = {}
        self._qt = None
        # True when developer mode was switched on after the app had already
        # started (see set_enabled/qt_ready) — the early startup marks then
        # never landed, and whatever *did* land looks like it took ages.
        self.late_start = False

    def since_start_ms(self) -> float:
        return (time.perf_counter() - self._origin) * 1000

    def write(self, record: dict) -> None:
        line = json.dumps({"ts": round(time.time(), 3), **record}, separators=(",", ":"))
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(FileNotFoundError):
                    if self.path.stat().st_size >= MAX_FILE_BYTES:
                        os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, (line + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
            except OSError:
                # Metrics must never get in the way of the app itself.
                pass

    def mark(self, step: str) -> None:
        with self._lock:
            if step in self.startup:
                return
            ms = round(self.since_start_ms(), 1)
            self.startup[step] = ms
        self.write({"type": "startup", "step": step, "ms": ms})

    def add_poll(self, laps: dict[str, float]) -> None:
        with self._lock:
            for stage, ms in laps.items():
                bucket = self.polls.get(stage)
                if bucket is None:
                    bucket = self.polls[stage] = deque(maxlen=POLL_WINDOW)
                bucket.append(ms)
            self.last_poll = dict(laps)
            first = "first_poll" not in self.startup
        if first:
            self.mark("first_poll")

    @contextlib.contextmanager
    def measure_network(self, service: str):
        start = time.perf_counter()
        error = None
        try:
            yield
        except BaseException as e:
            error = type(e).__name__
            raise
        finally:
            ms = round((time.perf_counter() - start) * 1000, 1)
            with self._lock:
                entry = self.network.setdefault(
                    service, {"count": 0, "failures": 0, "last_ms": 0.0, "max_ms": 0.0}
                )
                entry["count"] += 1
                entry["failures"] += error is not None
                entry["last_ms"] = ms
                entry["max_ms"] = max(entry["max_ms"], ms)
            record = {"type": "network", "service": service, "ms": ms, "ok": error is None}
            if error:
                record["error"] = error
            self.write(record)

    @contextlib.contextmanager
    def measure_build(self, window: str):
        start = time.perf_counter()
        yield
        ms = round((time.perf_counter() - start) * 1000, 1)
        with self._lock:
            self.builds[window] = ms
        self.write({"type": "window_build", "window": window, "ms": ms})

    def interaction(self, kind: str, **fields) -> None:
        key = kind if "target" not in fields else f"{kind} {fields['target']}"
        with self._lock:
            self.counters[key] += 1
        self.write({"type": "interaction", "kind": kind, **fields})

    def layout_findings(self, window: str, messages: list[str]) -> None:
        """Warn about each overflow once; later sightings only count."""
        with self._lock:
            new = [m for m in messages if m not in self.layout]
            self.layout.update(messages)
        for message in new:
            log.warning("%s", message)
            self.write({"type": "layout", "window": window, "message": message})

    def sample(self) -> None:
        usage = resource_usage()
        with self._lock:
            self.resources = usage
            stages = {stage: summarize(values) for stage, values in self.polls.items()}
        self.write({"type": "resources", **usage})
        if stages:
            self.write({"type": "polls", "stages": stages})

    def snapshot(self) -> dict:
        with self._lock:
            polls = {}
            for stage, values in self.polls.items():
                polls[stage] = {"last_ms": round(self.last_poll.get(stage, 0.0), 3)}
                polls[stage].update(summarize(values))
            return {
                "version": __version__,
                "file": str(self.path),
                "uptime_s": round(self.since_start_ms() / 1000, 1),
                "startup_ms": dict(self.startup),
                "late_start": self.late_start,
                "polls": polls,
                "network": {k: dict(v) for k, v in self.network.items()},
                "window_build_ms": dict(self.builds),
                "resources": dict(self.resources),
                "interactions": dict(self.counters),
                "layout": dict(self.layout),
            }


_recorder: Recorder | None = None


def enabled() -> bool:
    return _recorder is not None


def recorder() -> Recorder | None:
    return _recorder


def set_enabled(on: bool, path: Path | None = None) -> None:
    global _recorder
    if on == (_recorder is not None):
        return
    if on:
        rec = Recorder(path)
        # A QApplication already running means developer mode is being
        # switched on mid-session, not at process start — qt_ready() (called
        # once, right after the QApplication is built) clears this again
        # when it turns out we got here before that point after all.
        rec.late_start = QApplication.instance() is not None
        _recorder = rec
        rec.write({"type": "session", "state": "on", "version": __version__, "pid": os.getpid()})
        _attach_qt(rec, at_startup=False)
        return
    rec, _recorder = _recorder, None
    if rec._qt is not None:
        rec._qt.detach()
    rec.write({"type": "session", "state": "off"})


def qt_ready() -> None:
    """Called once the QApplication exists: marks it and starts the Qt-side collectors."""
    rec = _recorder
    if rec is None:
        return
    rec.mark("qapplication")
    rec.late_start = False
    _attach_qt(rec, at_startup=True)


def _attach_qt(rec: Recorder, at_startup: bool) -> None:
    app = QApplication.instance()
    if rec._qt is not None or not isinstance(app, QApplication):
        return
    rec._qt = QtCollector(rec, app, at_startup)


_WATCHED = frozenset(
    {
        QEvent.Type.Show,
        QEvent.Type.Hide,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.KeyPress,
        QEvent.Type.Resize,
        QEvent.Type.LanguageChange,
    }
)
_CANCEL_ROLES = frozenset({QMessageBox.ButtonRole.RejectRole, QMessageBox.ButtonRole.NoRole})


def widget_id(widget: QWidget) -> str:
    """A stable name for a widget: never its text, which may be translated or typed in."""
    if widget.objectName():
        return widget.objectName()
    window = widget.window()
    for name, value in vars(window).items():
        if value is widget:
            return name.lstrip("_")
    same = [w for w in window.findChildren(type(widget)) if type(w) is type(widget)]
    return f"{type(widget).__name__}#{same.index(widget) if widget in same else -1}"


class _OpenWindow:
    __slots__ = ("applied", "opened_at", "used")

    def __init__(self) -> None:
        self.opened_at = time.monotonic()
        self.used = False
        self.applied = False


class QtCollector(QObject):
    """Resource sampling and window/click/tab counts, attached only while developer mode is on."""

    def __init__(self, rec: Recorder, app: QApplication, at_startup: bool):
        super().__init__()
        self._rec = rec
        self._app = app
        self._open: dict[int, _OpenWindow] = {}
        self._start_window_pending = at_startup
        self._start_window: int | None = None
        self._first_click_pending = at_startup
        self._timer = QTimer(self)
        self._timer.setInterval(RESOURCE_INTERVAL_MS)
        self._timer.timeout.connect(rec.sample)
        self._timer.start()
        self._layout_pending: dict[int, QWidget] = {}
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(LAYOUT_CHECK_DELAY_MS)
        self._layout_timer.timeout.connect(self._check_layouts)
        app.installEventFilter(self)
        rec.sample()

    def detach(self) -> None:
        self._timer.stop()
        self._layout_timer.stop()
        self._layout_pending.clear()
        self._app.removeEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if kind not in _WATCHED or not isinstance(obj, QWidget):
            return False
        if kind in (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.LanguageChange):
            if kind == QEvent.Type.Show:
                self._on_show(obj)
            self._queue_layout_check(obj.window())
        elif kind == QEvent.Type.Hide:
            self._on_hide(obj)
        elif kind == QEvent.Type.KeyPress:
            self._mark_used(obj)
        elif kind == QEvent.Type.MouseButtonPress:
            if isinstance(obj, QTabBar) and event.button() == Qt.MouseButton.LeftButton:
                self._on_tab_press(obj, obj.tabAt(event.position().toPoint()))
        elif isinstance(obj, QAbstractButton) and event.button() == Qt.MouseButton.LeftButton:
            self.click(obj.window(), widget_id(obj))
        return False

    @staticmethod
    def _is_window(widget: QWidget) -> bool:
        return widget.isWindow() and widget.windowType() in (
            Qt.WindowType.Window,
            Qt.WindowType.Dialog,
        )

    def _on_show(self, widget: QWidget) -> None:
        if not self._is_window(widget) or id(widget) in self._open:
            return
        self._open[id(widget)] = _OpenWindow()
        if self._start_window_pending:
            self._start_window_pending = False
            self._start_window = id(widget)
        self._rec.interaction("window_opened", target=type(widget).__name__)

    def _queue_layout_check(self, window: QWidget) -> None:
        if id(window) in self._open:
            self._layout_pending[id(window)] = window
            self._layout_timer.start()

    def _check_layouts(self) -> None:
        from refrain.ui.layout_check import check_layout

        pending, self._layout_pending = self._layout_pending, {}
        for key, window in pending.items():
            if key in self._open and window.isVisible():
                findings = check_layout(window)
                self._rec.layout_findings(type(window).__name__, [f.message() for f in findings])

    def _on_hide(self, widget: QWidget) -> None:
        self._layout_pending.pop(id(widget), None)
        state = self._open.pop(id(widget), None)
        if state is None:
            return
        name = type(widget).__name__
        open_s = round(time.monotonic() - state.opened_at, 2)
        self._rec.interaction("window_closed", target=name, open_s=open_s)
        if isinstance(widget, QMessageBox):
            clicked = widget.clickedButton()
            cancelled = clicked is None or widget.buttonRole(clicked) in _CANCEL_ROLES
        elif isinstance(widget, QDialog):
            cancelled = widget.result() == QDialog.DialogCode.Rejected and not state.applied
        else:
            cancelled = False
        if cancelled:
            self._rec.interaction("dialog_cancelled", target=name, open_s=open_s)
        if id(widget) == self._start_window:
            self._start_window = None
            self._rec.interaction(
                "start_window_closed",
                target=name,
                open_s=open_s,
                used=state.used,
                closed_unused_quickly=not state.used and open_s < UNUSED_START_WINDOW_S,
            )

    def _mark_used(self, widget: QWidget | None) -> None:
        if widget is not None:
            state = self._open.get(id(widget.window()))
            if state is not None:
                state.used = True

    def _on_tab_press(self, bar: QTabBar, index: int) -> None:
        current = bar.currentIndex()
        if index < 0 or index == current:
            return
        window = bar.window()
        self._mark_used(window)
        self._first_click(f"{type(window).__name__}/tab{index}")
        self._rec.interaction(
            "tab_switched", target=f"{type(window).__name__}/tab{index}", back=index < current
        )

    def _first_click(self, target: str) -> None:
        if self._first_click_pending:
            self._first_click_pending = False
            self._rec.interaction(
                "first_click",
                target=target,
                s_since_start=round(self._rec.since_start_ms() / 1000, 2),
            )

    def click(self, window: QWidget | None, name: str) -> None:
        self._mark_used(window)
        target = f"{type(window).__name__ if window is not None else 'tray'}/{name}"
        self._first_click(target)
        self._rec.interaction("click", target=target)

    def applied(self, widget: QWidget) -> None:
        state = self._open.get(id(widget))
        if state is None:
            return
        state.applied = True
        self._rec.interaction(
            "applied",
            target=type(widget).__name__,
            s_since_open=round(time.monotonic() - state.opened_at, 2),
        )


def mark(step: str) -> None:
    rec = _recorder
    if rec is not None:
        rec.mark(step)


def poll_clock():
    rec = _recorder
    return NULL_CLOCK if rec is None else _PollClock(rec)


def network(service: str):
    rec = _recorder
    return _OFF if rec is None else rec.measure_network(service)


def build(window: str):
    rec = _recorder
    return _OFF if rec is None else rec.measure_build(window)


def interaction(kind: str, **fields) -> None:
    rec = _recorder
    if rec is not None:
        rec.interaction(kind, **fields)


def window_applied(widget) -> None:
    rec = _recorder
    if rec is not None and rec._qt is not None:
        rec._qt.applied(widget)


def tray_action(name: str) -> None:
    rec = _recorder
    if rec is not None and rec._qt is not None:
        rec._qt.click(None, name)


def snapshot() -> dict:
    rec = _recorder
    return {} if rec is None else rec.snapshot()


def export(path: Path) -> None:
    data = snapshot()
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
