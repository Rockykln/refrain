"""Developer-mode metrics: nothing when off, local records only when on."""

from __future__ import annotations

import ast
import json
import os
import socket
import stat
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from refrain import dev_metrics  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from tests.daemon_fakes import Player, install, make_config  # noqa: E402


@pytest.fixture(autouse=True)
def _off_afterwards():
    QApplication.instance() or QApplication(sys.argv)
    yield
    dev_metrics.set_enabled(False)


def _records(path: Path | None = None) -> list[dict]:
    path = path or dev_metrics.metrics_path()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _kinds(kind: str) -> list[dict]:
    return [r for r in _records() if r["type"] == "interaction" and r["kind"] == kind]


def test_off_is_a_no_op_and_writes_nothing():
    clock = dev_metrics.poll_clock()
    assert clock is dev_metrics.NULL_CLOCK
    clock.lap("source")
    clock.done()
    with dev_metrics.network("itunes"), dev_metrics.build("settings"):
        pass
    dev_metrics.mark("first_poll")
    dev_metrics.interaction("click", target="x")
    dev_metrics.window_applied(QWidget())
    dev_metrics.tray_action("settings")
    dev_metrics.qt_ready()
    dev_metrics.set_enabled(False)
    assert not dev_metrics.enabled()
    assert dev_metrics.recorder() is None
    assert dev_metrics.snapshot() == {}
    assert not dev_metrics.metrics_path().exists()


def test_on_writes_a_private_file_with_every_record_type():
    dev_metrics.set_enabled(True)
    dev_metrics.set_enabled(True)
    dev_metrics.qt_ready()
    dev_metrics.mark("config_loaded")
    clock = dev_metrics.poll_clock()
    clock.lap("source")
    clock.lap("position")
    clock.done()
    with dev_metrics.network("lastfm"):
        pass
    with dev_metrics.build("history"):
        pass
    dev_metrics.recorder().sample()
    path = dev_metrics.metrics_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    types = {r["type"] for r in _records()}
    assert types == {
        "session",
        "startup",
        "network",
        "window_build",
        "resources",
        "polls",
    }
    steps = [r["step"] for r in _records() if r["type"] == "startup"]
    assert steps == ["qapplication", "config_loaded", "first_poll"]
    snap = dev_metrics.snapshot()
    assert snap["polls"]["total"]["n"] == 1
    assert snap["network"]["lastfm"] == {
        "count": 1,
        "failures": 0,
        "last_ms": snap["network"]["lastfm"]["last_ms"],
        "max_ms": snap["network"]["lastfm"]["max_ms"],
    }
    assert "history" in snap["window_build_ms"]
    dev_metrics.set_enabled(False)
    assert _records()[-1]["state"] == "off"


def test_a_failed_network_call_counts_the_error_class_only():
    dev_metrics.set_enabled(True)
    with pytest.raises(TimeoutError), dev_metrics.network("github"):
        raise TimeoutError("https://api.example/secret?q=Glass+Tides")
    record = [r for r in _records() if r["type"] == "network"][0]
    assert record == {
        "ts": record["ts"],
        "type": "network",
        "service": "github",
        "ms": record["ms"],
        "ok": False,
        "error": "TimeoutError",
    }
    assert dev_metrics.snapshot()["network"]["github"]["failures"] == 1


def test_the_file_is_rotated_once_it_is_full(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_metrics, "MAX_FILE_BYTES", 200)
    path = tmp_path / "dev-metrics.jsonl"
    rec = dev_metrics.Recorder(path)
    for n in range(10):
        rec.interaction("click", target=f"button{n}")
    rotated = tmp_path / "dev-metrics.jsonl.1"
    assert rotated.exists()
    assert path.stat().st_size < 200 + 100
    assert len(_records(path)) + len(_records(rotated)) < 10


