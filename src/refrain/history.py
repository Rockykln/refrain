"""Recently played: the last songs Refrain saw, kept on this machine.

A song shows up the moment it starts playing, as "now playing", and
stays once it counts as listened — Last.fm's rule: half its length or
four minutes, whichever comes first, and never for anything of 30 s or
less. A song skipped before that leaves no trace in the list. Using the
rule Last.fm uses means the history and a Last.fm profile agree about
what was heard.

Stored in ``$XDG_STATE_HOME/refrain/history.json``: the songs that
counted, plus the one playing right now with how much of it has been
heard and where the player had it. That second part is written every
``_PROGRESS_SAVE_EVERY_MS`` of play and on quit, so stopping,
restarting or crashing mid-song doesn't start the count again from zero
— if the same play is still going when Refrain comes back, it carries
on as the same row. A song heard to the end and started again is a new
play with a row of its own, restart or not. Writes are atomic (tmp +
``os.replace``) like the scrobble queue.

Local only: nothing here is ever sent anywhere, which is why privacy
mode "off" doesn't stop it. It has its own switch, and turning that off
deletes the file.

Owned by the daemon thread. The GUI never touches a ``PlayHistory``; it
gets immutable :class:`HistorySnapshot` copies through
``DaemonWorker.historyChanged``.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from refrain.config import HISTORY_LIMIT_MAX, HistoryConfig
from refrain.paths import state_dir
from refrain.scrobble import accrue_play_ms, continues_play, is_replay, should_scrobble
from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

_FILE_VERSION = 1
# Without a known length Last.fm's half-way mark can't be computed, but
# its four-minute cap still can — so an AVRCP source that reports no
# duration isn't shut out of the history altogether.
_UNKNOWN_LENGTH_COUNTS_AFTER_MS = 240_000
# How often the progress of a song that hasn't counted yet is written.
# Longer than a typical skip, so skipping through a playlist still never
# touches the disk.
_PROGRESS_SAVE_EVERY_MS = 30_000
# Whether a song after a restart is still the same play is decided by
# scrobble.continues_play — shared, so the history and Last.fm agree.
# Apple Music's web player reports "paused" for a poll or two between
# songs — the end of one, the loading of the next. A pause only shows as
# "Paused" once it has lasted this long, so that doesn't flicker.
_PAUSE_SHOWN_AFTER_S = 2.0


def history_path() -> Path:
    return state_dir() / "history.json"


def clamp_limit(value: int) -> int:
    """A usable song count from whatever the config holds."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return HistoryConfig().max_entries
    return max(1, min(HISTORY_LIMIT_MAX, n))


def counts_as_played(played_ms: int, duration_ms: int) -> bool:
    """Has this song been heard long enough to stay in the history?"""
    if duration_ms <= 0:
        return played_ms >= _UNKNOWN_LENGTH_COUNTS_AFTER_MS
    return should_scrobble(played_ms, duration_ms)


@dataclass
class HistoryEntry:
    title: str
    artist: str = ""
    album: str = ""
    source: str = ""  # "mpris" | "bluetooth"
    player: str = ""  # browser / device name, when the source reported one
    started_at: int = 0  # unix seconds, when it started playing
    duration_ms: int = 0
    cover_url: str = ""
    url: str = ""  # Apple Music page for the song, when known
    scrobbled: bool = False


@dataclass(frozen=True)
class HistorySnapshot:
    """What the window shows, newest first.

    ``now_playing`` means ``entries[0]`` is the song on right now (it may
    not have counted yet); ``playing`` is False while that song is paused.
    """

    entries: tuple[HistoryEntry, ...] = ()
    now_playing: bool = False
    playing: bool = False
    limit: int = 30
    enabled: bool = True


@dataclass
class _Current:
    key: str
    entry: HistoryEntry
    played_ms: int = 0
    last_mono: float | None = None
    playing: bool = True
    # True once the song met the rule — from then on `entry` is also the
    # first element of the stored list (the same object, not a copy).
    counted: bool = False
    saved_played_ms: int = 0
    # What the window shows, which lags `playing` by _PAUSE_SHOWN_AFTER_S
    # when it goes from playing to paused.
    shown_playing: bool = True
    paused_at: float | None = None
    # The player's own position at the last poll; None when unknown.
    position_ms: int | None = None


@dataclass
class _Resume:
    """The song that was playing when Refrain last stopped.

    ``counted`` means it had already made the list — then ``entry`` is the
    list's newest song itself, and carrying on with it must not add it a
    second time. ``position_ms`` is where the player had it, if it said.
    """

    entry: HistoryEntry
    played_ms: int
    saved_at: int
    counted: bool = False
    position_ms: int | None = None


