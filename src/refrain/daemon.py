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
from refrain.timing import compute_rpc_start_ts

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
) -> tuple[TrackInfo, str, float]:
    """Pure-logic idle detection. Returns ``(track_or_empty, new_key, new_seen_at)``.

    When the same track has been reported as PLAYING for longer than its
    own duration plus ``grace_s`` seconds, returns ``TrackInfo.empty()``
    — the source is dangling (typical: closed browser tab whose MPRIS
    handle never released). Caller treats the empty result as "nothing
    is playing", which clears Discord and the tray.

    Logs the detection exactly once per dangling-track instance: the
    returned ``new_key`` is prefixed with a sentinel so subsequent
    polls of the same stuck track skip the log line. Without that
    suppression, every 1 Hz tick re-logged the idle state and drowned
    the live log in identical messages while the user was looking at
    a stuck Apple Music tab.
    """
    if grace_s <= 0:
        return track, "", 0.0
    if track.status != PlaybackStatus.PLAYING or not track.has_track or track.duration_ms <= 0:
        return track, "", 0.0
    track_key = f"{track.source}|{track.title}|{track.artist}|{track.album}"
    sentinel_key = _IDLE_LOG_KEY_SENTINEL + track_key
    if track_key != prev_track_key and prev_track_key != sentinel_key:
        return track, track_key, now
    deadline_s = (track.duration_ms / 1000.0) + grace_s
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
        self._rpc = DiscordRPC(config.discord.client_id)
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
            self._rpc.clear()
        with contextlib.suppress(Exception):
            self._rpc.close()
        self._cover_fetcher.shutdown()
        log.info("Daemon stopped")

    @Slot(object)
    def update_config(self, config: Config) -> None:
        old_client_id = self._config.discord.client_id
        old_interval = self._config.advanced.poll_interval_ms
        old_cover_cache = self._config.advanced.cover_cache_size
        self._config = config
        self._bluetooth.set_device(config.sources.bluetooth_device)
        self._mpris.set_browser_hints(config.sources.browser_hints_list())
        if config.discord.client_id != old_client_id:
            log.info("Discord client_id changed, reconnecting RPC")
            self._rpc.close()
            self._rpc = DiscordRPC(config.discord.client_id)
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
        if (
            active == "mpris"
            and self._config.sources.mpris_enabled
            and getattr(self._mpris, action)()
        ):
            return
        if (
            active == "bluetooth"
            and self._config.sources.bluetooth_enabled
            and getattr(self._bluetooth, action)()
        ):
            return
        # Fallback: try whichever is enabled.
        if self._config.sources.mpris_enabled and getattr(self._mpris, action)():
            self._active_source = "mpris"
            return
        if self._config.sources.bluetooth_enabled and getattr(self._bluetooth, action)():
            self._active_source = "bluetooth"
            return
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
        result, new_key, new_seen = compute_idle_state(
            track,
            self._idle_track_key,
            self._idle_seen_at,
            int(self._config.advanced.idle_grace_s),
            time.monotonic(),
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

        # Tray progress label: emit on every tick while playing
        if track.status == PlaybackStatus.PLAYING and track.duration_ms > 0:
            self.progressTick.emit(track.position_ms, track.duration_ms)

        self._update_rpc(track)

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
        suppressed."""
        if self._notify_timer is None:
            self._notify_timer = QTimer()
            self._notify_timer.setSingleShot(True)
            self._notify_timer.timeout.connect(self._fire_pending_notify)
        self._pending_notify_track = track
        self._notify_retry_count = 0
        self._notify_timer.start(max(0, self._config.behavior.notify_delay_ms))

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

    def _update_rpc(self, track: TrackInfo) -> None:
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
            and self._rpc_cover_wait_count < 3
        ):
            self._rpc_cover_wait_count += 1
            return
        self._rpc_cover_wait_count = 0

        # Discord elapsed-timer correctness — see refrain.timing for the
        # full rationale. Recomputes on track change, pause/resume, or seek;
        # otherwise leaves the start_ts stable so the progress bar doesn't
        # twitch every poll.
        new_start_ts, recomputed = compute_rpc_start_ts(
            prev_start_ts=self._rpc_start_ts,
            prev_track_key=self._rpc_track_key,
            track_key=track_key,
            position_ms=track.position_ms,
            now=time.time(),
        )
        if recomputed:
            if is_new_track:
                # New track — log raw values plus the iTunes-catalog
                # duration (if cached) so duration mismatches are
                # visible: e.g. MPRIS dur=673861 vs iTunes dur=278000
                # tells us the browser integration is wrong about
                # this song.
                itunes_dur_dbg = self._cover_fetcher.get_duration_ms(
                    track.artist, track.title, track.album
                )
                log.info(
                    "RPC reset for new track: pos=%dms mpris_dur=%dms itunes_dur=%dms",
                    track.position_ms,
                    track.duration_ms,
                    itunes_dur_dbg,
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

        payload: dict = {
            "details": details[:128],
            "state": state[:128],
            "start": self._rpc_start_ts,
            "large_image": large_image,
        }
        # Only emit `large_text` when it adds new info — Discord shows it
        # as a third visible line for LISTENING activity, and an
        # echo of `state` looks broken to viewers.
        if album_for_display and album_for_display.lower() != state.lower():
            payload["large_text"] = album_for_display[:128]

        # Pick a duration for Discord's "elapsed / total" display.
        #
        # MPRIS is usually correct — and critically, it knows the *actual*
        # playing track length, which iTunes doesn't: a "(Techno Remix)"
        # MPRIS reports 5:02 while iTunes returns the original 2:26, and
        # picking iTunes there cuts Discord's progress bar in half mid-song.
        #
        # The one MPRIS failure mode worth correcting is preview-clip
        # reporting: when the user isn't signed in (or for a region-locked
        # song), Apple Music in the browser hands MPRIS the 15-30 s
        # preview duration instead of the full track. We catch that by
        # falling back to iTunes when the MPRIS duration is suspiciously
        # short (< 30 s) and iTunes has a real value.
        itunes_dur = self._cover_fetcher.get_duration_ms(track.artist, track.title, track.album)
        if 0 < track.duration_ms < 30_000 and itunes_dur > 30_000:
            effective_duration_ms = itunes_dur
        elif track.duration_ms > 0:
            effective_duration_ms = track.duration_ms
        else:
            effective_duration_ms = itunes_dur
        if effective_duration_ms > 0:
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
