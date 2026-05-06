"""MPRIS-based Apple Music source.

Listens on the session bus for any MPRIS player, filters down to those that
look like browsers, and further down to those whose `xesam:url` points at
music.apple.com. The highest-scoring candidate wins. The bus name of the
winning player is remembered so playback controls can target it.
"""

from __future__ import annotations

import logging
import re

import dbus

from refrain.sources.base import PlaybackStatus, TrackInfo

log = logging.getLogger(__name__)

APPLE_MUSIC_HOSTS = ("music.apple.com",)
BROWSER_HINTS = (
    "firefox",
    "zen",
    "librewolf",
    "chromium",
    "chrome",
    "brave",
    "edge",
    "vivaldi",
    "opera",
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


def _looks_browser(name: str, identity: str, desktop_entry: str, hints: list[str]) -> bool:
    hay = f"{name} {identity} {desktop_entry}".lower()
    return any(h in hay for h in hints)


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
    if _normalize(album) and _normalize(title) and _normalize(album) in _normalize(title):
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
        # Browser-MPRIS players that are *capable* of the action but lack the
        # rich metadata. Apple Music in Chromium typically exposes two
        # players: KDE's `plasma-browser-integration` (good metadata, but
        # CanGoNext=False) and the browser's own MPRIS (CanGoNext=True but
        # only the tab title as metadata). We pick plasma for `read()`,
        # then fall back to the browser-native player for skip controls.
        self._control_fallback_names: list[str] = []
        self._browser_hints = list(browser_hints) if browser_hints else list(BROWSER_HINTS)

    def set_browser_hints(self, hints: list[str]) -> None:
        if hints:
            self._browser_hints = list(hints)

    def read(self) -> TrackInfo:
        try:
            bus = dbus.SessionBus()
            obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            dbus_iface = dbus.Interface(obj, "org.freedesktop.DBus")
            names = list(dbus_iface.ListNames())
        except Exception as e:
            log.debug("MPRIS: ListNames failed: %s", e)
            return TrackInfo.empty()

        candidates: list[tuple[int, TrackInfo, str]] = []
        # Browser-looking players that *can* control playback but failed the
        # apple-music URL filter — kept around so skip/next/prev fall back
        # onto them when the metadata player can't dispatch the action.
        fallbacks: list[str] = []
        for raw in names:
            name = str(raw)
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            ti, score, control_capable = self._read_player(bus, name)
            if ti is not None:
                candidates.append((score, ti, name))
            elif control_capable:
                fallbacks.append(name)

        self._control_fallback_names = fallbacks

        if not candidates:
            return TrackInfo.empty()
        candidates.sort(key=lambda c: c[0], reverse=True)
        best = candidates[0]
        self._last_player_name = best[2]
        return best[1]

    def play_pause(self) -> bool:
        return self._dispatch_action("PlayPause", "CanPause")

    def next(self) -> bool:
        return self._dispatch_action("Next", "CanGoNext")

    def previous(self) -> bool:
        return self._dispatch_action("Previous", "CanGoPrevious")

    def _dispatch_action(self, method: str, capability_prop: str) -> bool:
        """Call ``method`` on whichever known player advertises ``capability_prop``.

        Tries the rich-metadata player first, then any browser-native MPRIS
        players that we tagged as control-capable during the last ``read()``.
        Without this, Apple Music Web on Chromium can't be skipped — KDE's
        plasma-browser-integration wins the metadata pick but exposes
        `CanGoNext=False` / `CanGoPrevious=False`.
        """
        try:
            bus = dbus.SessionBus()
        except Exception as e:
            log.debug("MPRIS dispatch %s: bus connect failed: %s", method, e)
            return False

        # Build a try-list: primary first (if capable), then known fallbacks.
        targets: list[str] = []
        if self._last_player_name and self._player_can(bus, self._last_player_name, capability_prop):
            targets.append(self._last_player_name)
        for name in self._control_fallback_names:
            if name not in targets and self._player_can(bus, name, capability_prop):
                targets.append(name)
        # Last resort — the primary even if it doesn't advertise the capability;
        # some players lie about Can* and still respond.
        if self._last_player_name and self._last_player_name not in targets:
            targets.append(self._last_player_name)

        for target in targets:
            if self._call_method_on(bus, target, method):
                return True
        return False

    def _player_can(self, bus, name: str, prop: str) -> bool:
        try:
            obj = bus.get_object(name, "/org/mpris/MediaPlayer2")
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return bool(props.Get("org.mpris.MediaPlayer2.Player", prop))
        except Exception as e:
            log.debug("MPRIS %s.%s probe failed: %s", name, prop, e)
            return False

    def _call_method_on(self, bus, name: str, method: str) -> bool:
        try:
            obj = bus.get_object(name, "/org/mpris/MediaPlayer2")
            iface = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
            getattr(iface, method)()
            log.debug("MPRIS %s dispatched on %s", method, name)
            return True
        except dbus.DBusException as e:
            log.debug("MPRIS %s on %s failed: %s", method, name, e)
            return False
        except Exception as e:
            log.warning("MPRIS %s on %s unexpected error: %s", method, name, e)
            return False

    def _read_player(self, bus, name: str) -> tuple[TrackInfo | None, int, bool]:
        """Returns (track_info_or_None, score, is_browser_control_fallback).

        The third element is True iff this player looks like a browser
        playing media but failed the apple-music URL filter — meaning
        we can use it as a control fallback for skip/play/pause when the
        rich-metadata player can't dispatch those actions itself.
        """
        try:
            player = bus.get_object(name, "/org/mpris/MediaPlayer2")
            props = dbus.Interface(player, "org.freedesktop.DBus.Properties")

            identity = _safe_str(props.Get("org.mpris.MediaPlayer2", "Identity"))
            desktop_entry = _safe_str(props.Get("org.mpris.MediaPlayer2", "DesktopEntry"))
            playback = _safe_str(
                props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
            ).lower()
            metadata = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")

            title = _safe_str(metadata.get("xesam:title", ""))
            artists = _to_str_list(metadata.get("xesam:artist", []))
            artist = ", ".join(a for a in artists if a).strip()
            album = _safe_str(metadata.get("xesam:album", ""))
            url = _safe_str(metadata.get("xesam:url", ""))

            try:
                duration_ms = int(metadata.get("mpris:length", 0)) // 1000
            except Exception:
                duration_ms = 0
            try:
                position_ms = int(props.Get("org.mpris.MediaPlayer2.Player", "Position")) // 1000
            except Exception:
                position_ms = 0

            is_browser = _looks_browser(name, identity, desktop_entry, self._browser_hints)
            if not is_browser:
                return None, 0, False

            if not _looks_apple_music(url):
                # Browser is playing *something* — it might be the same Apple
                # Music tab seen from the browser's native MPRIS view, while
                # KDE's plasma-browser-integration owns the rich URL/metadata.
                # Tag it as a control fallback so skip/play/pause have a
                # capable player to dispatch onto.
                control_capable = playback in ("playing", "paused")
                return None, 0, control_capable

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

            return TrackInfo(
                source="mpris",
                title=title,
                artist=artist,
                album=_clean_album(album, title),
                duration_ms=duration_ms,
                position_ms=position_ms,
                status=status,
                url=url,
            ), score, False

        except dbus.DBusException as e:
            log.debug("MPRIS player %s gone or unreadable: %s", name, e)
            return None, 0, False
        except Exception as e:
            log.debug("MPRIS player %s read error: %s", name, e)
            return None, 0, False
