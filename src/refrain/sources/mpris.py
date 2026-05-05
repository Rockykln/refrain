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
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—")
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
        for raw in names:
            name = str(raw)
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            ti, score = self._read_player(bus, name)
            if ti is not None:
                candidates.append((score, ti, name))

        if not candidates:
            return TrackInfo.empty()
        candidates.sort(key=lambda c: c[0], reverse=True)
        best = candidates[0]
        self._last_player_name = best[2]
        return best[1]

    def play_pause(self) -> bool:
        return self._call_method("PlayPause")

    def next(self) -> bool:
        return self._call_method("Next")

    def previous(self) -> bool:
        return self._call_method("Previous")

    def _call_method(self, method: str) -> bool:
        if not self._last_player_name:
            return False
        try:
            bus = dbus.SessionBus()
            obj = bus.get_object(self._last_player_name, "/org/mpris/MediaPlayer2")
            iface = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
            getattr(iface, method)()
            return True
        except dbus.DBusException as e:
            log.debug("MPRIS %s on %s failed: %s", method, self._last_player_name, e)
            self._last_player_name = None
            return False
        except Exception as e:
            log.warning("MPRIS %s unexpected error: %s", method, e)
            return False

    def _read_player(self, bus, name: str) -> tuple[TrackInfo | None, int]:
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

            if not _looks_browser(name, identity, desktop_entry, self._browser_hints):
                return None, 0
            if not _looks_apple_music(url):
                return None, 0

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
            ), score

        except dbus.DBusException as e:
            log.debug("MPRIS player %s gone or unreadable: %s", name, e)
            return None, 0
        except Exception as e:
            log.debug("MPRIS player %s read error: %s", name, e)
            return None, 0
