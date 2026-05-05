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

_NOTIFY_BIN = shutil.which("notify-send")


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

    # ----------------------------------------------------------------- lifecycle

    @Slot()
    def start_polling(self) -> None:
        """Called on the worker thread once the QThread's event loop is up."""
        log.info("Daemon started")
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
        self._config = config
        self._bluetooth.set_device(config.sources.bluetooth_device)
        self._mpris.set_browser_hints(config.sources.browser_hints_list())
        if config.discord.client_id != old_client_id:
            log.info("Discord client_id changed, reconnecting RPC")
            self._rpc.close()
            self._rpc = DiscordRPC(config.discord.client_id)
        if self._timer is not None and config.advanced.poll_interval_ms != old_interval:
            self._timer.setInterval(max(config.advanced.poll_interval_ms, 250))

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

    def _dispatch(self, track: TrackInfo) -> None:
        fp = track.fingerprint()
        if fp != self._last_track_fp:
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

        # Discord elapsed-timer correctness — see refrain.timing for the
        # full rationale. Recomputes on track change, pause/resume, or seek;
        # otherwise leaves the start_ts stable so the progress bar doesn't
        # twitch every poll.
        track_key = f"{track.source}|{track.title}|{track.artist}|{track.album}"
        new_start_ts, recomputed = compute_rpc_start_ts(
            prev_start_ts=self._rpc_start_ts,
            prev_track_key=self._rpc_track_key,
            track_key=track_key,
            position_ms=track.position_ms,
            now=time.time(),
        )
        if recomputed and track_key == self._rpc_track_key:
            log.debug("RPC start resync — likely pause/resume or seek")
        self._rpc_track_key = track_key
        self._rpc_start_ts = new_start_ts

        details = track.title
        if track.artist and track.album:
            state = f"{track.artist} • {track.album}"
        elif track.artist:
            state = track.artist
        elif track.album:
            state = track.album
        else:
            state = "Apple Music"

        large_image = "refrain"
        if self._config.behavior.cover_art:
            url = self._cover_fetcher.get(track.artist, track.title, track.album)
            if url:
                large_image = url

        large_text = "Apple Music Web" if track.source == "mpris" else "Bluetooth"

        payload: dict = {
            "details": details[:128],
            "state": state[:128],
            "start": self._rpc_start_ts,
            "large_image": large_image,
            "large_text": large_text,
        }

        # Setting `end` makes Discord show "elapsed / total" (e.g. "1:23 / 3:45")
        # — the user-visible timer is bounded by the actual track length instead
        # of running open-ended. Only set when the source reported a duration.
        if track.duration_ms > 0:
            payload["end"] = self._rpc_start_ts + (track.duration_ms // 1000)

        # Prefer the iTunes-resolved song URL (links to the *specific* track)
        # over xesam:url from the browser tab (often the album / playlist page).
        if self._config.behavior.show_buttons:
            song_url = self._cover_fetcher.get_song_url(track.artist, track.title, track.album)
            link = song_url or (track.url if track.source == "mpris" else "")
            if link:
                payload["buttons"] = [{"label": "Listen on Apple Music", "url": link}]

        self._rpc.update(**payload)

    def _notify(self, track: TrackInfo) -> None:
        if not _NOTIFY_BIN:
            return
        body = track.artist
        if track.album:
            body = f"{track.artist} — {track.album}" if track.artist else track.album

        # `-i refrain` points at the themed app icon (small badge in the
        # corner of the notification). The album cover goes via the
        # `image-path` hint, which is what every spec-compliant
        # notification daemon (KDE, GNOME, Mako, Dunst, …) actually
        # consults to render an inline image. `-i <abs path>` works on
        # some daemons but not all — using both is reliable.
        cmd = [_NOTIFY_BIN, "-a", "Refrain", "-i", "refrain"]

        # Pick the image to embed. Only opt in to a "big image" when the
        # user has cover-art lookups enabled — if they've turned it off,
        # we respect that and let the notification render with just the
        # `-i refrain` app-icon badge.
        image_path: str | None = None
        if self._config.behavior.cover_art:
            local = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            if local is not None:
                image_path = str(local)
            else:
                # iTunes had no match — use the brand SVG as a stand-in
                # so the notification still has a visual.
                fallback = assets_dir() / "icons" / "refrain.svg"
                if fallback.exists():
                    image_path = str(fallback)

        if image_path:
            cmd.extend(
                [
                    "--hint",
                    f"string:image-path:{image_path}",
                    "--hint",
                    f"string:x-kde-iconName:{image_path}",
                ]
            )

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
