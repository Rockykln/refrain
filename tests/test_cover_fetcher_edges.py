"""CoverFetcher: pruning a broken cache dir, a temp dir that can't be created,
and skipping a download that already has its image."""

from __future__ import annotations

import time

import pytest

import refrain.cover_fetcher as cf
from refrain.cover_art import TrackLookup, image_path_for_url
from refrain.paths import cover_cache_dir

COVER = "https://example.org/covers/glass-tides-600x600bb.jpg"


class _UnreadableDir:
    """Stands in for the cache dir when listing it fails partway through."""

    def exists(self):
        return True

    def glob(self, _pattern):
        raise OSError("permission denied")


def test_a_prune_that_cannot_list_the_cache_dir_gives_up_quietly(monkeypatch):
    monkeypatch.setattr(cf, "cover_cache_dir", _UnreadableDir)
    assert cf._prune_lookup_cache(5) == 0


@pytest.fixture
def fetcher(xdg_tmp, monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(
        cf, "lookup_track_info", lambda *a: TrackLookup(cover_url=COVER, duration_ms=201_000)
    )
    monkeypatch.setattr(
        cf, "download_cover_image", lambda url, dest: dest.write_bytes(b"x") or dest
    )
    f = cf.CoverFetcher()
    yield f
    f.shutdown()


KEY = ("The Quiet Hours", "Paper Satellites", "Low Tide")


def _settle(fetcher, *key):
    fetcher.get(*key)
    deadline = time.monotonic() + 2
    while fetcher._inflight and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not fetcher._inflight


def test_a_temp_dir_that_cannot_be_created_is_not_fatal(fetcher, monkeypatch):
    def refuse(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(cf.tempfile, "mkdtemp", refuse)
    _settle(fetcher, *KEY)  # the lookup succeeds, but the image never lands anywhere
    assert fetcher.get_local_path(*KEY) is None


def test_downloading_a_cover_already_kept_does_nothing(fetcher, monkeypatch):
    calls = []
    monkeypatch.setattr(cf, "download_cover_image", lambda url, dest: calls.append(url))
    kept = image_path_for_url(COVER)
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(b"\xff\xd8already-kept")

    fetcher._download(COVER)

    assert calls == [], "the permanent cover cache already had it"
    assert not list(cover_cache_dir().glob("*.tmp"))


def test_downloading_a_cover_already_fetched_to_temp_is_not_repeated(fetcher, monkeypatch):
    _settle(fetcher, *KEY)  # the background fetch already downloaded it once
    assert fetcher.get_local_path(*KEY) is not None

    calls = []
    monkeypatch.setattr(cf, "download_cover_image", lambda url, dest: calls.append(url))
    fetcher._download(COVER)

    assert calls == [], "the temp copy was already there"


def test_get_album_is_empty_until_the_background_lookup_has_one(fetcher, monkeypatch):
    monkeypatch.setattr(
        cf,
        "lookup_track_info",
        lambda *a: TrackLookup(cover_url=COVER, duration_ms=201_000, album="Low Tide"),
    )
    assert fetcher.get_album(*KEY) == ""
    _settle(fetcher, *KEY)
    assert fetcher.get_album(*KEY) == "Low Tide"


def test_get_album_is_empty_for_missing_input(fetcher):
    assert fetcher.get_album("", "Title") == ""
    assert fetcher.get_album("Artist", "") == ""
