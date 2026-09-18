"""Persistent queue of plays waiting to be scrobbled, one JSON line per play.

A corrupt line never poisons the rest; failing I/O never crashes the daemon."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from refrain.paths import state_dir

log = logging.getLogger(__name__)

# Plenty for any realistic offline gap (≈ 70 h of music at 4 min/track).
# Past this the oldest entries are dropped — an unbounded queue from a
# permanently-broken session would otherwise grow forever.
_MAX_QUEUE = 1000


def queue_path() -> Path:
    return state_dir() / "scrobble_queue.jsonl"


def dedup_key(item: dict) -> str:
    """Stable identity for a queued play. Last.fm itself dedups by
    timestamp; we additionally avoid re-queueing the byte-identical
    play (same track started at the same second). SHA-256, not MD5: the
    format is ours, unlike the Last.fm request signature."""
    raw = "{}\x1f{}\x1f{}".format(
        str(item.get("artist", "")),
        str(item.get("track", "")),
        int(item.get("timestamp", 0) or 0),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize(item: dict) -> dict | None:
    """Coerce a raw item to the on-disk schema, or None if unusable.

    Last.fm requires a non-empty artist + track and a positive
    timestamp; anything else can never be scrobbled, so it's dropped
    rather than queued forever.
    """
    artist = str(item.get("artist", "")).strip()
    track = str(item.get("track", "")).strip()
    try:
        timestamp = int(item.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        timestamp = 0
    if not artist or not track or timestamp <= 0:
        return None
    try:
        duration = int(item.get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0
    norm = {
        "artist": artist,
        "track": track,
        "album": str(item.get("album", "")).strip(),
        "timestamp": timestamp,
        "duration": max(0, duration),
    }
    # The Last.fm account it was heard under. Entries from before this
    # was recorded carry none and go to whichever account is connected.
    account = item.get("account")
    if isinstance(account, str) and account.strip():
        norm["account"] = account.strip()
    return norm


class ScrobbleQueue:
    """Disk-backed FIFO of pending scrobbles.

    A new play is appended as one line; removals rewrite the whole file
    atomically. The file may run up to ``_slack`` rows past the cap before
    it is compacted — loading keeps only the newest ``max_entries`` rows,
    which are exactly the ones in memory.
    """

    def __init__(self, path: Path | None = None, max_entries: int = _MAX_QUEUE) -> None:
        self._path = path or queue_path()
        self._max = max(1, int(max_entries))
        self._slack = max(1, self._max // 10)
        self._lock = threading.Lock()
        # Rows in the file, and whether it holds anything besides them
        # (a torn or corrupt line) that an append would build on.
        self._file_rows = 0
        self._file_dirty = False
        self._items: list[dict] = self._load()
        # Dedup key -> how many queued rows carry it. A count, not a set:
        # a file from before dedup can hold the same play twice.
        self._keys: dict[str, int] = {}
        for it in self._items:
            self._add_key(it)

    def _add_key(self, item: dict) -> None:
        key = dedup_key(item)
        self._keys[key] = self._keys.get(key, 0) + 1

    def _drop_key(self, item: dict) -> None:
        key = dedup_key(item)
        left = self._keys.get(key, 0) - 1
        if left > 0:
            self._keys[key] = left
        else:
            self._keys.pop(key, None)

    # ------------------------------------------------------------- io

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        items: list[dict] = []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("Scrobble queue unreadable (%s) — starting empty", e)
            self._file_dirty = True
            return []
        clean = not text or text.endswith("\n")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                log.debug("Skipping corrupt scrobble-queue line")
                clean = False
                continue
            norm = _normalize(raw) if isinstance(raw, dict) else None
            if norm is not None:
                items.append(norm)
            else:
                clean = False
        self._file_rows = len(items)
        self._file_dirty = not clean
        if len(items) > self._max:
            items = items[-self._max :]
        return items

    def _append_locked(self, item: dict) -> None:
        if self._file_dirty or self._file_rows >= self._max + self._slack:
            self._save_locked()
            return
        line = json.dumps(item, ensure_ascii=False) + "\n"
        try:
            # No O_CREAT: a file that went missing needs the whole queue.
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND)
        except FileNotFoundError:
            self._save_locked()
            return
        except OSError as e:
            self._file_dirty = True
            log.warning("Could not persist scrobble queue (%s)", e)
            return
        try:
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            self._file_dirty = True
            log.warning("Could not persist scrobble queue (%s)", e)
            return
        self._file_rows += 1

    def _save_locked(self) -> None:
        """Persist ``self._items``. A write failure leaves
        the in-memory queue intact for this session and is logged, not
        raised — a daemon tick must never die because state-dir is
        read-only."""
        self._file_dirty = True
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            payload = "\n".join(json.dumps(it, ensure_ascii=False) for it in self._items)
            if payload:
                payload += "\n"
            try:
                tmp.write_text(payload, encoding="utf-8")
                # Owner-only, like the history: it says what someone listened to.
                with contextlib.suppress(OSError):
                    os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
            except OSError:
                if tmp.exists():
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                raise
        except OSError as e:
            log.warning("Could not persist scrobble queue (%s)", e)
            return
        self._file_rows = len(self._items)
        self._file_dirty = False

    # --------------------------------------------------------- mutate

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def pending(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def enqueue(self, item: dict) -> bool:
        """Add a played track. Returns True if it was stored, False if
        it was a duplicate or unusable (missing artist/track/ts)."""
        norm = _normalize(item)
        if norm is None:
            log.debug("Scrobble dropped — missing artist/track/timestamp")
            return False
        with self._lock:
            if dedup_key(norm) in self._keys:
                log.debug("Scrobble already queued — skipping duplicate")
                return False
            self._items.append(norm)
            self._add_key(norm)
            dropped = 0
            while len(self._items) > self._max:
                self._drop_key(self._items.pop(0))
                dropped += 1
            if dropped:
                log.warning(
                    "Scrobble queue full (%d) — dropped %d oldest entr%s",
                    self._max,
                    dropped,
                    "y" if dropped == 1 else "ies",
                )
            self._append_locked(norm)
        return True

    def drop_other_accounts(self, account: str) -> int:
        """Remove entries heard under a Last.fm account other than
        ``account``, so switching accounts never scrobbles one's plays to
        the other. Returns how many went."""
        if not account:
            return 0
        wanted = account.casefold()
        with self._lock:
            kept = [it for it in self._items if it.get("account", account).casefold() == wanted]
            dropped = len(self._items) - len(kept)
            if dropped:
                self._items = kept
                self._keys = {}
                for it in kept:
                    self._add_key(it)
                self._save_locked()
        return dropped

    def drain(self, submit: Callable[[list[dict]], int], batch_size: int = 50) -> int:
        """Submit queued scrobbles oldest-first in batches.

        ``submit(batch)`` should POST the batch and return the accepted
        count, or raise on failure. A batch that submits without raising
        is removed from the queue; on the first raising batch we stop
        and keep everything still pending (it'll retry next drain).
        Returns the total number of entries successfully submitted.
        """
        submitted = 0
        while True:
            with self._lock:
                if not self._items:
                    break
                batch = self._items[: max(1, batch_size)]
            try:
                submit(batch)
            except Exception as e:
                log.info(
                    "Scrobble submit failed (%s) — %d entr%s kept queued",
                    e,
                    len(self),
                    "y" if len(self) == 1 else "ies",
                )
                break
            with self._lock:
                # Drop exactly the rows we just submitted (by identity of
                # value); a concurrent enqueue appended to the end so
                # slicing the prefix is safe.
                for it in self._items[: len(batch)]:
                    self._drop_key(it)
                del self._items[: len(batch)]
                submitted += len(batch)
                self._save_locked()
        return submitted
