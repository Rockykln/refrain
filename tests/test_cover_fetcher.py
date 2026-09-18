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

    def fake_download(url: str, dest):
        if not url:
            return None
        dest.write_bytes(b"\xff\xd8\xff\xe0placeholder")
        return dest

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
        assert fetcher.get("Oskar Lind", "Ferrous", "Iron Garden") is None
        assert _wait_for(lambda: not fetcher._inflight)
        # Second call returns the URL synchronously
        url = fetcher.get("Oskar Lind", "Ferrous", "Iron Garden")
        assert url == "https://example/Oskar Lind-Ferrous-600x600bb.jpg"
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


def test_exception_in_lookup_is_swallowed_and_backs_off(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("FAILS", "X") is None
        assert _wait_for(lambda: not fetcher._inflight)
        # Not retried on the next poll; the daemon asks twice a second.
        assert fetcher.get("FAILS", "X") is None
        assert len(calls) == 1
    finally:
        fetcher.shutdown()


def test_failed_lookup_is_retried_after_the_cooldown(fetcher_module):
    """A network error is not an answer, so it must not be cached as one."""
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("FAILS", "X") is None
        assert _wait_for(lambda: not fetcher._inflight)
        assert len(calls) == 1
        # A failure leaves no cache entry to read back — only a
        # timestamp saying when to try again.
        assert fetcher._key("FAILS", "X", "") not in fetcher._url_cache

        # Wind the cooldown back and the next request goes out again.
        key = fetcher._key("FAILS", "X", "")
        fetcher._failed_at[key] = time.monotonic() - cf._RETRY_AFTER_S - 1
        assert fetcher.get("FAILS", "X") is None
        assert _wait_for(lambda: len(calls) == 2)
    finally:
        fetcher.shutdown()


def test_memo_cache_is_bounded(fetcher_module, monkeypatch):
    """A daemon left running for weeks must not remember every track."""
    cf, _calls = fetcher_module
    monkeypatch.setattr(cf, "_MAX_MEMO_ENTRIES", 3)
    fetcher = cf.CoverFetcher()
    try:
        for i in range(6):
            fetcher.get("Artist", f"Song {i}")
        assert _wait_for(lambda: not fetcher._inflight and len(fetcher._url_cache) <= 3)
        assert len(fetcher._url_cache) == 3
        assert len(fetcher._song_url_cache) == 3
        assert len(fetcher._duration_cache) == 3
        # The newest survive; the oldest were evicted.
        assert fetcher._key("Artist", "Song 5", "") in fetcher._url_cache
        assert fetcher._key("Artist", "Song 0", "") not in fetcher._url_cache
    finally:
        fetcher.shutdown()


def test_concurrent_requests_for_same_track_dedupe(fetcher_module):
    cf, calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        # Three rapid-fire requests for the same track should produce one lookup.
        for _ in range(3):
            fetcher.get("Oskar Lind", "Ferrous", "Iron Garden")
        assert _wait_for(lambda: not fetcher._inflight)
        time.sleep(0.05)
        assert len(calls) == 1
    finally:
        fetcher.shutdown()


def test_get_local_path_returns_downloaded_image(fetcher_module):
    cf, _ = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        assert fetcher.get("Oskar Lind", "Ferrous", "Iron Garden") is None
        # Wait for the BG fetch to complete
        assert _wait_for(
            lambda: fetcher.get_local_path("Oskar Lind", "Ferrous", "Iron Garden") is not None
        )
        p = fetcher.get_local_path("Oskar Lind", "Ferrous", "Iron Garden")
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
        assert fetcher.get_song_url("Oskar Lind", "Ferrous", "Iron Garden") is None
        # First call to get() schedules the BG fetch that populates song_url too
        fetcher.get("Oskar Lind", "Ferrous", "Iron Garden")
        assert _wait_for(lambda: not fetcher._inflight)
        url = fetcher.get_song_url("Oskar Lind", "Ferrous", "Iron Garden")
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


def test_the_failure_cooldowns_do_not_pile_up(fetcher_module):
    """Expired retry cooldowns are dropped; offline, the success-path trim never runs."""
    cf, _calls = fetcher_module
    fetcher = cf.CoverFetcher()
    try:
        for i in range(4):
            fetcher.get("FAILS", f"X{i}")
            assert _wait_for(lambda: not fetcher._inflight)
        assert len(fetcher._failed_at) == 4

        # Age the first three past the cooldown; the next failure sweeps them.
        stale = time.monotonic() - cf._RETRY_AFTER_S - 1
        for i in range(3):
            fetcher._failed_at[fetcher._key("FAILS", f"X{i}", "")] = stale
        fetcher.get("FAILS", "X9")
        assert _wait_for(lambda: not fetcher._inflight)

        assert set(fetcher._failed_at) == {
            fetcher._key("FAILS", "X3", ""),
            fetcher._key("FAILS", "X9", ""),
        }
    finally:
        fetcher.shutdown()
