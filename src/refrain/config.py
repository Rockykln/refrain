"""TOML config: load on startup, save on Apply, sensible defaults."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from refrain.paths import config_path

log = logging.getLogger(__name__)


def _coerce_value(annot, name: str, value):
    """Best-effort coercion of a single config value to its declared
    field type. Raises TypeError when the value can't be made to fit.

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
            dropped.append(k)
            continue
        try:
            accepted[k] = _coerce_value(hints.get(k), k, v)
        except (TypeError, ValueError) as e:
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
    # not just the first one found. Discord and Vencord/Vesktop are
    # separate processes with separate IPC sockets, so a status sent to
    # one is invisible in the other. Off by default: one client is the
    # normal case, and each extra connection is another IPC write per
    # track change.
    all_clients: bool = False

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


@dataclass
class BehaviorConfig:
    autostart: bool = False
    notifications: bool = True
    cover_art: bool = True
    show_buttons: bool = True
    # How long to wait after a track change before firing the desktop
    # notification. 0 = fire immediately; the retry loop in
    # `_fire_pending_notify` still polls up to 2 s for the cover image
    # to land before falling back to the brand fallback. Previously
    # 1500 ms / 600 ms — both felt sluggish to users; the retry loop
    # alone is enough to wait for cover art when needed.
    notify_delay_ms: int = 0
    # Set to True after the first-run wizard runs once. Prevents the
    # welcome dialog from re-appearing on every launch.
    first_run_complete: bool = False


@dataclass
class AdvancedConfig:
    poll_interval_ms: int = 500
    log_level: str = "INFO"
    # On-disk cover-art cache cap. Older files are pruned at startup when
    # the count exceeds this. ~50-150 KB per cover.
    cover_cache_size: int = 200
    # Idle-detection grace window (in seconds). When the *same* track has
    # been "playing" for longer than its own duration plus this grace,
    # Refrain assumes the source is dangling (e.g. browser tab closed
    # without releasing the MPRIS handle) and clears the Discord status.
    # Set to 0 to disable idle detection entirely.
    idle_grace_s: int = 30
    # Override UI language. "system" follows QLocale.system(); explicit
    # codes ("en", "de", "fr", …) force a specific translation. Takes
    # effect after restarting Refrain — the QTranslator is installed
    # once at app startup.
    language: str = "system"


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


@dataclass
class Config:
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    lastfm: LastfmConfig = field(default_factory=LastfmConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            log.info("Created default config at %s", path)
            return cfg
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
            return cls.from_dict(data)
        except Exception as e:
            log.warning(
                "Config at %s unreadable (%s), using defaults",
                path,
                e,
                exc_info=True,
            )
            return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        return cls(
            discord=_construct(DiscordConfig, data.get("discord")),
            sources=_construct(SourcesConfig, data.get("sources")),
            privacy=_construct(PrivacyConfig, data.get("privacy")),
            behavior=_construct(BehaviorConfig, data.get("behavior")),
            advanced=_construct(AdvancedConfig, data.get("advanced")),
            update=_construct(UpdateConfig, data.get("update")),
            lastfm=_construct(LastfmConfig, data.get("lastfm")),
        )

    def to_dict(self) -> dict[str, Any]:
        lastfm = asdict(self.lastfm)
        # SECURITY: the Last.fm shared secret and session key are
        # credentials — they are NEVER written to config.toml. They
        # live in the OS keyring (see refrain.secrets_store). Forcing
        # them empty here also means the comment-preserving writer
        # rewrites any legacy plaintext line to `… = ""` on the next
        # save, scrubbing secrets that an older build left on disk.
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
        }

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        # Comment-/unknown-key-preserving write: when a config file
        # already exists, rewrite only the `key = value` lines Refrain
        # owns and leave user comments, blank lines, ordering, and any
        # keys a newer Refrain wrote (that this one downgraded from)
        # intact. The old behaviour re-serialised from scratch on every
        # save — including the silent daily update-check stamping
        # `last_check_ts` — which quietly nuked hand-added comments.
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
    return f'"{s}"'


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
