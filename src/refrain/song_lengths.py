"""Song lengths Refrain measured itself, for songs the catalog has none for.

Stored as ``<key> <seconds> <confirmations>`` per line; the key is a hash, so no titles.
Last.fm's length for a song sits in a second file as ``<key> <seconds>``, 0 when it has none."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
from pathlib import Path

from refrain.cover_art import clean_title, normalize_name
from refrain.paths import state_dir

log = logging.getLogger(__name__)

# One matching second reading proves nothing — a skip ends a song early too.
# Two agreeing plays do: two skips landing within two seconds of each other
# is not something a listener does.
CONFIRMATIONS_NEEDED = 2
AGREES_WITHIN_S = 2
# Below Last.fm's floor a length changes nothing; above this it is not a song.
MIN_LENGTH_S = 30
MAX_LENGTH_S = 3600
# 22 bytes a line, so the cap costs ~22 KB. Past it the least recently
# heard song goes.
MAX_ENTRIES = 1000
# The daemon asks for the playing song's length several times a second.
_KEY_CACHE_SIZE = 64


def song_key(artist: str, title: str, album: str) -> str:
    """A stable, opaque key for a song.

    Same normalisation as the catalog lookup, so "Overexposed (feat. …)" and
    "Overexposed" are one song rather than two.
    """
    raw = "\x1f".join(
        (
            normalize_name(clean_title(title)),
            normalize_name(artist),
            normalize_name(clean_title(album)),
        )
    )
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=8).hexdigest()


def lengths_path() -> Path:
    return state_dir() / "song_lengths.txt"


def _write_lines(path: Path, lines: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("".join(lines), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as e:
        with contextlib.suppress(OSError):
            tmp.unlink()
        log.debug("Could not save %s (%s)", path.name, e)


class LearnedLengths:
    """Lengths measured from whole plays. Failure-tolerant throughout: a
    file that can't be read or written costs the fallback, nothing else.

    Plasma reports only the buffered segment for songs the catalog lacks,
    and without a length Last.fm's rules can't be applied at all."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or lengths_path()
        self._lock = threading.Lock()
        # key -> (seconds, confirmations), oldest first: dicts keep
        # insertion order, which is the recency the cap goes by.
        self._entries: dict[str, tuple[int, int]] = self._load()
        # key -> Last.fm's length in seconds, 0 for none: a second opinion
        # that can stand in for the confirming play, asked once per song.
        self._references_path = self._path.with_name(f"{self._path.stem}_lastfm.txt")
        self._references: dict[str, int] = self._load_references()
        self._asked: set[str] = set()
        # A differing reading for a confirmed length, waiting for a second.
        self._challengers: dict[str, int] = {}
        self._keys: dict[tuple[str, str, str], str] = {}
        # Bumped by every change, so a caller can hold on to a length
        # until it may have moved.
        self.generation = 0

    def _load(self) -> dict[str, tuple[int, int]]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as e:
            log.debug("Measured song lengths unreadable (%s) — starting empty", e)
            return {}
        entries: dict[str, tuple[int, int]] = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) != 3:
                continue
            key, seconds, count = parts
            try:
                s, c = int(seconds), int(count)
            except ValueError:
                continue
            if len(key) == 16 and MIN_LENGTH_S <= s <= MAX_LENGTH_S and c > 0:
                entries[key] = (s, c)
        return entries

    def _load_references(self) -> dict[str, int]:
        try:
            text = self._references_path.read_text(encoding="utf-8")
        except OSError:
            return {}
        references: dict[str, int] = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and len(parts[0]) == 16 and parts[1].isdigit():
                references[parts[0]] = int(parts[1])
        return references

    def _save_locked(self) -> None:
        _write_lines(self._path, [f"{k} {s} {c}\n" for k, (s, c) in self._entries.items()])

    def _key_locked(self, artist: str, title: str, album: str) -> str:
        names = (artist, title, album)
        key = self._keys.get(names)
        if key is None:
            key = song_key(artist, title, album)
            if len(self._keys) >= _KEY_CACHE_SIZE:
                self._keys.pop(next(iter(self._keys)))
            self._keys[names] = key
        return key

    def get_ms(self, artist: str, title: str, album: str) -> int:
        """The song's measured length, or 0 while it is still unconfirmed."""
        with self._lock:
            entry = self._entries.get(self._key_locked(artist, title, album))
        if entry is None or entry[1] < CONFIRMATIONS_NEEDED:
            return 0
        return entry[0] * 1000

    def observe(self, artist: str, title: str, album: str, played_ms: int) -> bool:
        """Record how long a song ran. True when that just confirmed a length.

        A reading that disagrees with an unconfirmed one replaces it — the
        song's length doesn't change, so a disagreement means one of the two
        was a play that ended early. A confirmed length gives way only to two
        new readings that agree with each other.
        """
        seconds = round(played_ms / 1000)
        if not MIN_LENGTH_S <= seconds <= MAX_LENGTH_S:
            return False
        with self._lock:
            key = self._key_locked(artist, title, album)
            known = self._entries.pop(key, None)
            if known is not None and abs(known[0] - seconds) <= AGREES_WITHIN_S:
                entry = (known[0], min(known[1] + 1, CONFIRMATIONS_NEEDED))
                self._challengers.pop(key, None)
            elif known is not None and known[1] >= CONFIRMATIONS_NEEDED:
                challenger = self._challengers.pop(key, None)
                if challenger is not None and abs(challenger - seconds) <= AGREES_WITHIN_S:
                    entry = (challenger, CONFIRMATIONS_NEEDED)
                else:
                    self._challengers[key] = seconds
                    entry = known
            else:
                entry = (seconds, 1)
            if entry[1] < CONFIRMATIONS_NEEDED and self._agrees_locked(key, entry[0]):
                entry = (entry[0], CONFIRMATIONS_NEEDED)
            self._entries[key] = entry
            while len(self._entries) > MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
            self.generation += 1
            self._save_locked()
        return entry[1] >= CONFIRMATIONS_NEEDED and entry != known

    def _agrees_locked(self, key: str, seconds: int) -> bool:
        reference = self._references.get(key, 0)
        return reference > 0 and abs(reference - seconds) <= AGREES_WITHIN_S

    def wants_reference(self, artist: str, title: str, album: str) -> bool:
        """True, once, for a song measured just once and never looked up."""
        with self._lock:
            key = self._key_locked(artist, title, album)
            entry = self._entries.get(key)
            if (
                entry is None
                or entry[1] >= CONFIRMATIONS_NEEDED
                or key in self._references
                or key in self._asked
            ):
                return False
            self._asked.add(key)
            return True

    def add_reference(self, artist: str, title: str, album: str, length_ms: int) -> bool | None:
        """Keep Last.fm's length for a song (0: it has none).

        True when it confirmed the measured length, as a second agreeing
        play would, False when it disagrees, None when it says nothing.
        """
        seconds = round(length_ms / 1000) if length_ms > 0 else 0
        with self._lock:
            key = self._key_locked(artist, title, album)
            self._references.pop(key, None)
            self._references[key] = seconds
            while len(self._references) > MAX_ENTRIES:
                self._references.pop(next(iter(self._references)))
            _write_lines(self._references_path, [f"{k} {s}\n" for k, s in self._references.items()])
            entry = self._entries.get(key)
            if not seconds or entry is None or entry[1] >= CONFIRMATIONS_NEEDED:
                return None
            if not self._agrees_locked(key, entry[0]):
                return False
            self._entries[key] = (entry[0], CONFIRMATIONS_NEEDED)
            self.generation += 1
            self._save_locked()
        return True

    def forget(self, artist: str, title: str, album: str) -> None:
        with self._lock:
            if self._entries.pop(self._key_locked(artist, title, album), None) is not None:
                self.generation += 1
                self._save_locked()
