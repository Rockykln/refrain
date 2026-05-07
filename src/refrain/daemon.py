"""Background daemon: polls sources, drives Discord + tray + notifications.

The worker lives on its own QThread so the GUI is never blocked. Polling is
driven by a QTimer running inside the worker's event loop — crucially, NOT
a `while/sleep` loop, which would block the event loop and prevent
cross-thread slot invocations (Play/Pause/Next/Previous from the tray) from
being delivered.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
import subprocess
import time

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot

from refrain.config import Config
from refrain.cover_fetcher import CoverFetcher
from refrain.discord_rpc import DiscordRPC
from refrain.paths import assets_dir
from refrain.sources.base import PlaybackStatus, TrackInfo
from refrain.sources.bluetooth import BluetoothSource
from refrain.sources.mpris import MPRISSource
from refrain.sources.mpris_server import MPRISServer
from refrain.timing import compute_rpc_start_ts, pick_effective_duration_ms

log = logging.getLogger(__name__)

# Resolved at module import. Re-resolved at call time inside `_notify`
# so a notify-send installed AFTER refrain starts (e.g. user installs
# libnotify mid-session) becomes available without a restart.
_NOTIFY_BIN: str | None = shutil.which("notify-send")


_IDLE_LOG_KEY_SENTINEL = "__refrain_idle_logged__:"


def compute_idle_state(
    track: TrackInfo,
    prev_track_key: str,
    prev_seen_at: float,
    grace_s: int,
    now: float,
    effective_duration_ms: int | None = None,
) -> tuple[TrackInfo, str, float]:
    """Pure-logic idle detection. Returns ``(track_or_empty, new_key, new_seen_at)``.

    When the same track has been reported as PLAYING for longer than its
    own duration plus ``grace_s`` seconds, returns ``TrackInfo.empty()``
    — the source is dangling (typical: closed browser tab whose MPRIS
    handle never released). Caller treats the empty result as "nothing
    is playing", which clears Discord and the tray.

    ``effective_duration_ms`` overrides ``track.duration_ms`` for the
    deadline calculation. Pass the iTunes-catalog value when MPRIS is
    obviously wrong so a 2:11 song doesn't get a 7:21 idle deadline
    just because Apple Music briefly reported a playlist total. None
    means "trust track.duration_ms as-is".

    Logs the detection exactly once per dangling-track instance: the
    returned ``new_key`` is prefixed with a sentinel so subsequent
    polls of the same stuck track skip the log line. Without that
    suppression, every 1 Hz tick re-logged the idle state and drowned
    the live log in identical messages while the user was looking at
    a stuck Apple Music tab.
    """
    if grace_s <= 0:
        return track, "", 0.0
    duration_ms = effective_duration_ms if effective_duration_ms is not None else track.duration_ms
    if track.status != PlaybackStatus.PLAYING or not track.has_track or duration_ms <= 0:
        return track, "", 0.0
    # Preview-clip metadata (effective duration < 30 s) is what Apple
    # Music hands the browser when the user isn't signed in or the
    # song is region-locked. Apple Music keeps reporting the same
    # metadata even after the clip ends — but it's still actually
    # playing the next song under the hood. Idle detection on those
    # would clear Discord while the user is mid-listen. Skip idle for
    # them entirely; the real-track case still gets the dangling-
    # handle protection.
    if duration_ms < 30_000:
        return track, "", 0.0
    track_key = f"{track.source}|{track.title}|{track.artist}|{track.album}"
    sentinel_key = _IDLE_LOG_KEY_SENTINEL + track_key
    if track_key != prev_track_key and prev_track_key != sentinel_key:
        return track, track_key, now
    deadline_s = (duration_ms / 1000.0) + grace_s
    if (now - prev_seen_at) > deadline_s:
        if prev_track_key != sentinel_key:
            log.info(
                "Idle source detected: same track for %.0fs > duration+grace (%.0fs); "
                "clearing playback state",
                now - prev_seen_at,
                deadline_s,
            )
        return TrackInfo.empty(), sentinel_key, prev_seen_at
    return track, track_key, prev_seen_at


def _format_album_for_display(album: str, artist: str, title: str) -> str:
    """Strip artist / title cruft from an album name for Discord's bottom
    line. MPRIS album fields sometimes embed the artist as a prefix
    (`"W&W & Scooter - Sun Rise"`) or repeat the title verbatim; without
    this the third RPC line just echoes what's already shown above.
    """
    if not album:
        return ""
    cleaned = album.strip()
    if artist:
        cleaned = re.sub(
            rf"^{re.escape(artist)}\s*[-:–—]\s*",  # noqa: RUF001 — en-dash and em-dash
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        cleaned = re.sub(
            rf"\s*[-:–—]\s*{re.escape(artist)}$",  # noqa: RUF001
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
    if title and cleaned and cleaned.lower() == title.lower():
        return ""
    return cleaned


class DaemonWorker(QObject):
    trackChanged = Signal(object)  # TrackInfo
    statusChanged = Signal(object)  # PlaybackStatus
    progressTick = Signal(int, int)  # position_ms, duration_ms (only when playing)
    discordConnectionChanged = Signal(bool)  # True = connected, False = disconnected

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._mpris = MPRISSource(config.sources.browser_hints_list())
        self._bluetooth = BluetoothSource(config.sources.bluetooth_device)
        # The active RPC client_id is decided per-source (see
        # `_rpc_client_id_for`). Start with the default; it's swapped in
        # `_update_rpc` the first time a source-specific override
        # applies.
        self._rpc = DiscordRPC(config.discord.client_id)
        self._rpc_active_client_id: str = config.discord.client_id
        self._cover_fetcher = CoverFetcher(max_cached_covers=config.advanced.cover_cache_size)
        self._timer: QTimer | None = None
        self._notify_timer: QTimer | None = None
        self._pending_notify_track: TrackInfo | None = None
        self._notify_retry_count = 0
        self._last_track_fp = ""
        self._last_status: PlaybackStatus | None = None
        self._last_notified_fp = ""
        self._last_rpc_connected: bool | None = None
        self._active_source: str = "none"
        # RPC `start` is recomputed only when the track *content* changes,
        # not every tick — otherwise Discord's elapsed timer jitters.
        self._rpc_track_key = ""
        self._rpc_start_ts = 0
        # Defer the first RPC update for a new track when the cover URL
        # isn't in cache yet — without this, Discord briefly shows the
        # `refrain` brand fallback for ~1-3 s while iTunes search
        # resolves. Capped so we don't block forever on iTunes misses.
        self._rpc_cover_wait_count = 0
        # Idle detection: when the same track-content key has been
        # reported as "playing" for longer than its own duration + a
        # grace window, the source is dangling (typical: browser tab
        # closed without releasing MPRIS). We track first-seen-at and
        # clear playback state in `_poll` once that window expires.
        self._idle_track_key = ""
        self._idle_seen_at: float = 0.0
        # Refrain-as-MPRIS-player. Lets KDE Plasma's panel media-controls
        # applet drive the same Play/Pause/Next/Previous as our tray.
        # Constructed eagerly but `start()` is deferred until after the
        # daemon is on its own thread, so a bus failure on construction
        # doesn't block the daemon coming up.
        self._mpris_server = MPRISServer(
            on_play_pause=lambda: self._control("play_pause"),
            on_next=lambda: self._control("next"),
            on_previous=lambda: self._control("previous"),
        )

    # ----------------------------------------------------------------- lifecycle

    @Slot()
    def start_polling(self) -> None:
        """Called on the worker thread once the QThread's event loop is up."""
        log.info("Daemon started")
        # Pure 1 Hz polling. A signal-driven path via QDBusConnection
        # was prototyped but PySide6's connect-signature handling proved
        # too brittle to ship; revisiting it is a v0.3 task.
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(max(self._config.advanced.poll_interval_ms, 250))
        # Publish ourselves as an MPRIS player so KDE Plasma's panel
        # media-controls applet shows refrain alongside (or instead of)
        # the browser's own MPRIS view. Failures are logged but don't
        # block daemon startup.
        self._mpris_server.start()

    @Slot()
    def cleanup(self) -> None:
        """Called from the main thread via BlockingQueuedConnection during stop."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._notify_timer is not None:
            self._notify_timer.stop()
            self._notify_timer = None
        with contextlib.suppress(Exception):
            self._mpris_server.stop()
        with contextlib.suppress(Exception):
            self._rpc.clear()
        with contextlib.suppress(Exception):
            self._rpc.close()
        self._cover_fetcher.shutdown()
        log.info("Daemon stopped")

    @Slot(object)
    def update_config(self, config: Config) -> None:
        old_default = self._config.discord.client_id
        old_mpris = self._config.discord.client_id_mpris
        old_bt = self._config.discord.client_id_bluetooth
        old_interval = self._config.advanced.poll_interval_ms
        old_cover_cache = self._config.advanced.cover_cache_size
        self._config = config
        self._bluetooth.set_device(config.sources.bluetooth_device)
        self._mpris.set_browser_hints(config.sources.browser_hints_list())
        # Drop the active RPC if any of the relevant client_ids changed —
        # `_update_rpc` reconnects under the right per-source ID on the
        # next tick. Just nilling `_rpc_active_client_id` triggers the
        # source-swap branch.
        if (
            config.discord.client_id != old_default
            or config.discord.client_id_mpris != old_mpris
            or config.discord.client_id_bluetooth != old_bt
        ):
            log.info("Discord client_id config changed, reconnecting RPC")
            with contextlib.suppress(Exception):
                self._rpc.close()
            self._rpc = DiscordRPC(config.discord.client_id)
            self._rpc_active_client_id = config.discord.client_id
            # Eagerly establish the IPC pipe instead of waiting for the
            # next playing-state tick to do it. `_update_rpc` only
            # touches `_rpc` when a track is actually playing, so a
            # user who entered their Application ID with Apple Music
            # paused would otherwise sit there waiting for Discord to
            # connect until they pressed Play.
            with contextlib.suppress(Exception):
                self._rpc._ensure_connected()
            # Trigger an immediate poll so any currently-playing track
            # shows up in Discord without waiting for the next tick.
            QTimer.singleShot(0, self._tick)
        if self._timer is not None and config.advanced.poll_interval_ms != old_interval:
            self._timer.setInterval(max(config.advanced.poll_interval_ms, 250))
        if config.advanced.cover_cache_size != old_cover_cache:
            # Re-prune to the new cap immediately so the user sees their
            # quota take effect without waiting for the next refrain
            # restart. Existing in-memory cache stays intact.
            from refrain.cover_fetcher import _prune_cover_cache

            _prune_cover_cache(config.advanced.cover_cache_size)

    # --------------------------------------------------------------- controls

    @Slot()
    def control_play_pause(self) -> None:
        self._control("play_pause")

    @Slot()
    def control_next(self) -> None:
        self._control("next")

    @Slot()
    def control_previous(self) -> None:
        self._control("previous")

    def _control(self, action: str) -> None:
        active = self._active_source
        log.debug("control %s → active=%s", action, active)

        def _try(src, label: str) -> bool:
            # Belt-and-braces: a bare `getattr(src, action)()` would
            # raise on bus disconnect / typo, killing the whole
            # _control before the follow-up polls fire. Each source
            # already swallows its own dbus errors, but reflection
            # itself can still TypeError if action ever drifts from
            # the source ABI.
            try:
                return bool(getattr(src, action)())
            except Exception as e:
                log.debug("control %s on %s failed: %s", action, label, e)
                return False

        dispatched = False
        if active == "mpris" and self._config.sources.mpris_enabled and _try(self._mpris, "mpris"):
            dispatched = True
        elif (
            active == "bluetooth"
            and self._config.sources.bluetooth_enabled
            and _try(self._bluetooth, "bluetooth")
        ):
            dispatched = True
        elif self._config.sources.mpris_enabled and _try(self._mpris, "mpris"):
            self._active_source = "mpris"
            dispatched = True
        elif self._config.sources.bluetooth_enabled and _try(self._bluetooth, "bluetooth"):
            self._active_source = "bluetooth"
            dispatched = True

        if dispatched:
            # Fast follow-up polls so we surface the new state (track
            # swap on Next/Previous, paused/playing flip on PlayPause)
            # in Discord, the tray, and the published MPRIS server as
            # close to real-time as the browser's mediaSession handler
            # allows. Cascade: 0 ms (immediate next event-loop tick),
            # then 50/150/350/750 ms — each retry catches a slightly
            # slower mediaSession ack while keeping the worst case
            # under one second.
            for delay_ms in (0, 50, 150, 350, 750):
                QTimer.singleShot(delay_ms, self._tick)
        else:
            log.debug("control %s: no source dispatched", action)

    # -------------------------------------------------------------------- core

    def _tick(self) -> None:
        try:
            track = self._poll()
            self._dispatch(track)
        except Exception:
            log.exception("Daemon tick failed")

    def _poll(self) -> TrackInfo:
        track = self._poll_sources()
        return self._apply_idle_detection(track)

    def _poll_sources(self) -> TrackInfo:
        if self._config.sources.mpris_enabled:
            t = self._mpris.read()
            if t.has_track or t.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED):
                self._active_source = "mpris"
                return t
        if self._config.sources.bluetooth_enabled:
            t = self._bluetooth.read()
            if t.has_track or t.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED):
                self._active_source = "bluetooth"
                return t
        return TrackInfo.empty()

    def _apply_idle_detection(self, track: TrackInfo) -> TrackInfo:
        # Idle detection's deadline keys off the track's *real* duration.
        # When MPRIS reports a wonky value (preview-clip 14 s, playlist
        # total 7:21 on a 2:11 song), the iTunes-catalog duration we
        # already cached for cover-art lookup is closer to truth.
        itunes_dur_ms = (
            self._cover_fetcher.get_duration_ms(track.artist, track.title, track.album)
            if self._config.behavior.cover_art
            else 0
        )
        effective_dur_ms = pick_effective_duration_ms(track.duration_ms, itunes_dur_ms)
        result, new_key, new_seen = compute_idle_state(
            track,
            self._idle_track_key,
            self._idle_seen_at,
            int(self._config.advanced.idle_grace_s),
            time.monotonic(),
            effective_duration_ms=effective_dur_ms,
        )
        self._idle_track_key = new_key
        self._idle_seen_at = new_seen
        return result

    def _dispatch(self, track: TrackInfo) -> None:
        fp = track.fingerprint()
        if fp != self._last_track_fp:
            log.info(
                "Track change [%s]: %s — %s (%s)",
                track.source,
                track.title or "—",
                track.artist or "—",
                track.status.value,
            )
            self.trackChanged.emit(track)
            self._last_track_fp = fp
            if (
                self._config.behavior.notifications
                and track.has_track
                and track.status == PlaybackStatus.PLAYING
                and fp != self._last_notified_fp
            ):
                # Kick off the cover download right away, then defer the
                # actual notify by ~1.5 s so the cached image is on disk
                # by the time notify-send reads it. Without this delay the
                # first notification for any track would always show the
                # default icon — the BG fetch hasn't completed yet.
                if self._config.behavior.cover_art:
                    self._cover_fetcher.get(track.artist, track.title, track.album)
                self._schedule_notify(track)
                self._last_notified_fp = fp

        if track.status != self._last_status:
            self.statusChanged.emit(track.status)
            self._last_status = track.status

        # Compute the iTunes-corrected duration once per tick — every
        # consumer downstream wants the same value (tray progress,
        # Discord RPC payload, the published MPRIS server forwarded to
        # Plasma). Doing it here keeps the three sites consistent and
        # avoids three independent cache lookups when one would do.
        # Falls back to MPRIS when iTunes has no match.
        itunes_dur_ms = (
            self._cover_fetcher.get_duration_ms(track.artist, track.title, track.album)
            if self._config.behavior.cover_art
            else 0
        )
        effective_dur_ms = pick_effective_duration_ms(track.duration_ms, itunes_dur_ms)

        # Tray progress label: emit on every tick while playing.
        if track.status == PlaybackStatus.PLAYING and effective_dur_ms > 0:
            # Clamp position to duration so the tray doesn't show a
            # nonsensical "2:30 / 0:14 (-0:00)" line during a brief
            # MPRIS preview-clip glitch on a longer song.
            display_pos = min(max(0, track.position_ms), effective_dur_ms)
            self.progressTick.emit(display_pos, effective_dur_ms)

        self._update_rpc(track, effective_dur_ms, itunes_dur_ms)

        # Push the same track + cover URL to the published MPRIS server
        # so KDE Plasma's panel media-controls applet (and any other
        # MPRIS-aware client) renders what Discord renders. Forward
        # effective_dur_ms so Plasma's panel sees the corrected
        # duration instead of MPRIS' raw (possibly wrong) value.
        with contextlib.suppress(Exception):
            cover_for_mpris = (
                self._cover_fetcher.get(track.artist, track.title, track.album)
                if self._config.behavior.cover_art
                else None
            )
            self._mpris_server.update(track, cover_for_mpris, effective_dur_ms)

        # Surface RPC connect/disconnect transitions so the tray can show
        # an at-a-glance "● Discord connected" indicator.
        rpc_connected = self._rpc.is_connected()
        if rpc_connected != self._last_rpc_connected:
            self.discordConnectionChanged.emit(rpc_connected)
            self._last_rpc_connected = rpc_connected

    # Up to 2 seconds of additional wait time, polled every 250 ms, in case
    # the cover image is still downloading when the initial notify-delay
    # fires. Worst case: notification arrives ~3.5 s after the track
    # change instead of immediately, but always with the album cover.
    _NOTIFY_RETRY_INTERVAL_MS = 250
    _NOTIFY_MAX_RETRIES = 8

    def _schedule_notify(self, track: TrackInfo) -> None:
        """Stash the track and start a single-shot timer; on fire, the
        notification reads the freshest cover from disk. If the track
        changes again before the timer fires, the stale notification is
        suppressed.

        If cover-art is already cached for this track, fire after only
        50 ms — the configured `notify_delay_ms` exists purely to give
        the iTunes search + download time to land before notify-send
        reads the cover off disk, so for cache hits it's just dead
        latency the user feels as "the popup is way too late".
        """
        if self._notify_timer is None:
            self._notify_timer = QTimer()
            self._notify_timer.setSingleShot(True)
            self._notify_timer.timeout.connect(self._fire_pending_notify)
        self._pending_notify_track = track
        self._notify_retry_count = 0
        delay_ms = max(0, self._config.behavior.notify_delay_ms)
        if self._config.behavior.cover_art:
            cached = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            if cached is not None:
                delay_ms = 50
        self._notify_timer.start(delay_ms)

    def _fire_pending_notify(self) -> None:
        track = self._pending_notify_track
        if track is None:
            return
        # Skip if the user already moved on
        if track.fingerprint() != self._last_track_fp:
            self._pending_notify_track = None
            self._notify_retry_count = 0
            return

        # If cover-art is on but the image hasn't landed on disk yet,
        # don't fire a "naked" notification. Retry briefly so the
        # notification consistently shows the album cover.
        if self._config.behavior.cover_art and self._notify_retry_count < self._NOTIFY_MAX_RETRIES:
            cover = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            if cover is None:
                self._notify_retry_count += 1
                self._notify_timer.start(self._NOTIFY_RETRY_INTERVAL_MS)
                return

        self._pending_notify_track = None
        self._notify_retry_count = 0
        self._notify(track)

    def _update_rpc(
        self,
        track: TrackInfo,
        effective_duration_ms: int,
        itunes_dur_ms: int,
    ) -> None:
        if self._config.privacy.mode == "off":
            self._rpc.clear()
            return
        if track.status != PlaybackStatus.PLAYING or not track.has_track:
            self._rpc.clear()
            return

        if self._config.privacy.mode == "minimal":
            self._rpc.update(
                details="Listening to music",
                large_image="refrain",
                large_text="Refrain",
            )
            return

        # Per-source Discord application: the active source picks which
        # client_id RPC connects under. Switching sources reconnects so
        # each source can render with its own application name + uploaded
        # artwork in the user's profile (Apple Music album-grid vs a
        # generic Bluetooth glyph, etc.). When source-specific overrides
        # are empty, both sources share the default client_id and no
        # reconnect is needed.
        target_client_id = self._config.discord.client_id_for(track.source)
        if target_client_id != self._rpc_active_client_id:
            log.info(
                "Discord RPC source-swap: %s → client_id=%s",
                track.source,
                target_client_id[:6] + "…" if target_client_id else "(none)",
            )
            with contextlib.suppress(Exception):
                self._rpc.close()
            self._rpc = DiscordRPC(target_client_id)
            self._rpc_active_client_id = target_client_id
            # Force a fresh start_ts on next compute, since pypresence
            # no longer has any state for the old activity.
            self._rpc_track_key = ""

        track_key = f"{track.source}|{track.title}|{track.artist}|{track.album}"
        is_new_track = track_key != self._rpc_track_key

        # Defer the first RPC update for a new track until the cover URL
        # is in cache — without this, Discord briefly shows the `refrain`
        # brand fallback for ~1-3 s while iTunes search resolves, then
        # flips to the real cover. Capped at ~3 polls (~3 s) so a song
        # that has no iTunes match still updates eventually.
        cover_url: str | None = None
        if self._config.behavior.cover_art:
            cover_url = self._cover_fetcher.get(track.artist, track.title, track.album)
        if (
            is_new_track
            and self._config.behavior.cover_art
            and cover_url is None
            and self._rpc_cover_wait_count < 1
        ):
            # Defer at most one poll for the cover URL — the previous 3-poll
            # cap added up to 3 s of perceived "Discord didn't update yet"
            # latency and felt non-live. With 500 ms default polling, one
            # defer is ~500 ms; on the second tick we update with whatever
            # cover-fetcher has, even if still None (Discord falls back to
            # the brand image and swaps to the real cover on the next tick).
            self._rpc_cover_wait_count += 1
            return
        self._rpc_cover_wait_count = 0

        # effective_duration_ms + itunes_dur_ms come from the caller —
        # _dispatch computes them once per tick and forwards to every
        # consumer (tray progress label, this RPC update, the published
        # MPRIS server) so all three render the same value.

        # Discord elapsed-timer correctness — see refrain.timing for the
        # full rationale. Recomputes on track change, pause/resume, or seek;
        # otherwise leaves the start_ts stable so the progress bar doesn't
        # twitch every poll. Preview-clip mode disables drift-resync: the
        # MPRIS Position field loops 0→8s while the preview replays,
        # which would otherwise reset the elapsed counter every loop.
        # The drift skip keys off MPRIS-reported duration (because the
        # position-loop is a property of MPRIS' preview-clip mode), not
        # the effective duration.
        new_start_ts, recomputed = compute_rpc_start_ts(
            prev_start_ts=self._rpc_start_ts,
            prev_track_key=self._rpc_track_key,
            track_key=track_key,
            position_ms=track.position_ms,
            now=time.time(),
            is_preview_clip=(0 < track.duration_ms < 30_000),
        )
        if recomputed:
            if is_new_track:
                log.info(
                    "RPC reset for new track: pos=%dms mpris_dur=%dms itunes_dur=%dms"
                    " effective=%dms",
                    track.position_ms,
                    track.duration_ms,
                    itunes_dur_ms,
                    effective_duration_ms,
                )
            else:
                log.debug("RPC start resync — likely pause/resume or seek")
        self._rpc_track_key = track_key
        self._rpc_start_ts = new_start_ts

        # Three-line layout, one piece of metadata per line — matches
        # how Spotify/other music RPCs render in Discord. Album is
        # filtered against artist / title so the bottom line never just
        # echoes what's already on a line above.
        details = track.title
        if track.artist:
            state = track.artist
        elif track.album:
            state = track.album
        else:
            state = "Apple Music"

        large_image = cover_url or "refrain"

        album_for_display = _format_album_for_display(track.album, track.artist, track.title)

        # Preview-clip mode for the Discord payload: drop start AND end
        # when the *effective* track length is under 30 s. Using the
        # effective duration (MPRIS overridden by iTunes when they
        # disagree) means a brief MPRIS preview-clip glitch on a
        # full-length song doesn't kill the progress bar — iTunes
        # tells us the real song is 2:11, so we send start/end based
        # on that instead of dropping them just because MPRIS said
        # "14 s" for one poll.
        is_short_track = 0 < effective_duration_ms < 30_000

        payload: dict = {
            "details": details[:128],
            "state": state[:128],
            "large_image": large_image,
        }
        if not is_short_track:
            payload["start"] = self._rpc_start_ts
        # Only emit `large_text` when it adds new info — Discord shows it
        # as a third visible line for LISTENING activity, and an
        # echo of `state` looks broken to viewers.
        if album_for_display and album_for_display.lower() != state.lower():
            payload["large_text"] = album_for_display[:128]

        if not is_short_track and effective_duration_ms > 0:
            payload["end"] = self._rpc_start_ts + (effective_duration_ms // 1000)

        # Prefer the iTunes-resolved song URL (links to the *specific* track)
        # over xesam:url from the browser tab (often the album / playlist page).
        if self._config.behavior.show_buttons:
            song_url = self._cover_fetcher.get_song_url(track.artist, track.title, track.album)
            link = song_url or (track.url if track.source == "mpris" else "")
            if link:
                payload["buttons"] = [{"label": "Listen on Apple Music", "url": link}]

        self._rpc.update(**payload)

    def _notify(self, track: TrackInfo) -> None:
        # Re-resolve so a notify-send installed after refrain started
        # picks up immediately. shutil.which is cheap and only runs on
        # actual track-change ticks.
        global _NOTIFY_BIN
        if _NOTIFY_BIN is None:
            _NOTIFY_BIN = shutil.which("notify-send")
        if not _NOTIFY_BIN:
            return
        body = track.artist
        if track.album:
            body = f"{track.artist} — {track.album}" if track.artist else track.album

        # Pick the image to embed. Only opt in to cover lookup when the
        # user has cover-art enabled — if they've turned it off, we fall
        # back to the bundled brand icon so the notification still has a
        # consistent visual identity.
        image_path: str | None = None
        if self._config.behavior.cover_art:
            local = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            if local is not None:
                image_path = str(local)
        if image_path is None:
            fallback = assets_dir() / "icons" / "refrain.png"
            if fallback.exists():
                image_path = str(fallback)

        # IMPORTANT: pass the SAME image as `-i` AND as the image-path
        # hint. KDE Plasma briefly renders `-i` first while it loads the
        # image-path file from disk; if `-i` is the themed app icon
        # (`refrain`) and image-path is the cover, the user sees a
        # ~50–100 ms flash of the brand badge before the cover paints.
        # Using the same file for both makes the transition invisible.
        # Falls back to the themed `refrain` name only when we have no
        # image at all (cover-art off + bundled fallback missing).
        cmd = [_NOTIFY_BIN, "-a", "Refrain", "-i", image_path or "refrain"]
        if image_path:
            # `string:image-path:` is the freedesktop-standard hint that
            # every modern notification daemon honors. file:// URI form
            # works more reliably across compositors than a bare path —
            # older libnotify versions rejected paths without the scheme.
            cmd.extend(["--hint", f"string:image-path:file://{image_path}"])

        cmd.extend([track.title, body or ""])

        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.debug("notify-send failed: %s", e)


class Daemon:
    """Owns a QThread + DaemonWorker. Worker code runs on the thread's event loop."""

    def __init__(self, config: Config):
        self.thread = QThread()
        self.thread.setObjectName("refrain-daemon")
        self.worker = DaemonWorker(config)
        self.worker.moveToThread(self.thread)
        # Once the thread's event loop is up, kick off polling on the worker thread.
        self.thread.started.connect(self.worker.start_polling)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        # Run cleanup on the worker thread synchronously (BlockingQueued) so
        # the timer is stopped and Discord status is cleared *before* we tear
        # down the thread itself.
        QMetaObject.invokeMethod(self.worker, "cleanup", Qt.ConnectionType.BlockingQueuedConnection)
        self.thread.quit()
        self.thread.wait(2500)
