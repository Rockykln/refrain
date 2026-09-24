"""Background daemon on its own QThread: polls sources, drives Discord, tray and notifications.

Polling runs on a QTimer, not a sleep loop, so queued slot calls from the tray still arrive."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import re
import shutil
import subprocess
import time
import urllib.parse
from typing import TypeGuard

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, QTimer, Signal, Slot

from refrain import dev_metrics
from refrain.config import AdvancedConfig, Config
from refrain.cover_fetcher import CoverFetcher
from refrain.discord_rpc import DiscordRPC, RPCState
from refrain.history import HistorySnapshot, PlayHistory
from refrain.paths import assets_dir
from refrain.scrobble import Scrobbler, mmss
from refrain.service_status import discord_status, lastfm_status
from refrain.song_lengths import LearnedLengths
from refrain.sources.base import PlaybackStatus, TrackInfo
from refrain.sources.bluetooth import BluetoothSource
from refrain.sources.mpris import MPRISSource
from refrain.sources.mpris_server import MPRISServer
from refrain.timing import (
    CLIP_MAX_MS,
    PositionState,
    PositionTier,
    compute_rpc_start_ts,
    elapsed_ms,
    pick_effective_duration_ms,
    resolve_position,
    source_position_is_fresh,
)

log = logging.getLogger(__name__)

# Resolved at module import. Re-resolved at call time inside `_notify`
# so a notify-send installed AFTER refrain starts (e.g. user installs
# libnotify mid-session) becomes available without a restart.
_NOTIFY_BIN: str | None = shutil.which("notify-send")


_IDLE_LOG_KEY_SENTINEL = "__refrain_idle_logged__:"


# Discord's limit for a button's link.
_BUTTON_URL_MAX = 512


def button_url(link: str) -> str:
    """``link`` as Discord accepts it in a button, or "" when it can't be.

    Plasma reports the tab's address decoded — "search?term=KYANU Fcuk up
    the Club", spaces and all — and Discord refuses the whole activity
    over it, not just the button.
    """
    link = urllib.parse.quote(link.strip(), safe=":/?#[]@!$&'()*+,;=%~")
    parts = urllib.parse.urlsplit(link)
    if parts.scheme != "https" or not parts.netloc or len(link) > _BUTTON_URL_MAX:
        return ""
    return link


def scrobble_duration_ms(
    effective_ms: int, disputed: bool, reported_length_ms: int, known_ms: int
) -> int:
    """The length to hand Last.fm, which is not always the one we display.

    When the source and the length measured from whole plays disagree
    about how long a track is, Refrain shows no total at all: a
    confident wrong number is worse than an honest blank. Last.fm can't work that way — its rules are
    written in terms of length (a 30-second floor, then half the track
    or four minutes, whichever comes first), so withholding it doesn't
    mean "we're not sure", it means the play never counts.

    So a disputed length falls back to a candidate, and among those that
    clear Last.fm's 30-second floor it takes the shorter — which is the
    direction the two mistakes point. Guess long and a real play never
    reaches the half-way mark, and the scrobble is lost with nothing to
    show for it. Guess short and the scrobble merely lands early, on a
    track the user demonstrably was playing; Last.fm caps the wait at
    four minutes anyway, so the difference is small and recoverable.

    The floor is what makes "shorter" conditional rather than absolute.
    Apple Music reports a 14-second preview-clip length for a few polls
    on a full-length song, and simply taking the smaller number there
    would hand the scrobbler a length below the floor — losing the play
    just as a zero length would.
    """
    if not disputed:
        return effective_ms
    usable = [ms for ms in (reported_length_ms, known_ms) if ms >= 30_000]
    if usable:
        return min(usable)
    # Neither candidate clears the floor, so the choice cannot rescue the
    # scrobble and the track may genuinely be that short. Fall back to the
    # ordinary pick rather than inventing a length.
    return pick_effective_duration_ms(reported_length_ms, 0, known_ms)


def compute_idle_state(
    track: TrackInfo,
    prev_track_key: str,
    prev_seen_at: float,
    grace_s: int,
    now: float,
    effective_duration_ms: int | None = None,
    source_alive: bool = False,
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

    ``source_alive`` says the source's own position moved recently. A
    dangling handle cannot do that — the tab is gone and nothing is
    advancing — so a moving position is proof the deadline should not be
    running yet, and it pushes the anchor forward. This is what keeps a
    wrong duration (a catalog match of 58 s for a 2:45 song) from
    clearing a track mid-play.

    Logs the detection exactly once per dangling-track instance: the
    returned ``new_key`` is prefixed with a sentinel so subsequent
    polls of the same stuck track skip the log line.
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
    track_key = track.content_key()
    sentinel_key = _IDLE_LOG_KEY_SENTINEL + track_key
    if track_key != prev_track_key and prev_track_key != sentinel_key:
        return track, track_key, now
    if source_alive:
        # Demonstrably playing: keep the track and restart the clock from
        # this proof of life, so the deadline only ever measures silence.
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


def select_source_track(
    mpris: TrackInfo | None,
    bluetooth: TrackInfo | None,
) -> tuple[TrackInfo, str]:
    """Pick which source's reading drives this tick.

    A source is a *candidate* when it has a track or is in a
    PLAYING / PAUSED state. Among candidates, an actively PLAYING
    source always outranks a merely paused / loaded one: with a static
    "MPRIS before Bluetooth" order, a stale *paused* Apple Music tab
    would mask music playing over Bluetooth, and idle detection only
    fires on PLAYING, so the tab would never clear either.

    When neither source is playing (both paused / loaded), MPRIS keeps
    priority so the active source doesn't flip-flop between two idle
    sources every poll — ``max`` returns the first maximal element, and
    MPRIS is inserted first.

    ``mpris`` / ``bluetooth`` are the per-source reads, or ``None`` when
    that source is disabled. Returns ``(track, source_name)``;
    ``(TrackInfo.empty(), "none")`` when nothing qualifies.
    """

    def _is_candidate(t: TrackInfo | None) -> TypeGuard[TrackInfo]:
        return t is not None and (
            t.has_track or t.status in (PlaybackStatus.PLAYING, PlaybackStatus.PAUSED)
        )

    candidates: list[tuple[int, str, TrackInfo]] = []
    for name, t in (("mpris", mpris), ("bluetooth", bluetooth)):
        if _is_candidate(t):
            rank = 1 if t.status == PlaybackStatus.PLAYING else 0
            candidates.append((rank, name, t))
    if not candidates:
        return TrackInfo.empty(), "none"
    best = max(candidates, key=lambda c: c[0])
    return best[2], best[1]


def _format_album_for_display(album: str, artist: str, title: str) -> str:
    """Strip artist / title cruft from an album name for Discord's bottom
    line. MPRIS album fields sometimes embed the artist as a prefix
    (`"Wren & Ash - Salt Flats"`) or repeat the title verbatim; without
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