def _content_key(track: TrackInfo) -> str:
    return f"{track.source}|{track.title}|{track.artist}|{track.album}"


def _entry_key(entry: HistoryEntry) -> str:
    return f"{entry.source}|{entry.title}|{entry.artist}|{entry.album}"


def _is_candidate(track: TrackInfo) -> bool:
    return track.has_track and track.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED)


def _is_song_page(url: str) -> bool:
    """Does a browser tab's URL point at one song?

    A tab playing from an album or a playlist reports that page, not the
    song — kept, it would send a click on the song somewhere else. Apple
    Music addresses a single song as ``/song/…`` or as an album page with
    ``?i=<track id>``.
    """
    return url.startswith("https://") and ("/song/" in url or "?i=" in url or "&i=" in url)


def _entry_from_dict(raw: dict) -> HistoryEntry | None:
    """Coerce one stored row, or None if it's unusable."""
    title = str(raw.get("title", "") or "").strip()
    if not title:
        return None

    def _int(name: str) -> int:
        try:
            return max(0, int(raw.get(name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _str(name: str) -> str:
        value = raw.get(name, "")
        return value if isinstance(value, str) else ""

    return HistoryEntry(
        title=title,
        artist=_str("artist"),
        album=_str("album"),
        source=_str("source"),
        player=_str("player"),
        started_at=_int("started_at"),
        duration_ms=_int("duration_ms"),
        cover_url=_str("cover_url"),
        url=_str("url"),
        scrobbled=raw.get("scrobbled") is True,
    )


def _resume_from_dict(raw) -> _Resume | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("entry"), dict):
        return None
    entry = _entry_from_dict(raw["entry"])
    if entry is None:
        return None
    try:
        played_ms = max(0, int(raw.get("played_ms", 0)))
        saved_at = int(raw.get("saved_at", 0))
    except (TypeError, ValueError):
        return None
    pos = raw.get("position_ms")
    return _Resume(
        entry,
        played_ms,
        saved_at,
        counted=raw.get("counted") is True,
        position_ms=pos if type(pos) is int and pos >= 0 else None,
    )


def _current_dict(
    entry: HistoryEntry, counted: bool, played_ms: int, position_ms: int | None, saved_at: int
) -> dict:
    return {
        "entry": dataclasses.asdict(entry),
        "counted": counted,
        "played_ms": played_ms,
        "position_ms": position_ms,
        "saved_at": saved_at,
    }


def _same_play(a: HistoryEntry, b: HistoryEntry) -> bool:
    return (a.started_at, a.title, a.artist) == (b.started_at, b.title, b.artist)


def _label(entry: HistoryEntry) -> str:
    return f"{entry.artist or '—'} — {entry.title}"


def _mmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


class PlayHistory:
    """The recently-played list plus the song being tracked right now."""

    def __init__(self, cfg: HistoryConfig, path: Path | None = None) -> None:
        self._path = path or history_path()
        self._lock = threading.Lock()
        self._enabled = bool(cfg.enabled)
        self._limit = clamp_limit(cfg.max_entries)
        self._entries: list[HistoryEntry] = []  # counted songs, newest first
        self._cur: _Current | None = None
        self._resume: _Resume | None = None
        # A song the user took out of the list while it played; kept out
        # until the next song, instead of coming straight back.
        self._ignore_key: str | None = None
        self._last_wall = time.time()
        if self._enabled:
            self._entries, self._resume = self._load()
            dropped = self._trim_locked()
            if dropped:
                self._save_locked()
            log.info("History: %d song(s) loaded (limit %d)", len(self._entries), self._limit)
        elif self._delete_file():
            # A hand-edit, or a file left behind by a crash between the
            # switch going off and the delete — the switch is a promise
            # that nothing is kept.
            log.info("History is off — deleted the stored history")

    # ------------------------------------------------------------- feed

    def update(
        self,
        track: TrackInfo,
        duration_ms: int,
        cover_url: str = "",
        song_url: str = "",
        now_wall: float | None = None,
        now_mono: float | None = None,
        position_ms: int | None = None,
        restarted: bool = False,
    ) -> bool:
        """Feed one daemon poll. Returns True when the snapshot changed.

        ``duration_ms`` is the length the scrobbler is given, so "counts
        as played" here and a scrobble on Last.fm are the same decision.
        ``position_ms`` is the player's own position, or None when the
        daemon has none it trusts; it tells a replay, and a restart that
        carries on, from a new play. ``restarted`` says the player showed
        the song beginning again this poll (see ``resolve_position``) —
        the replay signal of a source without a usable position.
        """
        now_wall = time.time() if now_wall is None else now_wall
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            if not self._enabled:
                return False
            self._last_wall = now_wall
            key = _content_key(track) if _is_candidate(track) else None
            cur = self._cur
            replay = (
                cur is not None
                and key == cur.key
                and (
                    is_replay(cur.counted, cur.position_ms, position_ms)
                    or (restarted and cur.counted)
                )
            )
            if replay:
                # Heard past the mark once already: this is a second play,
                # just as it is a second scrobble on Last.fm.
                log.info("History: %s started over — a new play", _label(cur.entry))
            if cur is None or key != cur.key or replay:
                # The next song of a queue can report "paused" while it
                # loads. Following a song that was just playing, it starts
                # all the same — only a song nobody pressed play on waits.
                follows_play = cur is not None and (
                    cur.playing
                    or (
                        cur.paused_at is not None
                        and now_mono - cur.paused_at < _PAUSE_SHOWN_AFTER_S
                    )
                )
                changed = self._finish_current_locked()
                if key != self._ignore_key:
                    self._ignore_key = None
                elif key is not None:
                    return changed
                if key is not None and self._resume_locked(
                    track, key, duration_ms, position_ms, now_mono
                ):
                    self._cur.position_ms = position_ms
                    return True
                # Only a song that actually plays starts an entry — a tab
                # sitting paused at startup isn't something you heard.
                if key is not None and (track.status == PlaybackStatus.PLAYING or follows_play):
                    self._start_locked(
                        track, key, duration_ms, cover_url, song_url, now_wall, now_mono
                    )
                    self._cur.position_ms = position_ms
                    if track.status != PlaybackStatus.PLAYING:
                        self._cur.playing = False
                        self._cur.paused_at = now_mono
                    changed = True
                return changed

            playing = track.status == PlaybackStatus.PLAYING
            cur.played_ms, cur.last_mono = accrue_play_ms(
                cur.played_ms, cur.last_mono, playing, now_mono
            )
            changed = False
            dirty = False
            # A later catalog-corrected length can replace an early bogus
            # one, exactly as in the scrobbler. Not worth a repaint.
            if duration_ms > 0 and duration_ms != cur.entry.duration_ms:
                cur.entry.duration_ms = duration_ms
                dirty = cur.counted
            if cover_url and cover_url != cur.entry.cover_url:
                cur.entry.cover_url = cover_url
                changed = True
                dirty = dirty or cur.counted
            if song_url and song_url != cur.entry.url:
                cur.entry.url = song_url
                dirty = dirty or cur.counted
            cur.playing = playing
            cur.position_ms = position_ms
            if playing:
                cur.paused_at = None
                if not cur.shown_playing:
                    cur.shown_playing = True
                    changed = True
            else:
                if cur.paused_at is None:
                    cur.paused_at = now_mono
                if cur.shown_playing and now_mono - cur.paused_at >= _PAUSE_SHOWN_AFTER_S:
                    cur.shown_playing = False
                    changed = True
            if not cur.counted and counts_as_played(cur.played_ms, cur.entry.duration_ms):
                cur.counted = True
                self._entries.insert(0, cur.entry)
                self._trim_locked()
                dirty = True
                changed = True
                log.info(
                    "History: kept %s (played %s of %s)",
                    _label(cur.entry),
                    _mmss(cur.played_ms),
                    _mmss(cur.entry.duration_ms) if cur.entry.duration_ms else "?",
                )
            elif cur.played_ms - cur.saved_played_ms >= _PROGRESS_SAVE_EVERY_MS:
                # Counted or not: a crash should leave a recent position to
                # tell a restart that carries on from the song starting over.
                dirty = True
            if dirty:
                self._save_locked()
            return changed

    def mark_scrobbled(self, artist: str, title: str) -> bool:
        """Flag the newest matching song as sent to Last.fm."""
        with self._lock:
            if not self._enabled:
                return False
            for entry in self._entries:
                if entry.artist == artist and entry.title == title:
                    if entry.scrobbled:
                        return False
                    entry.scrobbled = True
                    self._save_locked()
                    log.debug("History: marked %s as scrobbled", _label(entry))
                    return True
            return False

    # ----------------------------------------------------------- control

    def remove(self, started_at: int, title: str, artist: str) -> bool:
        """Take one song out of the list. Returns True if it was there.

        The song playing right now can be taken out too; it then stays
        out until the next song starts, rather than reappearing as "now
        playing" on the very next poll.
        """

        def same(e: HistoryEntry) -> bool:
            return e.started_at == started_at and e.title == title and e.artist == artist

        with self._lock:
            if not self._enabled:
                return False
            removed = False
            cur = self._cur
            if cur is not None and same(cur.entry):
                self._ignore_key = cur.key
                self._cur = None
                removed = True
            before = len(self._entries)
            self._entries = [e for e in self._entries if not same(e)]
            removed = removed or len(self._entries) != before
            if self._resume is not None and same(self._resume.entry):
                self._resume = None
            if removed:
                self._save_locked()
                log.info("History: removed %s — %s", artist or "—", title)
            return removed

    def clear(self) -> bool:
        """Forget everything, including the song playing right now.

        That song comes back as "now playing" on the next poll and has
        to count again from zero — clearing means starting over.
        """
        with self._lock:
            had = bool(self._entries) or self._cur is not None
            self._entries = []
            self._cur = None
            self._resume = None
            self._ignore_key = None
            self._delete_file()
            log.info("History cleared")
            return had

    def reconfigure(self, cfg: HistoryConfig) -> bool:
        """Apply new settings. Returns True when the snapshot changed."""
        enabled = bool(cfg.enabled)
        limit = clamp_limit(cfg.max_entries)
        with self._lock:
            if not enabled:
                was_on = self._enabled
                self._enabled = False
                self._limit = limit
                self._entries = []
                self._cur = None
                self._resume = None
                if self._delete_file() or was_on:
                    log.info("History turned off — stored history deleted")
                return was_on
            changed = False
            if not self._enabled:
                self._enabled = True
                self._entries, self._resume = self._load()
                log.info("History turned on")
                changed = True
            if limit != self._limit:
                self._limit = limit
                dropped = self._trim_locked()
                if dropped:
                    self._save_locked()
                    log.info(
                        "History limit is now %d — dropped the %d oldest song(s)", limit, dropped
                    )
                else:
                    log.info("History limit is now %d", limit)
                changed = True
            return changed

    def shutdown(self) -> None:
        """Called on quit. A song that counted is stored already; one that
        hasn't yet is saved with its progress, to carry on from if it is
        still playing when Refrain comes back."""
        with self._lock:
            if not self._enabled:
                return
            cur = self._cur
            if cur is not None and not cur.counted:
                log.debug(
                    "History: saving %s at %s for after the restart",
                    _label(cur.entry),
                    _mmss(cur.played_ms),
                )
            self._save_locked()
            self._cur = None

    def snapshot(self) -> HistorySnapshot:
        with self._lock:
            if not self._enabled:
                return HistorySnapshot(limit=self._limit, enabled=False)
            cur = self._cur
            entries = list(self._entries)
            if cur is not None and not cur.counted:
                entries.insert(0, cur.entry)
            entries = entries[: self._limit]
            return HistorySnapshot(
                entries=tuple(dataclasses.replace(e) for e in entries),
                now_playing=cur is not None,
                playing=cur.shown_playing if cur is not None else False,
                limit=self._limit,
                enabled=True,
            )

    # ---- internals (call with self._lock held) -------------------------

    def _start_locked(
        self,
        track: TrackInfo,
        key: str,
        duration_ms: int,
        cover_url: str,
        song_url: str,
        now_wall: float,
        now_mono: float,
    ) -> None:
        entry = HistoryEntry(
            title=track.title,
            artist=track.artist,
            album=track.album,
            source=track.source,
            player=track.player,
            started_at=int(now_wall),
            duration_ms=max(0, duration_ms),
            cover_url=cover_url,
            url=song_url or (track.url if _is_song_page(track.url) else ""),
        )
        self._cur = _Current(key=key, entry=entry, last_mono=now_mono)
        log.debug("History: now playing %s [%s]", _label(entry), track.source)

    def _resume_locked(
        self,
        track: TrackInfo,
        key: str,
        duration_ms: int,
        position_ms: int | None,
        now_mono: float,
    ) -> bool:
        """Carry on with the song that was playing when Refrain stopped.

        Only the first song seen after a start is compared. It goes on as
        the same entry — counting on from where it was, or, if it had
        counted already, as the list's newest song rather than a second
        copy — only when it is the same play: the same song, soon enough,
        and, where the player reports a position, one that follows on
        from where it was. A song that ended and began again meanwhile
        is a new play with a row of its own. A song that never counted
        and isn't carried on is let go.
        """
        resume, self._resume = self._resume, None
        if resume is None:
            return False
        entry = resume.entry
        away_s = max(0.0, self._last_wall - resume.saved_at)
        same_song = _entry_key(entry) == key
        same = same_song and continues_play(
            away_s, entry.duration_ms or duration_ms, resume.position_ms, position_ms
        )
        if same_song and not same:
            log.debug(
                "History: %s is on again, but not the play from before the restart "
                "(%.0f s away, at %s, was at %s)",
                _label(entry),
                away_s,
                "?" if position_ms is None else _mmss(position_ms),
                "?" if resume.position_ms is None else _mmss(resume.position_ms),
            )
        if not same:
            if resume.counted:
                return False  # it's in the list either way; nothing to let go
            log.debug(
                "History: let go of %s from before the restart — played %s, not enough to count",
                _label(entry),
                _mmss(resume.played_ms),
            )
            self._save_locked()
            return False
        if duration_ms > 0:
            entry.duration_ms = duration_ms
        self._cur = _Current(
            key=key,
            entry=entry,
            played_ms=resume.played_ms,
            last_mono=now_mono,
            playing=track.status == PlaybackStatus.PLAYING,
            counted=resume.counted,
            saved_played_ms=resume.played_ms,
            paused_at=None if track.status == PlaybackStatus.PLAYING else now_mono,
        )
        if resume.counted:
            log.info("History: carrying on with %s (already in the list)", _label(entry))
        else:
            log.info(
                "History: carrying on with %s (%s heard before the restart)",
                _label(entry),
                _mmss(resume.played_ms),
            )
        return True

    def _finish_current_locked(self) -> bool:
        cur = self._cur
        if cur is None:
            return False
        self._cur = None
        if cur.counted:
            # Already stored; write once more so a length or cover that
            # arrived after it counted is kept too.
            self._save_locked()
        else:
            log.debug(
                "History: dropped %s — played %s, not enough to count",
                _label(cur.entry),
                _mmss(cur.played_ms),
            )
            if cur.saved_played_ms:
                self._save_locked()  # its saved progress is stale now
        return True

    def _trim_locked(self) -> int:
        dropped = len(self._entries) - self._limit
        if dropped <= 0:
            return 0
        del self._entries[self._limit :]
        log.debug("History: dropped %d song(s) past the limit of %d", dropped, self._limit)
        return dropped

    def _load(self) -> tuple[list[HistoryEntry], _Resume | None]:
        if not self._path.exists():
            return [], None
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("History file %s unreadable (%s) — starting empty", self._path, e)
            return [], None
        try:
            raw = json.loads(text)
            rows = raw.get("entries") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                raise ValueError("no 'entries' list")
        except ValueError as e:
            # Keep the damaged file for a look rather than overwriting it
            # with the next save — it is the user's listening history.
            bad = self._path.with_suffix(self._path.suffix + ".bad")
            with contextlib.suppress(OSError):
                os.replace(self._path, bad)
            log.warning("History file %s is damaged (%s) — moved it to %s", self._path, e, bad)
            return [], None
        entries = [e for e in (_entry_from_dict(r) for r in rows if isinstance(r, dict)) if e]
        skipped = len(rows) - len(entries)
        if skipped:
            log.warning("History: skipped %d unreadable row(s) in %s", skipped, self._path)
        resume = _resume_from_dict(raw.get("current"))
        if resume is not None and resume.counted:
            # It counted, so it is the list's newest song: carry on with
            # that very entry — or with nothing, if it has left the list.
            if entries and _same_play(entries[0], resume.entry):
                resume.entry = entries[0]
            else:
                resume = None
        return entries, resume

    def _save_locked(self) -> None:
        """Best-effort: a failed write keeps the list for this session."""
        data: dict = {
            "version": _FILE_VERSION,
            "entries": [dataclasses.asdict(e) for e in self._entries],
        }
        # The song on right now, counted or not: how much of it was heard
        # and where it was — what a restart is compared against.
        cur = self._cur
        if cur is not None:
            cur.saved_played_ms = cur.played_ms
            data["current"] = _current_dict(
                cur.entry, cur.counted, cur.played_ms, cur.position_ms, int(self._last_wall)
            )
        elif self._resume is not None:
            # Not matched against a poll yet — keep it until it is.
            r = self._resume
            data["current"] = _current_dict(
                r.entry, r.counted, r.played_ms, r.position_ms, r.saved_at
            )
        payload = json.dumps(data, ensure_ascii=False, indent=1)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload + "\n", encoding="utf-8")
            # Owner-only: it's a record of what someone listened to.
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError as e:
            with contextlib.suppress(OSError):
                tmp.unlink()
            log.warning("Could not save history to %s (%s)", self._path, e)

    def _delete_file(self) -> bool:
        try:
            self._path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            log.warning("Could not delete history file %s (%s)", self._path, e)
            return False
