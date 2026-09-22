"""Last.fm API client and scrobble decision logic, opt-in alongside the Discord status.

Each user brings their own Last.fm API account, like the Discord ``client_id``."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from refrain import __version__, dev_metrics
from refrain.config import LastfmConfig
from refrain.paths import state_dir
from refrain.scrobble_queue import ScrobbleQueue
from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

API_ROOT = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
API_ACCOUNT_URL = "https://www.last.fm/api/account/create"
_USER_AGENT = f"Refrain/{__version__} (+https://github.com/Rockykln/refrain)"
_TIMEOUT_S = 10

# Last.fm error codes we treat specially. The full list lives in the
# API docs; these are the ones that change Refrain's behaviour.
ERR_INVALID_SESSION = 9  # session revoked / wrong — user must reconnect
ERR_TOKEN_NOT_AUTHORISED = 14
ERR_TOKEN_EXPIRED = 15
ERR_INVALID_TOKEN = 4
ERR_SERVICE_OFFLINE = 11
ERR_SERVICE_UNAVAILABLE = 16
ERR_RATE_LIMIT = 29
# Only these say something about the tracks themselves. A bad API key or
# signature, a suspended key or "operation failed" would reject every
# track the same way, so the queue waits for them to be fixed instead.
ERR_INVALID_PARAMETERS = 6
ERR_INVALID_RESOURCE = 7

# Last.fm's documented scrobble thresholds.
_MIN_TRACK_MS = 30_000  # tracks shorter than 30 s are never scrobbled
_SCROBBLE_AFTER_MS = 240_000  # …or after 4 minutes, whichever comes first

# track.scrobble accepts at most 50 items per call.
MAX_BATCH = 50


class LastfmError(Exception):
    """A Last.fm API call returned an ``error`` payload or failed.

    ``code`` is the Last.fm numeric error code when the failure was an
    API-level error response, else ``None`` (network / decode failure).
    """

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code

    @property
    def invalid_session(self) -> bool:
        return self.code == ERR_INVALID_SESSION

    @property
    def retryable(self) -> bool:
        # No code → transport failure (offline, timeout): keep the
        # scrobble queued and retry later. Explicit transient codes
        # likewise. An invalid session or a bad-params (6) error is
        # NOT retryable — retrying would just spin.
        if self.code is None:
            return True
        return self.code in (ERR_SERVICE_OFFLINE, ERR_SERVICE_UNAVAILABLE, ERR_RATE_LIMIT)


def api_signature(params: dict[str, str], shared_secret: str) -> str:
    """Last.fm request signature.

    Spec: sort the params by name, concatenate ``name + value`` with no
    separator, append the ``shared_secret``, MD5-hexdigest the UTF-8
    bytes. ``format`` (and ``callback``) are excluded from the signature
    by the spec. MD5 is mandated by the protocol (the shared secret gives
    authenticity, not the hash); ``usedforsecurity=False`` says so.
    """
    parts = [f"{k}{v}" for k, v in sorted(params.items()) if k not in ("format", "callback")]
    raw = ("".join(parts) + shared_secret).encode("utf-8")
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def should_scrobble(played_ms: int, duration_ms: int) -> bool:
    """Last.fm's client-side scrobble rule.

    A track counts as listened (and may be scrobbled) once it has been
    *played* for at least half its length, or for four minutes —
    whichever comes first — provided the track is longer than 30 s.
    ``played_ms`` is accumulated actual play time (pause/seek-aware),
    not wall-clock since start.
    """
    if duration_ms <= _MIN_TRACK_MS:
        return False
    if played_ms < 0:
        return False
    return played_ms * 2 >= duration_ms or played_ms >= _SCROBBLE_AFTER_MS


# A play that has counted and then jumps back to the very start is the
# song starting over — repeat-one, or played again from the top — and a
# play of its own. Both bounds, so a seek of a few seconds near the
# start never splits a play in two.
_REPLAY_START_MS = 10_000
_REPLAY_JUMP_MS = 30_000


def fills_in_album(key: str | None, track: TrackInfo) -> bool:
    """Is ``track`` the song ``key`` names, now with the album it lacked?

    A browser can send the album a poll after the title, and the catalog
    finds one a second or so into the song. Either way it is the same play.
    """
    return bool(key and track.album) and key == f"{track.source}|{track.title}|{track.artist}|"


def is_replay(counted: bool, prev_position_ms: int | None, position_ms: int | None) -> bool:
    """Has a play that already counted just started over from the beginning?

    Positions are the player's own; ``None`` (unknown) never makes a
    replay. Shared by the Scrobbler and the history, so a second play is
    a second scrobble and a second row alike.
    """
    if not counted or prev_position_ms is None or position_ms is None:
        return False
    return position_ms < _REPLAY_START_MS and prev_position_ms - position_ms >= _REPLAY_JUMP_MS


# A play saved when Refrain stopped is carried on after the restart only
# while it can still be the same play: within its own length plus this,
# of when it was saved.
RESUME_GRACE_S = 5 * 60
RESUME_UNKNOWN_LENGTH_S = 15 * 60
# Where the player has the song after a restart may differ this much from
# where it should be — a slow restart, a player that reports late.
RESUME_POSITION_SLACK_MS = 15_000
# How often the progress of a play is written, so a crash doesn't start
# it over. Longer than a typical skip: skipping never touches the disk.
PROGRESS_SAVE_EVERY_MS = 30_000


def continues_play(
    away_s: float, length_ms: int, saved_position_ms: int | None, position_ms: int | None
) -> bool:
    """Is the song playing after a restart the same play as the one saved?

    Soon enough — within its own length plus ``RESUME_GRACE_S`` of the
    save — and, where the player reported a position both times, one
    that follows on from it: not behind where it was (it ended and began
    again meanwhile) and no further on than the time away allows. Shared
    by the history and the Scrobbler, so a restart keeps one row and one
    scrobble alike. The caller has already checked it is the same song.
    """
    length_s = length_ms // 1000 or RESUME_UNKNOWN_LENGTH_S
    if away_s > length_s + RESUME_GRACE_S:
        return False
    if saved_position_ms is None or position_ms is None:
        return True
    slack = RESUME_POSITION_SLACK_MS
    return (
        saved_position_ms - slack <= position_ms <= saved_position_ms + int(away_s * 1000) + slack
    )


class LastfmClient:
    """Thin signed-request client. Blocking — call only off the poll
    thread (the daemon drives it from a worker executor). Hand-rolled on
    ``urllib``: ``pylast`` would break the three-runtime-deps rule."""

    def __init__(self, api_key: str, shared_secret: str, session_key: str = "") -> None:
        self.api_key = (api_key or "").strip()
        self.shared_secret = (shared_secret or "").strip()
        self.session_key = (session_key or "").strip()

    # ---------------------------------------------------------- transport

    def _call(self, method: str, *, http_post: bool, signed: bool = True, **params: str) -> dict:
        if not self.api_key or (signed and not self.shared_secret):
            raise LastfmError("Last.fm api_key / shared_secret not configured")
        req_params: dict[str, str] = {
            k: str(v) for k, v in params.items() if v is not None and v != ""
        }
        req_params["method"] = method
        req_params["api_key"] = self.api_key
        if signed:
            req_params["api_sig"] = api_signature(req_params, self.shared_secret)
        # `format` is added AFTER signing — the spec excludes it from the
        # signature, and api_signature() also filters it defensively.
        req_params["format"] = "json"
        body = urllib.parse.urlencode(req_params).encode("utf-8")

        # Defence in depth: never send credentials/scrobbles over a
        # plaintext transport, even if a future edit changes API_ROOT.
        if not API_ROOT.startswith("https://"):
            raise LastfmError("refusing non-HTTPS Last.fm endpoint")
        # Set on every path below that doesn't raise; None only if a context
        # manager swallowed the error, which the type check then reports.
        payload: object = None
        try:
            if http_post:
                request = urllib.request.Request(
                    API_ROOT,
                    data=body,
                    headers={"User-Agent": _USER_AGENT},
                    method="POST",
                )
            else:
                url = f"{API_ROOT}?{urllib.parse.urlencode(req_params)}"
                request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with (
                dev_metrics.network("lastfm"),
                urllib.request.urlopen(request, timeout=_TIMEOUT_S) as r,  # nosec B310
            ):
                payload = json.load(r)
        except LastfmError:
            raise
        except urllib.error.HTTPError as e:
            # Last.fm returns its JSON error body even on 4xx — try to
            # surface the real code/message instead of a bare HTTP 403.
            try:
                payload = json.loads(e.read().decode("utf-8", "replace"))
            except Exception as parse_err:
                raise LastfmError(f"Last.fm HTTP {e.code}: {e.reason}") from parse_err
        except Exception as e:
            raise LastfmError(f"Last.fm request failed: {e}") from e

        if not isinstance(payload, dict):
            raise LastfmError("Last.fm returned an unexpected (non-object) payload")
        if "error" in payload:
            code = payload.get("error")
            msg = str(payload.get("message", "unknown error"))
            raise LastfmError(
                f"Last.fm error {code}: {msg}",
                code=code if isinstance(code, int) else None,
            )
        return payload

    # ------------------------------------------------------------- auth

    def get_token(self) -> str:
        """Step 1 of the desktop auth flow — an unauthorized request token."""
        data = self._call("auth.getToken", http_post=False)
        token = str(data.get("token", "")).strip()
        if not token:
            raise LastfmError("Last.fm auth.getToken returned no token")
        return token

    def authorize_url(self, token: str) -> str:
        """The page the user opens in a browser to grant access (step 2)."""
        return f"{AUTH_URL}?{urllib.parse.urlencode({'api_key': self.api_key, 'token': token})}"

    def get_session(self, token: str) -> tuple[str, str]:
        """Step 3 — exchange an authorized token for a (session_key, username)."""
        data = self._call("auth.getSession", http_post=False, token=token)
        session = data.get("session") or {}
        key = str(session.get("key", "")).strip()
        name = str(session.get("name", "")).strip()
        if not key:
            raise LastfmError("Last.fm auth.getSession returned no session key")
        self.session_key = key
        return key, name

    def validate_session(self) -> str:
        """Confirm the stored session key is still accepted; return the user.

        Last.fm session keys do not expire on their own, but they stop
        working when the user revokes the application, changes their
        password, or the key was restored from a stale copy. Otherwise
        that only shows at the first failed scrobble, possibly an hour in.

        Raises ``LastfmError``; check ``.invalid_session`` to tell "the
        key is dead" apart from "Last.fm is unreachable right now".
        """
        if not self.session_key:
            raise LastfmError("Last.fm not connected (no session key)")
        data = self._call("user.getInfo", http_post=False, sk=self.session_key)
        user = data.get("user") or {}
        return str(user.get("name", "")).strip()

    def track_duration_ms(self, artist: str, track: str) -> int:
        """Last.fm's length for a track, 0 when it has none. Read-only, unsigned."""
        data = self._call(
            "track.getInfo", http_post=False, signed=False, artist=artist, track=track
        )
        try:
            return max(0, int((data.get("track") or {}).get("duration") or 0))
        except (TypeError, ValueError, AttributeError):
            return 0

    # ---------------------------------------------------------- scrobbling

    def update_now_playing(
        self, artist: str, track: str, album: str = "", duration_s: int = 0
    ) -> None:
        if not self.session_key:
            raise LastfmError("Last.fm not connected (no session key)")
        params: dict[str, str] = {"artist": artist, "track": track, "sk": self.session_key}
        if album:
            params["album"] = album
        if duration_s > 0:
            params["duration"] = str(int(duration_s))
        self._call("track.updateNowPlaying", http_post=True, **params)

    def scrobble(self, items: list[dict]) -> int:
        """Submit a batch of played tracks (≤ 50). Returns the accepted count.

        Each item: ``{artist, track, timestamp, album?, duration?}``.
        ``timestamp`` is the UTC unix time the track *started*.
        """
        if not self.session_key:
            raise LastfmError("Last.fm not connected (no session key)")
        if not items:
            return 0
        if len(items) > MAX_BATCH:
            raise LastfmError(f"scrobble batch too large ({len(items)} > {MAX_BATCH})")
        params: dict[str, str] = {"sk": self.session_key}
        for i, it in enumerate(items):
            params[f"artist[{i}]"] = str(it["artist"])
            params[f"track[{i}]"] = str(it["track"])
            params[f"timestamp[{i}]"] = str(int(it["timestamp"]))
            if it.get("album"):
                params[f"album[{i}]"] = str(it["album"])
            if it.get("duration"):
                params[f"duration[{i}]"] = str(int(it["duration"]))
        data = self._call("track.scrobble", http_post=True, **params)
        # Response shape differs for single vs batch; accepted count is
        # under scrobbles.@attr.accepted. The call not raising already
        # means Last.fm took it.
        try:
            return int(data.get("scrobbles", {}).get("@attr", {}).get("accepted", len(items)))
        except (TypeError, ValueError, AttributeError):
            return len(items)


