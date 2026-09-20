"""MPRIS source for Apple Music playing in a browser tab.

The highest-scoring browser player on music.apple.com wins; its bus name is kept for controls."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import re
import time

import dbus
import dbus.mainloop

from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

APPLE_MUSIC_HOSTS = ("music.apple.com",)
_APPLE_DEEPLINK_SCHEMES = ("itunes://", "itmss://", "itms://", "music://")
BROWSER_HINTS = (
    # Firefox family
    "firefox",
    "zen",
    "librewolf",
    "floorp",
    "waterfox",
    "mullvad-browser",
    "tor-browser",
    # Chromium family
    "chromium",
    "chrome",
    "brave",
    "edge",
    "vivaldi",
    "opera",
    "ungoogled-chromium",
    # Per-DE bridge
    "plasma-browser-integration",
)

_ROOT_IFACE = "org.mpris.MediaPlayer2"
_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_HUNG_ERRORS = ("NoReply", "Timeout")
_GONE_ERRORS = ("ServiceUnknown", "NameHasNoOwner", "Disconnected")


def _safe_str(v) -> str:
    return "" if v is None else str(v)


def _to_str_list(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    try:
        return [str(x) for x in v]
    except Exception:
        return []


def _looks_apple_music(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(h in u for h in APPLE_MUSIC_HOSTS)


def _is_apple_music_page_title(title: str) -> bool:
    """Is this a page title of Apple Music's own, like "Afterimage – Album von
    Kite Theory – Apple Music" or "Apple Music – Webplayer"?

    Merely naming it is not enough: another tab can play a video called
    "Apple Music review".
    """
    # split() folds every kind of space: the page title spells it
    # "Apple\xa0Music", with a no-break space. Apple also puts a
    # left-to-right mark in front.
    t = " ".join(title.replace("\u200e", "").casefold().split())
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    return t == "apple music" or t.endswith("- apple music") or t.startswith("apple music -")


def _looks_browser(name: str, identity: str, desktop_entry: str, hints: list[str]) -> bool:
    hay = f"{name} {identity} {desktop_entry}".lower()
    return any(h in hay for h in hints)


def _normalize_apple_url(url: str) -> str:
    if not url:
        return ""
    low = url.lower()
    for scheme in _APPLE_DEEPLINK_SCHEMES:
        if low.startswith(scheme):
            return "https://" + url[len(scheme) :]
    return url


def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s*-\s*.*", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s.strip()


def _clean_album(album: str, title: str) -> str:
    if not album:
        return ""
    cleaned = re.sub(r"\(.*?\)", "", album)
    cleaned = re.sub(r"\s*-\s*.*", "", cleaned)
    cleaned = re.sub(
        r"\b(single|ep|remastered|version|deluxe|edition)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Strip ASCII hyphen, en-dash, em-dash — three different glyphs the
    # iTunes catalog likes to use interchangeably in album titles.
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")  # noqa: RUF001
    # Drop the album only when, *after* cleanup, it's the same as the
    # track title — the iTunes catalog often duplicates the title in
    # the album field for singles ("Salt Flats" / "Salt Flats (Single)").
    # Not a substring match: Album="Sun" must survive
    # Title="Salt Flats (Extended Mix)".
    if _normalize(cleaned) and _normalize(title) and _normalize(cleaned) == _normalize(title):
        return ""
    return cleaned


class MPRISSource:
    """Reads — and controls — the currently active Apple Music browser session.

    ``browser_hints`` is the list of substrings to look for in MPRIS bus
    names / desktop entries to identify a browser. Defaults to the major
    Linux browsers; can be overridden via the Sources tab in settings.
    """

    def __init__(self, browser_hints: list[str] | None = None) -> None:
        self._last_player_name: str | None = None
        self._control_fallback_names: list[str] = []
        # The browser's own MPRIS entries for an Apple Music tab — no URL,
        # but the page title ends in "Apple Music". Unlike plasma-browser-
        # integration they describe the audio; see `read` and `play_pause`.
        self._native_apple_names: list[str] = []
        # The state that tab overruled plasma with last poll, for logging
        # the change once rather than twice a second.
        self._overruled: PlaybackStatus | None = None
        self._browser_hints = list(browser_hints) if browser_hints else list(BROWSER_HINTS)
        # Per-player blacklist: bus-name → monotonic time when it
        # becomes eligible to retry. A single property timeout banishes
        # the player for `_BLACKLIST_S` seconds so subsequent reads
        # don't keep eating the dbus reply timeout per stuck player.
        # plasma-browser-integration freezes under load; without this
        # the poll cycle backs up to ~50 s.
        self._timeout_blacklist: dict[str, float] = {}
        self._bus = None  # see _session_bus
        # MPRIS bus names from the last ListNames, and when that was.
        self._player_names: list[str] = []
        self._names_listed_at = 0.0
        self._names_stale = True
        # Properties proxy per bus name. dbus-python resolves the name to its
        # current owner once, in get_object (a blocking GetNameOwner); when the
        # owner goes away, calls fail with ServiceUnknown and it is dropped.
        self._proxies: dict[str, object] = {}
        # Identity / DesktopEntry per bus name; they never change while it is owned.
        self._identities: dict[str, tuple[str, str]] = {}
        # (bus name, interface) pairs whose GetAll failed; read property by property.
        self._no_get_all: set[tuple[str, str]] = set()

    _BLACKLIST_S = 5.0
    # New players (a browser started, a Firefox tab starting media) are noticed
    # this late at most; a vanished one is noticed on its failed read.
    _LIST_REFRESH_S = 3.0

    def set_browser_hints(self, hints: list[str]) -> None:
        if hints:
            self._browser_hints = list(hints)

    def _session_bus(self):
        """This source's own session-bus connection, with no main loop.

        Never ``dbus.SessionBus()``: that is the process-wide connection
        the MPRIS server exports on, dispatched by dbus-glib on the main
        thread. This source runs on the daemon thread, and dbus-glib keeps
        its per-connection timeout bookkeeping without any locking — a
        blocking call from here while the main thread dispatches on the
        same connection corrupts the heap ("malloc(): unaligned tcache
        chunk detected"). A private connection
        without a main loop never reaches dbus-glib; polling only needs
        blocking method calls, which work without one.
        """
        if self._bus is None:
            self._bus = dbus.SessionBus(private=True, mainloop=dbus.mainloop.NULL_MAIN_LOOP)
        return self._bus

    def _drop_bus(self) -> None:
        """Forget a connection that failed, so the next call opens a new one."""
        bus, self._bus = self._bus, None
        self._player_names = []
        self._names_stale = True
        self._proxies.clear()
        self._identities.clear()
        self._no_get_all.clear()
        if bus is not None:
            with contextlib.suppress(Exception):
                bus.close()

    def _list_players(self, bus) -> list[str]:
        obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        dbus_iface = dbus.Interface(obj, "org.freedesktop.DBus")
        names = []
        for raw in dbus_iface.ListNames():
            name = str(raw)
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            # Skip our own published MPRIS server — reading from
            # refrain's own bus name is circular, and the GLib-thread
            # response can race the polling thread badly enough to add
            # whole seconds of latency per tick.
            if name == "org.mpris.MediaPlayer2.refrain":
                continue
            names.append(name)
        for cache in (self._proxies, self._identities):
            for name in set(cache) - set(names):
                del cache[name]
        self._no_get_all = {key for key in self._no_get_all if key[0] in names}
        return names

    def _forget_player(self, name: str) -> None:
        self._names_stale = True
        self._proxies.pop(name, None)
        self._identities.pop(name, None)
        self._no_get_all = {key for key in self._no_get_all if key[0] != name}

    def read(self) -> TrackInfo:
        now = time.monotonic()
        if (
            self._names_stale
            or not self._player_names
            or now - self._names_listed_at >= self._LIST_REFRESH_S
        ):
            try:
                self._player_names = self._list_players(self._session_bus())
            except Exception as e:
                log.debug("MPRIS: ListNames failed: %s", e)
                self._drop_bus()
                return TrackInfo.empty()
            self._names_listed_at = now
            self._names_stale = False
        bus = self._bus

        candidates: list[tuple[int, TrackInfo, str]] = []
        # Browser-looking players that *can* control playback but failed the
        # apple-music URL filter — kept around so skip/next/prev fall back
        # onto them when the metadata player can't dispatch the action.
        fallbacks: list[str] = []
        native: dict[str, str] = {}  # the Apple Music tab's own entries → "playing"/"paused"
        for name in self._player_names:
            until = self._timeout_blacklist.get(name, 0.0)
            if now < until:
                continue  # this player just timed out; skip until cooldown ends
            had_proxy = name in self._proxies
            result = self._read_player(bus, name)
            if (
                had_proxy
                and name not in self._proxies
                and now >= self._timeout_blacklist.get(name, 0.0)
            ):
                # The name may have passed to a new owner; look it up again.
                result = self._read_player(bus, name)
            ti, score, control_capable, native_status = result
            if ti is not None:
                candidates.append((score, ti, name))
            elif control_capable:
                fallbacks.append(name)
            if native_status:
                native[name] = native_status

        self._native_apple_names = list(native)
        if not candidates:
            self._control_fallback_names = fallbacks
            return TrackInfo.empty()
        # On a tie (two paused tabs) stay with the last one instead of D-Bus name order.
        last = self._last_player_name
        candidates.sort(key=lambda c: (c[0], c[2] == last), reverse=True)
        best = candidates[0]
        self._last_player_name = best[2]
        # Every other apple-music candidate (typically the browser-native
        # MPRIS sitting next to plasma-browser-integration) joins the
        # fallback list. Their Next/Previous calls reach Apple Music's
        # mediaSession via a different path than plasma's wrapper, and
        # in practice that's the path that *actually* fires when plasma
        # claims success but the song doesn't change.
        for _, _, name in candidates[1:]:
            if name not in fallbacks:
                fallbacks.append(name)
        self._control_fallback_names = fallbacks
        track = best[1]
        if (
            "plasma-browser-integration" in best[2]
            and native
            and track.status != PlaybackStatus.STOPPED
        ):
            # plasma-browser-integration takes its metadata from the page's
            # media session but its state from whichever media element is
            # playing — on Apple Music, the looping artwork video. Paused,
            # the music keeps "playing" there. The tab's own entry is the
            # audio; its word on playing vs. paused wins. (Its "stopped"
            # blip at a song change doesn't count — see the condition.)
            status = (
                PlaybackStatus.PLAYING if "playing" in native.values() else PlaybackStatus.PAUSED
            )
            if status != track.status:
                if self._overruled != status:
                    log.debug("MPRIS: %s per the Apple Music tab itself, not plasma", status.value)
                self._overruled = status
                track = dataclasses.replace(track, status=status)
                return track
        self._overruled = None
        return track

    def play_pause(self) -> bool:
        # PlayPause is a TOGGLE — calling it twice is back to the
        # original state. Prefer the primary metadata player so a
        # single user click never races a fallback into double-toggling.
        # Except that the Apple Music tab's own entry goes first when there
        # is one: plasma's toggle follows the artwork video, which keeps
        # playing through a pause — so it can pause the music but never
        # start it again.
        return self._dispatch_action(
            "PlayPause", "CanPause", deprioritise_plasma=False, prefer=self._native_apple_names
        )

    def play(self) -> bool:
        # Play and Pause are no toggles, so a repeated Pause never restarts
        # the music. Same player order as the toggle.
        return self._dispatch_action(
            "Play", "CanPlay", deprioritise_plasma=False, prefer=self._native_apple_names
        )

    def pause(self) -> bool:
        return self._dispatch_action(
            "Pause", "CanPause", deprioritise_plasma=False, prefer=self._native_apple_names
        )

    def next(self) -> bool:
        # Next/Previous are idempotent for the user *intent* (skip one
        # song forward) but plasma-browser-integration's Next on Apple
        # Music silently no-ops more often than not. We deprioritise
        # plasma so the browser-native MPRIS gets the call first when
        # available — and stop on the first success so we don't skip
        # two songs.
        return self._dispatch_action("Next", "CanGoNext", deprioritise_plasma=True)

    def previous(self) -> bool:
        return self._dispatch_action("Previous", "CanGoPrevious", deprioritise_plasma=True)

    def _dispatch_action(
        self,
        method: str,
        capability_prop: str,
        *,
        deprioritise_plasma: bool,
        prefer: list[str] | None = None,
    ) -> bool:
        """Call ``method`` on whichever known player advertises ``capability_prop``.

        ``deprioritise_plasma=True`` reorders the try-list so the
        browser-native MPRIS (chromium, firefox) is tried before
        plasma-browser-integration. This is the right policy for
        Next/Previous, where plasma routinely returns success without
        actually skipping. PlayPause keeps "primary first"
        order so a single click never double-toggles by hitting two
        players.
        """
        try:
            bus = self._session_bus()
        except Exception as e:
            log.debug("MPRIS dispatch %s: bus connect failed: %s", method, e)
            self._drop_bus()
            return False

        # Build the try-list: primary first (if capable), then any
        # known fallbacks.
        candidates: list[str] = list(prefer or ())
        if self._last_player_name and self._last_player_name not in candidates:
            candidates.append(self._last_player_name)
        for name in self._control_fallback_names:
            if name not in candidates:
                candidates.append(name)

        capable: list[str] = [n for n in candidates if self._player_can(bus, n, capability_prop)]
        if deprioritise_plasma:
            capable.sort(key=lambda n: 1 if "plasma-browser-integration" in n else 0)

        for target in capable:
            if self._call_method_on(bus, target, method):
                return True
        return False

    def _player_can(self, bus, name: str, prop: str) -> bool:
        try:
            obj = bus.get_object(name, "/org/mpris/MediaPlayer2", introspect=False)
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return bool(props.Get("org.mpris.MediaPlayer2.Player", prop, timeout=0.5))
        except Exception as e:
            log.debug("MPRIS %s.%s probe failed: %s", name, prop, e)
            return False

    def _call_method_on(self, bus, name: str, method: str) -> bool:
        try:
            obj = bus.get_object(name, "/org/mpris/MediaPlayer2", introspect=False)
            iface = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
            getattr(iface, method)(timeout=0.5)
            # INFO so the live log shows which player received the action
            # when a fallback player handles it wrong.
            log.info("MPRIS %s dispatched on %s", method, name)
            return True
        except dbus.DBusException as e:
            log.debug("MPRIS %s on %s failed: %s", method, name, e)
            return False
        except Exception:
            log.exception("MPRIS %s on %s unexpected error", method, name)
            return False

    def _read_player(self, bus, name: str) -> tuple[TrackInfo | None, int, bool, str]:
        """Returns (track_info_or_None, score, is_browser_control_fallback,
        apple_music_tab_status).

        The third element is True iff this player looks like a browser
        playing media but failed the apple-music URL filter — meaning
        we can use it as a control fallback for skip/play/pause when the
        rich-metadata player can't dispatch those actions itself. The
        fourth is that fallback's "playing"/"paused" when its title — the
        page title — says it is the Apple Music tab, else "".
        """
        try:
            ident = self._identities.get(name)
            if ident is not None and not _looks_browser(name, *ident, self._browser_hints):
                return None, 0, False, ""
            props = self._proxies.get(name)
            if props is None:
                # introspect=False so a flaky MPRIS player can't hang our poll
                # for 25 s waiting for an Introspect reply; the signatures are known.
                player = bus.get_object(name, "/org/mpris/MediaPlayer2", introspect=False)
                props = dbus.Interface(player, "org.freedesktop.DBus.Properties")
                self._proxies[name] = props

            if ident is None:
                root, complete = self._read_props(
                    props, name, _ROOT_IFACE, ("Identity", "DesktopEntry")
                )
                ident = (
                    _safe_str(root.get("Identity", "")),
                    _safe_str(root.get("DesktopEntry", "")),
                )
                if complete:
                    self._identities[name] = ident
            identity, desktop_entry = ident
            if not _looks_browser(name, identity, desktop_entry, self._browser_hints):
                return None, 0, False, ""

            values, _ = self._read_props(
                props, name, _PLAYER_IFACE, ("PlaybackStatus", "Metadata", "Position")
            )
            playback = _safe_str(values.get("PlaybackStatus", "")).lower()
            metadata = values.get("Metadata", {})

            title = _safe_str(metadata.get("xesam:title", ""))
            artists = _to_str_list(metadata.get("xesam:artist", []))
            artist = ", ".join(a for a in artists if a).strip()
            album = _safe_str(metadata.get("xesam:album", ""))
            url = _normalize_apple_url(_safe_str(metadata.get("xesam:url", "")))

            try:
                duration_ms = int(metadata.get("mpris:length", 0)) // 1000
            except Exception:
                duration_ms = 0
            try:
                position_ms = int(values.get("Position", 0)) // 1000
            except Exception:
                position_ms = 0

            if not _looks_apple_music(url):
                # Browser is playing *something* — it might be the same Apple
                # Music tab seen from the browser's native MPRIS view, while
                # KDE's plasma-browser-integration owns the rich URL/metadata.
                # Tag it as a control fallback so skip/play/pause have a
                # capable player to dispatch onto.
                control_capable = playback in ("playing", "paused")
                apple_tab = control_capable and _is_apple_music_page_title(title)
                return None, 0, control_capable, playback if apple_tab else ""

            if not artist and _is_apple_music_page_title(title):
                # The page's own title ("Apple Music – Webplayer"), reported
                # while no song is loaded — not a track, however long it
                # "plays". Kept as a candidate for its state and controls.
                title = ""

            status = (
                PlaybackStatus.PLAYING
                if playback == "playing"
                else PlaybackStatus.PAUSED
                if playback == "paused"
                else PlaybackStatus.STOPPED
            )

            score = 0
            if status == PlaybackStatus.PLAYING:
                score += 100
            elif status == PlaybackStatus.PAUSED:
                score += 50
            if title:
                score += 10
            if artist:
                score += 5

            return (
                TrackInfo(
                    source="mpris",
                    title=title,
                    artist=artist,
                    album=_clean_album(album, title),
                    duration_ms=duration_ms,
                    position_ms=position_ms,
                    status=status,
                    url=url,
                    player=identity or desktop_entry,
                ),
                score,
                False,
                "",
            )

        except dbus.DBusException as e:
            log.debug("MPRIS player %s gone or unreadable: %s", name, e)
            self._forget_player(name)
            return None, 0, False, ""
        except Exception as e:
            log.debug("MPRIS player %s read error: %s", name, e)
            return None, 0, False, ""

    def _blacklist(self, name: str, what: str) -> None:
        self._timeout_blacklist[name] = time.monotonic() + self._BLACKLIST_S
        log.debug("MPRIS %s timed out on %s — blacklisting %.0fs", name, what, self._BLACKLIST_S)

    def _read_props(self, props, name: str, iface: str, keys: tuple[str, ...]) -> tuple[dict, bool]:
        """Read ``keys`` of ``iface``: one GetAll, or one Get each where GetAll failed before.

        Returns the values that could be read, and False when a property timed
        out. A missing value means the caller's default. A GetAll that times
        out or finds the player gone raises; any other failure falls back to
        single Gets and is remembered for this player.

        The 0.5 s timeout caps the cost of a hung player: dbus-python's
        default 25 s would freeze the poll loop.
        """
        if (name, iface) not in self._no_get_all:
            try:
                reply = props.GetAll(iface, timeout=0.5)
            except dbus.DBusException as e:
                err = str(e)
                if any(h in err for h in _HUNG_ERRORS):
                    self._blacklist(name, f"GetAll({iface})")
                    raise
                if any(g in err for g in _GONE_ERRORS):
                    raise
                reply = None
            except Exception:
                reply = None
            if isinstance(reply, dict):
                return {k: reply[k] for k in keys if k in reply}, True
            self._no_get_all.add((name, iface))
            log.debug("MPRIS %s: GetAll(%s) failed, reading properties singly", name, iface)

        # Each Get on its own: chromium's MPRIS rejects some optional
        # properties (DesktopEntry in particular) with a generic
        # `org.freedesktop.DBus.Error.Failed` instead of returning
        # an empty string, and one bad property must not drop the
        # whole player — least of all the chromium player whose
        # Next/Previous calls actually skip Apple Music tracks.
        values: dict = {}
        complete = True
        for key in keys:
            try:
                values[key] = props.Get(iface, key, timeout=0.5)
            except dbus.DBusException as e:
                err = str(e)
                # A hung player is skipped on the next polls instead of
                # eating another 0.5 s each. Other errors (Error.Failed,
                # UnknownProperty) are normal for optional properties.
                if any(h in err for h in _HUNG_ERRORS):
                    self._blacklist(name, key)
                    complete = False
                elif any(g in err for g in _GONE_ERRORS):
                    self._forget_player(name)
                    complete = False
            except Exception:
                pass
        return values, complete