def build_notify_argv(
    notify_bin: str,
    image_path: str | None,
    title: str,
    body: str,
    *,
    replace_id: int | None = None,
    print_id: bool = False,
) -> list[str]:
    """Assemble the ``notify-send`` argv.

    ``image_path`` is passed BOTH as ``-i`` and as the
    ``string:image-path:`` hint — KDE Plasma briefly renders ``-i``
    while it loads the hint file from disk, so using the same file for
    both makes the cover→cover transition invisible. Falls back to the
    themed ``refrain`` name when there's no image at all.

    ``replace_id`` emits ``--replace-id`` so the notification daemon
    updates the existing bubble in place instead of stacking a second
    one — used to swap a late-arriving cover into an already-shown
    brand-fallback notification. ``print_id`` adds ``--print-id`` so
    the daemon prints the (new) notification id to stdout for us to
    capture.
    """
    argv = [notify_bin, "-a", "Refrain", "-i", image_path or "refrain"]
    if image_path:
        # file:// URI form works more reliably across compositors than
        # a bare path — older libnotify versions rejected schemeless
        # paths for the image-path hint.
        argv.extend(["--hint", f"string:image-path:file://{image_path}"])
    if replace_id is not None:
        argv.extend(["--replace-id", str(replace_id)])
    if print_id:
        argv.append("--print-id")
    # "--" ends option parsing: a song title may start with a dash.
    argv.extend(["--", title, body or ""])
    return argv


def notify_over_dbus(
    image_path: str | None,
    title: str,
    body: str,
    *,
    replace_id: int | None = None,
) -> int | None:
    """Show a notification without notify-send, over the same D-Bus service.

    Returns the notification id, which the interface hands back directly —
    no --print-id round trip. None when the service is not reachable.
    """
    try:
        import dbus

        bus = dbus.SessionBus()
        server = bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
        hints = {"image-path": f"file://{image_path}"} if image_path else {}
        new_id = dbus.Interface(server, "org.freedesktop.Notifications").Notify(
            "Refrain",
            dbus.UInt32(replace_id or 0),
            image_path or "refrain",
            title,
            body,
            [],
            hints,
            -1,
        )
        return int(new_id)
    except Exception as e:
        log.debug("Notification over D-Bus failed: %s", e)
        return None


def parse_notify_id(stdout: str) -> int | None:
    """Parse the integer id ``notify-send --print-id`` writes to stdout.

    Returns ``None`` for empty / non-numeric output (a libnotify build
    without ``--print-id`` support, a wrapper that prints nothing) so
    the caller degrades to "first notification shown, no later swap"
    rather than crashing.
    """
    line = (stdout or "").strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0].strip())
    except ValueError:
        return None