# --------------------------------------------------------------------------- #
# Play-time accounting (pure)                                                  #
# --------------------------------------------------------------------------- #

# A single tick gap longer than this is treated as "the machine was
# asleep / the daemon stalled", not as listening time — so a laptop
# suspended mid-song doesn't credit hours of phantom playback.
_MAX_TICK_GAP_MS = 30_000


def accrue_play_ms(
    prev_played_ms: int,
    prev_last_mono: float | None,
    is_playing: bool,
    now_mono: float,
) -> tuple[int, float]:
    """Wall-clock-while-playing accumulator.

    Returns ``(new_played_ms, new_last_mono)``. ``prev_last_mono is
    None`` means "first observation of this track" — establish the
    baseline, credit nothing. Each subsequent tick credits the elapsed
    monotonic time *only while playing*, so paused gaps don't count and
    seeks/bad MPRIS positions can't inflate it. The per-tick delta is
    clamped to ``_MAX_TICK_GAP_MS``; ``now_mono`` is monotonic so a
    wall-clock change (NTP, DST) can't corrupt the count.
    """
    if prev_last_mono is None:
        return prev_played_ms, now_mono
    delta_ms = int((now_mono - prev_last_mono) * 1000)
    if is_playing and 0 < delta_ms <= _MAX_TICK_GAP_MS:
        prev_played_ms += delta_ms
    return prev_played_ms, now_mono


