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

Images are downloaded to a private temporary directory, removed again by
``drop_temp_covers`` and ``shutdown``. Only ``keep_covers`` — the songs
in the Recently played history — puts images in the lasting cover cache.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from refrain.cover_art import (
    TrackLookup,
    download_cover_image,
    has_image,
    image_name,
    image_path_for_url,
    keep_cover_images,
    lookup_track_info,
)
from refrain.paths import cover_cache_dir

log = logging.getLogger(__name__)

# Cap for the lookup answers (``.txt``, a few hundred bytes each).
_MAX_CACHED_LOOKUPS = 200

# In-memory memo cap. The dicts hold a few short strings per track, so
# this is small in bytes; the point is that a daemon left running for
# weeks shouldn't accumulate every track it has ever seen.
_MAX_MEMO_ENTRIES = 500

# How long a *failed* lookup (network error, timeout) is left alone
# before the next poll may retry it. A lookup that succeeded and simply
# found nothing is a real answer and stays cached for good; an error is
# not an answer, and caching it meant one offline minute cost the track
# its cover, its song link and its duration for the rest of the session.
_RETRY_AFTER_S = 60.0


def _prune_lookup_cache(max_entries: int = _MAX_CACHED_LOOKUPS) -> int:
    """Drop the oldest lookup answers beyond ``max_entries``; returns how many.

    An unreadable cache dir must not stop the app from starting, so
    errors are only logged.
    """
    removed = 0
    cache_dir = cover_cache_dir()
    if not cache_dir.exists():
        return 0
    try:
        files = sorted(cache_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        for p in files[: max(0, len(files) - max_entries)]:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    except OSError as e:
        log.debug("Cover-cache prune failed: %s", e)
        return removed
    if removed:
        log.info("Pruned %d cover-cache file(s)", removed)
    return removed


def _temp_base() -> str | None:
    """$XDG_RUNTIME_DIR (private, gone at logout), else the system default."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    return runtime if os.path.isabs(runtime) and os.path.isdir(runtime) else None


class CoverFetcher:
    def __init__(self) -> None:
        _prune_lookup_cache()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refrain-cover")
        self._lock = threading.Lock()
        self._url_cache: dict[str, str] = {}  # key → cover URL ("" = negative)
        self._song_url_cache: dict[str, str] = {}  # key → song page URL
        self._duration_cache: dict[str, int] = {}  # key → trackTimeMillis (0 = unknown)
        self._inflight: set[str] = set()
        self._failed_at: dict[str, float] = {}  # key → monotonic ts of last error
        self._image_inflight: set[str] = set()  # cover URLs being downloaded
        self._image_failed_at: dict[str, float] = {}  # cover URL → monotonic ts
        self._temp_dir: Path | None = None
        self._closed = False
        # Serialises the copies into, and the deletions from, the cover cache.
        self._keep_lock = threading.Lock()
        self._keep: frozenset[str] = frozenset()
        self._kept_once = False

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
            failed_at = self._failed_at.get(key)
            if failed_at is not None:
                if (time.monotonic() - failed_at) < _RETRY_AFTER_S:
                    return None
                # Cooldown is over — let this one through again rather
                # than retrying on every poll tick while we're offline.
                del self._failed_at[key]
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

        Used as the ``-i`` argument to ``notify-send``. Returns None while
        the image is still downloading and when there is none; a known
        cover whose temporary file is gone is downloaded again.
        """
        if not artist or not title:
            return None
        key = self._key(artist, title, album)
        with self._lock:
            url = self._url_cache.get(key, "")
        if not url:
            return None
        kept = image_path_for_url(url)
        if has_image(kept):
            return kept
        temp = self._temp_file(url)
        if temp is not None and has_image(temp):
            return temp
        self._download_again(url)
        return None

    def keep_covers(self, urls: Iterable[str]) -> None:
        """Keep the images of exactly these cover URLs in the cover cache.

        Called with the Recently played history's covers whenever it
        changes: songs that left it lose their image.
        """
        keep = frozenset(u for u in urls if u)
        with self._keep_lock:
            # Pausing or a scrobble mark changes the history but not its
            # covers; an image that arrives later is kept by _download.
            if self._kept_once and keep == self._keep:
                return
            self._kept_once = True
            self._keep = keep
            keep_cover_images(set(keep), self._temp_sources())

    def drop_temp_covers(self, artist: str = "", title: str = "", album: str = "") -> None:
        """Delete the temporary images, except the one of the given song.

        Runs on the worker, after the lookups and downloads queued before
        it — deleting from here could remove an image that has just
        landed before its URL is known.
        """
        key = self._key(artist, title, album) if artist and title else ""
        with contextlib.suppress(RuntimeError):  # shut down already
            self._executor.submit(self._drop_temp, key)

    def _drop_temp(self, key: str) -> None:
        with self._lock:
            temp = self._temp_dir
            spare_url = self._url_cache.get(key, "") if key else ""
        if temp is None:
            return
        spare = image_name(spare_url) if spare_url else ""
        with contextlib.suppress(OSError):
            for p in temp.glob("*.jpg"):
                if p.name != spare:
                    with contextlib.suppress(OSError):
                        p.unlink()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._closed = True
            temp, self._temp_dir = self._temp_dir, None
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)

    @staticmethod
    def _key(artist: str, title: str, album: str = "") -> str:
        return f"{artist}|{title}|{album}".lower()

    def _temp_sources(self) -> list[Path]:
        with self._lock:
            return [self._temp_dir] if self._temp_dir is not None else []

    def _temp_file(self, url: str, create: bool = False) -> Path | None:
        with self._lock:
            if self._temp_dir is None and create and not self._closed:
                try:
                    self._temp_dir = Path(
                        tempfile.mkdtemp(prefix="refrain-covers-", dir=_temp_base())
                    )
                except OSError as e:
                    log.debug("Could not create a temporary cover directory: %s", e)
            temp = self._temp_dir
        return temp / image_name(url) if temp is not None else None

    def _download(self, url: str) -> None:
        """Fetch ``url`` into the temporary directory unless it is kept already."""
        if has_image(image_path_for_url(url)):
            return
        dest = self._temp_file(url, create=True)
        if dest is None or has_image(dest):
            return
        if download_cover_image(url, dest) is None:
            with self._lock:
                now = time.monotonic()
                self._image_failed_at = {
                    u: ts for u, ts in self._image_failed_at.items() if now - ts < _RETRY_AFTER_S
                }
                self._image_failed_at[url] = now
            return
        with self._keep_lock:
            # The song made the history while its image was on its way.
            if url in self._keep:
                keep_cover_images(set(self._keep), self._temp_sources())

    def _download_again(self, url: str) -> None:
        with self._lock:
            if url in self._image_inflight:
                return
            failed_at = self._image_failed_at.get(url)
            if failed_at is not None and time.monotonic() - failed_at < _RETRY_AFTER_S:
                return
            self._image_failed_at.pop(url, None)
            self._image_inflight.add(url)
        future = self._executor.submit(self._download, url)
        future.add_done_callback(lambda _f, u=url: self._image_done(u))

    def _image_done(self, url: str) -> None:
        with self._lock:
            self._image_inflight.discard(url)

    def _fetch_all(self, artist: str, title: str, album: str) -> tuple[str, str, int]:
        info: TrackLookup = lookup_track_info(artist, title, album)
        if info.cover_url:
            self._download(info.cover_url)
        return info.cover_url, info.song_url, info.duration_ms

    def _on_done(self, key: str, future: Future) -> None:
        try:
            cover_url, song_url, duration_ms = future.result()
        except Exception as e:
            log.debug("CoverFetcher background lookup failed: %s", e)
            with self._lock:
                now = time.monotonic()
                # Drop the cooldowns that have already run out. They are
                # only ever read to decide whether to retry, so an expired
                # one is dead weight — and while we're offline no lookup
                # succeeds, so this is the only place that gets to prune.
                self._failed_at = {
                    k: ts for k, ts in self._failed_at.items() if (now - ts) < _RETRY_AFTER_S
                }
                self._failed_at[key] = now
                self._inflight.discard(key)
            return
        with self._lock:
            self._url_cache[key] = cover_url
            self._song_url_cache[key] = song_url
            self._duration_cache[key] = duration_ms
            self._inflight.discard(key)
            self._failed_at.pop(key, None)
            self._trim_locked()

    def _trim_locked(self) -> None:
        """Drop the oldest memo entries once the cache outgrows its cap.

        Caller holds ``self._lock``. Insertion order is eviction order:
        dicts keep it, and the track least recently *learned about* is
        the one least likely to come round again. All three dicts share
        one key space, so they are trimmed together.
        """
        excess = len(self._url_cache) - _MAX_MEMO_ENTRIES
        if excess <= 0:
            return
        for key in list(self._url_cache)[:excess]:
            self._url_cache.pop(key, None)
            self._song_url_cache.pop(key, None)
            self._duration_cache.pop(key, None)
