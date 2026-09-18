"""Song lengths Refrain measured itself, for songs the catalog has none for.

A song the iTunes catalog doesn't know has no length in the browser at all:
plasma reports the buffered media segment, not the song. Without a length
Last.fm's rules can't be applied, so the play is never scrobbled — around
one song in ten here. But a song that plays to its end measures itself: our
own clock says how long it ran. Heard twice with the same answer, that is
the song's length, and the next play counts.

Stored as ``<key> <seconds> <confirmations>`` per line — the key a hash, so
the file holds no song titles.
"""

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


def song_key(artist: str, title: str, album: str) -> str:
    """A stable, opaque key for a song.

    Same normalisation as the catalog lookup, so "f**k dich (feat. …)" and
    "f**k dich" are one song rather than two.
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


class LearnedLengths:
    """Lengths measured from whole plays. Failure-tolerant throughout: a
    file that can't be read or written costs the fallback, nothing else."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or lengths_path()
        self._lock = threading.Lock()
        # key -> (seconds, confirmations), oldest first: dicts keep
        # insertion order, which is the recency the cap goes by.
        self._entries: dict[str, tuple[int, int]] = self._load()

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

    def _save_locked(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                "".join(f"{k} {s} {c}\n" for k, (s, c) in self._entries.items()), encoding="utf-8"
            )
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError as e:
            with contextlib.suppress(OSError):
                tmp.unlink()
            log.debug("Could not save the measured song lengths (%s)", e)

    def get_ms(self, artist: str, title: str, album: str) -> int:
        """The song's measured length, or 0 while it is still unconfirmed."""
        with self._lock:
            entry = self._entries.get(song_key(artist, title, album))
        if entry is None or entry[1] < CONFIRMATIONS_NEEDED:
            return 0
        return entry[0] * 1000

    def observe(self, artist: str, title: str, album: str, played_ms: int) -> bool:
        """Record how long a song ran. True when it is now confirmed.

        A reading that disagrees with the one on file replaces it — the
        song's length doesn't change, so a disagreement means one of the two
        was a play that ended early.
        """
        seconds = round(played_ms / 1000)
        if not MIN_LENGTH_S <= seconds <= MAX_LENGTH_S:
            return False
        key = song_key(artist, title, album)
        with self._lock:
            known = self._entries.pop(key, None)
            if known is not None and abs(known[0] - seconds) <= AGREES_WITHIN_S:
                entry = (known[0], min(known[1] + 1, CONFIRMATIONS_NEEDED))
            else:
                entry = (seconds, 1)
            self._entries[key] = entry
            while len(self._entries) > MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
            self._save_locked()
        return entry[1] >= CONFIRMATIONS_NEEDED

    def forget(self, artist: str, title: str, album: str) -> None:
        with self._lock:
            if self._entries.pop(song_key(artist, title, album), None) is not None:
                self._save_locked()

    def clear(self) -> None:
        with self._lock:
            self._entries = {}
            with contextlib.suppress(OSError):
                self._path.unlink()
