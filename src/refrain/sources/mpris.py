"""MPRIS-based Apple Music source.

Listens on the session bus for any MPRIS player, filters down to those that
look like browsers, and further down to those whose `xesam:url` points at
music.apple.com. The highest-scoring candidate wins. The bus name of the
winning player is remembered so playback controls can target it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import re

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


def _safe_str(v) -> str:
    return "" if v is None else str(v)


def _to_str_list(v) -> list[str]:
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
    """Is this a page title of Apple Music's own, like "Sehnsucht – Album von
    Rammstein – Apple Music" or "Apple Music – Webplayer"?

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
    # the album field for singles ("Sun Rise" / "Sun Rise (Single)").
    # Substring-style matching was overly aggressive: a short album
    # name that happens to be a prefix of the title (Album="Sun",
    # Title="Sun Rise (Extended Mix)") would falsely drop.
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
        # Apple Music's plasma-browser-integration freezes mid-session
        # under load; without this the daemon's poll cycle backed up
        # to ~50 s and notifications + Discord status froze with it.
        self._timeout_blacklist: dict[str, float] = {}
        self._bus = None  # see _session_bus

    _BLACKLIST_S = 5.0

    def set_browser_hints(self, hints: list[str]) -> None:
        if hints:
            self._browser_hints = list(hints)

    def _session_bus(self):
        """This source's own session-bus connection, with no main loop.

        Never ``dbus.SessionBus()``: that is the process-wide connection
        the MPRIS server exports on, dispatched by dbus-glib on the main
        thread. This source runs on the daemon thread, and dbus-glib keeps
        its per-connection timeout bookkeeping without any locking — a
        blocking call from here while the main thread dispatched on the
        same connection corrupted the heap, and Refrain died with
        "malloc(): unaligned tcache chunk detected". A private connection
        without a main loop never reaches dbus-glib; polling only needs
        blocking method calls, which work without one.
        """
        if self._bus is None:
            self._bus = dbus.SessionBus(private=True, mainloop=dbus.mainloop.NULL_MAIN_LOOP)
        return self._bus

    def _drop_bus(self) -> None:
        """Forget a connection that failed, so the next call opens a new one."""
        bus, self._bus = self._bus, None
        if bus is not None:
            with contextlib.suppress(Exception):
                bus.close()

    def read(self) -> TrackInfo:
        import time as _time

        try:
            bus = self._session_bus()
            obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            dbus_iface = dbus.Interface(obj, "org.freedesktop.DBus")
            names = list(dbus_iface.ListNames())
        except Exception as e:
            log.debug("MPRIS: ListNames failed: %s", e)
            self._drop_bus()
            return TrackInfo.empty()

        now = _time.monotonic()
        candidates: list[tuple[int, TrackInfo, str]] = []
        # Browser-looking players that *can* control playback but failed the
        # apple-music URL filter — kept around so skip/next/prev fall back
        # onto them when the metadata player can't dispatch the action.
        fallbacks: list[str] = []
        native: dict[str, str] = {}  # the Apple Music tab's own entries → "playing"/"paused"
        for raw in names:
            name = str(raw)
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            # Skip our own published MPRIS server — reading from
            # refrain's own bus name is circular, and the GLib-thread
            # response can race the polling thread badly enough to add
            # whole seconds of latency per tick.
            if name == "org.mpris.MediaPlayer2.refrain":
                continue
            until = self._timeout_blacklist.get(name, 0.0)
            if now < until:
                continue  # this player just timed out; skip until cooldown ends
            ti, score, control_capable, native_status = self._read_player(bus, name)
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
        candidates.sort(key=lambda c: c[0], reverse=True)
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
            # the music went on "playing" there. The tab's own entry is the
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
        # playing through a pause — so it could pause the music but never
        # start it again.
        return self._dispatch_action(
            "PlayPause", "CanPause", deprioritise_plasma=False, prefer=self._native_apple_names
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
        actually skipping. PlayPause keeps the old "primary first"
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
            # INFO (not debug) so users can see in the live log which
            # MPRIS player actually received Next/Previous/PlayPause —
            # critical for diagnosing "Next pauses instead of skipping"
            # type bugs where a fallback player handles the action wrong.
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
            # introspect=False so a flaky MPRIS player (we're looking
            # at you, plasma-browser-integration) can't hang our poll
            # for 25 s waiting for an Introspect reply that never
            # comes. We don't need the introspection XML — we already
            # know the property/method signatures.
            player = bus.get_object(name, "/org/mpris/MediaPlayer2", introspect=False)
            props = dbus.Interface(player, "org.freedesktop.DBus.Properties")

            # Each Get is wrapped: chromium's MPRIS rejects some optional
            # properties (DesktopEntry in particular) with a generic
            # `org.freedesktop.DBus.Error.Failed` instead of returning
            # an empty string, and one bad property in a shared
            # try/except dropped the whole player from our candidate
            # list — including, critically, the chromium player whose
            # Next/Previous calls actually skip Apple Music tracks.
            #
            # 0.5 s timeout per Get caps total cost: dbus-python's
            # default 25 s reply timeout would let a single hung
            # player freeze the poll loop for half a minute.
            def _safe_get(iface: str, prop: str, default=""):
                try:
                    return props.Get(iface, prop, timeout=0.5)
                except dbus.DBusException as e:
                    err = str(e)
                    # NoReply / Timeout means the player is hung — blacklist
                    # so the next poll skips it instead of eating another
                    # 0.5 s here. Other errors (Error.Failed, UnknownProp)
                    # are normal for optional properties; just default.
                    if "NoReply" in err or "Timeout" in err:
                        import time as _time

                        self._timeout_blacklist[name] = _time.monotonic() + self._BLACKLIST_S
                        log.debug(
                            "MPRIS %s timed out on %s — blacklisting %.0fs",
                            name,
                            prop,
                            self._BLACKLIST_S,
                        )
                    return default
                except Exception:
                    return default

            identity = _safe_str(_safe_get("org.mpris.MediaPlayer2", "Identity"))
            desktop_entry = _safe_str(_safe_get("org.mpris.MediaPlayer2", "DesktopEntry"))
            playback = _safe_str(
                _safe_get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
            ).lower()
            metadata = _safe_get("org.mpris.MediaPlayer2.Player", "Metadata", default={})

            title = _safe_str(metadata.get("xesam:title", ""))
            artists = _to_str_list(metadata.get("xesam:artist", []))
            artist = ", ".join(a for a in artists if a).strip()
            album = _safe_str(metadata.get("xesam:album", ""))
            url = _normalize_apple_url(_safe_str(metadata.get("xesam:url", "")))

            try:
                duration_ms = int(metadata.get("mpris:length", 0)) // 1000
            except Exception:
                duration_ms = 0
            raw_position = _safe_get("org.mpris.MediaPlayer2.Player", "Position", default=0)
            try:
                position_ms = int(raw_position) // 1000
            except Exception:
                position_ms = 0

            is_browser = _looks_browser(name, identity, desktop_entry, self._browser_hints)
            if not is_browser:
                return None, 0, False, ""

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
            return None, 0, False, ""
        except Exception as e:
            log.debug("MPRIS player %s read error: %s", name, e)
            return None, 0, False, ""
