"""iTunes Search API lookup with persistent disk cache.

Each lookup yields **two** URLs and a length:

- ``cover_url`` — the 600x600 album-cover image URL (used as Discord's
  ``large_image`` and downloaded for ``notify-send -i``).
- ``song_url`` — the canonical Apple Music page URL for the specific song
  (used as the "Listen on Apple Music" button target).
- ``duration_ms`` — the catalog's track length, which overrides the
  lengths Apple Music's web player misreports.

Finding the song: the store of the desktop's own country is asked first
(a German release can be missing from the US store), with the title
stripped of "feat." and remaster/version tags and, failing that, only
the first of several credited artists — MPRIS joins every artist into
one string the catalog rarely has. A result only counts when its artist
*and* title match; taking the first hit blindly can put a ballad on a
rap track.

Caching layout:

- ``<key>.txt`` — ``cover_url``, ``song_url``, ``duration_ms``, the
  format version and when it was checked, one per line. A miss expires
  after ``_NEGATIVE_TTL_S``; entries without the current version (the
  old first-hit matcher's) are looked up again.
- ``<urlhash>.jpg`` — image bytes, named after the cover-URL hash, only
  for songs in the Recently played history (see ``keep_cover_images``).
  Images for anything else are downloaded to a private temporary
  directory owned by ``CoverFetcher``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from refrain import __version__, dev_metrics
from refrain.paths import cover_cache_dir

log = logging.getLogger(__name__)

_ITUNES_SEARCH = "https://itunes.apple.com/search"
# Track the real version (was a hardcoded "Refrain/0.1" that never
# updated — every iTunes request advertised a fake old version).
USER_AGENT = f"Refrain/{__version__} (+https://github.com/Rockykln/refrain)"
_TIMEOUT_S = 5
_MAX_IMAGE_BYTES = 2_000_000  # 2 MB — well above any 600x600 album cover

_CACHE_VERSION = "v3"
# A miss is asked about again after this long: the catalog grows, and a
# song that isn't there on release day often is a week later.
_NEGATIVE_TTL_S = 3 * 24 * 3600


@dataclass(frozen=True)
class TrackLookup:
    cover_url: str = ""
    song_url: str = ""
    # Authoritative track length from the iTunes catalog. Used to
    # override MPRIS when the browser integration reports a wonky
    # value (preview clips, playlist lengths). 0 means unknown.
    duration_ms: int = 0
    # The catalog's album name. Browsers often send none, and Last.fm needs one
    # to find its cover art.
    album: str = ""


# --------------------------------------------------------------------------- #
# Matching                                                                     #
# --------------------------------------------------------------------------- #

_FEAT_PAREN = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.I)
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s.*$", re.I)
_TAG_PAREN = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:remaster(?:ed)?|version|edit|live|mono|stereo|deluxe|bonus|"
    r"explicit|clean|radio)\b[^\)\]]*[\)\]]",
    re.I,
)
_TAG_DASH = re.compile(
    r"\s+[-–—]\s+[^-–—]*\b(?:remaster(?:ed)?|version|edit|live|mono|stereo|radio)\b.*$", re.I
)
_ARTIST_SEP = re.compile(
    r"\s*(?:,|&|\s+x\s+|\s+(?:feat\.?|ft\.?|featuring|with|vs\.?)\s+)\s*", re.I
)


def clean_title(title: str) -> str:
    """The title without "feat." credits and remaster/version tags."""
    out = title
    for pattern in (_FEAT_PAREN, _FEAT_TAIL, _TAG_PAREN, _TAG_DASH):
        out = pattern.sub("", out)
    return out.strip() or title.strip()


_ANY_TAIL = re.compile(r"\s+[-–—]\s+.*$|\s*[\(\[][^\)\]]*[\)\]]\s*$")


def bare_title(title: str) -> str:
    """The title without any trailing "- …" or "(…)" part, whatever it says.

    Labels tag releases in ways no keyword list keeps up with ("- Remix",
    "(HardTekk)"), and the catalog often files the song under the plain name.
    """
    out = _ANY_TAIL.sub("", clean_title(title)).strip()
    return out or clean_title(title)


def _split_artists(artist: str) -> list[str]:
    parts = [p.strip() for p in _ARTIST_SEP.split(artist) if p and p.strip()]
    return parts or [artist.strip()]


def _primary_artist(artist: str) -> str:
    """The first credited artist, for searching.

    MPRIS joins several artists with ", ", and that is the split to trust:
    "&" is as often part of one act's name ("Darius & Finlay", "Simon &
    Garfunkel") as it joins two.
    """
    return artist.split(",")[0].strip() or artist.strip()


def normalize_name(text: str) -> str:
    """Case-, accent- and punctuation-blind form for comparing names.

    Letters of every script survive — dropping everything outside a-z
    would reduce a Chinese title to "" and "" matches anything.
    """
    text = unicodedata.normalize("NFKD", text.casefold().replace("&", " and "))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", text)


def _store_country() -> str:
    """The iTunes store to ask first, from the desktop locale ("de_DE" → DE)."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        m = re.match(r"[a-z]{2,3}_([A-Z]{2})\b", os.environ.get(var, ""))
        if m:
            return m.group(1)
    return "US"