class DaemonWorker(QObject):
    trackChanged = Signal(object)  # TrackInfo
    statusChanged = Signal(object)  # PlaybackStatus
    progressTick = Signal(int, int)  # position_ms, duration_ms (only when playing)
    # The time in progressTick is an estimate from before a restart; only on change.
    progressEstimated = Signal(bool)
    # A refrain.service_status value and the reason behind it; only on change.
    discordStateChanged = Signal(str, str)
    lastfmStateChanged = Signal(str, str)
    historyChanged = Signal(object)  # HistorySnapshot
    coverChanged = Signal(str)  # cover URL of the song playing now, "" when there is none

    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        self._mpris = MPRISSource(config.sources.browser_hints_list())
        self._bluetooth = BluetoothSource(config.sources.bluetooth_device)
        # The active RPC client_id is decided per-source (see
        # `_rpc_client_id_for`). Start with the default; it's swapped in
        # `_update_rpc` the first time a source-specific override
        # applies.
        self._rpc = DiscordRPC(config.discord.client_id, config.discord.all_clients)
        self._rpc_active_client_id: str = config.discord.client_id
        # The Status window shows the same song; a popup on top of it is noise.
        self._notifications_muted = False
        self._cover_emitted = ""
        self._cover_fetcher = CoverFetcher()
        # Last.fm scrobbling — opt-in, alongside (never replacing) the
        # Discord RPC. Constructed always; inert until the user enables
        # it + connects an account. All network work runs on its own
        # worker executor so the poll tick never blocks.
        # Recently played — see refrain.history. Loaded here, on the
        # main thread, so the window has its first snapshot before the
        # daemon thread starts; from then on only the worker touches it.
        self._history = PlayHistory(config.history)
        # Also clears out cover images an older version kept by count.
        self._keep_history_covers(self._history.snapshot())
        # Lengths measured from whole plays, for songs the catalog has none
        # for — see refrain.song_lengths.
        self._song_lengths = LearnedLengths()
        # The known length of the song this tick is about, shared by every
        # pipeline stage; dropped at the start of each tick so a catalog
        # answer that arrived since is picked up.
        self._tick_known: tuple[tuple, tuple[int, int]] | None = None
        self._album_display: tuple[tuple[str, str, str], str] = (("", "", ""), "")
        # (source, title, artist) of the song playing, the album it is known
        # under for this play, and the one the history and Last.fm keep.
        self._song_album: tuple[tuple[str, str, str], str, str] = (("", "", ""), "", "")
        self._catalog_album: tuple[tuple[str, str, str], str] = (("", "", ""), "")
        self._prev_track: TrackInfo | None = None
        self._max_reported_ms = 0
        # When we last skipped a song ourselves: a play we cut short says
        # nothing about how long the song is.
        self._control_at = 0.0
        self._history_error_logged = False
        self._scrobbler = Scrobbler(config.lastfm, on_queued=self._on_scrobble_queued)
        self._timer: QTimer | None = None
        self._clock = dev_metrics.NULL_CLOCK
        self._stopped = False
        self._notify_timer: QTimer | None = None
        self._pending_notify_track: TrackInfo | None = None
        self._notify_retry_count = 0
        # Cover-replace watch: when the initial retry window times out
        # without a cover, fire the brand-fallback notification, remember
        # its id, and keep watching — once the cover finishes downloading
        # we re-issue the notification with `--replace-id` so it swaps in
        # place (no second popup) instead of the user never seeing it.
        self._replace_timer: QTimer | None = None
        self._replace_track: TrackInfo | None = None
        self._replace_attempts = 0
        self._notify_id: int | None = None
        self._notify_id_fp = ""
        self._last_track_fp = ""
        # `player` alone (no other field changing) still needs to reach the
        # Status window's browser hint, and fingerprint() deliberately
        # ignores it — see _tick.
        self._last_track_player = ""
        self._last_status: PlaybackStatus | None = None
        self._last_notified_fp = ""
        self._last_rpc_connected = False
        self._last_discord_state: tuple[str, str] | None = None
        self._last_lastfm_state: tuple[str, str] | None = None
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
        self._rpc_cover_wait_key = ""
        # Idle detection: when the same track-content key has been
        # reported as "playing" for longer than its own duration + a
        # grace window, the source is dangling (typical: browser tab
        # closed without releasing MPRIS). We track first-seen-at and
        # clear playback state in `_poll` once that window expires.
        self._idle_track_key = ""
        self._idle_seen_at: float = 0.0
        # Position resolution. Sources misreport position in several
        # ways (a queue-cumulative timeline, a Position that stops
        # refreshing mid-track), so `_resolve_position` runs one tiered
        # decision per poll: the source's own value when it holds up,
        # our own clock when it doesn't, and nothing at all when neither
        # is honest. `_position_known` carries that last case to the
        # tray, Discord and the MPRIS server so they hide the time
        # instead of rendering a wrong one.
        self._position_state = PositionState()
        self._position_tier = PositionTier.UNKNOWN
        self._position_known = False
        self._progress_estimated = False
        # True for the one tick in which the resolver saw the song begin
        # again on the same track — a replay, for the history and Last.fm.
        self._track_restarted = False
        self._last_rpc_timing: tuple | None = None
        # Refrain-as-MPRIS-player. Lets KDE Plasma's panel media-controls
        # applet drive the same Play/Pause/Next/Previous as our tray.
        # Constructed eagerly but `start()` is deferred until after the
        # daemon is on its own thread, so a bus failure on construction
        # doesn't block the daemon coming up.
        # Plasma's calls arrive on the thread that dispatches the server's
        # connection — the main thread. Queue them over to this worker,
        # which owns the sources and their D-Bus connections, instead of
        # running the sources on two threads at once.
        self._mpris_server = MPRISServer(
            on_play_pause=lambda: self._queue_control("control_play_pause"),
            on_play=lambda: self._queue_control("control_play"),
            on_pause=lambda: self._queue_control("control_pause"),
            on_next=lambda: self._queue_control("control_next"),
            on_previous=lambda: self._queue_control("control_previous"),
        )

    # ----------------------------------------------------------------- lifecycle

    @Slot()
    def start_polling(self) -> None:
        """Called on the worker thread once the QThread's event loop is up."""
        log.info("Daemon started")
        dev_metrics.mark("daemon_started")
        # Pure-polling design (default 500 ms; user-configurable via
        # advanced.poll_interval_ms, floored at 250 ms). Not signal-driven:
        # PySide6's QDBusConnection connect-signature handling is too brittle.
        self._timer = QTimer(self)
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
        # A single-shot poll queued by update_config or a control can still
        # be pending, and must not reconnect Discord after the clear below.
        self._stopped = True
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._notify_timer is not None:
            self._notify_timer.stop()
            self._notify_timer = None
        if self._replace_timer is not None:
            self._replace_timer.stop()
            self._replace_timer = None
        with contextlib.suppress(Exception):
            self._mpris_server.stop()
        with contextlib.suppress(Exception):
            # Past the rate limit: the socket closes right after, and a
            # held-back clear would leave the last song on the profile.
            self._rpc.clear(force=True)
        with contextlib.suppress(Exception):
            self._rpc.close()
        with contextlib.suppress(Exception):
            # Banks a qualifying in-progress track to the on-disk queue
            # so quitting mid-song still scrobbles it next launch.
            self._scrobbler.shutdown()
        with contextlib.suppress(Exception):
            # After the Scrobbler, whose final scrobble may still mark a
            # song. Keeps the song playing right now if it already counted.
            self._history.shutdown()
        self._cover_fetcher.shutdown()
        log.info("Daemon stopped")

    @Slot(object)
    def update_config(self, config: Config) -> None:
        old_default = self._config.discord.client_id
        old_mpris = self._config.discord.client_id_mpris
        old_bt = self._config.discord.client_id_bluetooth
        old_all_clients = self._config.discord.all_clients
        old_interval = self._config.advanced.poll_interval_ms
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
            or config.discord.all_clients != old_all_clients
        ):
            log.info("Discord settings changed, reconnecting RPC")
            with contextlib.suppress(Exception):
                self._rpc.close()
            self._rpc = DiscordRPC(config.discord.client_id, config.discord.all_clients)
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
        # Last.fm: pick up enable/disable, new credentials, or a freshly
        # connected session without a process restart (unlike the
        # Discord client_id, the Scrobbler rebinds cleanly in place).
        with contextlib.suppress(Exception):
            self._scrobbler.reconfigure(config.lastfm)
        # History: on/off (off deletes the stored list) and the song
        # limit (a lower one drops the oldest songs) apply straight away.
        with contextlib.suppress(Exception):
            if self._history.reconfigure(config.history):
                self._emit_history()

    # ---------------------------------------------------------------- history

    def history_snapshot(self) -> HistorySnapshot:
        return self._history.snapshot()

    @Slot(bool)
    def set_notifications_muted(self, muted: bool) -> None:
        self._notifications_muted = muted

    @Slot()
    def clear_history(self) -> None:
        self._history.clear()
        self._emit_history()

    @Slot(object)
    def remove_from_history(self, entry) -> None:
        if self._history.remove(entry.started_at, entry.title, entry.artist):
            self._emit_history()

    def _emit_history(self) -> None:
        snapshot = self._history.snapshot()
        # Before the window hears of it, so it finds the thumbnails.
        self._keep_history_covers(snapshot)
        self.historyChanged.emit(snapshot)

    def _keep_history_covers(self, snapshot: HistorySnapshot) -> None:
        """Cover images stay on disk only for the songs in the history."""
        try:
            self._cover_fetcher.keep_covers(e.cover_url for e in snapshot.entries)
        except Exception:
            log.exception("Could not sync the cover images with the history")

    def _on_scrobble_queued(self, artist: str, title: str) -> None:
        # Called by the Scrobbler on this thread, with its lock held.
        if self._history.mark_scrobbled(artist, title):
            self._emit_history()

    def _update_history(
        self,
        track: TrackInfo,
        duration_ms: int,
        position_ms: int | None,
        restarted: bool,
        album: str = "",
        shown_ms: int | None = None,
    ) -> None:
        """``album`` is one found after the song started; the cover is
        still looked up under the album the song is known by."""
        try:
            cover_url = song_url = ""
            if track.has_track and self._config.behavior.cover_art:
                cover_url = self._cover_fetcher.get(track.artist, track.title, track.album) or ""
                song_url = (
                    self._cover_fetcher.get_song_url(track.artist, track.title, track.album) or ""
                )
            if self._history.update(
                dataclasses.replace(track, album=album) if album else track,
                duration_ms,
                cover_url=cover_url,
                song_url=song_url,
                position_ms=position_ms,
                restarted=restarted,
                shown_ms=shown_ms,
            ):
                self._emit_history()
        except Exception:
            # Once, not every tick: a history that can't update is worth
            # one line in the log, not one every 500 ms.
            if not self._history_error_logged:
                self._history_error_logged = True
                log.exception("History update failed")

    # --------------------------------------------------------------- controls

    @Slot()
    def control_play_pause(self) -> None:
        self._control("play_pause")

    @Slot()
    def control_play(self) -> None:
        self._control("play")

    @Slot()
    def control_pause(self) -> None:
        self._control("pause")

    @Slot()
    def control_next(self) -> None:
        self._control("next")

    @Slot()
    def control_previous(self) -> None:
        self._control("previous")

    def _queue_control(self, slot: str) -> None:
        QMetaObject.invokeMethod(self, slot, Qt.ConnectionType.QueuedConnection)

    def _control(self, action: str) -> None:
        active = self._active_source
        log.debug("control %s → active=%s", action, active)

        def _try(src, label: str) -> bool:
            # A bare `getattr(src, action)()` would
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
            self._control_at = time.monotonic()
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
        if self._stopped:
            return
        self._tick_known = None
        self._clock = dev_metrics.poll_clock()
        try:
            track = self._poll()
            # Nothing else about an untracked poll ever changes, so the
            # fingerprint-gated emit below stays silent — but the Status
            # window's browser hint (in `player`) still needs to reach it.
            if track.has_track:
                self._last_track_player = ""
            elif track.player != self._last_track_player:
                self._last_track_player = track.player
                self.trackChanged.emit(track)
            self._dispatch(track)
            self._clock.done()
        except Exception:
            log.exception("Daemon tick failed")
        finally:
            self._clock = dev_metrics.NULL_CLOCK

    def _poll(self) -> TrackInfo:
        track = self._keep_song_album(self._poll_sources())
        self._clock.lap("source")
        track = self._apply_idle_detection(self._resolve_position(track))
        self._clock.lap("position")
        return track

    def _poll_sources(self) -> TrackInfo:
        mpris_t = self._mpris.read() if self._config.sources.mpris_enabled else None
        # Short-circuit: an actively-playing MPRIS source already
        # outranks anything Bluetooth could report (nothing beats
        # PLAYING — see `select_source_track`), so skip the extra
        # system-bus round-trip in the common "music playing in the
        # browser" case. We only pay for the Bluetooth read when MPRIS
        # is paused / loaded / absent — exactly the case where a stale
        # paused tab could mask an actively-playing BT source.
        if mpris_t is not None and mpris_t.status == PlaybackStatus.PLAYING:
            bt_t = None
        else:
            bt_t = self._bluetooth.read() if self._config.sources.bluetooth_enabled else None
        track, source = select_source_track(mpris_t, bt_t)
        if source != "none":
            self._active_source = source
        elif mpris_t is not None and mpris_t.player:
            # No source is actually playing anything Refrain recognises, but
            # MPRIS saw a browser player with no URL at all — pass its name
            # through for the Status window's troubleshooting hint.
            track = dataclasses.replace(track, player=mpris_t.player)
        return track

    def _keep_song_album(self, track: TrackInfo) -> TrackInfo:
        """The song keeps the album it started with until another song plays.

        A browser can send the album a poll after the title, or drop it for
        a poll. The notification, Discord and the catalog lookup key a song
        by its album too, so either would end the play and start it again.
        A late album goes to the history and Last.fm alone (see `_heard`).
        Two different albums are two recordings.
        """
        if not track.has_track:
            return track
        names = (track.source, track.title, track.artist)
        known_names, album, heard = self._song_album
        if names == known_names and (not album or not track.album):
            if not heard and track.album:
                self._song_album = (names, album, track.album)
            return track if track.album == album else dataclasses.replace(track, album=album)
        self._song_album = (names, track.album, track.album)
        return track

    def _heard(self, track: TrackInfo) -> TrackInfo:
        """The track as the history and Last.fm keep it.

        They take an album that turns up after the song started, from the
        player or the catalog, into the same play (see
        `refrain.scrobble.fills_in_album`). Once it has one, it keeps it.
        """
        names, album, heard = self._song_album
        if not track.has_track or names != (track.source, track.title, track.artist):
            return track
        if not heard:
            heard = track.album
            if not heard and self._config.behavior.cover_art:
                heard = self._cover_fetcher.get_album(track.artist, track.title, track.album)
            self._song_album = (names, album, heard)
        return (
            track if not heard or heard == track.album else dataclasses.replace(track, album=heard)
        )

    def _catalog_album_for(self, track: TrackInfo) -> str:
        """The catalog's album for a song the player sends none for.

        Asked once per song: one that arrives while it plays would make it
        a new song downstream, just as a late album from the player would.
        """
        names = (track.source, track.title, track.artist)
        if self._catalog_album[0] != names:
            found = self._cover_fetcher.get_album(track.artist, track.title, track.album)
            self._catalog_album = (names, found)
        return self._catalog_album[1]

    def _catalog_duration_ms(self, track: TrackInfo) -> int:
        return (
            self._cover_fetcher.get_duration_ms(track.artist, track.title, track.album)
            if self._config.behavior.cover_art
            else 0
        )

    def _known_duration_ms(self, track: TrackInfo, catalog_ms: int | None = None) -> int:
        """The song's length from outside the player: the iTunes catalog,
        else what whole plays of it measured (see refrain.song_lengths)."""
        if catalog_ms is None:
            catalog_ms = self._catalog_duration_ms(track)
        return catalog_ms or self._song_lengths.get_ms(track.artist, track.title, track.album)

    def _tick_lengths(self, track: TrackInfo) -> tuple[int, int]:
        """``(catalog_ms, known_ms)``, looked up once per tick and song.

        A length measured or dropped mid-tick bumps the learned lengths'
        generation, so the stages after it see the new answer."""
        key = (track.artist, track.title, track.album, self._song_lengths.generation)
        if self._tick_known is not None and self._tick_known[0] == key:
            return self._tick_known[1]
        catalog = self._catalog_duration_ms(track)
        lengths = (catalog, self._known_duration_ms(track, catalog))
        self._tick_known = (key, lengths)
        return lengths

    def _tick_known_ms(self, track: TrackInfo) -> int:
        """`_known_duration_ms`, looked up once per tick and song."""
        return self._tick_lengths(track)[1]

    def _duration_for(
        self,
        track: TrackInfo,
        state: PositionState | None = None,
        lengths: tuple[int, int] | None = None,
    ) -> tuple[int, bool]:
        """The track length every consumer should use, and whether it's disputed.

        The catalog's length wins whenever the lookup matched the song
        (see `pick_effective_duration_ms`). A browser's `mpris:length` is
        too often its buffer, or a number short of the song, and the first
        song after a start gives no track change to catch it out with.

        Without one, the player's length stands against the one measured
        from whole plays. A stream-relative player's length describes its
        buffer, and then only the measured length knows. Until
        `resolve_position` has established which case applies, a
        disagreement between the two is undecidable, and saying so is more
        use than picking one. Centralised here so position resolution,
        idle detection and `_dispatch` can't drift apart on the answer.

        ``lengths`` is `_tick_lengths`' answer, when the caller has it.
        Returns ``(effective_ms, disputed)``.
        """
        state = state or self._position_state
        if lengths is None:
            lengths = (self._catalog_duration_ms(track), self._known_duration_ms(track))
        catalog_ms, known_ms = lengths
        if catalog_ms > 0:
            return catalog_ms, False
        mpris_dur_ms = track.duration_ms
        if state.cumulative:
            # Such a length can grow by 135 s over 144 s of playback on
            # one unchanging track. Better no total at all
            # than a 6:52 one on a 2:24 song.
            return known_ms, False
        if (
            mpris_dur_ms > 0
            and known_ms > 0
            and not state.track_relative
            # A clip or a buffered segment is no rival length.
            and not mpris_dur_ms < CLIP_MAX_MS <= known_ms
            and abs(mpris_dur_ms - known_ms) > max(5_000, known_ms * 0.15)
        ):
            return 0, True
        return pick_effective_duration_ms(mpris_dur_ms, 0, known_ms), False

    def _measure_previous_song(self, now: float) -> None:
        """Note how long the song that just ended ran, if that is its length.

        Only a play we watched from its start to an end the player reached
        itself measures the song — a track change, or the song beginning
        again on repeat. Our own clock knows where it began and how much of
        the time since was spent paused. A song we skipped, or one already
        playing when Refrain started, says nothing.
        """
        prev, state = self._prev_track, self._position_state
        if prev is None or not state.anchored or not state.track_key:
            return
        if self._position_tier is PositionTier.UNKNOWN:
            return
        if now - self._control_at <= 2.0:
            return  # we skipped it
        if self._catalog_duration_ms(prev) > 0:
            return  # its length is known; measuring it changes nothing
        # Our clock starts late when the change is reported late.
        played = self._max_reported_ms or elapsed_ms(state, now)
        if self._song_lengths.observe(prev.artist, prev.title, prev.album, played):
            log.info(
                "Length: %s — %s runs %s, measured from whole plays",
                prev.artist or "—",
                prev.title,
                mmss(self._song_lengths.get_ms(prev.artist, prev.title, prev.album)),
            )
        elif (
            self._config.privacy.mode != "off"
            and self._scrobbler.looks_up_lengths
            and self._song_lengths.wants_reference(prev.artist, prev.title, prev.album)
        ):
            self._scrobbler.check_length(
                prev.artist, prev.title, lambda ms: self._on_lastfm_length(prev, ms)
            )

    def _on_lastfm_length(self, track: TrackInfo, length_ms: int | None) -> None:
        """Last.fm's length for a song measured once; on the scrobbler's worker."""
        if length_ms is None:
            return
        agreed = self._song_lengths.add_reference(track.artist, track.title, track.album, length_ms)
        if agreed:
            log.info(
                "Length: %s — %s runs %s, measured and confirmed by Last.fm",
                track.artist or "—",
                track.title,
                mmss(self._song_lengths.get_ms(track.artist, track.title, track.album)),
            )
        elif agreed is False:
            log.debug(
                "Length: Last.fm gives %s — %s as %s, not its measured length",
                track.artist or "—",
                track.title,
                mmss(length_ms),
            )

    def _follow_reported_position(self, track: TrackInfo, state: PositionState) -> None:
        """A measured length the player plays past was measured short."""
        if not track.has_track or state.cumulative or track.loop_track:
            # On repeat-one some players keep counting across the loops.
            return
        if 0 < track.duration_ms < CLIP_MAX_MS:
            return  # a media segment's position, not the song's
        if state.track_relative:
            self._max_reported_ms = max(self._max_reported_ms, track.position_ms)
        if self._catalog_duration_ms(track) > 0:
            return
        measured = self._song_lengths.get_ms(track.artist, track.title, track.album)
        if measured and track.position_ms > measured + 2_000:
            self._song_lengths.forget(track.artist, track.title, track.album)
            log.info(
                "Length: %s — %s plays past its measured %s; measuring again",
                track.artist or "—",
                track.title,
                mmss(measured),
            )

    def _resolve_position(self, track: TrackInfo) -> TrackInfo:
        """Decide what this track's position actually is — or that we don't know.

        Runs first in the poll pipeline so every stage below it, and
        every consumer in `_dispatch`, works from the same answer.
        Sitting upstream of idle detection also keeps the clock's anchor
        alive across an idle clear, since the source itself carries on
        reporting the track.
        """
        track_key = track.content_key() if track.has_track else ""
        now = time.monotonic()
        estimate_ms = None
        if track_key != self._position_state.track_key:
            self._measure_previous_song(now)
            if track_key:
                estimate_ms = self._history.resume_estimate_ms(track, time.time())

        def resolve(length: tuple[int, bool]):
            return resolve_position(
                self._position_state,
                track_key,
                track.position_ms,
                length[0],
                track.status == PlaybackStatus.PLAYING,
                now,
                reported_length_ms=track.duration_ms,
                duration_disputed=length[1],
                stall_after_s=float(self._config.advanced.position_stall_s),
                loop_track=track.loop_track,
                estimate_ms=estimate_ms,
            )

        lengths = self._tick_lengths(track)
        length = self._duration_for(track, lengths=lengths)
        position_ms, tier, new_state = resolve(length)
        # This poll can itself reveal what the source's length describes
        # (a length that moves latches it as a stream). Judge the position
        # against the length the rest of the tick will use.
        settled = self._duration_for(track, new_state, lengths)
        if settled != length:
            length = settled
            position_ms, tier, new_state = resolve(length)
        duration_ms, disputed = length
        self._track_restarted = (
            new_state.track_key == self._position_state.track_key
            and new_state.restarts > self._position_state.restarts
        )
        if self._track_restarted:
            log.info("Position: %s — %s began again", track.artist or "—", track.title)
            # A song starting over has just run its full length, the same
            # as one ending at a track change.
            self._measure_previous_song(now)
        if new_state.track_key != self._position_state.track_key or self._track_restarted:
            self._max_reported_ms = 0
        self._position_state = new_state
        self._prev_track = track if track.has_track else None
        self._follow_reported_position(track, new_state)
        self._position_known = position_ms is not None
        # Log the tier transitions only — a degraded source stays
        # degraded for hundreds of polls, and per-tick logging would
        # drown the live log.
        if tier != self._position_tier and track.has_track:
            log.debug(
                "Position inputs: reported=%s length=%s duration=%s disputed=%s playing=%s "
                "cumulative=%s anchored=%s track_relative=%s",
                track.position_ms,
                track.duration_ms,
                duration_ms,
                disputed,
                track.status == PlaybackStatus.PLAYING,
                new_state.cumulative,
                new_state.anchored,
                new_state.track_relative,
            )
            log.info(
                "Position: %s → %s%s",
                self._position_tier.value,
                tier.value,
                {
                    PositionTier.REPORTED: " (source's own value)",
                    PositionTier.COMPUTED: " (source unusable; counting from the track start)",
                    PositionTier.ESTIMATED: " (source unusable; estimated from before the restart)",
                    PositionTier.UNKNOWN: " (no honest value — hiding the time)",
                }[tier],
            )
        self._position_tier = tier
        if position_ms == track.position_ms:
            return track
        # An unresolvable position is zeroed rather than left as the
        # source reported it: the MPRIS server publishes whatever is on
        # the track, and forwarding a value we have just declared
        # dishonest would put it in front of every other MPRIS client.
        return dataclasses.replace(track, position_ms=position_ms or 0)

    def _apply_idle_detection(self, track: TrackInfo) -> TrackInfo:
        # Idle detection's deadline keys off the track's *real* duration.
        # When MPRIS reports a wonky value (preview-clip 14 s, playlist
        # total 7:21 on a 2:11 song), the iTunes-catalog duration we
        # already cached for cover-art lookup is closer to truth.
        lengths = self._tick_lengths(track)
        known_ms = lengths[1]
        effective_dur_ms, disputed = self._duration_for(track, lengths=lengths)
        if disputed:
            # No length to show, but the deadline only needs an upper bound:
            # the longer candidate never clears a real play early.
            effective_dur_ms = max(track.duration_ms, known_ms)
        now = time.monotonic()
        # The source's position moving is proof the handle isn't
        # dangling, whatever the duration says. `position_stall_s = 0`
        # switches off the resolver's freshness check, not idle detection,
        # so the movement window then falls back to the default.
        stall_s = self._config.advanced.position_stall_s
        source_alive = source_position_is_fresh(
            self._position_state.moved_at,
            now,
            float(stall_s if stall_s > 0 else AdvancedConfig.position_stall_s),
        )
        result, new_key, new_seen = compute_idle_state(
            track,
            self._idle_track_key,
            self._idle_seen_at,
            int(self._config.advanced.idle_grace_s),
            now,
            effective_duration_ms=effective_dur_ms,
            source_alive=source_alive,
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
            self._drop_old_temp_covers(track)
            if (
                self._config.behavior.notifications
                and not self._notifications_muted
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

        # Settle the track length once per tick — the tray label, the
        # Discord payload and the MPRIS server we publish must all render
        # the same number, and `_duration_for` is the one place that
        # weighs the player's length against the catalog's. The raw
        # catalog value is kept alongside it purely for the RPC log line.
        lengths = self._tick_lengths(track)
        itunes_dur_ms = lengths[1]
        effective_dur_ms, duration_disputed = self._duration_for(track, lengths=lengths)

        # Tray progress label, emitted on every tick while playing. The
        # tray reads a negative position as "hide the line" and a
        # zero/absent duration as "elapsed only" — an unknown total is no
        # reason to drop an elapsed count we do trust.
        if track.status == PlaybackStatus.PLAYING:
            estimated = self._position_tier is PositionTier.ESTIMATED
            if estimated != self._progress_estimated:
                self._progress_estimated = estimated
                self.progressEstimated.emit(estimated)
            if not self._position_known:
                self.progressTick.emit(-1, 0)
            elif effective_dur_ms > 0:
                # Clamp position to duration so the tray doesn't show a
                # nonsensical "2:30 / 0:14 (-0:00)" line during a brief
                # MPRIS preview-clip glitch on a longer song.
                display_pos = min(max(0, track.position_ms), effective_dur_ms)
                self.progressTick.emit(display_pos, effective_dur_ms)
            else:
                self.progressTick.emit(max(0, track.position_ms), 0)

        self._clock.lap("tray")
        # The Status window shows the cover whether or not the history keeps it.
        cover_now = ""
        if track.has_track and self._config.behavior.cover_art:
            cover_now = self._cover_fetcher.get(track.artist, track.title, track.album) or ""
            if not track.album:
                # Browsers rarely send one, and without it Last.fm finds no cover.
                found = self._catalog_album_for(track)
                if found:
                    track = dataclasses.replace(track, album=found)
        if cover_now != self._cover_emitted:
            self._cover_emitted = cover_now
            self.coverChanged.emit(cover_now)
        self._update_rpc(track, effective_dur_ms, itunes_dur_ms)
        self._clock.lap("discord")

        # Last.fm and the history judge "played" from the same length:
        # the iTunes-corrected duration the RPC + tray see, except where
        # the two lengths disagree — see `scrobble_duration_ms`.
        played_dur_ms = scrobble_duration_ms(
            effective_dur_ms, duration_disputed, track.duration_ms, itunes_dur_ms
        )

        # Recently played. Local only, so unlike Last.fm it keeps going
        # with privacy "off" — `history.enabled` is its own switch. Fed
        # before the Scrobbler, so a song the Scrobbler banks this tick
        # is already in the list when the scrobbled mark arrives.
        # The player's own position, which tells a replay — and a restart
        # that carries on — from a new play. Only a reported one: a
        # computed position starts again at zero along with Refrain.
        player_pos_ms = track.position_ms if self._position_tier is PositionTier.REPORTED else None
        heard = self._heard(track)
        self._update_history(
            track,
            played_dur_ms,
            player_pos_ms,
            self._track_restarted,
            heard.album,
            track.position_ms if self._position_known else None,
        )
        self._clock.lap("history")

        # Last.fm scrobbling. Gated on privacy "off" (the global
        # no-external-broadcasting kill switch); the Scrobbler itself is
        # inert until the user enables it and connects an account.
        # Wrapped so a scrobble-side failure can never break the tick.
        with contextlib.suppress(Exception):
            self._scrobbler.update(
                heard,
                played_dur_ms,
                privacy_off=self._config.privacy.mode == "off",
                position_ms=player_pos_ms,
                restarted=self._track_restarted,
            )
        self._clock.lap("scrobble")

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
            self._clock.lap("covers")
            # Publishing a length with no position to go with it leaves
            # Plasma's applet showing a progress bar pinned at 0:00, so
            # an unresolvable position drops the length too and the
            # applet renders the track without a bar.
            self._mpris_server.update(
                track,
                cover_for_mpris,
                effective_dur_ms if self._position_known else 0,
            )
        self._clock.lap("mpris")

        # A status held back by the rate limit goes out even on a tick
        # that brings nothing new.
        self._rpc.pump()
        self._emit_service_states(track)

    def _emit_service_states(self, track: TrackInfo) -> None:
        rpc_connected = self._rpc.is_connected()
        if rpc_connected and not self._last_rpc_connected:
            dev_metrics.mark("discord_connected")
        self._last_rpc_connected = rpc_connected
        privacy = self._config.privacy.mode
        discord = (str(discord_status(self._rpc.state, privacy, track)), self._rpc.detail)
        if discord != self._last_discord_state:
            self._last_discord_state = discord
            self.discordStateChanged.emit(*discord)
        session_invalid, waiting = self._scrobbler.health()
        state, detail = lastfm_status(self._config.lastfm, privacy, session_invalid, waiting)
        lastfm = (str(state), detail)
        if lastfm != self._last_lastfm_state:
            self._last_lastfm_state = lastfm
            self.lastfmStateChanged.emit(*lastfm)

    def _drop_old_temp_covers(self, track: TrackInfo) -> None:
        """The previous song's notification is done with its image.

        A song in the history has its own copy in the cover cache."""
        try:
            if track.has_track and self._config.behavior.cover_art:
                self._cover_fetcher.drop_temp_covers(track.artist, track.title, track.album)
            else:
                self._cover_fetcher.drop_temp_covers()
        except Exception:
            log.exception("Could not delete the temporary cover images")

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
            self._notify_timer = QTimer(self)
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
        cover_present = True
        if self._config.behavior.cover_art:
            cover = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            cover_present = cover is not None
            if not cover_present and self._notify_retry_count < self._NOTIFY_MAX_RETRIES:
                self._notify_retry_count += 1
                assert self._notify_timer is not None
                self._notify_timer.start(self._NOTIFY_RETRY_INTERVAL_MS)
                return

        self._pending_notify_track = None
        self._notify_retry_count = 0
        # Cover-art on but the image never landed within the ~2 s retry
        # window: fire now with the brand fallback, capture the
        # notification id, and start a longer watch — once the cover
        # finishes downloading we re-issue with `--replace-id` so it
        # swaps into the existing bubble (no second popup) instead of
        # the user never seeing the cover at all.
        need_replace_watch = self._config.behavior.cover_art and not cover_present
        nid = self._notify(track, capture_id=need_replace_watch)
        if need_replace_watch and nid is not None:
            self._notify_id = nid
            self._notify_id_fp = track.fingerprint()
            self._start_cover_replace_watch(track)

    # Watch beyond the initial 2 s notify-retry window for a cover that
    # iTunes is slow to resolve. 16 × 500 ms ≈ 8 s of extra patience;
    # past that iTunes almost certainly has no match and the brand-
    # fallback notification stays as it is.
    _COVER_REPLACE_INTERVAL_MS = 500
    _COVER_REPLACE_MAX_ATTEMPTS = 16

    def _start_cover_replace_watch(self, track: TrackInfo) -> None:
        if self._replace_timer is None:
            self._replace_timer = QTimer(self)
            self._replace_timer.setSingleShot(True)
            self._replace_timer.timeout.connect(self._fire_cover_replace)
        self._replace_track = track
        self._replace_attempts = 0
        self._replace_timer.start(self._COVER_REPLACE_INTERVAL_MS)

    def _fire_cover_replace(self) -> None:
        track = self._replace_track
        if track is None:
            return
        # Track moved on — the normal notify path will issue a fresh
        # notification for whatever's playing now; nothing to swap into
        # the old bubble.
        if track.fingerprint() != self._last_track_fp:
            self._replace_track = None
            return
        # The captured id must still belong to this exact track.
        if self._notify_id is None or self._notify_id_fp != track.fingerprint():
            self._replace_track = None
            return
        if self._config.behavior.cover_art:
            cover = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            if cover is not None:
                # Cover landed: re-issue the SAME notification in place.
                log.debug("Cover landed late — replacing notification %d", self._notify_id)
                self._notify(track, replace_id=self._notify_id)
                self._replace_track = None
                return
        self._replace_attempts += 1
        if self._replace_attempts < self._COVER_REPLACE_MAX_ATTEMPTS:
            assert self._replace_timer is not None
            self._replace_timer.start(self._COVER_REPLACE_INTERVAL_MS)
        else:
            self._replace_track = None

    def _update_rpc(
        self,
        track: TrackInfo,
        effective_duration_ms: int,
        itunes_dur_ms: int,
    ) -> None:
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
            self._rpc = DiscordRPC(target_client_id, self._config.discord.all_clients)
            self._rpc_active_client_id = target_client_id
            # Force a fresh start_ts on next compute, since pypresence
            # no longer has any state for the old activity.
            self._rpc_track_key = ""

        if self._config.privacy.mode == "off":
            self._rpc.clear()
            return
        if track.status != PlaybackStatus.PLAYING or not track.title.strip():
            # Connected while idle too, so "ready" and a refused Application
            # ID show before the first song, not only once one plays.
            self._rpc._ensure_connected()
            self._rpc.clear()
            return

        if self._config.privacy.mode == "minimal":
            self._rpc.update(
                details="Listening to music",
                large_image="refrain",
                large_text="Refrain",
            )
            return

        track_key = track.content_key()
        is_new_track = track_key != self._rpc_track_key

        # Hold a new song back for up to 3 polls until its cover is cached, so
        # Discord doesn't flash the Refrain logo before the cover lands. Only
        # while nothing is on the profile yet: leaving the song before showing
        # would be telling Discord's viewers something untrue.
        cover_url: str | None = None
        if self._config.behavior.cover_art:
            cover_url = self._cover_fetcher.get(track.artist, track.title, track.album)
        if self._rpc_cover_wait_key != track_key:
            # A song skipped while it waited must not use up the next one's wait.
            self._rpc_cover_wait_key = track_key
            self._rpc_cover_wait_count = 0
        if (
            is_new_track
            and self._config.behavior.cover_art
            and cover_url is None
            and self._rpc_cover_wait_count < 3
            and self._rpc.state is not RPCState.SHOWING
        ):
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
        if not self._position_known and not is_new_track:
            # No position to follow; its stand-in 0 is no seek.
            new_start_ts, recomputed = self._rpc_start_ts, False
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

        names = (track.album, track.artist, track.title)
        if names != self._album_display[0]:
            self._album_display = (names, _format_album_for_display(*names))
        album_for_display = self._album_display[1]

        # Preview-clip mode for the Discord payload: drop start AND end
        # when the *effective* track length is under 30 s. Using the
        # effective duration (MPRIS overridden by iTunes when they
        # disagree) means a brief MPRIS preview-clip glitch on a
        # full-length song doesn't kill the progress bar — iTunes
        # tells us the real song is 2:11, so we send start/end based
        # on that instead of dropping them just because MPRIS said
        # "14 s" for one poll.
        is_short_track = 0 < effective_duration_ms < 30_000
        # `start`/`end` are what Discord renders the elapsed timer and
        # progress bar from. With no trustworthy position there is no
        # honest pair to send, so they're dropped and Discord shows the
        # track without a timer. An estimate after a restart does go out:
        # Discord can't mark it, but it comes from a save under a minute
        # old and is dropped once it passes the song's end.
        send_timing = not is_short_track and self._position_known

        payload: dict = {
            "details": details,
            "state": state,
            "large_image": large_image,
        }
        # Discord's LISTENING activity type intentionally does
        # NOT render `small_image` — only PLAYING / WATCHING activities
        # show a small-icon overlay. We keep activity_type=LISTENING
        # (from DiscordRPC.update) so the status reads "Listening to
        # Refrain" instead of "Playing Refrain", and accept that the
        # small-icon corner stays empty. The cover_url already
        # carries the visual identity in the large slot.
        if send_timing:
            payload["start"] = self._rpc_start_ts
        # Only emit `large_text` when it adds new info — Discord shows it
        # as a third visible line for LISTENING activity, and an
        # echo of `state` looks broken to viewers.
        if album_for_display and album_for_display.lower() != state.lower():
            payload["large_text"] = album_for_display

        if send_timing and effective_duration_ms > 0:
            payload["end"] = self._rpc_start_ts + (effective_duration_ms // 1000)

        # Prefer the iTunes-resolved song URL (links to the *specific* track)
        # over xesam:url from the browser tab (often the album / playlist page).
        if self._config.behavior.show_buttons:
            song_url = self._cover_fetcher.get_song_url(track.artist, track.title, track.album)
            link = button_url(song_url or (track.url if track.source == "mpris" else ""))
            if link:
                payload["buttons"] = [{"label": "Listen on Apple Music", "url": link}]

        # The timing pair decides Discord's progress bar, and it is
        # assembled from four different sources. Logged whenever it
        # changes — every poll would be twice a second of identical
        # lines, since a stable track deliberately keeps the same pair.
        timing = (payload.get("start"), payload.get("end"), self._position_tier)
        if timing != self._last_rpc_timing:
            log.debug(
                "RPC timing: %s — start=%s end=%s (position tier %s, effective_dur=%dms)",
                details[:40],
                payload.get("start", "—"),
                payload.get("end", "—"),
                self._position_tier.value,
                effective_duration_ms,
            )
            self._last_rpc_timing = timing
        self._rpc.update(**payload)

    def _notify(
        self,
        track: TrackInfo,
        *,
        replace_id: int | None = None,
        capture_id: bool = False,
    ) -> int | None:
        """Fire a desktop notification for ``track``.

        ``replace_id`` updates an existing bubble in place (used to swap
        a late cover into an already-shown brand-fallback notification).
        ``capture_id`` / ``replace_id`` make us read back the
        notification id via ``--print-id`` — that path blocks the worker
        thread on ``notify-send`` (a fast D-Bus round-trip, capped at
        2 s) instead of the fire-and-forget ``Popen``; the common path
        (cover already present, cover-art off) stays non-blocking.
        Returns the notification id when captured, else ``None``.
        """
        # Re-resolve so a notify-send installed after refrain started
        # picks up immediately. shutil.which is cheap and only runs on
        # actual track-change ticks.
        global _NOTIFY_BIN
        if _NOTIFY_BIN is None:
            _NOTIFY_BIN = shutil.which("notify-send")
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

        if not _NOTIFY_BIN:
            # No libnotify binary: the notification service itself is still
            # there on any desktop that shows notifications at all.
            return notify_over_dbus(image_path, track.title, body or "", replace_id=replace_id)

        want_id = capture_id or replace_id is not None
        cmd = build_notify_argv(
            _NOTIFY_BIN,
            image_path,
            track.title,
            body or "",
            replace_id=replace_id,
            print_id=want_id,
        )

        if not want_id:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                log.debug("notify-send failed: %s", e)
            return None
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except Exception as e:
            log.debug("notify-send (capture) failed: %s", e)
            return None
        return parse_notify_id(proc.stdout)


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
        # A blocking call into a thread that has already quit never returns,
        # and uninstall stops the daemon before the normal exit does.
        if not self.thread.isRunning():
            return
        # Run cleanup on the worker thread synchronously (BlockingQueued) so
        # the timer is stopped and Discord status is cleared *before* we tear
        # down the thread itself.
        QMetaObject.invokeMethod(self.worker, "cleanup", Qt.ConnectionType.BlockingQueuedConnection)
        self.thread.quit()
        self.thread.wait(2500)
