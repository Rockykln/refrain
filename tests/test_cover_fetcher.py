"""CoverFetcher async wrapper around the synchronous cover-art lookup."""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def fetcher_module(monkeypatch, xdg_tmp):
    """Provide a CoverFetcher with stubbed synchronous lookup + download."""
    import importlib

    import refrain.cover_art

    importlib.reload(refrain.cover_art)
    import refrain.cover_fetcher as cf

    importlib.reload(cf)

    calls: list[tuple[str, str, str]] = []

    def fake_lookup(artist: str, title: str, album: str = ""):
        calls.append((artist, title, album))
        if artist == "FAILS":
            raise RuntimeError("simulated network failure")
        if artist == "EMPTY":
            return cf.TrackLookup()
        cover = f"https://example/{artist}-{title}-600x600bb.jpg"
        song = f"https://music.apple.com/us/album/{artist}-{title}/1?i=2"
        return cf.TrackLookup(cover_url=cover, song_url=song)

    def fake_download(url: str):
        # Drop a tiny placeholder file at the deterministic path so
        # get_local_path() finds something.
        from refrain.cover_art import image_path_for_url

        if not url:
            return None
        p = image_path_for_url(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8\xff\xe0placeholder")
        return p

    monkeypatch.setattr(cf, "lookup_track_info", fake_lookup)
    monkeypatch.setattr(cf, "download_cover_image", fake_download)
    return cf, calls


def _wait_for(predicate, timeout_s: float = 2.0, interval_s: float = 0.01):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def test_first_call_returns_none_then_caches_url(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("Drake", "One Dance", "Views") is None
        assert _wait_for(lambda: not fetcher._inflight)
        # Second call returns the URL synchronously
        url = fetcher.get("Drake", "One Dance", "Views")
        assert url == "https://example/Drake-One Dance-600x600bb.jpg"
        # Still only one network call
        assert len(calls) == 1
    finally:
        fetcher.shutdown()


def test_negative_result_is_cached_as_none(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("EMPTY", "X", "Y") is None
        assert _wait_for(lambda: not fetcher._inflight)
        # Second call still None — but no new background work
        assert fetcher.get("EMPTY", "X", "Y") is None
        assert len(calls) == 1
    finally:
        fetcher.shutdown()


def test_blank_input_does_not_trigger_lookup(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("", "Title") is None
        assert fetcher.get("Artist", "") is None
        time.sleep(0.05)
        assert calls == []
    finally:
        fetcher.shutdown()


def test_exception_in_lookup_is_swallowed_and_caches_none(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("FAILS", "X") is None
        assert _wait_for(lambda: not fetcher._inflight)
        # Subsequent call still None, lookup not retried
        assert fetcher.get("FAILS", "X") is None
        assert len(calls) == 1
    finally:
        fetcher.shutdown()


def test_concurrent_requests_for_same_track_dedupe(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        # Three rapid-fire requests for the same track should produce one lookup.
        for _ in range(3):
            fetcher.get("Drake", "One Dance", "Views")
        assert _wait_for(lambda: not fetcher._inflight)
        time.sleep(0.05)
        assert len(calls) == 1
    finally:
        fetcher.shutdown()


def test_get_local_path_returns_downloaded_image(fetcher_module):
    cf, _ = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("Drake", "One Dance", "Views") is None
        # Wait for the BG fetch to complete
        assert _wait_for(lambda: fetcher.get_local_path("Drake", "One Dance", "Views") is not None)
        p = fetcher.get_local_path("Drake", "One Dance", "Views")
        assert p is not None
        assert p.exists()
        assert p.stat().st_size > 0
    finally:
        fetcher.shutdown()


def test_get_local_path_returns_none_on_negative_lookup(fetcher_module):
    cf, _ = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("EMPTY", "X", "Y") is None
        assert _wait_for(lambda: "empty|x|y" in fetcher._url_cache)
        assert fetcher.get_local_path("EMPTY", "X", "Y") is None
    finally:
        fetcher.shutdown()


def test_get_song_url_returns_after_lookup(fetcher_module):
    cf, _ = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get_song_url("Drake", "One Dance", "Views") is None
        # First call to get() schedules the BG fetch that populates song_url too
        fetcher.get("Drake", "One Dance", "Views")
        assert _wait_for(lambda: not fetcher._inflight)
        url = fetcher.get_song_url("Drake", "One Dance", "Views")
        assert url is not None
        assert url.startswith("https://music.apple.com/")
    finally:
        fetcher.shutdown()


def test_get_song_url_none_for_empty_input(fetcher_module):
    cf, _ = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get_song_url("", "Title") is None
        assert fetcher.get_song_url("Artist", "") is None
    finally:
        fetcher.shutdown()