def _to_lookup(result: dict) -> TrackLookup:
    art = str(result.get("artworkUrl100", "") or "")
    cover_url = art.replace("100x100bb.jpg", "600x600bb.jpg") if art else ""
    try:
        duration_ms = int(result.get("trackTimeMillis", 0) or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    return TrackLookup(
        cover_url=cover_url,
        song_url=str(result.get("trackViewUrl", "") or ""),
        duration_ms=duration_ms,
        album=str(result.get("collectionName", "") or ""),
    )


def _best_match(results, artist: str, title: str, album: str) -> TrackLookup | None:
    """The result that is this song, or None when none of them is."""
    if not isinstance(results, list):
        return None
    # Generous on purpose — the title has to match as well.
    names = [artist, *artist.split(","), *_split_artists(artist)]
    want_artists = {a for a in (normalize_name(x) for x in names) if a}
    want_title = normalize_name(clean_title(title))
    want_album = normalize_name(clean_title(album)) if album else ""
    if not want_artists or not want_title:
        return None
    best, best_score = None, -1
    for r in results:
        if not isinstance(r, dict) or r.get("kind", "song") != "song":
            continue
        got_artist = normalize_name(str(r.get("artistName", "") or ""))
        got_title = normalize_name(clean_title(str(r.get("trackName", "") or "")))
        if not got_artist or not got_title:
            continue
        if not any(a in got_artist or got_artist in a for a in want_artists):
            continue
        exact = got_title == want_title
        close = min(len(got_title), len(want_title)) >= 4 and (
            want_title in got_title or got_title in want_title
        )
        if not (exact or close):
            continue
        got_album = normalize_name(clean_title(str(r.get("collectionName", "") or "")))
        score = (2 if exact else 0) + (1 if want_album and want_album == got_album else 0)
        if score > best_score:
            best, best_score = r, score
    return _to_lookup(best) if best is not None else None


def _query(term: str, country: str, limit: int) -> list:
    params = urllib.parse.urlencode(
        {"term": term, "country": country, "media": "music", "entity": "song", "limit": limit}
    )
    url = f"{_ITUNES_SEARCH}?{params}"
    if not url.startswith("https://"):
        # Defensive: _ITUNES_SEARCH is a module constant, but be explicit so
        # the urlopen call below cannot ever be coerced into file:// or ftp://.
        return []
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with (
        dev_metrics.network("itunes"),
        urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r,  # nosec B310
    ):
        data = json.load(r)
    # iTunes can in theory answer with a non-dict (an error string, an
    # unwrapped list…) — anything but the expected shape is "no results".
    results = data.get("results", []) if isinstance(data, dict) else []
    return results if isinstance(results, list) else []


def _search(artist: str, title: str, album: str) -> TrackLookup:
    """Ask the catalog until an answer matches. Raises LookupError when
    the catalog could not be reached at all — that is not a miss."""
    clean = clean_title(title)
    primary = _primary_artist(artist)
    local = _store_country()
    attempts = [(f"{artist} {clean}", local, 10)]
    if primary != artist:
        attempts.append((f"{primary} {clean}", local, 10))
    if local != "US":
        attempts.append((f"{primary} {clean}", "US", 10))
    bare = bare_title(title)
    if bare != clean:
        attempts.append((f"{primary} {bare}", local, 10))
    # Last resort: the title alone, checked against the artist.
    attempts.append((clean, local, 25))
    if bare != clean:
        attempts.append((bare, local, 25))

    reached = False
    for term, country, limit in dict.fromkeys(attempts):
        try:
            results = _query(term, country, limit)
        except Exception as e:
            log.debug("iTunes query %r (%s) failed: %s", term, country, e)
            continue
        reached = True
        match = _best_match(results, artist, title, album)
        if match is not None:
            log.debug("iTunes: %s — %s matched via %r (%s)", artist, title, term, country)
            return match
    if not reached:
        raise LookupError("the iTunes Search API could not be reached")
    log.info("Cover lookup: no catalog match for %s — %s", artist, title)
    return TrackLookup()


def lookup_track_info(artist: str, title: str, album: str = "") -> TrackLookup:
    """Resolve a track to its cover URL, Apple Music page and length.

    A miss is cached too (for ``_NEGATIVE_TTL_S``), so a song that isn't
    in the catalog doesn't cost a round of requests every time it plays.
    Raises LookupError when iTunes can't be reached; nothing is cached
    then, and the caller retries later.
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

    result = _search(artist, title, album)
    _write_cache(key, result)
    return result


# --------------------------------------------------------------------------- #
# Cache                                                                        #
# --------------------------------------------------------------------------- #


def _key(artist: str, title: str, album: str) -> str:
    """Stable filename-safe cache key. Hash is non-cryptographic — used only
    for cache-file naming, never for security or integrity decisions."""
    raw = f"{artist}|{title}|{album}".lower().encode("utf-8")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def _read_cache(key: str) -> TrackLookup | None:
    """The cached answer, or None when there is none worth trusting."""
    p = cover_cache_dir() / f"{key}.txt"
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    if len(lines) < 6 or lines[3].strip() != _CACHE_VERSION:
        return None  # written by the old first-hit matcher
    cover_url = lines[0].strip()
    try:
        duration_ms = int(lines[2].strip() or 0)
        checked = int(lines[4].strip() or 0)
    except ValueError:
        return None
    if not cover_url and time.time() - checked > _NEGATIVE_TTL_S:
        return None  # an old miss — the catalog gets another chance
    return TrackLookup(
        cover_url=cover_url,
        song_url=lines[1].strip(),
        duration_ms=duration_ms,
        album=lines[5].strip(),
    )


def _make_private_dir(d: Path) -> None:
    # The cached lookups and covers reveal what the user listens to.
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)


def _write_cache(key: str, info: TrackLookup) -> None:
    """Write the URL-cache .txt file. A failure here
    must not propagate up because the caller is in a worker-thread
    Future and the in-memory cache is already populated. The user
    just won't get the disk-cache hit on next session, which is
    acceptable degradation."""
    d = cover_cache_dir()
    try:
        _make_private_dir(d)
        (d / f"{key}.txt").write_text(
            f"{info.cover_url}\n{info.song_url}\n{info.duration_ms}\n"
            f"{_CACHE_VERSION}\n{int(time.time())}\n{info.album}\n",
            encoding="utf-8",
        )
    except OSError as e:
        log.debug("Cover URL-cache write failed for %s: %s", key, e)


def image_name(url: str) -> str:
    return hashlib.blake2b(url.encode("utf-8"), digest_size=12).hexdigest() + ".jpg"


def image_path_for_url(url: str) -> Path:
    """Where the kept image of a song in the history lives."""
    return cover_cache_dir() / image_name(url)


def has_image(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def keep_cover_images(urls: set[str], sources: list[Path]) -> None:
    """Make the kept images exactly those of ``urls``.

    A missing one is copied from the first directory in ``sources`` that
    has it; every other image in the cover cache is deleted.
    """
    d = cover_cache_dir()
    wanted = {image_name(u) for u in urls if u}
    for name in wanted:
        dest = d / name
        if has_image(dest):
            continue
        src = next((s / name for s in sources if has_image(s / name)), None)
        if src is None:
            continue
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            _make_private_dir(d)
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        except OSError as e:
            log.debug("Could not keep cover %s: %s", name, e)
            with contextlib.suppress(OSError):
                tmp.unlink()
    if not d.is_dir():
        return
    removed = 0
    try:
        for p in d.glob("*.jpg*"):
            if p.name not in wanted:
                with contextlib.suppress(OSError):
                    p.unlink()
                    removed += 1
    except OSError as e:
        log.debug("Cover cleanup failed: %s", e)
    if removed:
        log.debug("Deleted %d cover image(s) not in the history", removed)


def download_cover_image(url: str, dest: Path) -> Path | None:
    """Download ``url`` to ``dest`` and return it, or None on failure.

    ``dest``'s directory must exist already. Used only for
    ``notify-send -i`` and the history's thumbnails; Discord fetches the
    URL itself.
    """
    if not url.startswith("https://"):
        log.debug("Cover download refused: non-HTTPS URL (%s)", url[:60])
        return None
    log.debug("Cover download starting: %s → %s", url, dest.name)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with (
            dev_metrics.network("cover_image"),
            urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r,  # nosec B310
        ):
            # One byte past the cap tells "exactly 2 MB" from "cut off".
            data = r.read(_MAX_IMAGE_BYTES + 1)
    except Exception as e:
        log.debug("Cover image download failed for %s: %s", url, e)
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        log.warning("Cover image larger than %d bytes, discarded: %s", _MAX_IMAGE_BYTES, url[:120])
        return None
    log.debug("Cover downloaded: %s (%d bytes)", dest.name, len(data))
    # Write to a sibling temp file and atomically rename. Without this,
    # a daemon kill mid-download would leave a truncated file that
    # `has_image` happily accepts on the next tick.
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError as e:
        log.debug("Cover image write failed for %s: %s", url, e)
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()
        return None
    return dest