def test_an_unwritable_file_is_ignored(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    rec = dev_metrics.Recorder(blocker / "dev-metrics.jsonl")
    rec.interaction("click", target="apply_btn")
    assert rec.counters["click apply_btn"] == 1


def test_summaries():
    assert dev_metrics.summarize([]) == {"n": 0}
    assert dev_metrics.summarize([3.0, 1.0, 2.0]) == {
        "n": 3,
        "median_ms": 2.0,
        "p95_ms": 3.0,
        "max_ms": 3.0,
    }
    assert dev_metrics.summarize([1.0, 2.0, 3.0, 4.0])["median_ms"] == 2.5
    assert dev_metrics.summarize(range(1, 101))["p95_ms"] == 95


def test_process_start_and_resources_survive_a_missing_proc(monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("no /proc")

    assert dev_metrics._process_start() <= dev_metrics.time.perf_counter()
    usage = dev_metrics.resource_usage()
    assert usage["rss_kb"] > 0 and usage["threads"] >= 1 and usage["open_files"] > 0
    monkeypatch.setattr(dev_metrics, "open", broken, raising=False)
    monkeypatch.setattr(dev_metrics.os, "listdir", broken)
    assert dev_metrics.resource_usage() == {"rss_kb": None, "threads": None, "open_files": None}
    before = dev_metrics.time.perf_counter()
    assert dev_metrics._process_start() >= before


def test_marks_are_kept_once():
    dev_metrics.set_enabled(True)
    dev_metrics.mark("tray_visible")
    first = dev_metrics.snapshot()["startup_ms"]["tray_visible"]
    dev_metrics.mark("tray_visible")
    assert dev_metrics.snapshot()["startup_ms"]["tray_visible"] == first


def test_developer_mode_on_from_the_start_is_not_flagged_as_late():
    """qt_ready() is only reached when developer mode was already on at boot."""
    dev_metrics.set_enabled(True)
    dev_metrics.qt_ready()
    dev_metrics.mark("config_loaded")
    assert dev_metrics.snapshot()["late_start"] is False


def test_developer_mode_switched_on_while_running_is_flagged_as_late():
    """Without qt_ready() first, set_enabled(True) attaches straight to the
    already-running QApplication — the signature of a mid-session toggle."""
    dev_metrics.set_enabled(True)
    assert dev_metrics.snapshot()["late_start"] is True


def test_a_poll_tick_records_stages_but_never_the_song(monkeypatch):
    clock, _ = install(monkeypatch)
    worker = DaemonWorker(make_config())
    player = Player(worker, clock)
    dev_metrics.set_enabled(True)
    player.play(title="Glass Tides", artist="Neon Harbor", album="Night Ferry")
    player.tick(n=3)
    dev_metrics.recorder().sample()
    polls = dev_metrics.snapshot()["polls"]
    for stage in ("source", "position", "tray", "discord", "history", "scrobble", "mpris"):
        assert polls[stage]["n"] == 3
    assert polls["total"]["max_ms"] >= polls["source"]["max_ms"]
    text = dev_metrics.metrics_path().read_text(encoding="utf-8")
    for word in ("Glass Tides", "Neon Harbor", "Night Ferry"):
        assert word not in text
    assert "first_poll" in text and "discord_connected" in text


def test_off_leaves_the_tick_untouched(monkeypatch):
    clock, _ = install(monkeypatch)
    worker = DaemonWorker(make_config())
    Player(worker, clock).tick(n=2)
    assert worker._clock is dev_metrics.NULL_CLOCK
    assert not dev_metrics.metrics_path().exists()


def test_export_writes_the_snapshot(tmp_path):
    dev_metrics.set_enabled(True)
    dev_metrics.interaction("click", target="SettingsWindow/apply_btn")
    out = tmp_path / "export.json"
    dev_metrics.export(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["interactions"] == {"click SettingsWindow/apply_btn": 1}
    assert data["file"] == str(dev_metrics.metrics_path())


def test_the_module_holds_no_network_code():
    source = Path(dev_metrics.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"urllib", "socket", "http", "ssl", "requests", "asyncio", "dbus"}
    assert "QtNetwork" not in source and "urlopen" not in source


def test_nothing_is_sent_while_measuring(monkeypatch, tmp_path):
    opened = []

    class _NoSocket(socket.socket):
        def __init__(self, *args, **kwargs):
            opened.append(args)
            raise AssertionError("developer mode opened a socket")

    monkeypatch.setattr(socket, "socket", _NoSocket)
    monkeypatch.setattr(socket, "create_connection", _NoSocket)
    dev_metrics.set_enabled(True)
    dev_metrics.qt_ready()
    clock = dev_metrics.poll_clock()
    clock.lap("source")
    clock.done()
    dev_metrics.recorder().sample()
    dev_metrics.interaction("click", target="tray/settings")
    dev_metrics.export(tmp_path / "out.json")
    dev_metrics.set_enabled(False)
    assert opened == []


# Qt side --------------------------------------------------------------------


class _Window(QDialog):
    def __init__(self):
        super().__init__()
        self.apply_btn = QPushButton("Apply", self)
        named = QPushButton("Named", self)
        named.setObjectName("named_btn")
        self.named = named
        self.tabs = QTabWidget(self)
        self.tabs.addTab(QWidget(), "One")
        self.tabs.addTab(QWidget(), "Two")
        layout = QVBoxLayout(self)
        for w in (self.apply_btn, named, QPushButton("Loose"), self.tabs):
            layout.addWidget(w)


@pytest.fixture
def collector():
    dev_metrics.set_enabled(True)
    dev_metrics.recorder()._qt.detach()
    dev_metrics.recorder()._qt = None
    dev_metrics.qt_ready()
    return dev_metrics.recorder()._qt


def test_widget_ids_never_use_the_label():
    w = _Window()
    loose = w.findChildren(QPushButton)[2]
    assert dev_metrics.widget_id(w.apply_btn) == "apply_btn"
    assert dev_metrics.widget_id(w.named) == "named_btn"
    assert dev_metrics.widget_id(loose) == "QPushButton#2"
    lone = QPushButton("Alone")
    assert dev_metrics.widget_id(lone) == "QPushButton#-1"


def test_a_start_window_closed_unused_is_noticed(collector):
    w = _Window()
    w.show()
    w.show()
    w.reject()
    closed = _kinds("start_window_closed")[0]
    assert closed["target"] == "_Window"
    assert closed["used"] is False and closed["closed_unused_quickly"] is True
    assert _kinds("dialog_cancelled")[0]["target"] == "_Window"
    assert _kinds("window_closed")[0]["open_s"] >= 0


def test_clicks_tabs_keys_and_the_first_click(collector):
    w = _Window()
    w.show()
    QTest.mouseClick(w.apply_btn, Qt.MouseButton.LeftButton)
    QTest.mouseClick(w.apply_btn, Qt.MouseButton.RightButton)
    QTest.mouseClick(w, Qt.MouseButton.LeftButton)
    bar = w.tabs.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    QTest.mouseClick(bar, Qt.MouseButton.RightButton, pos=bar.tabRect(1).center())
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(bar.width() + 50, 1))
    QTest.keyClick(w, Qt.Key.Key_A)
    dev_metrics.window_applied(w)
    dev_metrics.window_applied(QWidget())
    w.hide()
    assert _kinds("first_click")[0]["target"] == "_Window/apply_btn"
    assert [r["target"] for r in _kinds("click")] == ["_Window/apply_btn"]
    tabs = _kinds("tab_switched")
    assert [(t["target"], t["back"]) for t in tabs] == [
        ("_Window/tab1", False),
        ("_Window/tab0", True),
    ]
    assert _kinds("applied")[0]["target"] == "_Window"
    assert _kinds("dialog_cancelled") == []
    assert _kinds("start_window_closed")[0]["used"] is True


def test_the_first_click_can_be_a_tab_or_the_tray(collector):
    dev_metrics.tray_action("settings_action")
    assert _kinds("first_click")[0]["target"] == "tray/settings_action"
    assert dev_metrics.snapshot()["interactions"]["click tray/settings_action"] == 1


def test_a_tab_can_be_the_first_click(collector):
    w = _Window()
    w.show()
    bar = w.tabs.tabBar()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(1).center())
    assert _kinds("first_click")[0]["target"] == "_Window/tab1"


def test_message_boxes_count_as_cancelled_by_their_button_role(collector):
    box = QMessageBox()
    box.addButton("Go", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("Stop", QMessageBox.ButtonRole.RejectRole)
    box.show()
    cancel.click()
    silent = QMessageBox()
    silent.show()
    silent.hide()
    ok = QMessageBox()
    go = ok.addButton("Go", QMessageBox.ButtonRole.AcceptRole)
    ok.show()
    go.click()
    assert len(_kinds("dialog_cancelled")) == 2


def test_plain_windows_and_children_are_not_dialogs(collector):
    plain = QWidget()
    plain.show()
    child = QPushButton("Child", plain)
    child.show()
    child.hide()
    plain.hide()
    accepted = _Window()
    accepted.show()
    accepted.accept()
    assert [r["target"] for r in _kinds("window_opened")] == ["QWidget", "_Window"]
    assert _kinds("dialog_cancelled") == []


def test_events_for_non_widgets_are_ignored(collector):
    obj = QObject()
    assert collector.eventFilter(obj, QEvent(QEvent.Type.Show)) is False
    assert collector.eventFilter(QWidget(), QEvent(QEvent.Type.Timer)) is False
    QTest.keyClick(QWidget(), Qt.Key.Key_A)
    assert dev_metrics.snapshot()["interactions"] == {}


def test_no_qt_parts_without_an_application(monkeypatch):
    monkeypatch.setattr(dev_metrics.QApplication, "instance", staticmethod(lambda: None))
    dev_metrics.set_enabled(True)
    dev_metrics.qt_ready()
    assert dev_metrics.recorder()._qt is None
    dev_metrics.window_applied(QWidget())
    dev_metrics.tray_action("quit_action")
    assert dev_metrics.snapshot()["interactions"] == {}


def test_turning_off_removes_the_event_filter(collector):
    dev_metrics.set_enabled(False)
    w = _Window()
    w.show()
    w.hide()
    assert not dev_metrics.metrics_path().read_text(encoding="utf-8").count("window_opened")


class _Cramped(QDialog):
    def __init__(self):
        super().__init__()
        button = QPushButton("A button label far too long for it", self)
        button.setObjectName("cramped_btn")
        button.setFixedWidth(40)
        QVBoxLayout(self).addWidget(button)


def test_text_that_does_not_fit_is_warned_about_once_and_then_counted(collector, caplog):
    w = _Cramped()
    w.show()
    QTest.qWait(dev_metrics.LAYOUT_CHECK_DELAY_MS + 200)
    w.resize(w.width() + 10, w.height())
    QTest.qWait(dev_metrics.LAYOUT_CHECK_DELAY_MS + 200)
    warnings = [r.getMessage() for r in caplog.records if "cramped_btn" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].startswith("UI: ")
    ((message, seen),) = dev_metrics.snapshot()["layout"].items()
    assert "cramped_btn" in message and seen == 2
    assert [r["window"] for r in _records() if r["type"] == "layout"] == ["_Cramped"]
    w.hide()


def test_a_window_hidden_before_the_check_is_skipped(collector):
    w = _Cramped()
    w.show()
    w.hide()
    QTest.qWait(dev_metrics.LAYOUT_CHECK_DELAY_MS + 200)
    assert dev_metrics.snapshot()["layout"] == {}
