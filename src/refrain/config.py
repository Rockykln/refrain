"""TOML config: load on startup, save on Apply, sensible defaults."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import re
import threading
import time
import tomllib
import zoneinfo
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from refrain.paths import config_path

log = logging.getLogger(__name__)

# The GUI thread and the Discord app-name refresh thread both save, and
# they share one tmp file name.
_SAVE_LOCK = threading.Lock()


def _coerce_value(annot, name: str, value):
    """Coerce a single config value to its declared field type.
    Raises TypeError when the value can't be made to fit.

    Coercion rules:
      bool → accept bool; "true"/"false"/"yes"/"no"/"0"/"1" strings
        from a hand-edit get converted; otherwise reject.
      int  → accept int (but reject bool, since
        ``poll_interval_ms = true`` is much more likely a typo than
        a literal 1); coerce floats by truncating; coerce numeric
        strings; reject otherwise.
      str  → accept str; coerce other primitives via str(); reject
        otherwise.
    """
    if annot is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes"):
                return True
            if low in ("false", "0", "no"):
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        raise TypeError(f"{name}={value!r} is not a bool")
    if annot is int:
        if isinstance(value, bool):
            raise TypeError(f"{name}={value!r} is bool, not int")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        raise TypeError(f"{name}={value!r} is not an int")
    if annot is str:
        if isinstance(value, str):
            return value
        # Accept numeric primitives (rare hand-edit case where someone
        # wrote `client_id = 1234` instead of `"1234"`). Reject bool
        # explicitly — `log_level = false` is much more likely a typo
        # for `log_level = "INFO"` than the literal string "False".
        if isinstance(value, bool):
            raise TypeError(f"{name}={value!r} is bool, not str")
        if isinstance(value, (int, float)):
            return str(value)
        raise TypeError(f"{name}={value!r} is not a str")
    # Un-annotated / non-primitive field type — accept as-is.
    return value


# Populated lazily from typing.get_type_hints() the first time each
# section is loaded. Keyed by ``cls.__qualname__`` so reloads (tests,
# hot-reload) refresh per-section without rebuilding everything.
_DATACLASS_HINTS: dict[str, dict[str, type]] = {}

# Keys older versions wrote that are no longer used; skipped without a warning.
_RETIRED_KEYS = {("AdvancedConfig", "cover_cache_size")}

# Defaults that changed. A file without the key was written by a Refrain
# that had the old default, so its user never chose the new one.
_LEGACY_DEFAULTS: dict[str, dict[str, Any]] = {"behavior": {"notifications": True}}


def _with_legacy_defaults(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for section, old in _LEGACY_DEFAULTS.items():
        body = out.get(section) or {}
        if isinstance(body, dict):
            out[section] = {**old, **body}
    return out


def _construct(cls, payload):
    """Build a dataclass from ``payload`` while ignoring unknown keys
    AND coercing/dropping wrongly-typed values.

    Plain ``cls(**payload)`` raises TypeError on any key that isn't a
    declared field — including keys written by a *newer* Refrain that
    the user has since downgraded from, or hand-edited typos. The
    surrounding except in ``Config.load`` would then drop the *whole*
    config back to defaults, silently losing every other setting the
    user picked. Filtering + per-field coercion means a single stray
    key (or a wrong type, e.g. ``log_level = false``) just gets
    dropped (with a warning) and the rest of the section survives.
    """
    import typing

    qual = cls.__qualname__
    if qual not in _DATACLASS_HINTS:
        _DATACLASS_HINTS[qual] = typing.get_type_hints(cls)
    hints = _DATACLASS_HINTS[qual]

    if not payload:
        return cls()
    known_field_names = {f.name for f in dataclasses.fields(cls)}
    accepted: dict = {}
    dropped: list[str] = []
    for k, v in payload.items():
        if k not in known_field_names:
            if (cls.__name__, k) not in _RETIRED_KEYS:
                dropped.append(k)
            continue
        try:
            accepted[k] = _coerce_value(hints.get(k), k, v)
        except (TypeError, ValueError, OverflowError) as e:
            log.warning(
                "Config: dropping wrongly-typed %s.%s value (%s) — using default",
                cls.__name__,
                k,
                e,
            )
    if dropped:
        log.warning(
            "Config: ignoring unknown %s keys %s — likely from a different "
            "Refrain version or a hand-edit typo",
            cls.__name__,
            sorted(dropped),
        )
    return cls(**accepted)


@dataclass
class DiscordConfig:
    # Empty by default — every user registers their own Discord app at
    # https://discord.com/developers/applications and pastes the
    # Application ID into Settings → General. The status won't appear in
    # Discord until this is filled in. This is also the fallback ID used
    # when a per-source override is empty.
    client_id: str = ""
    # Optional per-source override Application IDs. When the active
    # source flips (Apple Music ↔ Bluetooth) the daemon reconnects RPC
    # under the source-specific ID so each source can render with its
    # own application name + uploaded artwork in the user's profile.
    # Empty falls back to the default `client_id` above.
    client_id_mpris: str = ""
    client_id_bluetooth: str = ""
    # Publish the same status to *every* Discord client that is running,
    # not just the first one found. Two Discord builds running side by side
    # are separate processes with separate IPC sockets, so a status sent to
    # one is invisible in the other. Off by default: one client is the
    # normal case, and each extra connection is another IPC write per
    # track change.
    all_clients: bool = False
    # Cached display name for `client_id` — the name Discord puts next
    # to "Listening to", shown in Settings so a mistyped ID stops being
    # invisible. Cached because it changes about never, and asking on
    # every keystroke would be a request per digit. The ID it belongs to
    # is stored with it: the name is wrong the moment the ID changes,
    # and a stale name on a new ID would be worse than none. Refreshed
    # at startup and every `refrain.discord_app.NAME_TTL_S`.
    app_name: str = ""
    app_name_for_id: str = ""
    app_name_checked_ts: int = 0
    # Whether to look the name up at all. Opt-in, and off by default:
    # this is the only request Refrain would make to Discord's *servers*
    # rather than to the local client, and by default Refrain sends
    # nothing anywhere on its own.
    resolve_app_name: bool = False

    def client_id_for(self, source: str) -> str:
        """Return the per-source client_id, falling back to the default."""
        if source == "mpris" and self.client_id_mpris:
            return self.client_id_mpris
        if source == "bluetooth" and self.client_id_bluetooth:
            return self.client_id_bluetooth
        return self.client_id


DEFAULT_BROWSER_HINTS = (
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


@dataclass
class SourcesConfig:
    mpris_enabled: bool = True
    bluetooth_enabled: bool = True
    bluetooth_device: str = ""  # empty = auto-detect, otherwise MAC like "AA:BB:CC:DD:EE:FF"
    # Comma-separated MPRIS bus-name / desktop-entry hints. Refrain only
    # picks up players whose name/identity matches one of these. Edit if
    # your browser isn't auto-detected.
    browser_hints: str = ",".join(DEFAULT_BROWSER_HINTS)

    def browser_hints_list(self) -> list[str]:
        return [h.strip().lower() for h in self.browser_hints.split(",") if h.strip()]


@dataclass
class PrivacyConfig:
    mode: str = "full"  # "full" | "minimal" | "off"
    # What "Resume sharing" returns to after "Pause sharing" set mode to off.
    resume_mode: str = "full"


@dataclass
class BehaviorConfig:
    autostart: bool = False
    # Off for new installs; a config file without this key keeps the old
    # default, see _LEGACY_DEFAULTS.
    notifications: bool = False
    cover_art: bool = True
    show_buttons: bool = True
    # How long to wait after a track change before firing the desktop
    # notification. 0 = fire immediately; the retry loop in
    # `_fire_pending_notify` still polls up to 2 s for the cover image
    # to land before falling back to the brand fallback.
    notify_delay_ms: int = 0
    # The icon has to suit the panel, not the app theme, and most panels are
    # dark even under a light theme. "auto" follows the system colour scheme.
    tray_icon: str = "white"
    # Set to True after the first-run wizard runs once. Prevents the
    # welcome dialog from re-appearing on every launch.
    first_run_complete: bool = False
    # The one-time "keeps running in the tray" hint has been shown.
    tray_hint_shown: bool = False


@dataclass
class AdvancedConfig:
    poll_interval_ms: int = 500
    log_level: str = "INFO"
    # Idle-detection grace window (in seconds). When the *same* track has
    # been "playing" for longer than its own duration plus this grace,
    # Refrain assumes the source is dangling (e.g. browser tab closed
    # without releasing the MPRIS handle) and clears the Discord status.
    # Set to 0 to disable idle detection entirely.
    idle_grace_s: int = 30
    # Frozen-position detection. When a PLAYING source keeps reporting
    # the same MPRIS `Position` for longer than this many seconds,
    # Refrain stops trusting it and runs the track clock from wall time
    # instead (see refrain.timing.resolve_position). Set to 0 to disable
    # the freshness check entirely and always echo the source's value.
    position_stall_s: int = 4
    # Override UI language. "system" follows QLocale.system(); explicit
    # codes ("en", "de", "fr", …) force a specific translation. Takes
    # effect after restarting Refrain — the QTranslator is installed
    # once at app startup.
    language: str = "system"
    # Not in Settings on purpose: the desktop decides the clock, and these two
    # are for the rare setup where it decides wrong. "system" follows it.
    time_format: str = "system"  # "system", "12h" or "24h"
    time_zone: str = ""  # an IANA name like "Europe/Berlin"; empty = the system's
    # How long the mouse has to rest on a song in the Status window before its
    # title scrolls past. 0 turns the scrolling off.
    hover_scroll_ms: int = 1500
    developer_mode: bool = False
    # Once unlocked, the developer switch stays in Settings even while off.
    developer_unlocked: bool = False


@dataclass
class UpdateConfig:
    auto_check: bool = True
    last_check_ts: int = 0  # unix epoch seconds


@dataclass
class LastfmConfig:
    # Opt-in, *alongside* the Discord Rich Presence (never a
    # replacement). Off by default — scrobbling broadcasts listening
    # history to a third party, so the user has to turn it on.
    enabled: bool = False
    # Each user registers their own Last.fm API account at
    # https://www.last.fm/api/account/create and pastes both values in
    # Settings → Last.fm — same "bring your own credentials" pattern as
    # the Discord client_id.
    api_key: str = ""
    shared_secret: str = ""
    # Obtained via the desktop auth flow (auth.getToken → browser
    # authorize → auth.getSession). Long-lived until the user revokes
    # it on last.fm. `username` is display-only (shown in Settings so
    # the user can see which account is connected).
    session_key: str = ""
    username: str = ""
    # Also push the ephemeral "now playing" indicator (Last.fm's
    # equivalent of the Discord RPC). Cheap; on by default when
    # scrobbling is enabled.
    scrobble_now_playing: bool = True


# Song counts offered in Settings → General → History. A hand-edited
# value outside the list still works as long as it's within 1–100; the
# settings combo adds it as an extra entry rather than rounding it.
HISTORY_LIMIT_CHOICES = (10, 20, 30, 50, 75, 100)
HISTORY_LIMIT_MAX = 100


@dataclass
class HistoryConfig:
    # "Recently played": a list of the last songs heard, kept on this
    # machine and never sent anywhere — which is why it's on by default
    # and why privacy mode doesn't touch it. Turning it off is a hard
    # off: nothing is recorded and the stored list is deleted.
    enabled: bool = True
    # How many songs are kept (and therefore shown). Lowering it drops
    # the oldest songs straight away.
    max_entries: int = 30
    # The window's size when it was last closed; 0 means the default.
    # Only the size — on Wayland the compositor places windows.
    window_width: int = 0
    window_height: int = 0


# A rule returns the value to keep and, when the original was not fine,
# why. A value that only needed tidying (case, surrounding spaces) comes
# back changed with no reason and is fixed without a warning.
_Check = Callable[[Any], tuple[Any, str | None]]

PRIVACY_MODES = ("full", "minimal", "off")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
TRAY_ICONS = ("white", "black", "auto")
_WINDOW_SIZE_MAX = 16384
_MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_CLIENT_ID_RE = re.compile(r"\d{17,20}")
_CLIENT_ID_JUNK_RE = re.compile(r"\s|[\x00-\x1f\x7f]")
_BROWSER_HINT_RE = re.compile(r"[a-z0-9][a-z0-9 ._-]{0,63}")


def _field_default(cls, key: str) -> Any:
    return getattr(cls(), key)


def _require_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(value)
    return value


def _clamp(lo: int, hi: int) -> _Check:
    def check(value):
        n = _require_int(value)
        if n < lo:
            return lo, f"is below {lo}"
        if n > hi:
            return hi, f"is above {hi}"
        return n, None

    return check


def _range_or(default: int, lo: int, hi: int) -> _Check:
    """For values where the edge of the range would be a poor guess."""

    def check(value):
        n = _require_int(value)
        if n != default and not lo <= n <= hi:
            return default, f"is outside {lo}–{hi}"
        return n, None

    return check


def _one_of(choices: tuple[str, ...], fallback: str, *, upper: bool = False) -> _Check:
    def check(value):
        norm = value.strip()
        norm = norm.upper() if upper else norm.lower()
        if norm in choices:
            return norm, None
        return fallback, f"is not one of {', '.join(choices)}"

    return check


@cache
def shipped_languages() -> frozenset[str]:
    """Codes that have a real translation next to this module."""
    folder = Path(__file__).parent / "i18n"
    return frozenset(
        p.stem.split("_", 1)[1] for p in folder.glob("refrain_*.qm") if p.stat().st_size > 100
    )


def _check_language(value: str):
    code = value.strip().replace("-", "_")
    if not code:
        return "system", None
    if code == "system" or code in shipped_languages():
        return code, None
    # "de_DE" worked before this check existed; keep it working as "de".
    base = code.split("_", 1)[0]
    if base in shipped_languages():
        return base, "is not a shipped translation code"
    return "system", "is not a shipped translation"


def _check_bluetooth_device(value: str):
    mac = value.strip()
    if not mac or _MAC_RE.fullmatch(mac):
        return mac, None
    return "", "is not a Bluetooth address like AA:BB:CC:DD:EE:FF"


def _check_client_id(value: str):
    cid = value.strip()
    if not cid or _CLIENT_ID_RE.fullmatch(cid):
        return cid, None
    # A near-miss (a digit short, a stray letter) stays: Settings shows it
    # with "Expected 17-20 digits", and clearing it would turn Discord off
    # without the user ever seeing what was wrong with their paste.
    if len(cid) <= 32 and not _CLIENT_ID_JUNK_RE.search(cid):
        return cid, "does not look like a Discord Application ID (17-20 digits)"
    return "", "cannot be a Discord Application ID"


def _check_browser_hints(value: str):
    tokens = [t.strip().lower() for t in value.split(",") if t.strip()]
    good = [t for t in tokens if _BROWSER_HINT_RE.fullmatch(t)]
    if not good:
        return ",".join(DEFAULT_BROWSER_HINTS), "has no usable player name"
    if len(good) != len(tokens):
        return ",".join(good), "contains unusable entries"
    return value, None


def _check_timestamp(value):
    ts = _require_int(value)
    if ts < 0:
        return 0, "is negative"
    # A day of slack for a clock that was briefly wrong.
    if ts > time.time() + 86400:
        return 0, "is in the future"
    return ts, None


def _check_time_zone(value: object) -> tuple[object, str | None]:
    """An IANA zone name, or "" for the system's own."""
    if not isinstance(value, str):
        return "", 'must be a time-zone name like "Europe/Berlin"'
    name = value.strip()
    if not name:
        return "", None
    try:
        zoneinfo.ZoneInfo(name)
    except Exception:
        return "", "is not a known time zone"
    return name, None


