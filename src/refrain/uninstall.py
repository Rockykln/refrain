"""Full uninstall — wipe every file and credential Refrain created.

Removing the *package* itself is install-type-specific (pip / pipx /
AUR / Flatpak / AppImage) and a running binary can't reliably delete
itself, so we print the exact command for the detected install type.
The part Refrain *can* do on any distro / install method — and does
here — is delete everything it wrote:

- ``$XDG_CONFIG_HOME/refrain/``  (config.toml, secrets fallback)
- ``$XDG_STATE_HOME/refrain/``   (logs, scrobble queue)
- ``$XDG_CACHE_HOME/refrain/``   (cover-art cache)
- the autostart entry, the menu ``.desktop`` + icon
- the Last.fm credentials in the OS keyring

Importable without Qt or a D-Bus connection (it runs as a one-shot
``--uninstall`` before the GUI starts, and the tests exercise it
hermetically).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from refrain.paths import autostart_path, cache_dir, config_dir, state_dir

log = logging.getLogger(__name__)


def _user_apps_desktop() -> Path:
    return Path.home() / ".local" / "share" / "applications" / "refrain.desktop"


def _user_icon_svg() -> Path:
    return (
        Path.home()
        / ".local"
        / "share"
        / "icons"
        / "hicolor"
        / "scalable"
        / "apps"
        / "refrain.svg"
    )


def collect_paths() -> list[Path]:
    """Every filesystem path a full uninstall would remove that
    currently exists (directories included). Order: data dirs first,
    then the desktop-integration files."""
    candidates = [
        config_dir(),
        state_dir(),
        cache_dir(),
        autostart_path(),
        _user_apps_desktop(),
        _user_icon_svg(),
    ]
    return [p for p in candidates if p.exists()]


def removal_command(install_type: str, appimage_path: str | None = None) -> str:
    """The command that removes the *package* for ``install_type``.

    Refrain can't uninstall its own running binary; this is the
    one-liner the user runs afterwards.
    """
    if install_type == "appimage":
        target = appimage_path or "<the Refrain .AppImage file>"
        return f"rm {target}"
    if install_type == "pip":
        return "pip uninstall refrain"
    if install_type == "pipx":
        return "pipx uninstall refrain"
    if install_type == "flatpak":
        return "flatpak uninstall io.github.Rockykln.Refrain"
    if install_type == "aur":
        helper = next(
            (h for h in ("yay", "paru", "trizen", "pikaur") if shutil.which(h)),
            None,
        )
        return f"{helper} -R refrain" if helper else "sudo pacman -R refrain"
    if install_type == "dev":
        return "delete the cloned source checkout (this is a dev install)"
    return "use your distribution's package manager to remove 'refrain'"


@dataclass
class UninstallReport:
    removed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    secrets_purged: bool = False


def _purge_secrets(store=None) -> bool:
    """Delete the Last.fm credentials from the keyring (or fallback
    file). Best-effort — never raises. ``store`` is injectable for
    tests so the suite never touches the real keyring."""
    try:
        from refrain.secrets_store import (
            LASTFM_SESSION_KEY,
            LASTFM_SHARED_SECRET,
        )
        from refrain.secrets_store import _default as default_store

        s = store or default_store
        s.delete(LASTFM_SHARED_SECRET)
        s.delete(LASTFM_SESSION_KEY)
        return True
    except Exception as e:
        log.warning("Could not purge Last.fm credentials: %s", e)
        return False


def purge(secret_store=None) -> UninstallReport:
    """Delete every file + credential Refrain created. Idempotent and
    failure-tolerant: a missing path is fine, an un-removable one is
    recorded in ``failed`` but never raises (a half-done uninstall
    must still report and exit cleanly)."""
    report = UninstallReport()
    for p in collect_paths():
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
            report.removed.append(str(p))
            log.info("Uninstall removed %s", p)
        except OSError as e:
            report.failed.append(f"{p}: {e}")
            log.warning("Uninstall could not remove %s: %s", p, e)
    report.secrets_purged = _purge_secrets(secret_store)
    return report
