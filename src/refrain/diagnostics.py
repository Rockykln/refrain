"""What Refrain runs on and how it is set up, as plain text for a bug report.

Nothing here identifies the person or what they listen to: no song titles, no
Last.fm credentials, no Bluetooth addresses, no Discord Application ID — only
whether each of those is set. Home directories are written as ``~``, and the
time zone is left out: it says where someone lives.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from refrain import __version__
from refrain.config import Config
from refrain.paths import config_path, state_dir


def _os_name() -> str:
    try:
        fields = dict(
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
    except OSError:
        return platform.platform()
    return fields.get("PRETTY_NAME", "").strip('"') or platform.platform()


def _qt_versions() -> tuple[str, str]:
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        return pyside_version, qVersion()
    except Exception:
        return "—", "—"


def _install_type() -> str:
    try:
        from refrain.updater import detect_install_type

        return detect_install_type()
    except Exception:
        return "unknown"


def _short(path: Path) -> str:
    """A path without the home directory, which carries the user's name."""
    home = str(Path.home())
    text = str(path)
    return f"~{text[len(home) :]}" if text.startswith(home) else text


def _yes_no(value: object) -> str:
    return "yes" if value else "no"


def report(config: Config) -> str:
    """A block a user can paste into an issue."""
    pyside_version, qt_version = _qt_versions()
    crash_log = state_dir() / "crash.log"
    lines = [
        f"Refrain {__version__} ({_install_type()})",
        f"OS: {_os_name()}",
        f"Kernel: {platform.release()}",
        f"Python: {platform.python_version()}",
        f"PySide6: {pyside_version} / Qt: {qt_version}",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '—')}"
        f" · session: {os.environ.get('XDG_SESSION_TYPE', '—')}",
        f"Locale: {os.environ.get('LANG', '—')}"
        f" · language setting: {config.advanced.language}"
        f" · clock: {config.advanced.time_format}",
        "",
        f"Sources: MPRIS {_yes_no(config.sources.mpris_enabled)}"
        f" · Bluetooth {_yes_no(config.sources.bluetooth_enabled)}"
        f" (device chosen: {_yes_no(config.sources.bluetooth_device)})",
        f"Browser hints: {config.sources.browser_hints}",
        f"Discord: Application ID set {_yes_no(config.discord.client_id)}"
        f" · per source {_yes_no(config.discord.client_id_mpris or config.discord.client_id_bluetooth)}"
        f" · all clients {_yes_no(config.discord.all_clients)}"
        f" · name lookup {_yes_no(config.discord.resolve_app_name)}",
        f"Privacy: {config.privacy.mode} (resumes to {config.privacy.resume_mode})",
        f"Last.fm: connected {_yes_no(config.lastfm.session_key)}"
        f" · scrobbling {_yes_no(config.lastfm.enabled)}"
        f" · now playing {_yes_no(config.lastfm.scrobble_now_playing)}",
        f"Recently played: {_yes_no(config.history.enabled)} · keeps {config.history.max_entries}",
        f"Notifications: {_yes_no(config.behavior.notifications)}"
        f" · cover art {_yes_no(config.behavior.cover_art)}"
        f" · buttons {_yes_no(config.behavior.show_buttons)}"
        f" · autostart {_yes_no(config.behavior.autostart)}",
        f"Advanced: poll {config.advanced.poll_interval_ms} ms"
        f" · log level {config.advanced.log_level}"
        f" · developer mode {_yes_no(config.advanced.developer_mode)}",
        f"Updates: auto-check {_yes_no(config.update.auto_check)}",
        "",
        f"Config: {_short(config_path())}",
        f"State: {_short(state_dir())} · crash reports: {_crash_reports(crash_log)}",
    ]
    return "\n".join(lines)


def _crash_reports(path: Path) -> int:
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("Fatal Python error")
    except OSError:
        return 0