_RULES: tuple[tuple[str, str, _Check], ...] = (
    ("discord", "client_id", _check_client_id),
    ("discord", "client_id_mpris", _check_client_id),
    ("discord", "client_id_bluetooth", _check_client_id),
    ("sources", "bluetooth_device", _check_bluetooth_device),
    ("sources", "browser_hints", _check_browser_hints),
    # Not the default "full": a mistyped mode must not share more than intended.
    ("privacy", "mode", _one_of(PRIVACY_MODES, "minimal")),
    ("privacy", "resume_mode", _one_of(("full", "minimal"), "full")),
    # Same limits as the spin boxes in Settings → Advanced.
    ("behavior", "notify_delay_ms", _clamp(0, 10_000)),
    ("behavior", "tray_icon", _one_of(TRAY_ICONS, "white")),
    ("advanced", "poll_interval_ms", _clamp(250, 10_000)),
    ("advanced", "log_level", _one_of(LOG_LEVELS, "INFO", upper=True)),
    # 0 switches both checks off, so it has to stay reachable.
    ("advanced", "idle_grace_s", _clamp(0, 3600)),
    ("advanced", "position_stall_s", _clamp(0, 600)),
    ("advanced", "language", _check_language),
    ("advanced", "time_format", _one_of(("system", "12h", "24h"), "system")),
    ("advanced", "time_zone", _check_time_zone),
    ("advanced", "hover_scroll_ms", _range_or(1500, 0, 10_000)),
    ("update", "last_check_ts", _check_timestamp),
    ("history", "max_entries", _clamp(1, HISTORY_LIMIT_MAX)),
    ("history", "window_width", _range_or(0, 1, _WINDOW_SIZE_MAX)),
    ("history", "window_height", _range_or(0, 1, _WINDOW_SIZE_MAX)),
)


