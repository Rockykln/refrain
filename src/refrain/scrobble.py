"""Last.fm API client + scrobble decision logic.

Opt-in, *alongside* the Discord Rich Presence — never a replacement.
Each user registers their own Last.fm API account and pastes the
``api_key`` / ``shared_secret`` into Settings (same "bring your own
credentials" model as the Discord ``client_id``); the per-user
``session_key`` is obtained through the desktop auth flow.

No extra dependency — the API surface we need is three signed methods,
so it's hand-rolled on ``urllib`` + ``hashlib`` exactly like
``cover_art.py`` and ``updater.py``. A ``pylast`` dependency would break
the project's three-runtime-deps rule.

Note on MD5: the Last.fm API **mandates** the request signature be
``md5(sorted_params + shared_secret)``. This is a protocol-defined,
non-security digest (the ``shared_secret`` provides authenticity, not
the hash) — SHA-256 would produce a signature Last.fm rejects outright.
``usedforsecurity=False`` documents that intent and keeps bandit quiet.
SHA-256 *is* used where we control the format (the offline-queue dedup
key — see ``scrobble_queue``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from refrain.config import LastfmConfig
from refrain.scrobble_queue import ScrobbleQueue
from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

API_ROOT = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
API_ACCOUNT_URL = "https://www.last.fm/api/account/create"
_USER_AGENT = "Refrain (+https://github.com/Rockykln/refrain)"
_TIMEOUT_S = 10

# Last.fm error codes we treat specially. The full list lives in the
# API docs; these are the ones that change Refrain's behaviour.
ERR_INVALID_SESSION = 9  # session revoked / wrong — user must reconnect
ERR_SERVICE_OFFLINE = 11
ERR_SERVICE_UNAVAILABLE = 16
ERR_RATE_LIMIT = 29

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
    by the spec. Protocol-mandated MD5 — see the module docstring.
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


class LastfmClient:
    """Thin signed-request client. Blocking — call only off the poll
    thread (the daemon drives it from a worker executor)."""

    def __init__(self, api_key: str, shared_secret: str, session_key: str = "") -> None:
        self.api_key = (api_key or "").strip()
        self.shared_secret = (shared_secret or "").strip()
        self.session_key = (session_key or "").strip()

    # ---------------------------------------------------------- transport

    def _call(self, method: str, *, http_post: bool, signed: bool = True, **params: str) -> dict:
        if not self.api_key or not self.shared_secret:
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
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as r:
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
        # under scrobbles.@attr.accepted. Best-effort — the call not
        # raising already means Last.fm took it.
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


class Scrobbler:
    """Drives Last.fm now-playing + scrobbling off the daemon tick.

    The daemon calls :meth:`update` every poll with the current track;
    all network work is handed to a single-worker executor so the poll
    loop never blocks (same pattern as ``CoverFetcher``). Scrobbles are
    persisted to :class:`ScrobbleQueue` the instant they qualify, so an
    offline window or a quit mid-listen never loses them.
    """

    def __init__(self, cfg: LastfmConfig, queue: ScrobbleQueue | None = None) -> None:
        self._lock = threading.Lock()
        self._cfg = cfg
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
        self._nowplaying_key: str | None = None
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

    def reconfigure(self, cfg: LastfmConfig) -> None:
        with self._lock:
            self._cfg = cfg
            self._client = self._make_client(cfg)
            # A fresh session key clears a previous "invalid" latch.
            self._session_invalid = False
            # Dropping the in-progress track on a config change is the
            # safe choice: the user may have just turned scrobbling off
            # or switched accounts — don't scrobble the half-played
            # track under the new (or no) identity.
            self._key = None
            self._last_mono = None

    def shutdown(self) -> None:
        # Persist a qualifying in-progress track so quitting mid-song
        # still scrobbles it on the next run (the queue survives). No
        # network here — the executor is torn down; the queue drains
        # next launch.
        with self._lock:
            self._finalize_current_locked()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------- core

    def update(
        self,
        track: TrackInfo,
        effective_duration_ms: int,
        privacy_off: bool,
        now_wall: float | None = None,
        now_mono: float | None = None,
    ) -> None:
        now_wall = time.time() if now_wall is None else now_wall
        now_mono = time.monotonic() if now_mono is None else now_mono
        with self._lock:
            # Gated: scrobbling disabled / not connected, or privacy is
            # the global "off" kill switch. Drop the in-progress track
            # (don't scrobble under a disabled/anonymised state) but
            # keep the persisted queue untouched.
            if self._client is None or privacy_off or self._session_invalid:
                self._key = None
                self._last_mono = None
                return

            candidate = self._is_candidate(track, effective_duration_ms)
            key = self._content_key(track) if candidate else None

            if key != self._key:
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
            else:
                is_playing = track.status == PlaybackStatus.PLAYING
                self._played_ms, self._last_mono = accrue_play_ms(
                    self._played_ms, self._last_mono, is_playing, now_mono
                )
                # A later iTunes-corrected duration can replace an early
                # bogus one; keep the most recent positive value.
                if effective_duration_ms > 0:
                    self._duration_ms = effective_duration_ms

            # Now-playing: once per track, when it's actually playing.
            if (
                key is not None
                and track.status == PlaybackStatus.PLAYING
                and self._cfg.scrobble_now_playing
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

    def _finalize_current_locked(self) -> None:
        if self._key is None:
            return
        if should_scrobble(self._played_ms, self._duration_ms):
            stored = self._queue.enqueue(
                {
                    "artist": self._cur_artist,
                    "track": self._cur_title,
                    "album": self._cur_album,
                    "timestamp": self._started_unix,
                    "duration": int(self._duration_ms // 1000),
                }
            )
            if stored:
                log.info("Scrobble queued: %s — %s", self._cur_artist, self._cur_title)
                self._maybe_drain_locked(time.monotonic(), force=True)
        self._key = None
        self._last_mono = None

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

    def _do_drain(self) -> None:
        try:
            client = self._client
            if client is None:
                return
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
            if e.invalid_session or e.retryable:
                # Transient (offline / outage / rate-limit) or a revoked
                # session: re-raise so ScrobbleQueue.drain stops and
                # keeps the batch for a later retry / reconnect.
                raise
            # Permanently rejected (bad params, suspended API key, …) —
            # re-queuing it forever would head-of-line-block every later
            # scrobble behind a batch that can never succeed. Drop it
            # (report it as "submitted" so drain advances past it).
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