# Re-drain the offline queue at most this often (seconds) while a track
# is just ticking along — plus an immediate drain right after a new
# scrobble is enqueued.
_DRAIN_INTERVAL_S = 60.0


def mmss(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def current_play_path() -> Path:
    """The play in progress, kept across a restart of Refrain."""
    return state_dir() / "scrobble_current.json"


class Scrobbler:
    """Drives Last.fm now-playing + scrobbling off the daemon tick.

    The daemon calls :meth:`update` every poll with the current track;
    all network work is handed to a single-worker executor so the poll
    loop never blocks (same pattern as ``CoverFetcher``). Scrobbles are
    persisted to :class:`ScrobbleQueue` the instant they qualify, so an
    offline window or a quit mid-listen never loses them.
    """

    def __init__(
        self,
        cfg: LastfmConfig,
        queue: ScrobbleQueue | None = None,
        on_queued: Callable[[str, str], None] | None = None,
        current_path: Path | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._cfg = cfg
        # Told ``(artist, title)`` whenever a play is banked for Last.fm
        # — how the history marks a song as scrobbled. Called with the
        # lock held, so it must not call back into the Scrobbler.
        self._on_queued = on_queued
        self._client = self._make_client(cfg)
        self._queue = queue if queue is not None else ScrobbleQueue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refrain-scrobble")
        # Current-track accounting. Fields are stored verbatim (not
        # re-parsed from `_key`) so a "|" in a title can't corrupt the
        # scrobble.
        self._key: str | None = None
        self._cur_artist = ""
        self._cur_title = ""
        self._cur_album = ""
        self._played_ms = 0
        self._last_mono: float | None = None
        self._started_unix = 0
        self._duration_ms = 0
        self._position_ms: int | None = None  # the player's, last tick
        self._nowplaying_key: str | None = None
        self._skipped_key: str | None = None
        self._skipped_since: float | None = None
        # Across a restart. The play in progress is written to
        # `_current_path` on quit and every PROGRESS_SAVE_EVERY_MS of play;
        # `_resume` is that play as the last run left it, until the first
        # poll says whether it is still going (_settle_resume_locked).
        # `_banked` marks a play already in the queue — queued on quit
        # because it counted — so carrying on with it never queues it twice.
        self._current_path = current_path or current_play_path()
        self._banked = False
        self._saved_played_ms = 0
        # Wall clock of the last poll: the moment the saved position is from.
        self._last_wall = time.time()
        self._resume: dict | None = self._load_current()
        # Drain bookkeeping.
        self._drain_inflight = False
        self._last_drain_mono = 0.0
        self._session_invalid = False

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _make_client(cfg: LastfmConfig) -> LastfmClient | None:
        if not cfg.enabled:
            return None
        if not cfg.api_key or not cfg.shared_secret or not cfg.session_key:
            return None
        return LastfmClient(cfg.api_key, cfg.shared_secret, cfg.session_key)

    @staticmethod
    def _content_key(track: TrackInfo) -> str:
        return f"{track.source}|{track.title}|{track.artist}|{track.album}"

    @staticmethod
    def _is_candidate(track: TrackInfo, effective_duration_ms: int) -> bool:
        # Last.fm needs a real artist + track; preview clips / very
        # short items (< 30 s effective) are skipped entirely, matching
        # how the Discord/idle paths treat them.
        return bool(
            track.has_track
            and track.title
            and track.artist
            and track.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED)
            and effective_duration_ms >= 30_000
        )

    # ----------------------------------------------------------- lifecycle

    @staticmethod
    def _identity(cfg: LastfmConfig) -> tuple:
        """Who scrobbles, and whether: what a play in progress belongs to."""
        return (bool(cfg.enabled), cfg.api_key, cfg.shared_secret, cfg.session_key, cfg.username)

    def reconfigure(self, cfg: LastfmConfig) -> None:
        with self._lock:
            unchanged = self._identity(cfg) == self._identity(self._cfg)
            self._cfg = cfg
            if unchanged:
                # Apply on any tab of Settings sends the whole config. With
                # Last.fm untouched, the play in progress carries on rather
                # than counting from zero after every Apply.
                return
            self._client = self._make_client(cfg)
            # A fresh session key clears a previous "invalid" latch.
            self._session_invalid = False
            # Dropping the in-progress track on a config change is the
            # safe choice: the user may have just turned scrobbling off
            # or switched accounts — don't scrobble the half-played
            # track under the new (or no) identity.
            self._key = None
            self._last_mono = None
            self._banked = False
            self._resume = None
            self._clear_current_file()

    def shutdown(self) -> None:
        # A play that already counts goes into the queue now, so quitting
        # mid-song never loses it. The executor goes first: queueing would
        # otherwise start a drain, and quitting would wait on Last.fm. The
        # queue drains next launch. Either way the play is saved as it
        # stands: if it is still going when Refrain comes back, counting
        # carries on instead of starting over, so a long song isn't
        # scrobbled twice.
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            if (
                self._key is not None
                and not self._banked
                and should_scrobble(self._played_ms, self._duration_ms)
            ):
                self._enqueue_locked(
                    self._cur_artist,
                    self._cur_title,
                    self._cur_album,
                    self._started_unix,
                    self._duration_ms,
                )
                self._banked = True
            if self._key is not None or self._resume is None:
                # Else the last run's play was never compared with a song:
                # its file stays for the next launch to settle.
                self._save_current_locked(self._last_wall)

    @property
    def looks_up_lengths(self) -> bool:
        """Whether `check_length` may ask Last.fm: enabled and with a key."""
        with self._lock:
            return bool(self._cfg.enabled and self._cfg.api_key.strip())

    def check_length(
        self, artist: str, title: str, on_answer: Callable[[int | None], None]
    ) -> None:
        """Ask Last.fm for a track's length off the poll thread.

        ``on_answer`` runs on the worker with the length in ms (0: Last.fm
        has none), or None when Last.fm could not be asked.
        """
        with self._lock:
            api_key = self._cfg.api_key
        self._submit(self._do_check_length, LastfmClient(api_key, ""), artist, title, on_answer)

    def health(self) -> tuple[bool, int]:
        """Whether Last.fm refused the session, and how many scrobbles
        wait for a retry after a failed send."""
        with self._lock:
            invalid, sending = self._session_invalid, self._drain_inflight
        return invalid, 0 if sending else len(self._queue)

    # ----------------------------------------------------------- core

    def update(
        self,
        track: TrackInfo,
        effective_duration_ms: int,
        privacy_off: bool,
        now_wall: float | None = None,
        now_mono: float | None = None,
        position_ms: int | None = None,
        restarted: bool = False,
    ) -> None:
        """``position_ms`` is the player's own position, or None when the
        daemon has none it trusts, and ``restarted`` says the player showed
        the song beginning again this poll — both only tell a replay apart."""
        now_wall = time.time() if now_wall is None else now_wall
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            self._last_wall = now_wall
            # Gated: scrobbling disabled / not connected, or privacy is
            # the global "off" kill switch. Drop the in-progress track
            # (don't scrobble under a disabled/anonymised state) but
            # keep the persisted queue untouched.
            if self._client is None or privacy_off:
                if self._key is not None or self._resume is not None:
                    self._resume = None
                    self._clear_current_file()
                self._key = None
                self._last_mono = None
                self._position_ms = None
                self._banked = False
                return

            candidate = self._is_candidate(track, effective_duration_ms)
            key = self._content_key(track) if candidate else None
            if not candidate:
                self._note_too_short_locked(track, effective_duration_ms, now_mono)
            # Not before a song shows up: a first poll that sees nothing
            # yet says nothing about whether the saved play is still going.
            if self._resume is not None and key is not None:
                self._settle_resume_locked(
                    key, effective_duration_ms, position_ms, now_wall, now_mono
                )
            if key is not None and key != self._key and fills_in_album(self._key, track):
                # The scrobble goes out later and takes the album along;
                # "now playing" went out without it and is not sent again.
                if self._nowplaying_key == self._key:
                    self._nowplaying_key = key
                self._key = key
                self._cur_album = track.album
            # The same song again from the top, after it had counted.
            counted = should_scrobble(self._played_ms, self._duration_ms)
            replay = (
                key is not None
                and key == self._key
                and (is_replay(counted, self._position_ms, position_ms) or (restarted and counted))
            )
            self._position_ms = position_ms

            if key != self._key or replay:
                # Track boundary: bank the previous one if it earned it,
                # then start fresh accounting for the new one.
                self._finalize_current_locked()
                self._key = key
                self._cur_artist = track.artist if key is not None else ""
                self._cur_title = track.title if key is not None else ""
                self._cur_album = track.album if key is not None else ""
                self._played_ms = 0
                self._last_mono = now_mono if key is not None else None
                self._started_unix = int(now_wall)
                self._duration_ms = effective_duration_ms
                self._nowplaying_key = None
                self._banked = False
                self._saved_played_ms = 0
            else:
                is_playing = track.status == PlaybackStatus.PLAYING
                self._played_ms, self._last_mono = accrue_play_ms(
                    self._played_ms, self._last_mono, is_playing, now_mono
                )
                # A later iTunes-corrected duration can replace an early
                # bogus one; keep the most recent positive value.
                if effective_duration_ms > 0:
                    self._duration_ms = effective_duration_ms
                if self._key is not None and (
                    self._played_ms - self._saved_played_ms >= PROGRESS_SAVE_EVERY_MS
                    # And the moment it counts, so a crash right after keeps it.
                    or (not counted and should_scrobble(self._played_ms, self._duration_ms))
                ):
                    self._save_current_locked(now_wall)

            # Now-playing: once per track, when it's actually playing.
            if (
                key is not None
                and track.status == PlaybackStatus.PLAYING
                and self._cfg.scrobble_now_playing
                and not self._session_invalid
                and self._nowplaying_key != key
            ):
                self._nowplaying_key = key
                self._submit(
                    self._do_now_playing,
                    track.artist,
                    track.title,
                    track.album,
                    int(self._duration_ms // 1000),
                )

            self._maybe_drain_locked(now_mono)

    # ---- internals (call with self._lock held) -------------------------

    def _note_too_short_locked(
        self, track: TrackInfo, effective_duration_ms: int, now_mono: float
    ) -> None:
        # Without this a song that never counts looks like a Last.fm fault.
        if not (
            track.has_track
            and track.title
            and track.artist
            and track.status == PlaybackStatus.PLAYING
        ):
            return
        skipped = self._content_key(track)
        if skipped != self._skipped_key:
            # Browsers report the buffered length first; wait for the real one.
            self._skipped_key = skipped
            self._skipped_since = now_mono
            return
        if self._skipped_since is None or now_mono - self._skipped_since < _MIN_TRACK_MS / 1000:
            return
        self._skipped_since = None
        log.info(
            "Not scrobbled: %s — %s (%s)",
            track.artist,
            track.title,
            "the player reported no song length"
            if effective_duration_ms <= 0
            else "shorter than 30 seconds",
        )

    def _finalize_current_locked(self) -> None:
        if self._key is None:
            return
        if not self._banked and should_scrobble(self._played_ms, self._duration_ms):
            self._enqueue_locked(
                self._cur_artist,
                self._cur_title,
                self._cur_album,
                self._started_unix,
                self._duration_ms,
            )
        if self._saved_played_ms or self._banked:
            self._clear_current_file()  # the saved progress of a play that is over
        self._key = None
        self._last_mono = None
        self._banked = False
        self._saved_played_ms = 0

    def _enqueue_locked(
        self, artist: str, title: str, album: str, started_unix: int, duration_ms: int
    ) -> None:
        stored = self._queue.enqueue(
            {
                "artist": artist,
                "track": title,
                "album": album,
                "timestamp": started_unix,
                "duration": int(duration_ms // 1000),
                "account": self._cfg.username,
            }
        )
        if stored:
            log.info("Scrobble queued: %s — %s", artist, title)
            if self._on_queued is not None:
                try:
                    self._on_queued(artist, title)
                except Exception:
                    log.debug("Scrobble on_queued hook failed", exc_info=True)
            self._maybe_drain_locked(time.monotonic(), force=True)

    def _settle_resume_locked(
        self,
        key: str | None,
        duration_ms: int,
        position_ms: int | None,
        now_wall: float,
        now_mono: float,
    ) -> None:
        """Carry on with the play the last run left, or close it.

        Only the first poll after a start is compared. The same play still
        going carries on — counting from where it was, under the time it
        began, queued already if it was on quit. Otherwise it ended while
        Refrain was away, and if it counted without being queued — a crash
        — it is queued now, under the time it began.
        """
        r, self._resume = self._resume, None
        away_s = max(0.0, now_wall - r["saved_at"])
        if (
            key is not None
            and key == r["key"]
            and continues_play(
                away_s, r["duration_ms"] or duration_ms, r["position_ms"], position_ms
            )
        ):
            self._key = key
            self._cur_artist, self._cur_title, self._cur_album = r["artist"], r["title"], r["album"]
            self._played_ms = self._saved_played_ms = r["played_ms"]
            self._last_mono = now_mono
            self._started_unix = r["started_unix"]
            self._duration_ms = duration_ms or r["duration_ms"]
            self._position_ms = position_ms
            self._banked = r["banked"]
            self._nowplaying_key = None
            log.info(
                "Scrobble: carrying on with %s — %s (%s heard before the restart%s)",
                r["artist"],
                r["title"],
                mmss(r["played_ms"]),
                ", queued already" if r["banked"] else "",
            )
            return
        if not r["banked"] and should_scrobble(r["played_ms"], r["duration_ms"]):
            self._enqueue_locked(
                r["artist"], r["title"], r["album"], r["started_unix"], r["duration_ms"]
            )
        self._clear_current_file()

    def _load_current(self) -> dict | None:
        try:
            raw = json.loads(self._current_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.debug("Scrobble: saved play in progress unreadable (%s) — ignored", e)
            return None
        try:
            pos = raw.get("position_ms")
            r = {
                "key": str(raw["key"]),
                "artist": str(raw["artist"]),
                "title": str(raw["title"]),
                "album": str(raw.get("album", "")),
                "started_unix": int(raw["started_unix"]),
                "played_ms": max(0, int(raw["played_ms"])),
                "duration_ms": max(0, int(raw.get("duration_ms", 0))),
                "position_ms": pos if type(pos) is int and pos >= 0 else None,
                "saved_at": float(raw["saved_at"]),
                "banked": raw.get("banked") is True,
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            log.debug("Scrobble: saved play in progress malformed — ignored")
            return None
        return r if r["key"] and r["artist"] and r["title"] else None

    def _save_current_locked(self, now_wall: float) -> None:
        """A failed write only costs the carry-on after a restart."""
        if self._key is None:
            self._clear_current_file()
            return
        self._saved_played_ms = self._played_ms
        data = {
            "key": self._key,
            "artist": self._cur_artist,
            "title": self._cur_title,
            "album": self._cur_album,
            "started_unix": self._started_unix,
            "played_ms": self._played_ms,
            "duration_ms": self._duration_ms,
            "position_ms": self._position_ms,
            "saved_at": now_wall,
            "banked": self._banked,
        }
        tmp = self._current_path.with_suffix(self._current_path.suffix + ".tmp")
        try:
            self._current_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
            # Owner-only, like the history: it says what someone is listening to.
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self._current_path)
        except OSError as e:
            with contextlib.suppress(OSError):
                tmp.unlink()
            log.debug("Could not save the play in progress (%s)", e)

    def _clear_current_file(self) -> None:
        try:
            self._current_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.debug("Could not remove %s (%s)", self._current_path, e)

    def _maybe_drain_locked(self, now_mono: float, force: bool = False) -> None:
        if self._client is None or self._session_invalid or self._drain_inflight:
            return
        if len(self._queue) == 0:
            return
        if not force and (now_mono - self._last_drain_mono) < _DRAIN_INTERVAL_S:
            return
        self._drain_inflight = True
        self._last_drain_mono = now_mono
        self._submit(self._do_drain)

    def _submit(self, fn, *args) -> None:
        try:
            self._executor.submit(fn, *args)
        except RuntimeError:
            # Executor already shut down (app quitting) — ignore.
            pass

    # ---- executor-thread work -----------------------------------------

    def _do_now_playing(self, artist: str, title: str, album: str, dur_s: int) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.update_now_playing(artist, title, album, dur_s)
        except LastfmError as e:
            self._handle_lastfm_error(e, "now-playing")
        except Exception as e:
            log.debug("Last.fm now-playing failed: %s", e)

    def _do_check_length(
        self,
        client: LastfmClient,
        artist: str,
        title: str,
        on_answer: Callable[[int | None], None],
    ) -> None:
        try:
            length_ms: int | None = client.track_duration_ms(artist, title)
        except LastfmError as e:
            # A code is Last.fm's answer (mostly "track not found"); none
            # means it was not reached, and the next play may ask again.
            length_ms = 0 if e.code is not None else None
            log.debug("Last.fm length lookup failed: %s", e)
        on_answer(length_ms)

    def _do_drain(self) -> None:
        try:
            with self._lock:
                client, account = self._client, self._cfg.username
            if client is None:
                return
            # Here, on the one worker, since no drain can be halfway
            # through the queue at the same time.
            dropped = self._queue.drop_other_accounts(account)
            if dropped:
                log.warning(
                    "Dropped %d queued scrobble(s) heard under another Last.fm account",
                    dropped,
                )
            n = self._queue.drain(self._submit_batch)
            if n:
                log.info("Scrobbled %d queued track(s) to Last.fm", n)
        finally:
            with self._lock:
                self._drain_inflight = False

    def _submit_batch(self, batch: list[dict]) -> int:
        client = self._client
        if client is None:
            raise LastfmError("Last.fm client gone")
        try:
            return client.scrobble(batch)
        except LastfmError as e:
            self._handle_lastfm_error(e, "scrobble")
            if e.code not in (ERR_INVALID_PARAMETERS, ERR_INVALID_RESOURCE):
                # Offline, an outage, a revoked session or a key problem:
                # re-raise so ScrobbleQueue.drain stops and keeps the batch.
                raise
            # Last.fm rejected these tracks. Re-queuing them forever would
            # block every later scrobble, so drop them (reported as
            # "submitted" so drain advances past them).
            log.warning("Dropping %d unscrobblable queued track(s): %s", len(batch), e)
            return len(batch)

    def _handle_lastfm_error(self, e: LastfmError, where: str) -> None:
        if e.invalid_session:
            with self._lock:
                self._session_invalid = True
            log.warning(
                "Last.fm session invalid (%s) — reconnect in Settings → Last.fm. "
                "Queued scrobbles are kept and will submit after reconnect.",
                where,
            )
        else:
            log.info("Last.fm %s error: %s", where, e)