@dataclass
class Config:
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    lastfm: LastfmConfig = field(default_factory=LastfmConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            cfg = cls()
            try:
                cfg.save(path)
            except OSError as e:
                # A full disk or a read-only home must not stop Refrain from
                # starting; there is no window yet to say so in.
                log.warning(
                    "Cannot write a default config to %s (%s); running on defaults", path, e
                )
                return cfg
            log.info("Created default config at %s", path)
            return cfg
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
            return cls.from_dict(data)
        except Exception as e:
            # The next save would write defaults over every value in it, so
            # keep the file where the user can still fix it.
            broken = path.with_name(path.name + ".broken")
            try:
                path.replace(broken)
            except OSError:
                broken = path
            log.warning(
                "Config at %s unreadable (%s), using defaults; the file is kept as %s",
                path,
                e,
                broken,
                exc_info=True,
            )
            return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        data = _with_legacy_defaults(data)
        return cls(
            discord=_construct(DiscordConfig, data.get("discord")),
            sources=_construct(SourcesConfig, data.get("sources")),
            privacy=_construct(PrivacyConfig, data.get("privacy")),
            behavior=_construct(BehaviorConfig, data.get("behavior")),
            advanced=_construct(AdvancedConfig, data.get("advanced")),
            update=_construct(UpdateConfig, data.get("update")),
            lastfm=_construct(LastfmConfig, data.get("lastfm")),
            history=_construct(HistoryConfig, data.get("history")),
        ).validate()

    def validate(self) -> Config:
        """Replace values that have the right type but make no sense, in place.

        One warning per field. Never writes the file: the next save does, and
        until then a hand-edit stays visible as the user wrote it.
        """
        for section, key, check in _RULES:
            part = getattr(self, section)
            value = getattr(part, key)
            try:
                fixed, problem = check(value)
            except (TypeError, ValueError, AttributeError):
                fixed, problem = _field_default(type(part), key), "has the wrong type"
            if problem is None:
                if fixed != value:
                    setattr(part, key, fixed)
                continue
            if fixed == value:
                log.warning("Config: %s.%s = %r %s; kept as is", section, key, value, problem)
            else:
                setattr(part, key, fixed)
                log.warning(
                    "Config: %s.%s = %r %s; using %r instead", section, key, value, problem, fixed
                )
        return self

    def to_dict(self) -> dict[str, Any]:
        lastfm = asdict(self.lastfm)
        # The Last.fm shared secret and session key live in the OS
        # keyring (refrain.secrets_store), never in config.toml. Forcing
        # them empty here also makes the comment-preserving writer
        # rewrite any legacy plaintext line to `… = ""` on the next save.
        lastfm["shared_secret"] = ""
        lastfm["session_key"] = ""
        return {
            "discord": asdict(self.discord),
            "sources": asdict(self.sources),
            "privacy": asdict(self.privacy),
            "behavior": asdict(self.behavior),
            "advanced": asdict(self.advanced),
            "update": asdict(self.update),
            "lastfm": lastfm,
            "history": asdict(self.history),
        }

    def save(self, path: Path | None = None) -> None:
        with _SAVE_LOCK:
            self._save_unlocked(path or config_path())

    def _save_unlocked(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        # Comment-/unknown-key-preserving write: when a config file
        # already exists, rewrite only the `key = value` lines Refrain
        # owns and leave user comments, blank lines, ordering, and any
        # keys a newer Refrain wrote (that this one downgraded from)
        # intact — the silent daily update check saves too.
        text = _serialize(payload)
        if path.exists():
            try:
                text = _merge_into_existing(path.read_text(encoding="utf-8"), payload)
            except OSError as e:
                log.warning(
                    "Config: could not read %s for comment-preserving save (%s); "
                    "rewriting from scratch",
                    path,
                    e,
                )
            except Exception:
                log.exception("Config: comment-preserving merge failed; rewriting from scratch")
        # Atomic write: tmp file + os.replace. Without this, a crash or
        # power-cut between truncate-and-write would leave an empty or
        # half-written config — and refrain falls back to defaults on
        # malformed TOML, silently losing every setting the user picked.
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            # Disk full / permission denied / read-only fs — clean up
            # the partial tmp file before re-raising so we don't leak
            # a stale .tmp next to the real config.
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()
            raise
        # Defense in depth: config.toml never holds secrets (they're in
        # the keyring) but it does hold the Discord/Last.fm api_key and
        # the user's listening-related preferences — keep it owner-only.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        log.info("Config saved to %s", path)


def _serialize(data: dict[str, Any]) -> str:
    """Minimal TOML writer for our flat-section schema (no nested tables)."""
    lines: list[str] = []
    for section, body in data.items():
        lines.append(f"[{section}]")
        for k, v in body.items():
            lines.append(f"{k} = {_format_value(v)}")
        lines.append("")
    return "\n".join(lines)


def _format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # TOML basic-string escaping: backslash + double-quote are
    # required by the spec; tab / newline / carriage-return are
    # added so an accidentally-multiline value (paste from a wider
    # field) doesn't produce a half-line that tomllib would then
    # reject on next load and trip the "config unreadable, using
    # defaults" fallback.
    s = (
        str(v)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    s = _CONTROL_RE.sub(lambda m: f"\\u{ord(m.group()):04x}", s)
    return f'"{s}"'


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_-]*)\s*=")


def _merge_into_existing(existing: str, data: dict[str, Any]) -> str:
    """Rewrite only the ``key = value`` lines Refrain owns, passing
    everything else through verbatim.

    ``data`` is ``Config.to_dict()`` — every known section and key.
    Owned keys already present in the file are updated in place;
    owned keys missing from a section are appended at the end of that
    section; sections absent entirely are appended at the end of the
    file. Comments, blank lines, line order, and any section/key not in
    ``data`` (e.g. a key written by a newer Refrain) are preserved as-is.

    Known limitation: an inline trailing comment on an owned key
    (``client_id = "x"  # note``) is not preserved — distinguishing a
    real comment from a ``#`` inside the value needs a full TOML parser,
    and Refrain's schema is flat scalars edited almost entirely through
    the GUI. Whole-line comments (the common case) survive.
    """
    remaining: dict[str, dict] = {s: dict(kv) for s, kv in data.items()}
    out: list[str] = []
    current: str | None = None

    def _flush(section: str | None) -> None:
        # Emit owned keys for `section` that never appeared in the file,
        # so they land *inside* that section rather than at EOF.
        if section is None or section not in remaining:
            return
        for k, v in remaining[section].items():
            out.append(f"{k} = {_format_value(v)}")
        remaining.pop(section, None)

    for line in existing.splitlines():
        m_sec = _SECTION_RE.match(line)
        if m_sec:
            # Section change — flush the section that just ended first.
            _flush(current)
            current = m_sec.group(1).strip()
            out.append(line)
            continue
        m_key = _KEY_RE.match(line)
        if m_key and current in remaining and m_key.group(2) in remaining[current]:
            indent, key = m_key.group(1), m_key.group(2)
            value = remaining[current].pop(key)
            out.append(f"{indent}{key} = {_format_value(value)}")
            continue
        out.append(line)

    _flush(current)
    # Sections that never appeared in the file at all (data order).
    for section in data:
        if section in remaining:
            out.append(f"[{section}]")
            for k, v in remaining[section].items():
                out.append(f"{k} = {_format_value(v)}")
            out.append("")
            remaining.pop(section, None)

    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text
