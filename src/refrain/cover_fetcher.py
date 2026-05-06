"""Asynchronous track-info prefetch with in-memory cache.

Wraps the synchronous ``lookup_track_info`` and ``download_cover_image`` in a
single-worker thread pool so the daemon's polling tick never blocks while
iTunes Search responds.

- ``get(artist, title, album)``           returns the iTunes cover URL.
- ``get_local_path(artist, title, album)`` returns a local image Path
  (for ``notify-send -i``).
- ``get_song_url(artist, title, album)``   returns the canonical Apple
  Music page URL for the specific song (used as the "Listen on Apple
  Music" Discord button target).

On startup the on-disk cache is pruned so the cover dir never grows unbounded.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from refrain.cover_art import (
    TrackLookup,
    download_cover_image,
    image_path_for_url,
    lookup_track_info,
)
from refrain.paths import cover_cache_dir

log = logging.getLogger(__name__)

# Disk-cache cap: the busiest realistic listening sessions barely brush this.
# At 50 KB-150 KB per cover, 200 covers ≈ 10-30 MB.
_MAX_CACHED_COVERS = 200


def _prune_cover_cache(max_entries: int = _MAX_CACHED_COVERS) -> int:
    """Drop the oldest cover files when the cache exceeds ``max_entries``.

    Returns the number of files removed. Url-cache (``.txt``) and image
    files (``.jpg``) are tracked separately so a track without a cover
    doesn't displace a track that has one.
    """
    removed = 0
    cache_dir = cover_cache_dir()
    if not cache_dir.exists():
        return 0
    for ext in (".jpg", ".txt"):
        files = sorted(cache_dir.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - max_entries
        if excess > 0:
            for p in files[:excess]:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
    if removed:
        log.info("Pruned %d cover-cache file(s)", removed)
    return removed


class CoverFetcher:
    def __init__(self, max_cached_covers: int = _MAX_CACHED_COVERS) -> None:
        _prune_cover_cache(max_cached_covers)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refrain-cover")
        self._lock = threading.Lock()
        self._url_cache: dict[str, str] = {}  # key → cover URL ("" = negative)
        self._song_url_cache: dict[str, str] = {}  # key → song page URL
        self._local_cache: dict[str, str] = {}  # key → local path ("" = no image)
        self._duration_cache: dict[str, int] = {}  # key → trackTimeMillis (0 = unknown)
        self._inflight: set[str] = set()

    def get(self, artist: str, title: str, album: str = "") -> str | None:
        """Returns the iTunes cover URL or None.

        First call for a given (artist, title, album) tuple queues the
        background lookup + download and returns None.
        """
        if not artist or not title:
            return None
        key = self._key(artist, title, album)
        with self._lock:
            if key in self._url_cache:
                return self._url_cache[key] or None
            if key in self._inflight:
                return None
            self._inflight.add(key)
        future = self._executor.submit(self._fetch_all, artist, title, album)
        future.add_done_callback(lambda f, k=key: self._on_done(k, f))
        return None

    def get_duration_ms(self, artist: str, title: str, album: str = "") -> int:
        """Returns the iTunes-catalog track length in ms, or 0 if unknown.

        Used by the daemon's RPC builder to override MPRIS-reported
        durations that are obviously wrong (browser MPRIS sometimes
        reports a 15 s preview-clip length, or the playlist total
        instead of the current track). Returns 0 until the background
        lookup has populated the cache.
        """
        if not artist or not title:
            return 0
        key = self._key(artist, title, album)
        with self._lock:
            return self._duration_cache.get(key, 0)

    def get_song_url(self, artist: str, title: str, album: str = "") -> str | None:
        """Returns the Apple Music page URL for this specific song, or None.

        Returns None until the background lookup has populated it. Callers
        (the daemon's RPC builder) should fall back to whatever URL the
        playback source itself reported in that case.
        """
        if not artist or not title:
            return None
        key = self._key(artist, title, album)
        with self._lock:
            return self._song_url_cache.get(key) or None

    def get_local_path(self, artist: str, title: str, album: str = "") -> Path | None:
        """Returns the local image path or None.

        Used as the ``-i`` argument to ``notify-send``. Returns None when the
        image hasn't been downloaded yet (background fetch still pending) or
        when the lookup failed.
        """
        if not artist or not title:
            return None
        key = self._key(artist, title, album)
        with self._lock:
            local = self._local_cache.get(key)
        if local:
            p = Path(local)
            if p.exists() and p.stat().st_size > 0:
                return p
        # Fall back to deriving the path from a known URL — covers the case
        # where the URL cache is populated (e.g. from disk) but the image
        # path hasn't been re-cached in-process yet.
        with self._lock:
            url = self._url_cache.get(key, "")
        if url:
            p = image_path_for_url(url)
            if p.exists() and p.stat().st_size > 0:
                with self._lock:
                    self._local_cache[key] = str(p)
                return p
        return None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _key(artist: str, title: str, album: str = "") -> str:
        return f"{artist}|{title}|{album}".lower()

    def _fetch_all(self, artist: str, title: str, album: str) -> tuple[str, str, str, int]:
        info: TrackLookup = lookup_track_info(artist, title, album)
        local = ""
        if info.cover_url:
            p = download_cover_image(info.cover_url)
            if p:
                local = str(p)
        return info.cover_url, info.song_url, local, info.duration_ms

    def _on_done(self, key: str, future: Future) -> None:
        try:
            cover_url, song_url, local, duration_ms = future.result()
        except Exception as e:
            log.debug("CoverFetcher background lookup failed: %s", e)
            cover_url, song_url, local, duration_ms = "", "", "", 0
        with self._lock:
            self._url_cache[key] = cover_url
            self._song_url_cache[key] = song_url
            self._local_cache[key] = local
            self._duration_cache[key] = duration_ms
            self._inflight.discard(key)
