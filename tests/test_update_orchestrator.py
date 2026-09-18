"""UpdateOrchestrator: startup cooldown, manual vs silent checks and the signals they emit."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain import __version__, app  # noqa: E402
from refrain.config import Config  # noqa: E402
from refrain.updater import ReleaseInfo  # noqa: E402

NEWER = "999.0.0"


def _release(version: str) -> ReleaseInfo:
    return ReleaseInfo(
        tag=f"v{version}",
        version=version,
        name=f"Refrain v{version}",
        body="notes",
        html_url=f"https://github.com/Rockykln/refrain/releases/tag/v{version}",
    )


class _Harness:
    def __init__(self, monkeypatch):
        QApplication.instance() or QApplication(sys.argv)
        self.result: ReleaseInfo | None | Exception = None
        self.fetches = 0
        self.gate: threading.Event | None = None
        self.saves = 0
        monkeypatch.setattr(app, "check_latest_release", self._fetch)
        self.config = Config()
        self.config.update.last_check_ts = 1_234_567
        self.config.save = self._save
        self.orch = app.UpdateOrchestrator(self.config)
        self.events: list[tuple[str, object]] = []
        for name in ("updateAvailable", "checkUpToDate", "checkFailed", "releaseInfoFetched"):
            getattr(self.orch, name).connect(lambda v, n=name: self.events.append((n, v)))

    def _fetch(self):
        self.fetches += 1
        if self.gate is not None:
            self.gate.wait(5)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def _save(self):
        self.saves += 1

    def names(self) -> list[str]:
        return [n for n, _ in self.events]

    def wait(self, fetched: int = 1) -> None:
        deadline = time.monotonic() + 5
        while self.names().count("releaseInfoFetched") < fetched:
            assert time.monotonic() < deadline, "update check never finished"
            QCoreApplication.processEvents()
            time.sleep(0.002)

    def check(self, **kwargs) -> None:
        self.orch.check_now(**kwargs)
        self.wait()


@pytest.fixture
def h(monkeypatch):
    harness = _Harness(monkeypatch)
    yield harness
    if harness.gate is not None:
        harness.gate.set()
    thread = harness.orch._thread
    if thread is not None:
        thread.quit()
        thread.wait(2000)


def test_newer_release_is_announced_and_restarts_the_cooldown(h):
    h.result = _release(NEWER)
    h.check(manual=False)
    assert h.names() == ["releaseInfoFetched", "updateAvailable"]
    assert h.events[1][1].version == NEWER
    assert h.orch.latest.version == NEWER
    assert abs(h.config.update.last_check_ts - time.time()) < 5
    assert h.saves == 1


def test_silent_check_only_fills_the_release_notes(h):
    h.result = _release(NEWER)
    h.check(manual=False, silent=True)
    assert h.names() == ["releaseInfoFetched"]
    assert h.orch.latest.version == NEWER
    assert h.config.update.last_check_ts == 1_234_567
    assert h.saves == 0


def test_manual_check_on_latest_says_up_to_date(h):
    h.result = _release(__version__)
    h.check(manual=True)
    assert h.names() == ["releaseInfoFetched", "checkUpToDate"]
    assert h.events[1][1] == __version__


def test_auto_check_on_latest_stays_quiet(h):
    h.result = _release(__version__)
    h.check(manual=False)
    assert h.names() == ["releaseInfoFetched"]
    assert h.saves == 1


def test_manual_check_without_answer_reports_failure(h):
    h.check(manual=True)
    assert h.names() == ["releaseInfoFetched", "checkFailed"]
    assert h.events[0][1] is None
    assert "GitHub" in h.events[1][1]


def test_worker_exception_counts_as_no_answer(h):
    h.result = RuntimeError("boom")
    h.check(manual=True)
    assert h.names() == ["releaseInfoFetched", "checkFailed"]


def test_auto_check_without_answer_stays_quiet_and_keeps_the_last_release(h):
    h.result = _release(NEWER)
    h.check(manual=False, silent=True)
    h.result = None
    h.orch.check_now(manual=False)
    h.wait(fetched=2)
    assert h.names() == ["releaseInfoFetched", "releaseInfoFetched"]
    assert h.orch.latest.version == NEWER


def test_failed_save_does_not_swallow_the_result(h):
    def _refuse():
        raise OSError("read-only")

    h.config.save = _refuse
    h.result = _release(NEWER)
    h.check(manual=False)
    assert "updateAvailable" in h.names()


def test_a_second_check_while_one_runs_is_ignored(h):
    h.gate = threading.Event()
    h.result = _release(__version__)
    h.orch.check_now(manual=False)
    h.orch.check_now(manual=False)
    h.gate.set()
    h.wait()
    QCoreApplication.processEvents()
    assert h.fetches == 1
    assert h.names() == ["releaseInfoFetched"]


def test_new_config_receives_the_timestamp(h):
    fresh = Config()
    fresh.save = lambda: None
    h.orch.use_config(fresh)
    h.result = _release(__version__)
    h.check(manual=True)
    assert fresh.update.last_check_ts > 1_234_567
    assert h.config.update.last_check_ts == 1_234_567


@pytest.fixture
def startup(h, monkeypatch):
    calls = []
    monkeypatch.setattr(h.orch, "check_now", lambda **kw: calls.append(kw))
    return calls


def test_startup_check_is_skipped_when_auto_check_is_off(h, startup):
    h.config.update.auto_check = False
    h.orch.maybe_check_on_startup()
    assert startup == []


def test_startup_check_within_a_day_runs_silently(h, startup):
    h.config.update.last_check_ts = int(time.time()) - 3600
    h.orch.maybe_check_on_startup()
    assert startup == [{"manual": False, "silent": True}]


def test_startup_check_after_a_day_may_announce(h, startup):
    h.config.update.last_check_ts = (
        int(time.time()) - app.UpdateOrchestrator.UPDATE_CHECK_COOLDOWN_S
    )
    h.orch.maybe_check_on_startup()
    assert startup == [{"manual": False, "silent": False}]


def test_manual_check_during_a_silent_fetch_still_gives_feedback(h):
    h.gate = threading.Event()
    h.result = _release(NEWER)
    h.orch.check_now(manual=False, silent=True)
    h.orch.check_now(manual=True)
    h.gate.set()
    h.wait()
    QCoreApplication.processEvents()
    assert "updateAvailable" in h.names()
