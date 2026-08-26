"""Background daemon: polls sources, drives Discord + tray + notifications.

The worker lives on its own QThread so the GUI is never blocked. Polling is
driven by a QTimer running inside the worker's event loop — crucially, NOT
a `while/sleep` loop, which would block the event loop and prevent
cross-thread slot invocations (Play/Pause/Next/Previous from the tray) from
being delivered.
"""

from __future__ import annotations

import contextlib
import dataclasses
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
from refrain.scrobble import Scrobbler
from refrain.sources.base import PlaybackStatus, TrackInfo
from refrain.sources.bluetooth import BluetoothSource
from refrain.sources.mpris import MPRISSource
from refrain.sources.mpris_server import MPRISServer
from refrain.timing import (
    PositionState,
    PositionTier,
    compute_rpc_start_ts,
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


def scrobble_duration_ms(
    effective_ms: int, disputed: bool, reported_length_ms: int, catalog_ms: int
) -> int:
    """The length to hand Last.fm, which is not always the one we display.

    When the source and the catalog disagree about how long a track is,
    Refrain shows no total at all: a confident wrong number is worse
    than an honest blank. Last.fm can't work that way — its rules are
    written in terms of length (a 30-second floor, then half the track
    or four minutes, whichever comes first), so withholding it doesn't
    mean "we're not sure", it means the play never counts. Users lost
    scrobbles to a disagreement they never saw.

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
    exactly the way the disputed zero used to.
    """
    if not disputed:
        return effective_ms
    usable = [ms for ms in (reported_length_ms, catalog_ms) if ms >= 30_000]
    if usable:
        return min(usable)
    # Neither candidate clears the floor, so the choice cannot rescue the
    # scrobble and the track may genuinely be that short. Fall back to the
    # ordinary pick rather than inventing a length.
    return pick_effective_duration_ms(reported_length_ms, catalog_ms)


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
    wrong duration from clearing a track mid-play: a catalog search that
    returned 58 s for a 2:45 song used to hand idle detection an
    88-second deadline, and the status vanished a minute into the song
    while it was audibly still playing.

    Logs the detection exactly once per dangling-track instance: the
    returned ``new_key`` is prefixed with a sentinel so subsequent
    polls of the same stuck track skip the log line. Without that
    suppression, every poll tick re-logged the idle state and drowned
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
    PLAYING / PAUSED state. Among candidates, an actively **PLAYING**
    source always outranks a merely paused / loaded one.

    Why this matters: with a static "MPRIS before Bluetooth" order, a
    stale *paused* Apple Music tab in the browser (``has_track=True``,
    ``PAUSED``) permanently masked music actively playing over
    Bluetooth headphones — and idle detection only fires on PLAYING,
    so the paused tab never got cleared either. Ranking PLAYING first
    fixes both.

    When neither source is playing (both paused / loaded), MPRIS keeps
    priority so the active source doesn't flip-flop between two idle
    sources every poll — ``max`` returns the first maximal element, and
    MPRIS is inserted first.

    ``mpris`` / ``bluetooth`` are the per-source reads, or ``None`` when
    that source is disabled. Returns ``(track, source_name)``;
    ``(TrackInfo.empty(), "none")`` when nothing qualifies.
    """

    def _is_candidate(t: TrackInfo | None) -> bool:
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
    argv.extend([title, body or ""])
    return argv


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
        self._rpc = DiscordRPC(config.discord.client_id, config.discord.all_clients)
        self._rpc_active_client_id: str = config.discord.client_id
        self._cover_fetcher = CoverFetcher(max_cached_covers=config.advanced.cover_cache_size)
        # Last.fm scrobbling — opt-in, alongside (never replacing) the
        # Discord RPC. Constructed always; inert until the user enables
        # it + connects an account. All network work runs on its own
        # worker executor so the poll tick never blocks.
        self._scrobbler = Scrobbler(config.lastfm)
        self._timer: QTimer | None = None
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
        self._last_rpc_timing: tuple | None = None
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
        # Pure-polling design (default 500 ms; user-configurable via
        # advanced.poll_interval_ms, floored at 250 ms). A signal-driven
        # path via QDBusConnection was prototyped but PySide6's
        # connect-signature handling proved too brittle to ship.
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
        if self._replace_timer is not None:
            self._replace_timer.stop()
            self._replace_timer = None
        with contextlib.suppress(Exception):
            self._mpris_server.stop()
        with contextlib.suppress(Exception):
            self._rpc.clear()
        with contextlib.suppress(Exception):
            self._rpc.close()
        with contextlib.suppress(Exception):
            # Banks a qualifying in-progress track to the on-disk queue
            # so quitting mid-song still scrobbles it next launch.
            self._scrobbler.shutdown()
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
        if config.advanced.cover_cache_size != old_cover_cache:
            # Re-prune to the new cap immediately so the user sees their
            # quota take effect without waiting for the next refrain
            # restart. Existing in-memory cache stays intact.
            from refrain.cover_fetcher import _prune_cover_cache

            _prune_cover_cache(config.advanced.cover_cache_size)
        # Last.fm: pick up enable/disable, new credentials, or a freshly
        # connected session without a process restart (unlike the
        # Discord client_id, the Scrobbler rebinds cleanly in place).
        with contextlib.suppress(Exception):
            self._scrobbler.reconfigure(config.lastfm)

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
        track = self._resolve_position(track)
        return self._apply_idle_detection(track)

    def _poll_sources(self) -> TrackInfo:
        mpris_t = self._mpris.read() if self._config.sources.mpris_enabled else None
        # Short-circuit: an actively-playing MPRIS source already
        # outranks anything Bluetooth could report (nothing beats
        # PLAYING — see `select_source_track`), so skip the extra
        # system-bus round-trip in the common "music playing in the
        # browser" case. We only pay for the Bluetooth read when MPRIS
        # is paused / loaded / absent — exactly the case where a stale
        # paused tab used to mask an actively-playing BT source.
        if mpris_t is not None and mpris_t.status == PlaybackStatus.PLAYING:
            bt_t = None
        else:
            bt_t = self._bluetooth.read() if self._config.sources.bluetooth_enabled else None
        track, source = select_source_track(mpris_t, bt_t)
        if source != "none":
            self._active_source = source
        return track

    def _duration_for(self, track: TrackInfo) -> tuple[int, bool]:
        """The track length every consumer should use, and whether it's disputed.

        Two parties can answer, and either can be wrong. The player's
        `mpris:length` describes the element actually playing, so it
        normally wins; the iTunes duration is a catalog search that can
        match the wrong record. But a stream-relative player's length
        describes its buffer rather than the song, and then only the
        catalog knows.

        Which case applies is what `resolve_position` establishes by
        watching the source across a track change. Until it has, a
        disagreement is genuinely undecidable — at startup mid-track, a
        position past the catalog length fits "wrong catalog match" and
        "this is a stream" equally well — and saying so is more use than
        picking one. Centralised here so position resolution, idle
        detection and `_dispatch` can't drift apart on the answer.

        Returns ``(effective_ms, disputed)``.
        """
        itunes_dur_ms = (
            self._cover_fetcher.get_duration_ms(track.artist, track.title, track.album)
            if self._config.behavior.cover_art
            else 0
        )
        mpris_dur_ms = track.duration_ms
        if self._position_state.cumulative:
            # Measured live: this length grew by 135 s over 144 s of
            # playback on one unchanging track. Better no total at all
            # than a 6:52 one on a 2:24 song.
            return itunes_dur_ms, False
        if (
            mpris_dur_ms > 0
            and itunes_dur_ms > 0
            and not self._position_state.track_relative
            and abs(mpris_dur_ms - itunes_dur_ms) > max(5_000, itunes_dur_ms * 0.15)
        ):
            return 0, True
        return pick_effective_duration_ms(mpris_dur_ms, itunes_dur_ms), False

    def _effective_duration_ms(self, track: TrackInfo) -> int:
        return self._duration_for(track)[0]

    def _resolve_position(self, track: TrackInfo) -> TrackInfo:
        """Decide what this track's position actually is — or that we don't know.

        Runs first in the poll pipeline so every stage below it, and
        every consumer in `_dispatch`, works from the same answer.
        Sitting upstream of idle detection also keeps the clock's anchor
        alive across an idle clear, since the source itself carries on
        reporting the track.
        """
        track_key = (
            f"{track.source}|{track.title}|{track.artist}|{track.album}" if track.has_track else ""
        )
        duration_ms, disputed = self._duration_for(track)
        position_ms, tier, new_state = resolve_position(
            self._position_state,
            track_key,
            track.position_ms,
            duration_ms,
            track.status == PlaybackStatus.PLAYING,
            time.monotonic(),
            reported_length_ms=track.duration_ms,
            duration_disputed=disputed,
            stall_after_s=float(self._config.advanced.position_stall_s),
        )
        self._position_state = new_state
        self._position_known = position_ms is not None
        # Log the tier transitions only — a degraded source stays
        # degraded for hundreds of polls, and per-tick logging would
        # drown the live log the way un-suppressed idle detection did.
        if tier != self._position_tier and track.has_track:
            log.info(
                "Position: %s → %s%s",
                self._position_tier.value,
                tier.value,
                {
                    PositionTier.REPORTED: " (source's own value)",
                    PositionTier.COMPUTED: " (source unusable; counting from the track start)",
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
        effective_dur_ms = self._effective_duration_ms(track)
        now = time.monotonic()
        # The source's position moving is proof the handle isn't
        # dangling, whatever the duration says — the same freshness the
        # position resolver goes by, asked through the same function so
        # the two can't drift apart on what `position_stall_s = 0` means.
        source_alive = source_position_is_fresh(
            self._position_state.moved_at,
            now,
            float(self._config.advanced.position_stall_s),
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

        # Settle the track length once per tick — the tray label, the
        # Discord payload and the MPRIS server we publish must all render
        # the same number, and `_duration_for` is the one place that
        # weighs the player's length against the catalog's. The raw
        # catalog value is kept alongside it purely for the RPC log line.
        itunes_dur_ms = (
            self._cover_fetcher.get_duration_ms(track.artist, track.title, track.album)
            if self._config.behavior.cover_art
            else 0
        )
        effective_dur_ms, duration_disputed = self._duration_for(track)

        # Tray progress label, emitted on every tick while playing. The
        # tray reads a negative position as "hide the line" and a
        # zero/absent duration as "elapsed only" — an unknown total is no
        # reason to drop an elapsed count we do trust.
        if track.status == PlaybackStatus.PLAYING:
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

        self._update_rpc(track, effective_dur_ms, itunes_dur_ms)

        # Last.fm scrobbling. Fed the same iTunes-corrected duration the
        # RPC + tray see, except where the two lengths disagree — see
        # `scrobble_duration_ms`. Gated on privacy "off" (the global
        # no-external-broadcasting kill switch); the Scrobbler itself is
        # inert until the user enables it and connects an account.
        # Wrapped so a scrobble-side failure can never break the tick.
        with contextlib.suppress(Exception):
            self._scrobbler.update(
                track,
                scrobble_duration_ms(
                    effective_dur_ms, duration_disputed, track.duration_ms, itunes_dur_ms
                ),
                privacy_off=self._config.privacy.mode == "off",
            )

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
            # Publishing a length with no position to go with it leaves
            # Plasma's applet showing a progress bar pinned at 0:00, so
            # an unresolvable position drops the length too and the
            # applet renders the track without a bar.
            self._mpris_server.update(
                track,
                cover_for_mpris,
                effective_dur_ms if self._position_known else 0,
            )

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
        cover_present = True
        if self._config.behavior.cover_art:
            cover = self._cover_fetcher.get_local_path(track.artist, track.title, track.album)
            cover_present = cover is not None
            if not cover_present and self._notify_retry_count < self._NOTIFY_MAX_RETRIES:
                self._notify_retry_count += 1
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
            self._replace_timer = QTimer()
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
            self._replace_timer.start(self._COVER_REPLACE_INTERVAL_MS)
        else:
            self._replace_track = None

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
            self._rpc = DiscordRPC(target_client_id, self._config.discord.all_clients)
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
            and self._rpc_cover_wait_count < 3
        ):
            # Defer up to 3 polls (~1.5 s at 500 ms) so iTunes search
            # has time to land before we push the activity. Without
            # this, Discord briefly shows the `refrain` brand fallback
            # in the large-image slot for the first ~500 ms-1 s of a
            # new track, then flips to the cover when iTunes returns
            # — visible flicker on every track change. The 3-poll cap
            # bounds the worst case: a song with no iTunes match
            # still updates after 1.5 s with the brand fallback.
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
        # `start`/`end` are what Discord renders the elapsed timer and
        # progress bar from. With no trustworthy position there is no
        # honest pair to send, so they're dropped and Discord shows the
        # track without a timer.
        send_timing = not is_short_track and self._position_known

        payload: dict = {
            "details": details[:128],
            "state": state[:128],
            "large_image": large_image,
        }
        # Note: Discord's LISTENING activity type intentionally does
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
            payload["large_text"] = album_for_display[:128]

        if send_timing and effective_duration_ms > 0:
            payload["end"] = self._rpc_start_ts + (effective_duration_ms // 1000)

        # Prefer the iTunes-resolved song URL (links to the *specific* track)
        # over xesam:url from the browser tab (often the album / playlist page).
        if self._config.behavior.show_buttons:
            song_url = self._cover_fetcher.get_song_url(track.artist, track.title, track.album)
            link = song_url or (track.url if track.source == "mpris" else "")
            if link.startswith("https://"):
                payload["buttons"] = [{"label": "Listen on Apple Music", "url": link}]

        # The timing pair is what both the "Discord shows no progress bar"
        # and the "bar is already in the past" reports come down to, and
        # it is assembled from four different sources. Logged whenever it
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
        if not _NOTIFY_BIN:
            return None
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
        # Run cleanup on the worker thread synchronously (BlockingQueued) so
        # the timer is stopped and Discord status is cleared *before* we tear
        # down the thread itself.
        QMetaObject.invokeMethod(self.worker, "cleanup", Qt.ConnectionType.BlockingQueuedConnection)
        self.thread.quit()
        self.thread.wait(2500)
