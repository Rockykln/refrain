"""TOML config: load on startup, save on Apply, sensible defaults."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from refrain.paths import config_path

log = logging.getLogger(__name__)


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

    def client_id_for(self, source: str) -> str:
        """Return the per-source client_id, falling back to the default."""
        if source == "mpris" and self.client_id_mpris:
            return self.client_id_mpris
        if source == "bluetooth" and self.client_id_bluetooth:
            return self.client_id_bluetooth
        return self.client_id


DEFAULT_BROWSER_HINTS = (
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
class Config:
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)

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
            discord=DiscordConfig(**(data.get("discord") or {})),
            sources=SourcesConfig(**(data.get("sources") or {})),
            privacy=PrivacyConfig(**(data.get("privacy") or {})),
            behavior=BehaviorConfig(**(data.get("behavior") or {})),
            advanced=AdvancedConfig(**(data.get("advanced") or {})),
            update=UpdateConfig(**(data.get("update") or {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "discord": asdict(self.discord),
            "sources": asdict(self.sources),
            "privacy": asdict(self.privacy),
            "behavior": asdict(self.behavior),
            "advanced": asdict(self.advanced),
            "update": asdict(self.update),
        }

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp file + os.replace. Without this, a crash or
        # power-cut between truncate-and-write would leave an empty or
        # half-written config — and refrain falls back to defaults on
        # malformed TOML, silently losing every setting the user picked.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(_serialize(self.to_dict()), encoding="utf-8")
        os.replace(tmp, path)
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
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
