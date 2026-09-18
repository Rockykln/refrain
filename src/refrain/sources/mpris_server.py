"""Refrain as an MPRIS player, so Plasma's media controls show and control its track.

dbus-python rather than QDBus: ``service.Object`` needs no hand-kept adaptor XML."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop, threads_init

from refrain.paths import desktop_entry
from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

# `set_as_default=True` wires dbus-python's signal/method dispatch into
# GLib. Without it, `dbus.service.Object` registers on the bus but
# Plasma's `PlayPause` call never reaches our handler (the dispatcher
# would have nothing to pump). Idempotent; the second call is a no-op.
_DBUS_LOOP_INITIALIZED = False
_DBUS_LOOP_INIT_FAILED = False
_GLIB_THREAD: threading.Thread | None = None


def _ensure_dbus_glib_loop() -> bool:
    """Attach dbus-python's dispatch to the GLib main loop.

    Must run before the process opens its first session bus.
    ``dbus.SessionBus()`` is a process-wide singleton: whoever creates it
    first decides whether that connection carries a main loop, and every
    later caller gets the same object back. Register too late and
    ``dbus.service.Object`` can never export ("D-Bus connections must be
    attached to a main loop"), which silently costs us Plasma's media
    controls.

    This only wires up the dispatch; it deliberately does *not* spin a
    GLib loop of its own — see ``ensure_dbus_dispatch_pump``. Returns
    True on success, False if PyGObject isn't installed (in which case
    the MPRIS server falls back to polling-only mode and Plasma controls
    won't reach us, but the rest of refrain keeps working).
    """
    global _DBUS_LOOP_INITIALIZED, _DBUS_LOOP_INIT_FAILED
    if _DBUS_LOOP_INITIALIZED:
        return True
    if _DBUS_LOOP_INIT_FAILED:
        # Already warned once; subsequent callers (the eager init from
        # app.py, then the lazy one from MPRISServer.start) get a silent
        # False instead of a duplicate WARNING in the log.
        return False
    try:
        import gi.repository.GLib  # noqa: F401  — availability probe only
    except ImportError as e:
        _DBUS_LOOP_INIT_FAILED = True
        log.warning(
            "MPRIS server: PyGObject not installed (%s) — Plasma media "
            "controls won't work. Install: python-gobject (Arch / "
            "CachyOS / Manjaro), python3-gi (Debian / Ubuntu / Mint), "
            "python3-gobject (Fedora / RHEL / openSUSE).",
            e,
        )
        return False
    DBusGMainLoop(set_as_default=True)
    # dbus-glib's own rule for any program with more than one thread. The
    # daemon thread doesn't talk over this connection (its sources
    # keep private ones, the server defers to _on_bus_thread), so this
    # is the second line of defence, not the first.
    threads_init()
    _DBUS_LOOP_INITIALIZED = True
    log.debug("MPRIS server: dbus-python dispatch wired into GLib")
    return True


def _qt_pumps_glib_context() -> bool:
    """Whether Qt's event dispatcher already runs the default GMainContext.

    On Linux Qt normally uses ``QEventDispatcherGlib``, which pumps the
    default context for us — that is exactly what dbus-python needs, so
    no loop of our own is required. Builds with glib disabled (or
    ``QT_NO_GLIB=1``) use ``QEventDispatcherUNIX`` instead and leave the
    context unpumped.
    """
    try:
        from PySide6.QtCore import QAbstractEventDispatcher, QCoreApplication

        if QCoreApplication.instance() is None:
            return False
        dispatcher = QAbstractEventDispatcher.instance()
        if dispatcher is None:
            return False
        # "QPAEventDispatcherGlib" / "QEventDispatcherGlib" vs
        # "QEventDispatcherUNIX". className() is a str.
        return "Glib" in dispatcher.metaObject().className()
    except Exception as e:  # pragma: no cover - defensive; Qt should be importable
        # Don't swallow silently: a wrong answer here costs Plasma's
        # media controls or spins a loop we don't need.
        log.debug("Could not classify Qt's event dispatcher (%s); assuming non-glib", e)
        return False


def ensure_dbus_dispatch_pump() -> None:
    """Guarantee something actually pumps dbus-python's dispatch.

    Call once ``QApplication`` exists. Normally a no-op: Qt's glib event
    dispatcher already runs the default GMainContext on the main thread.
    Only when Qt is *not* glib-backed do we fall back to our own GLib
    loop in a daemon thread.

    Starting that thread unconditionally — and worse, before
    ``QApplication`` is constructed — makes the worker acquire the
    default context first, so Qt's own dispatcher then trips
    ``g_main_context_push_thread_default: assertion 'acquired_context'
    failed`` and the process dies with a segfault once timers start
    crossing threads.
    """
    global _GLIB_THREAD
    if not _DBUS_LOOP_INITIALIZED or _GLIB_THREAD is not None:
        return
    if _qt_pumps_glib_context():
        log.debug("MPRIS server: Qt's glib dispatcher pumps dbus; no extra loop")
        return
    from gi.repository import GLib

    _GLIB_THREAD = threading.Thread(
        target=GLib.MainLoop().run,
        name="refrain-glib-loop",
        daemon=True,
    )
    _GLIB_THREAD.start()
    log.debug("MPRIS server: Qt is not glib-backed; started our own GLib loop")


def _bus_thread() -> threading.Thread:
    """The thread that dispatches the shared session-bus connection."""
    return _GLIB_THREAD if _GLIB_THREAD is not None else threading.main_thread()


def _on_bus_thread(fn: Callable[..., object], *args) -> None:
    """Run ``fn`` on the thread that dispatches the shared connection.

    The exported MPRIS object lives on the process-wide session bus, and
    dbus-glib dispatches that connection without locking its own
    bookkeeping. The daemon thread calling in directly — to publish, or
    to announce a new track — puts two threads on one connection, which
    corrupts the heap ("malloc(): unaligned tcache chunk detected").
    ``GLib.idle_add`` is thread-safe and runs ``fn`` on that thread's
    next loop pass instead.
    """
    if threading.current_thread() is _bus_thread():
        fn(*args)
        return
    from gi.repository import GLib

    def _once() -> bool:
        try:
            fn(*args)
        except Exception:
            log.exception("MPRIS server: deferred call failed")
        return False  # GLib.SOURCE_REMOVE — run once

    GLib.idle_add(_once)


_BUS_NAME = "org.mpris.MediaPlayer2.refrain"
_OBJECT_PATH = "/org/mpris/MediaPlayer2"
_ROOT_IFACE = "org.mpris.MediaPlayer2"
_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"


def _status_str(status: PlaybackStatus) -> str:
    """Map our internal enum to MPRIS' string vocabulary."""
    if status == PlaybackStatus.PLAYING:
        return "Playing"
    if status == PlaybackStatus.PAUSED:
        return "Paused"
    return "Stopped"


def _track_id(track: TrackInfo) -> dbus.ObjectPath:
    """MPRIS requires `mpris:trackid` to be a non-empty object path. We
    derive a stable-per-track value from the title so KDE can detect a
    track change. Anything but ASCII letters and digits becomes `_`: an
    object path allows nothing else, and an "ö" in the title failed every
    read of the metadata.
    """
    safe = "".join(c if c.isascii() and c.isalnum() else "_" for c in (track.title or "unknown"))
    return dbus.ObjectPath(f"/refrain/track/{safe or 'unknown'}")


def _build_metadata(
    track: TrackInfo,
    cover_url: str | None,
    effective_duration_ms: int | None = None,
) -> dbus.Dictionary:
    """Build the MPRIS Metadata dict. Empty fields are dropped so KDE's
    panel doesn't render placeholder text.

    ``effective_duration_ms`` overrides ``track.duration_ms`` for the
    published ``mpris:length`` so Plasma's panel widget shows the
    iTunes-corrected duration instead of whatever Apple Music's
    browser MPRIS happened to report this poll. ``None`` falls back
    to ``track.duration_ms``.
    """
    md: dict = {"mpris:trackid": _track_id(track)}
    duration_ms = effective_duration_ms if effective_duration_ms is not None else track.duration_ms
    if duration_ms > 0:
        # MPRIS spec: mpris:length is microseconds, not milliseconds.
        md["mpris:length"] = dbus.Int64(duration_ms * 1000)
    if track.title:
        md["xesam:title"] = dbus.String(track.title)
    if track.artist:
        md["xesam:artist"] = dbus.Array([dbus.String(track.artist)], signature="s")
    if track.album:
        md["xesam:album"] = dbus.String(track.album)
    if track.url:
        md["xesam:url"] = dbus.String(track.url)
    if cover_url:
        md["mpris:artUrl"] = dbus.String(cover_url)
    return dbus.Dictionary(md, signature="sv")


class MPRISServer(dbus.service.Object):
    """Publishes Refrain on the session bus as an MPRIS MediaPlayer.

    Only the methods KDE Plasma's panel actually calls are wired up
    (PlayPause / Play / Pause / Next / Previous + the Metadata /
    PlaybackStatus / Position properties on the Player interface, plus
    the bare-minimum root interface). Volume, Seek, OpenUri and friends
    are stubs that return sensible defaults — implementing them would
    require source-side support we don't have on the controllers we
    forward to.

    Failing to start is non-fatal; Refrain works as a Discord client without it.
    """

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
    ) -> None:
        # Constructed lazily in `start()` — bus_name registration may fail.
        self._on_play_pause = on_play_pause
        self._on_next = on_next
        self._on_previous = on_previous
        self._bus_name: dbus.service.BusName | None = None
        self._track: TrackInfo = TrackInfo.empty()
        self._cover_url: str | None = None
        self._effective_duration_ms: int | None = None
        # dbus.service.Object.__init__ is deferred to start() so a bus
        # connect failure doesn't propagate from the daemon constructor.

    # ----------------------------------------------------------- lifecycle

    def start(self) -> bool:
        """Register on the bus. Returns True on success — or, called from
        another thread (the daemon's), once registering is scheduled on
        the bus thread; see ``_on_bus_thread``.

        Safe to call again after a failed start to retry."""
        if self._bus_name is not None:
            return True
        if not _ensure_dbus_glib_loop():
            return False
        if threading.current_thread() is not _bus_thread():
            _on_bus_thread(self._register)
            return True
        return self._register()

    def _register(self) -> bool:
        if self._bus_name is not None:
            return True
        try:
            bus = dbus.SessionBus()
            # `do_not_queue=True` so two refrain instances can't end up
            # both trying to own the well-known name and silently
            # serialising on each other.
            self._bus_name = dbus.service.BusName(_BUS_NAME, bus, do_not_queue=True)
            dbus.service.Object.__init__(self, self._bus_name, _OBJECT_PATH)
            log.info("MPRIS server published as %s", _BUS_NAME)
            return True
        except Exception as e:
            log.warning("MPRIS server start failed: %s", e)
            self._bus_name = None
            return False

    def stop(self) -> None:
        # Runs right here, even from the daemon's cleanup: the main thread
        # is blocked waiting on that cleanup, so nothing else dispatches
        # the connection meanwhile — and an idle callback would never run,
        # the event loop having already ended.
        if self._bus_name is None:
            return
        try:
            self.remove_from_connection()
        except Exception as e:
            log.debug("MPRIS server remove_from_connection: %s", e)
        self._bus_name = None
        log.info("MPRIS server unpublished")

    # ---------------------------------------------------- updates from daemon

    def update(
        self,
        track: TrackInfo,
        cover_url: str | None,
        effective_duration_ms: int | None = None,
    ) -> None:
        """Push the current track + cover URL into the published Metadata
        and PlaybackStatus properties. PropertiesChanged is emitted so
        Plasma's media-controls applet refreshes immediately.

        ``effective_duration_ms`` is the iTunes-corrected song length
        (when MPRIS lies — see ``timing.pick_effective_duration_ms``).
        Forwarded to ``_build_metadata`` so Plasma's panel widget
        shows the correct duration in the same situation Discord
        does.

        Called on the daemon thread; the state change and the signal both
        happen on the bus thread (``_on_bus_thread``), which is also where
        Plasma's property reads are answered from that state.
        """
        if self._bus_name is None:
            return
        _on_bus_thread(self._apply, track, cover_url, effective_duration_ms)

    def _apply(
        self,
        track: TrackInfo,
        cover_url: str | None,
        effective_duration_ms: int | None,
    ) -> None:
        if self._bus_name is None:
            return
        prev_track = self._track
        prev_cover = self._cover_url
        prev_duration = self._effective_duration_ms
        self._track = track
        self._cover_url = cover_url
        self._effective_duration_ms = effective_duration_ms
        # The length often arrives a poll or two after the title.
        if (
            track.fingerprint() != prev_track.fingerprint()
            or cover_url != prev_cover
            or track.status != prev_track.status
            or effective_duration_ms != prev_duration
        ):
            with self._safe("emit PropertiesChanged"):
                self.PropertiesChanged(
                    _PLAYER_IFACE,
                    {
                        "Metadata": _build_metadata(track, cover_url, effective_duration_ms),
                        "PlaybackStatus": _status_str(track.status),
                    },
                    [],
                )

    # ---------------------------------------------------- helpers

    class _SafeCtx:
        def __init__(self, label: str) -> None:
            self.label = label

        def __enter__(self) -> MPRISServer._SafeCtx:
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            if exc is not None:
                log.debug("MPRIS server %s failed: %s", self.label, exc)
            return True  # swallow

    def _safe(self, label: str) -> MPRISServer._SafeCtx:
        return MPRISServer._SafeCtx(label)

    # ---------------------------------------------------- root interface

    @dbus.service.method(_ROOT_IFACE, in_signature="", out_signature="")
    def Raise(self) -> None:
        # No window to raise — refrain lives in the tray.
        return

    @dbus.service.method(_ROOT_IFACE, in_signature="", out_signature="")
    def Quit(self) -> None:
        # Quitting via MPRIS is a footgun; KDE doesn't surface this in
        # the panel UI. CanQuit=False below also tells clients to not
        # offer it.
        return

    # ---------------------------------------------------- player methods

    @dbus.service.method(_PLAYER_IFACE, in_signature="", out_signature="")
    def PlayPause(self) -> None:
        with self._safe("PlayPause"):
            self._on_play_pause()

    # The sources only have a toggle, so Play, Pause and Stop each toggle
    # only when that gets them where they ask to go — judged by the state
    # this server last published, the same one Plasma is looking at. A
    # blind toggle would turn Plasma's "Stop" into "start playing" whenever
    # the music is already paused.

    @dbus.service.method(_PLAYER_IFACE, in_signature="", out_signature="")
    def Play(self) -> None:
        with self._safe("Play"):
            if self._track.status != PlaybackStatus.PLAYING:
                self._on_play_pause()

    @dbus.service.method(_PLAYER_IFACE, in_signature="", out_signature="")
    def Pause(self) -> None:
        with self._safe("Pause"):
            if self._track.status == PlaybackStatus.PLAYING:
                self._on_play_pause()

    @dbus.service.method(_PLAYER_IFACE, in_signature="", out_signature="")
    def Stop(self) -> None:
        # No real stop in the sources; pausing is the nearest thing, and
        # Plasma offers "Stop" for every controllable player regardless.
        with self._safe("Stop"):
            if self._track.status == PlaybackStatus.PLAYING:
                self._on_play_pause()

    @dbus.service.method(_PLAYER_IFACE, in_signature="", out_signature="")
    def Next(self) -> None:
        with self._safe("Next"):
            self._on_next()

    @dbus.service.method(_PLAYER_IFACE, in_signature="", out_signature="")
    def Previous(self) -> None:
        with self._safe("Previous"):
            self._on_previous()

    @dbus.service.method(_PLAYER_IFACE, in_signature="x", out_signature="")
    def Seek(self, _offset: int) -> None:  # noqa: N802
        # Sources don't surface seek; ignored. Spec says clients should
        # check CanSeek before calling, which we expose as False.
        return

    @dbus.service.method(_PLAYER_IFACE, in_signature="ox", out_signature="")
    def SetPosition(self, _track_id: object, _position: int) -> None:  # noqa: N802
        return

    @dbus.service.method(_PLAYER_IFACE, in_signature="s", out_signature="")
    def OpenUri(self, _uri: str) -> None:  # noqa: N802
        return

    # ---------------------------------------------------- properties (Get/Set/All)

    @dbus.service.method(_PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface: str, prop: str) -> object:
        all_props = self.GetAll(interface)
        if prop not in all_props:
            raise dbus.exceptions.DBusException(
                f"No such property {prop} on {interface}",
                name="org.freedesktop.DBus.Error.UnknownProperty",
            )
        return all_props[prop]

    @dbus.service.method(_PROPS_IFACE, in_signature="ssv", out_signature="")
    def Set(self, _interface: str, _prop: str, _value: object) -> None:
        # Nothing settable for now. Plasma tries to set Volume etc.
        return

    @dbus.service.method(_PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        if interface == _ROOT_IFACE:
            return dbus.Dictionary(
                {
                    "CanQuit": dbus.Boolean(False),
                    "CanRaise": dbus.Boolean(False),
                    "HasTrackList": dbus.Boolean(False),
                    "Identity": dbus.String("Refrain"),
                    "DesktopEntry": dbus.String(desktop_entry()),
                    "SupportedUriSchemes": dbus.Array([], signature="s"),
                    "SupportedMimeTypes": dbus.Array([], signature="s"),
                },
                signature="sv",
            )
        if interface == _PLAYER_IFACE:
            return dbus.Dictionary(
                {
                    "PlaybackStatus": dbus.String(_status_str(self._track.status)),
                    "LoopStatus": dbus.String("None"),
                    "Rate": dbus.Double(1.0),
                    "Shuffle": dbus.Boolean(False),
                    "Metadata": _build_metadata(
                        self._track, self._cover_url, self._effective_duration_ms
                    ),
                    "Volume": dbus.Double(1.0),
                    "Position": dbus.Int64(self._track.position_ms * 1000),
                    "MinimumRate": dbus.Double(1.0),
                    "MaximumRate": dbus.Double(1.0),
                    "CanGoNext": dbus.Boolean(True),
                    "CanGoPrevious": dbus.Boolean(True),
                    "CanPlay": dbus.Boolean(True),
                    "CanPause": dbus.Boolean(True),
                    "CanSeek": dbus.Boolean(False),
                    "CanControl": dbus.Boolean(True),
                },
                signature="sv",
            )
        return dbus.Dictionary({}, signature="sv")

    # ---------------------------------------------------- signals

    @dbus.service.signal(_PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(  # noqa: N802
        self,
        _interface: str,
        _changed: dict,
        _invalidated: list,
    ) -> None:
        # The body is auto-emitted by dbus-python — this is just the
        # signal declaration so clients can subscribe.
        pass
