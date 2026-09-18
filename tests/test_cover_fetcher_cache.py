"""CoverFetcher: lookup-cache pruning, the learned duration, and where cover images live and go."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import refrain.cover_fetcher as cf
from refrain.cover_art import TrackLookup, image_path_for_url
from refrain.paths import cover_cache_dir

COVER = "https://example.org/covers/low-tide-600x600bb.jpg"


def _fill(ext, count):
    """``count`` cache files, oldest first."""
    cache = cover_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    now = time.time()
    files = []
    for i in range(count):
        p = cache / f"entry{i}{ext}"
        p.write_bytes(b"x")
        os.utime(p, (now - 1000 + i, now - 1000 + i))
        files.append(p)
    return files


def test_pruning_drops_the_oldest_lookups_and_leaves_images_alone(xdg_tmp):
    jpgs = _fill(".jpg", 3)
    txts = _fill(".txt", 3)
    assert cf._prune_lookup_cache(2) == 1
    assert [p.exists() for p in txts] == [False, True, True]
    assert all(p.exists() for p in jpgs)


def test_pruning_a_cache_within_bounds_removes_nothing(xdg_tmp):
    files = _fill(".txt", 3)
    assert cf._prune_lookup_cache(3) == 0
    assert all(p.exists() for p in files)


def test_pruning_without_a_cache_dir_is_a_no_op(xdg_tmp):
    assert not cover_cache_dir().exists()
    assert cf._prune_lookup_cache(1) == 0


@pytest.fixture
def downloads(monkeypatch):
    """Stand-in for the image download; records every URL it fetched."""
    fetched = []

    def download(url, dest):
        fetched.append(url)
        if "missing" in url:
            return None
        dest.write_bytes(b"\xff\xd8\xff\xe0cover")
        return dest

    monkeypatch.setattr(cf, "download_cover_image", download)
    return fetched


@pytest.fixture
def fetcher(xdg_tmp, monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    def lookup(artist, title, album=""):
        return TrackLookup(cover_url=COVER, song_url="", duration_ms=201_000)

    monkeypatch.setattr(cf, "lookup_track_info", lookup)
    monkeypatch.setattr(cf, "download_cover_image", lambda url, dest: None)
    f = cf.CoverFetcher()
    yield f
    f.shutdown()


def _settle(fetcher, *key):
    fetcher.get(*key)
    deadline = time.monotonic() + 2
    while fetcher._inflight and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not fetcher._inflight


def test_the_duration_is_known_after_the_lookup(fetcher):
    key = ("The Quiet Hours", "Paper Satellites", "Low Tide")
    assert fetcher.get_duration_ms(*key) == 0
    _settle(fetcher, *key)
    assert fetcher.get_duration_ms(*key) == 201_000
    assert fetcher.get_duration_ms("", "Paper Satellites") == 0


def test_a_blank_track_has_no_local_cover(fetcher):
    assert fetcher.get_local_path("The Quiet Hours", "") is None


def test_a_kept_cover_is_found_from_a_known_url(fetcher):
    key = ("The Quiet Hours", "Paper Satellites", "Low Tide")
    _settle(fetcher, *key)
    assert fetcher.get_local_path(*key) is None, "nothing downloaded yet"
    image = image_path_for_url(COVER)
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\xff\xd8\xff\xe0cover")
    assert fetcher.get_local_path(*key) == image


def test_an_empty_image_file_is_no_cover(fetcher):
    key = ("The Quiet Hours", "Paper Satellites", "Low Tide")
    _settle(fetcher, *key)
    image = image_path_for_url(COVER)
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"")
    assert fetcher.get_local_path(*key) is None


KEY = ("The Quiet Hours", "Paper Satellites", "Low Tide")


def _wait(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    return predicate()


def test_a_downloaded_cover_lives_in_a_private_temp_dir_only(fetcher, downloads):
    _settle(fetcher, *KEY)
    local = fetcher.get_local_path(*KEY)
    assert local is not None
    assert local.parent.parent == Path(os.environ["XDG_RUNTIME_DIR"])
    assert local.parent.stat().st_mode & 0o777 == 0o700
    assert not image_path_for_url(COVER).exists()
    assert not list(cover_cache_dir().glob("*.jpg"))


def test_shutdown_removes_the_temp_dir(fetcher, downloads):
    _settle(fetcher, *KEY)
    temp = fetcher.get_local_path(*KEY).parent
    fetcher.shutdown()
    assert not temp.exists()


def _drop(fetcher, *key):
    fetcher.drop_temp_covers(*key)
    fetcher._executor.submit(lambda: None).result(timeout=2)


def test_the_temp_image_goes_with_the_next_song(fetcher, downloads):
    _settle(fetcher, *KEY)
    local = fetcher.get_local_path(*KEY)
    _drop(fetcher, *KEY)
    assert local.exists(), "the song still playing keeps its image"
    _drop(fetcher)
    assert not local.exists()


def test_a_dropped_temp_image_is_downloaded_again_when_needed(fetcher, downloads):
    _settle(fetcher, *KEY)
    _drop(fetcher)
    assert fetcher.get_local_path(*KEY) is None
    assert _wait(lambda: fetcher.get_local_path(*KEY) is not None)
    assert downloads == [COVER, COVER]


def test_a_failed_download_is_not_retried_straight_away(fetcher, downloads, monkeypatch):
    missing = "https://example.org/covers/missing.jpg"
    monkeypatch.setattr(
        cf, "lookup_track_info", lambda *a: TrackLookup(cover_url=missing, duration_ms=1)
    )
    _settle(fetcher, *KEY)
    for _ in range(5):
        assert fetcher.get_local_path(*KEY) is None
    assert _wait(lambda: not fetcher._image_inflight)
    assert downloads == [missing]


def test_a_song_in_the_history_keeps_its_image(fetcher, downloads):
    _settle(fetcher, *KEY)
    fetcher.keep_covers([COVER])
    kept = image_path_for_url(COVER)
    assert kept.exists()
    assert cover_cache_dir().stat().st_mode & 0o777 == 0o700
    _drop(fetcher)
    fetcher.shutdown()
    assert kept.exists()
    assert fetcher.get_local_path(*KEY) == kept


def test_a_song_leaving_the_history_loses_its_image(fetcher, downloads):
    _settle(fetcher, *KEY)
    fetcher.keep_covers([COVER])
    fetcher.keep_covers([])
    assert not image_path_for_url(COVER).exists()


def test_an_image_arriving_for_a_song_already_in_the_history_is_kept(fetcher, downloads):
    fetcher.keep_covers([COVER])
    _settle(fetcher, *KEY)
    assert image_path_for_url(COVER).exists()


def test_the_same_covers_again_leave_the_disk_alone(fetcher, downloads, monkeypatch):
    _settle(fetcher, *KEY)
    fetcher.keep_covers([COVER])
    synced = []
    monkeypatch.setattr(cf, "keep_cover_images", lambda urls, sources: synced.append(urls))
    fetcher.keep_covers([COVER, "", COVER])
    fetcher.keep_covers(u for u in [COVER])
    assert synced == []
    fetcher.keep_covers([])
    assert synced == [set()]


def test_the_first_sync_cleans_up_even_with_nothing_to_keep(fetcher):
    stray = image_path_for_url("https://example.org/covers/gone-600x600bb.jpg")
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"\xff\xd8\xff\xe0cover")
    fetcher.keep_covers([])
    assert not stray.exists()
