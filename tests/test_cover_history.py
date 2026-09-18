"""Cover images on disk follow the Recently played history, driven through the daemon."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from refrain import cover_fetcher, daemon, history  # noqa: E402
from refrain.cover_art import TrackLookup, image_path_for_url  # noqa: E402
from refrain.daemon import DaemonWorker  # noqa: E402
from refrain.paths import cover_cache_dir  # noqa: E402
from tests.daemon_fakes import Player, install, make_config  # noqa: E402

FIRST, SECOND = "Paper Satellites", "Low Tide"


def _cover(title: str) -> str:
    return f"https://example.org/covers/{title.replace(' ', '-').lower()}.jpg"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(monkeypatch, qapp):
    def build(**sections):
        clock, _ = install(monkeypatch)
        monkeypatch.setattr(history, "time", clock)
        monkeypatch.setattr(daemon, "PlayHistory", history.PlayHistory)
        monkeypatch.setattr(daemon, "CoverFetcher", cover_fetcher.CoverFetcher)
        monkeypatch.setattr(
            cover_fetcher,
            "lookup_track_info",
            lambda artist, title, album="": TrackLookup(cover_url=_cover(title)),
        )

        def download(url, dest):
            dest.write_bytes(b"\xff\xd8\xff\xe0cover")
            return dest

        monkeypatch.setattr(cover_fetcher, "download_cover_image", download)
        worker = DaemonWorker(make_config(**sections))
        return worker, Player(worker, clock)

    yield build


def _settle(worker):
    fetcher = worker._cover_fetcher
    deadline = time.monotonic() + 2
    while (fetcher._inflight or fetcher._image_inflight) and time.monotonic() < deadline:
        time.sleep(0.005)


def _listen(worker, player, title, seconds=120):
    player.play(title=title)
    player.tick()
    _settle(worker)
    player.tick(seconds=1.0, n=seconds)


def _kept():
    return sorted(p.name for p in cover_cache_dir().glob("*.jpg*"))


def _temp(worker):
    worker._cover_fetcher._executor.submit(lambda: None).result(timeout=2)
    temp = worker._cover_fetcher._temp_dir
    return sorted(p.name for p in temp.glob("*.jpg")) if temp else []


def test_a_song_in_the_history_keeps_its_image(rig):
    worker, player = rig()
    _listen(worker, player, FIRST)
    _listen(worker, player, SECOND, seconds=5)
    assert _kept() == sorted(image_path_for_url(_cover(t)).name for t in (FIRST, SECOND))
    player.stop()
    player.tick(seconds=1.0, n=15)
    worker.cleanup()
    assert _kept() == [image_path_for_url(_cover(FIRST)).name], "an uncounted song lets go"
    assert worker._cover_fetcher._temp_dir is None


def test_a_removed_song_loses_its_image(rig):
    worker, player = rig()
    _listen(worker, player, FIRST)
    player.stop()
    player.tick(seconds=1.0, n=15)
    entry = worker.history_snapshot().entries[0]
    assert _kept() == [image_path_for_url(entry.cover_url).name]
    worker.remove_from_history(entry)
    assert _kept() == []
    worker.cleanup()


def test_clearing_and_trimming_the_history_delete_images(rig):
    worker, player = rig(history={"max_entries": 1})
    _listen(worker, player, FIRST)
    _listen(worker, player, SECOND)
    assert _kept() == [image_path_for_url(_cover(SECOND)).name]
    worker.clear_history()
    player.stop()
    player.tick(seconds=1.0, n=15)
    assert _kept() == []
    worker.cleanup()


def test_history_off_leaves_no_image_files(rig):
    worker, player = rig()
    _listen(worker, player, FIRST)
    assert _kept()
    worker.update_config(make_config(history={"enabled": False}))
    assert _kept() == []
    _listen(worker, player, SECOND)
    assert _kept() == []
    assert _temp(worker) == [image_path_for_url(_cover(SECOND)).name], "for its notification"
    player.stop()
    player.tick()
    assert _temp(worker) == []
    worker.cleanup()
    assert not list(cover_cache_dir().glob("*.jpg*"))


def test_old_images_not_in_the_history_are_cleared_at_startup(rig):
    worker, player = rig()
    _listen(worker, player, FIRST)
    player.stop()
    player.tick(seconds=1.0, n=15)
    worker.cleanup()
    stray = [cover_cache_dir() / "0123456789abcdef01234567.jpg", cover_cache_dir() / "x.jpg.tmp"]
    for p in stray:
        p.write_bytes(b"old")
    lookup = cover_cache_dir() / "0123456789abcdef0123456789abcdef.txt"
    lookup.write_text("x\n")

    worker, _ = rig()
    assert _kept() == [image_path_for_url(_cover(FIRST)).name]
    assert lookup.exists()
    worker.cleanup()

    worker, _ = rig(history={"enabled": False})
    assert _kept() == []
    worker.cleanup()
