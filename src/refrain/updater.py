"""GitHub-based update checker.

Detects the install type (AppImage / pip / Flatpak / AUR / system / dev),
queries the GitHub Releases API for the latest tag, and exposes a typed
``ReleaseInfo`` plus an ``apply_update()`` action whose behavior is install-
type-specific:

- **AppImage**: downloads the new ``*.AppImage`` from the release assets and
  replaces the running binary in place (atomic rename), then prompts restart.
- **pip / venv**: runs ``pip install --upgrade refrain`` via the same Python
  interpreter the daemon is running on.
- **Flatpak / AUR / system**: never modifies system files; surfaces the
  distro-specific upgrade command for the user to run themselves.

The HTTP client is plain ``urllib`` so the module has no extra deps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from refrain import __version__

log = logging.getLogger(__name__)

GITHUB_REPO = "Rockykln/refrain"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_USER_AGENT = f"Refrain/{__version__} (+https://github.com/{GITHUB_REPO})"
_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Install-type detection
# ---------------------------------------------------------------------------


def detect_install_type() -> str:
    """Returns one of: ``appimage``, ``flatpak``, ``aur``, ``system``, ``pip``, ``dev``.

    Detection is best-effort — when ambiguous we err on the side that *won't*
    auto-modify system files.
    """
    if os.environ.get("APPIMAGE"):
        return "appimage"
    if os.environ.get("FLATPAK_ID") or os.environ.get("container") == "flatpak":
        return "flatpak"

    # Canonical venv detection — works regardless of whether sys.executable is
    # the venv's symlink or has been resolved to the underlying system python.
    if sys.prefix != sys.base_prefix:
        return "pip"

    # Use the unresolved path so a venv's symlinked python isn't classified
    # as "system" just because its target lives in /usr/.
    exe_str = sys.executable
    if "/.local/" in exe_str or ".venv" in exe_str or "/venv/" in exe_str:
        return "pip"

    if exe_str.startswith(("/usr/", "/opt/")):
        # Distro-managed Python interpreter; check pacman for AUR ownership
        if shutil.which("pacman"):
            try:
                result = subprocess.run(
                    ["pacman", "-Qo", "/usr/bin/refrain"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0 and "refrain" in result.stdout:
                    return "aur"
            except Exception:
                pass
        return "system"

    # Fallback: source checkout (editable install) or unknown
    project_root = Path(__file__).resolve().parents[2]
    if (project_root / "pyproject.toml").exists() and (project_root / ".git").exists():
        return "dev"
    return "pip"


# ---------------------------------------------------------------------------
# Version compare
# ---------------------------------------------------------------------------


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _parse_version(s: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.match(s.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer(remote: str, local: str) -> bool:
    r = _parse_version(remote)
    l = _parse_version(local)  # noqa: E741 — readable as "local"
    if r is None or l is None:
        return False
    return r > l


# ---------------------------------------------------------------------------
# Release info
# ---------------------------------------------------------------------------


@dataclass
class ReleaseInfo:
    tag: str  # e.g. "v0.2.0"
    version: str  # "0.2.0"
    name: str  # "Refrain v0.2.0"
    body: str  # Markdown-formatted release notes
    html_url: str  # https://github.com/.../releases/tag/v0.2.0
    appimage_url: str | None = None  # browser_download_url of the AppImage asset
    appimage_size: int = 0
    assets: list[dict] = field(default_factory=list)

    @property
    def is_newer_than_current(self) -> bool:
        return is_newer(self.version, __version__)


def check_latest_release(timeout_s: float = _TIMEOUT_S) -> ReleaseInfo | None:
    """Hit GitHub's Releases API once. Returns None on any error."""
    if not RELEASES_API.startswith("https://"):
        return None
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # nosec B310
            data = json.load(r)
    except Exception as e:
        log.debug("Update check failed: %s", e)
        return None

    tag = str(data.get("tag_name", "")).strip()
    if not tag:
        return None
    version = tag.lstrip("v")

    appimage_url = None
    appimage_size = 0
    assets = data.get("assets", []) or []
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.lower().endswith(".appimage"):
            appimage_url = str(asset.get("browser_download_url", ""))
            appimage_size = int(asset.get("size", 0) or 0)
            break

    return ReleaseInfo(
        tag=tag,
        version=version,
        name=str(data.get("name", "") or tag),
        body=str(data.get("body", "") or ""),
        html_url=str(data.get("html_url", "")),
        appimage_url=appimage_url,
        appimage_size=appimage_size,
        assets=assets,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@dataclass
class UpdateResult:
    success: bool
    message: str
    needs_restart: bool = False


def apply_update(release: ReleaseInfo, install_type: str | None = None) -> UpdateResult:
    """Type-aware update. Never modifies system files."""
    install_type = install_type or detect_install_type()

    if install_type == "appimage":
        return _apply_appimage(release)
    if install_type == "pip":
        return _apply_pip()
    if install_type == "dev":
        return UpdateResult(
            success=False,
            message="This is a development checkout — pull manually with "
            "`git pull` and reinstall with `pip install -e .`.",
        )
    if install_type == "flatpak":
        return UpdateResult(
            success=False,
            message="Flatpak install detected. Update via:\n\n"
            "    flatpak update io.github.Rockykln.Refrain",
        )
    if install_type == "aur":
        return UpdateResult(
            success=False,
            message="AUR install detected. Update via your AUR helper:\n\n    yay -Syu refrain",
        )
    return UpdateResult(
        success=False,
        message="System install detected. Use your distribution's package "
        "manager to update Refrain.",
    )


def _apply_appimage(release: ReleaseInfo) -> UpdateResult:
    appimage_path = os.environ.get("APPIMAGE")
    if not appimage_path:
        return UpdateResult(
            success=False,
            message="APPIMAGE environment variable is missing — Refrain wasn't "
            "launched from an AppImage after all.",
        )
    if not release.appimage_url:
        return UpdateResult(
            success=False,
            message="The latest release doesn't ship an .AppImage asset.",
        )

    target = Path(appimage_path)
    tmp = target.with_suffix(target.suffix + ".new")

    try:
        req = urllib.request.Request(
            release.appimage_url,
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:  # nosec B310
            shutil.copyfileobj(r, out)
        # AppImages must be executable to run; 0o755 matches what `chmod +x`
        # produces and what `linuxdeploy --output appimage` writes. Atomic
        # in-place replacement on Linux even while the old file is mmap'd.
        os.chmod(tmp, 0o755)  # nosec B103  lgtm[py/overly-permissive-file]
        os.replace(tmp, target)
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return UpdateResult(success=False, message=f"AppImage download failed: {e}")

    return UpdateResult(
        success=True,
        message=f"Downloaded Refrain {release.version}. Restart Refrain to use it.",
        needs_restart=True,
    )


def _apply_pip() -> UpdateResult:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "refrain"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as e:
        return UpdateResult(success=False, message=f"pip invocation failed: {e}")

    if result.returncode != 0:
        return UpdateResult(
            success=False,
            message=f"pip exited with code {result.returncode}:\n{result.stderr.strip()}",
        )
    return UpdateResult(
        success=True,
        message="pip upgrade complete. Restart Refrain to load the new version.",
        needs_restart=True,
    )
