"""iTunes Search API lookup with persistent disk cache.

Each lookup yields **two** URLs:

- ``cover_url`` — the 600x600 album-cover image URL (used as Discord's
  ``large_image`` and downloaded to disk for ``notify-send -i``).
- ``song_url`` — the canonical Apple Music page URL for the specific song
  (used as the "Listen on Apple Music" button target).

Caching layout:

- ``<key>.txt``    — two-line text file: ``cover_url\\nsong_url``. Older
  single-line entries are read transparently with an empty ``song_url``.
- ``<urlhash>.jpg`` — image bytes, named after the cover-URL hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from refrain.paths import cover_cache_dir

log = logging.getLogger(__name__)

_ITUNES_SEARCH = "https://itunes.apple.com/search"
_USER_AGENT = "Refrain/0.1 (+https://github.com/Rockykln/refrain)"
_TIMEOUT_S = 5
_MAX_IMAGE_BYTES = 2_000_000  # 2 MB — well above any 600x600 album cover


@dataclass(frozen=True)
class TrackLookup:
    cover_url: str = ""
    song_url: str = ""
    # Authoritative track length from the iTunes catalog. Used to
    # override MPRIS when the browser integration reports a wonky
    # value (preview clips, playlist lengths). 0 means unknown.
    duration_ms: int = 0


def lookup_track_info(artist: str, title: str, album: str = "") -> TrackLookup:
    """Resolve a track to its iTunes-hosted cover URL and Apple Music page URL.

    Empty results are also cached so we don't keep hammering the API for
    tracks that legitimately have no match.
    """
    if not artist or not title:
        log.debug("iTunes lookup skipped — missing artist or title (%r / %r)", artist, title)
        return TrackLookup()

    key = _key(artist, title, album)
    cached = _read_cache(key)
    if cached is not None:
        log.debug("iTunes cache hit for %s — %s (key=%s)", artist, title, key[:8])
        return cached
    log.debug("iTunes cache miss for %s — %s (key=%s); querying API", artist, title, key[:8])

    term = f"{artist} {title}".strip()
    params = urllib.parse.urlencode(
        {
            "term": term,
            "media": "music",
            "entity": "song",
            "limit": 5,
        }
    )

    url = f"{_ITUNES_SEARCH}?{params}"
    if not url.startswith("https://"):
        # Defensive: _ITUNES_SEARCH is a module constant, but be explicit so
        # the urlopen call below cannot ever be coerced into file:// or ftp://.
        return TrackLookup()

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:  # nosec B310
            data = json.load(r)
    except Exception as e:
        log.debug("iTunes lookup failed for %s — %s: %s", artist, title, e)
        return TrackLookup()

    result = _extract(data.get("results", []))
    _write_cache(key, result)
    log.debug(
        "iTunes lookup result for %s — %s: cover=%s song=%s",
        artist,
        title,
        bool(result.cover_url),
        bool(result.song_url),
    )
    return result


def _extract(results: list) -> TrackLookup:
    if not results:
        return TrackLookup()
    first = results[0]
    art = str(first.get("artworkUrl100", "") or "")
    cover_url = art.replace("100x100bb.jpg", "600x600bb.jpg") if art else ""
    song_url = str(first.get("trackViewUrl", "") or "")
    try:
        duration_ms = int(first.get("trackTimeMillis", 0) or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    return TrackLookup(cover_url=cover_url, song_url=song_url, duration_ms=duration_ms)


def _key(artist: str, title: str, album: str) -> str:
    """Stable filename-safe cache key. Hash is non-cryptographic — used only
    for cache-file naming, never for security or integrity decisions."""
    raw = f"{artist}|{title}|{album}".lower().encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def _read_cache(key: str) -> TrackLookup | None:
    p = cover_cache_dir() / f"{key}.txt"
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return None
    lines = text.splitlines()
    cover_url = lines[0].strip() if len(lines) >= 1 else ""
    song_url = lines[1].strip() if len(lines) >= 2 else ""
    # Older cache files only had cover_url + song_url. New entries
    # carry trackTimeMillis on the third line; missing it is fine.
    duration_ms = 0
    if len(lines) >= 3:
        try:
            duration_ms = int(lines[2].strip() or 0)
        except ValueError:
            duration_ms = 0
    return TrackLookup(cover_url=cover_url, song_url=song_url, duration_ms=duration_ms)


def _write_cache(key: str, info: TrackLookup) -> None:
    d = cover_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.txt").write_text(
        f"{info.cover_url}\n{info.song_url}\n{info.duration_ms}\n",
        encoding="utf-8",
    )


def image_path_for_url(url: str) -> Path:
    """Deterministic on-disk path for a given image URL."""
    name = hashlib.blake2b(url.encode("utf-8"), digest_size=12).hexdigest() + ".jpg"
    return cover_cache_dir() / name


def download_cover_image(url: str) -> Path | None:
    """Download ``url`` to the cover cache, return the local path on success.

    Idempotent: re-uses an existing non-empty file if the URL has been
    downloaded before. Used only for the ``notify-send -i`` path; Discord
    fetches the URL itself, so downloading is unnecessary for RPC.
    """
    if not url.startswith("https://"):
        log.debug("Cover download refused: non-HTTPS URL (%s)", url[:60])
        return None
    dest = image_path_for_url(url)
    if dest.exists() and dest.stat().st_size > 0:
        log.debug("Cover already on disk: %s (%d bytes)", dest.name, dest.stat().st_size)
        return dest
    log.debug("Cover download starting: %s → %s", url, dest.name)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:  # nosec B310
            data = r.read(_MAX_IMAGE_BYTES)
    except Exception as e:
        log.debug("Cover image download failed for %s: %s", url, e)
        return None
    log.debug("Cover downloaded: %s (%d bytes)", dest.name, len(data))
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file and atomically rename. Without this,
    # a daemon kill mid-download would leave a truncated file that
    # `dest.exists() and st_size > 0` happily returns from on the next
    # tick, then notify-send would render a broken thumbnail forever
    # until the cache pruner evicts it.
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest
