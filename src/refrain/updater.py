"""GitHub-based update checker.

Detects the install type (AppImage / pip / Flatpak / AUR / system / dev),
queries the GitHub Releases API for the latest tag, and exposes a typed
``ReleaseInfo`` plus an ``apply_update()`` action whose behavior is install-
type-specific:

- **AppImage**: checks the Ed25519 signature on the release's ``SHA256SUMS``,
  downloads the new ``*.AppImage``, checks its hash against that file and
  replaces the running binary in place (atomic rename), then prompts restart.
- **pip / venv**: runs ``pip install --upgrade refrain`` via the same Python
  interpreter the daemon is running on.
- **Flatpak / AUR / system**: never modifies system files; surfaces the
  distro-specific upgrade command for the user to run themselves.

The HTTP client is plain ``urllib`` so the module has no extra deps.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import shlex
import shutil
import site
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from refrain import __version__, dev_metrics, ed25519

# Match http(s) URLs not already inside <>, [text](…), or `code`.
_BARE_URL_RE = re.compile(
    r"(?<![<\[(`])"
    r"(https?://[^\s<>\[\]()`]+)"
    r"(?![>\])`])"
)


def prepare_release_notes(body: str | None) -> str:
    """Wrap bare http(s) URLs in Markdown inline-link syntax.

    Several Markdown renderers (Qt's QTextBrowser among them) break bare
    URLs at sequences like ``...``, which is exactly what GitHub compare
    links contain (``/compare/v0.1.0...v0.1.1``). v0.1.2 wrapped them in
    ``<URL>`` autolink syntax, which works in QTextBrowser today but is
    more sensitive to renderer quirks. ``[URL](URL)`` (CommonMark inline
    link with the URL as both text and target) is universally
    interpreted as one indivisible token across every renderer we've
    checked — Qt, GitHub, KDialog, mdcat, glow.
    """
    if not body:
        return "_No release notes provided._"
    return _BARE_URL_RE.sub(r"[\1](\1)", body)


log = logging.getLogger(__name__)

GITHUB_REPO = "Rockykln/refrain"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_USER_AGENT = f"Refrain/{__version__} (+https://github.com/{GITHUB_REPO})"
_TIMEOUT_S = 10
_DOWNLOAD_PREFIX = f"https://github.com/{GITHUB_REPO}/releases/download/"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
# Hex Ed25519 public key whose signature an AppImage self-update requires on
# SHA256SUMS; the private key never leaves the owner's machine. Empty: every
# AppImage self-update is refused. See packaging/README.md.
RELEASE_PUBLIC_KEY = "b4ffe3f4c0e79c94d91b3c13ddc5d0b0e26159ab66a1f0a78ae35168ad2a516c"
# Far below any AppImage that can carry Python and Qt.
_MIN_APPIMAGE_BYTES = 1024 * 1024
_MAX_SUMS_BYTES = 64 * 1024
_SUMS_ASSET = "SHA256SUMS"
_SIG_ASSET = "SHA256SUMS.sig"
_MAX_SIG_BYTES = 1024
_ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


def _machine_arch() -> str:
    machine = platform.machine().lower()
    return _ARCH_ALIASES.get(machine, machine)


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"refusing redirect to non-https URL {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_download_opener = urllib.request.build_opener(_HttpsOnlyRedirects)


def _open_download(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return _download_opener.open(req, timeout=timeout)  # nosec B310


# ---------------------------------------------------------------------------
# Install-type detection
# ---------------------------------------------------------------------------


def detect_install_type() -> str:
    """Returns one of: ``appimage``, ``flatpak``, ``pipx``, ``aur``,
    ``system``, ``pip``, ``dev``.

    When in doubt, the answer is one that won't modify system files.
    """
    if os.environ.get("APPIMAGE"):
        return "appimage"
    # `container=flatpak` is lowercase by systemd / Flatpak convention,
    # so SIM112's "should be uppercase" advice doesn't apply here.
    if os.environ.get("FLATPAK_ID") or os.environ.get("container") == "flatpak":  # noqa: SIM112
        return "flatpak"

    # Before the venv→pip branch: pip must never upgrade over a checkout.
    project_root = Path(__file__).resolve().parents[2]
    if (project_root / "pyproject.toml").exists() and (project_root / ".git").exists():
        return "dev"

    # pipx MUST be checked before the generic venv→pip branch: a pipx
    # app lives in its own venv (sys.prefix != base_prefix) but that
    # venv has *no pip*, so `python -m pip install -U` fails with
    # "No module named pip". The correct upgrade path is
    # `pipx upgrade refrain`. Canonical layout is
    # `<PIPX_HOME>/venvs/<app>/...` (default ~/.local/share/pipx);
    # the stable marker is the `/pipx/venvs/` path segment.
    exe_str = sys.executable
    if "/pipx/venvs/" in exe_str or "/pipx/venvs/" in sys.prefix:
        return "pipx"

    # Canonical venv detection — works regardless of whether sys.executable is
    # the venv's symlink or has been resolved to the underlying system python.
    if sys.prefix != sys.base_prefix:
        return "pip"

    # Use the unresolved path so a venv's symlinked python isn't classified
    # as "system" just because its target lives in /usr/.
    if "/.local/" in exe_str or ".venv" in exe_str or "/venv/" in exe_str:
        return "pip"

    # `pip install --user` runs the system interpreter, but the package lives
    # in the user's site-packages, which no package manager owns.
    if _in_user_site():
        return "pip"

    if exe_str.startswith(("/usr/", "/opt/")):
        # Distro-managed Python interpreter; check pacman for AUR ownership
        if shutil.which("pacman"):
            with contextlib.suppress(Exception):
                result = subprocess.run(
                    ["pacman", "-Qo", "/usr/bin/refrain"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0 and "refrain" in result.stdout:
                    return "aur"
        return "system"

    return "pip"


def _in_user_site() -> bool:
    try:
        user_site = Path(site.getusersitepackages()).resolve()
    except Exception:
        return False
    return Path(__file__).resolve().is_relative_to(user_site)


# ---------------------------------------------------------------------------
# Version compare
# ---------------------------------------------------------------------------


_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:(\+.*)|[-.]?((?:dev|a|b|rc|pre).*))?$")


def _parse_version(s: str) -> tuple[int, int, int, int] | None:
    """``(major, minor, patch, final)`` — 0.5.3.dev0 sorts before 0.5.3."""
    m = _VERSION_RE.match(s.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if m.group(5) else 1


def is_newer(remote: str, local: str) -> bool:
    r = _parse_version(remote)
    loc = _parse_version(local)
    if r is None or loc is None:
        return False
    return r > loc


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
    appimage_name: str = ""
    sha256sums_url: str | None = None
    sha256sums_sig_url: str | None = None

    @property
    def is_newer_than_current(self) -> bool:
        return is_newer(self.version, __version__)


def check_latest_release(timeout_s: float = _TIMEOUT_S) -> ReleaseInfo | None:
    """Hit GitHub's Releases API once. Returns None on any error."""
    if not RELEASES_API.startswith("https://"):
        log.warning("RELEASES_API is not https — refusing to fetch: %s", RELEASES_API)
        return None
    log.debug("Querying GitHub Releases API: %s (timeout=%ss)", RELEASES_API, timeout_s)
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with dev_metrics.network("github"):
            with urllib.request.urlopen(req, timeout=timeout_s) as r:  # nosec B310
                data = json.load(r)
    except Exception as e:
        log.info("Update check failed: %s", e)
        return None

    tag = str(data.get("tag_name", "")).strip()
    if not tag:
        log.debug("Release JSON had no tag_name; payload keys: %s", list(data)[:10])
        return None
    version = tag.lstrip("v")

    arch_suffix = f"-{_machine_arch()}.appimage"
    appimage_url = None
    appimage_name = ""
    appimage_size = 0
    sha256sums_url = None
    sha256sums_sig_url = None
    assets = data.get("assets", []) or []
    for asset in assets:
        name = str(asset.get("name", ""))
        if name == _SUMS_ASSET:
            sha256sums_url = str(asset.get("browser_download_url", ""))
        elif name == _SIG_ASSET:
            sha256sums_sig_url = str(asset.get("browser_download_url", ""))
        elif appimage_url is None and name.lower().endswith(arch_suffix):
            appimage_url = str(asset.get("browser_download_url", ""))
            appimage_name = name
            appimage_size = int(asset.get("size", 0) or 0)
            log.debug("Release %s carries AppImage: %s (%d bytes)", tag, name, appimage_size)

    log.debug(
        "Latest release: tag=%s asset_count=%d appimage_present=%s",
        tag,
        len(assets),
        bool(appimage_url),
    )
    return ReleaseInfo(
        tag=tag,
        version=version,
        name=str(data.get("name", "") or tag),
        body=str(data.get("body", "") or ""),
        html_url=str(data.get("html_url", "")),
        appimage_url=appimage_url,
        appimage_size=appimage_size,
        assets=assets,
        appimage_name=appimage_name,
        sha256sums_url=sha256sums_url,
        sha256sums_sig_url=sha256sums_sig_url,
    )


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@dataclass
class UpdateResult:
    success: bool
    message: str
    needs_restart: bool = False
    cancelled: bool = False


class _DownloadCancelled(Exception):
    """Internal — raised from the chunked download loop on user cancel."""


class UnverifiedReleaseError(Exception):
    """The release's SHA256SUMS is missing or not signed with RELEASE_PUBLIC_KEY."""


def _fetch_release_file(url: str | None, name: str, limit: int) -> bytes:
    if not url:
        raise UnverifiedReleaseError(f"the release has no {name}")
    if not url.startswith(_DOWNLOAD_PREFIX):
        raise UnverifiedReleaseError(f"{name} is not served from the Refrain releases: {url}")
    with _open_download(url, _TIMEOUT_S) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UnverifiedReleaseError(f"{name} is larger than {limit} bytes")
    return data


def _expected_sha256(release: ReleaseInfo) -> str:
    """The AppImage's digest from the release's signed SHA256SUMS."""
    try:
        public_key = bytes.fromhex(RELEASE_PUBLIC_KEY)
    except ValueError:
        public_key = b""
    if len(public_key) != 32:
        raise UnverifiedReleaseError("this build of Refrain has no release signing key")
    sums = _fetch_release_file(release.sha256sums_url, _SUMS_ASSET, _MAX_SUMS_BYTES)
    sig_text = _fetch_release_file(release.sha256sums_sig_url, _SIG_ASSET, _MAX_SIG_BYTES)
    try:
        signature = base64.b64decode(sig_text.strip(), validate=True)
    except binascii.Error:
        signature = b""
    if not ed25519.verify(public_key, sums, signature):
        raise UnverifiedReleaseError(f"the signature on {_SUMS_ASSET} is not valid")
    # A validly signed SHA256SUMS of an older release must not pass as this one.
    if not release.appimage_name.startswith(f"Refrain-{release.version}-"):
        raise UnverifiedReleaseError(
            f"{release.appimage_name} is not the AppImage of version {release.version}"
        )
    for line in sums.decode("utf-8", "replace").splitlines():
        digest, _, name = line.strip().partition(" ")
        if name.strip().lstrip("*") == release.appimage_name and re.fullmatch(
            r"[0-9a-fA-F]{64}", digest
        ):
            return digest.lower()
    raise UnverifiedReleaseError(f"{_SUMS_ASSET} has no entry for {release.appimage_name}")


def cleanup_orphan_downloads() -> None:
    """Remove a leftover ``.AppImage.new`` from a previously interrupted update.

    Normally ``_apply_appimage`` removes its ``tmp`` file on any error
    path, but a hard kill (SIGKILL, OOM, power loss) bypasses that
    cleanup and leaves the partial download next to the running binary
    forever. Calling this once at startup makes the situation
    self-healing.
    """
    appimage_path = os.environ.get("APPIMAGE")
    if not appimage_path:
        return
    target = Path(appimage_path)
    tmp = target.with_suffix(target.suffix + ".new")
    if not tmp.exists():
        return
    try:
        tmp.unlink()
        log.info("Removed orphan update download: %s", tmp)
    except OSError as e:
        log.debug("Could not remove orphan update download %s: %s", tmp, e)


# Terminal emulators we know how to launch a command inside, in
# preference order, with the arguments that precede the command.
_TERMINAL_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("konsole", ("konsole", "-e")),
    ("gnome-terminal", ("gnome-terminal", "--")),
    ("xfce4-terminal", ("xfce4-terminal", "-x")),
    ("kitty", ("kitty",)),
    ("alacritty", ("alacritty", "-e")),
    ("foot", ("foot",)),
    ("xterm", ("xterm", "-e")),
    ("wezterm", ("wezterm", "start", "--")),
)

_FLATPAK_UPDATE = ("flatpak", "update", "-y", "io.github.Rockykln.Refrain")
_AUR_HELPERS = ("yay", "paru", "trizen", "pikaur")
_PACMAN_UPDATE = ("sudo", "pacman", "-Syu", "refrain")
# The only commands that ever reach the shell in _run_in_terminal.
_TERMINAL_COMMANDS = frozenset(
    {_FLATPAK_UPDATE, _PACMAN_UPDATE, *((helper, "-Syu", "refrain") for helper in _AUR_HELPERS)}
)


def _run_in_terminal(cmd: Sequence[str]) -> bool:
    """Pop up a terminal emulator running ``cmd``, one of ``_TERMINAL_COMMANDS``.

    bash keeps the terminal open after the command exits, so the user can
    read the package-manager output and any error before the window
    vanishes. Returns True iff we successfully spawned a terminal; False
    if the command isn't allowed or no known emulator is on PATH.
    """
    cmd = tuple(cmd)
    if cmd not in _TERMINAL_COMMANDS:
        log.warning("Refusing to run a command that isn't allowlisted: %r", cmd)
        return False
    script = f"{shlex.join(cmd)}; echo; read -rp 'Press Enter to close…'"
    for binary, prefix in _TERMINAL_PROBES:
        if not shutil.which(binary):
            continue
        argv = [*prefix, "bash", "-c", script]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("Spawned %s for update command", binary)
            return True
        except Exception as e:
            log.debug("Failed to spawn %s: %s", binary, e)
    log.info("No known terminal emulator found — falling back to message-box hint")
    return False


def _aur_helper() -> tuple[str, ...]:
    """Pick the user's AUR helper. Falls back to bare ``pacman -Syu``
    when no helper is on PATH (won't update AUR packages, but at least
    won't be wrong — the user gets to see the issue + run their own
    helper manually).
    """
    for helper in _AUR_HELPERS:
        if shutil.which(helper):
            return (helper, "-Syu", "refrain")
    return _PACMAN_UPDATE


def apply_update(
    release: ReleaseInfo,
    install_type: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> UpdateResult:
    """Type-aware update. Never modifies system files directly — for
    AUR / Flatpak / system installs we shell out to the user's package
    manager via a terminal emulator so the user confirms any sudo
    prompt themselves and the package manager retains state."""
    install_type = install_type or detect_install_type()
    log.info("apply_update: target=%s install_type=%s", release.version, install_type)

    if install_type == "appimage":
        return _apply_appimage(release, cancelled=cancelled)
    if install_type == "pip":
        return _apply_pip()
    if install_type == "pipx":
        return _apply_pipx()
    if install_type == "dev":
        return UpdateResult(
            success=False,
            message="This is a development checkout — pull manually with "
            "`git pull` and reinstall with `pip install -e .`.",
        )
    if install_type == "flatpak":
        cmd: tuple[str, ...] = _FLATPAK_UPDATE
        if _run_in_terminal(cmd):
            return UpdateResult(
                success=True,
                message=(
                    "Launched the update in a new terminal:\n\n"
                    f"    {shlex.join(cmd)}\n\n"
                    "Confirm any prompts there. Restart Refrain afterwards "
                    "to load the new version."
                ),
                needs_restart=True,
            )
        return UpdateResult(
            success=False,
            message=f"Flatpak install detected. Update via:\n\n    {shlex.join(cmd)}",
        )
    if install_type == "aur":
        cmd = _aur_helper()
        if _run_in_terminal(cmd):
            return UpdateResult(
                success=True,
                message=(
                    "Launched the update in a new terminal:\n\n"
                    f"    {shlex.join(cmd)}\n\n"
                    "Confirm any sudo prompt there. Restart Refrain "
                    "afterwards to load the new version."
                ),
                needs_restart=True,
            )
        return UpdateResult(
            success=False,
            message=f"AUR install detected. Update via your AUR helper:\n\n    {shlex.join(cmd)}",
        )
    return UpdateResult(
        success=False,
        message="System install detected. Use your distribution's package "
        "manager to update Refrain.",
    )


def _apply_appimage(
    release: ReleaseInfo,
    cancelled: Callable[[], bool] | None = None,
) -> UpdateResult:
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
            message=f"The latest release doesn't ship an .AppImage for {_machine_arch()}.",
        )
    if not release.appimage_url.startswith(_DOWNLOAD_PREFIX):
        log.warning("AppImage URL outside the Refrain releases: %s", release.appimage_url)
        return UpdateResult(
            success=False,
            message="The AppImage download isn't hosted on the Refrain releases page.",
        )
    if release.appimage_size < _MIN_APPIMAGE_BYTES:
        log.warning("Implausible AppImage size in release: %d bytes", release.appimage_size)
        return UpdateResult(
            success=False,
            message="The release reports an implausible size for the AppImage.",
        )

    target = Path(appimage_path)
    tmp = target.with_suffix(target.suffix + ".new")
    log.info(
        "AppImage update: %s → %s (%d bytes from %s)",
        target,
        tmp,
        release.appimage_size,
        release.appimage_url,
    )

    try:
        expected_sha256 = _expected_sha256(release)
        digest = hashlib.sha256()
        written = 0
        # Chunked read so we can poll the cancel flag between blocks.
        # 64 KiB is large enough not to dominate syscall overhead and
        # small enough to stay responsive on slow links (~50ms ticks
        # at 1 MB/s).
        chunk_size = 64 * 1024
        with _open_download(release.appimage_url, 60) as r, open(tmp, "wb") as out:
            while True:
                if cancelled is not None and cancelled():
                    raise _DownloadCancelled()
                chunk = r.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > release.appimage_size:
                    raise OSError(f"download exceeds the expected {release.appimage_size} bytes")
                digest.update(chunk)
                out.write(chunk)
        # A truncated download must never replace the working AppImage.
        if written != release.appimage_size:
            raise OSError(
                f"size mismatch — downloaded {written} bytes, expected {release.appimage_size}"
            )
        if digest.hexdigest() != expected_sha256:
            raise OSError("SHA-256 checksum does not match the release's SHA256SUMS")
        if cancelled is not None and cancelled():
            raise _DownloadCancelled()
        # Preserve the existing AppImage's mode (or fall back to a
        # private user-only +x if we can't read it). Mirroring the
        # original mode keeps the user's chosen permissions stable
        # across upgrades, and avoids hard-coding a world-readable
        # 0o755 that CodeQL flags as overly permissive when 0o700
        # already gives the only thing AppImages strictly need (the
        # owner's execute bit). os.replace is atomic on Linux even
        # while the old file is mmap'd.
        try:
            preserved_mode = target.stat().st_mode & 0o777
        except OSError:
            preserved_mode = 0o700
        os.chmod(tmp, preserved_mode)
        os.replace(tmp, target)
        log.info("AppImage update complete: %s now at v%s", target, release.version)
    except UnverifiedReleaseError as e:
        log.warning("AppImage update refused: %s", e)
        return UpdateResult(
            success=False,
            message=(
                f"Refrain could not verify this update: {e}.\n\n"
                "Your AppImage was not changed. Download the new one "
                f"from the Releases page instead:\n\n    {RELEASES_PAGE}"
            ),
        )
    except _DownloadCancelled:
        log.info("AppImage update cancelled by user")
        if tmp.exists():
            with contextlib.suppress(Exception):
                tmp.unlink()
        return UpdateResult(success=False, message="Update canceled.", cancelled=True)
    except Exception as e:
        log.warning("AppImage update aborted: %s", e)
        if tmp.exists():
            with contextlib.suppress(Exception):
                tmp.unlink()
        return UpdateResult(success=False, message=f"AppImage download failed: {e}")

    return UpdateResult(
        success=True,
        message=f"Downloaded Refrain {release.version}. Restart Refrain to use it.",
        needs_restart=True,
    )


def _apply_pipx() -> UpdateResult:
    """Upgrade a pipx-managed install via ``pipx upgrade refrain``.

    pipx app venvs ship without pip, so the plain ``python -m pip``
    path can't work here — and even with pip it would desync pipx's
    metadata. ``pipx`` itself is normally on PATH for a pipx user; if
    it isn't, surface the one-liner instead of failing opaquely.
    """
    pipx = shutil.which("pipx")
    if not pipx:
        return UpdateResult(
            success=False,
            message=(
                "Refrain was installed with pipx, but the `pipx` command "
                "isn't on PATH so it can't self-update. Update manually:\n\n"
                "    pipx upgrade refrain"
            ),
        )
    cmd = [pipx, "upgrade", "refrain"]
    log.info("pipx update: invoking %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as e:
        log.warning("pipx update invocation failed: %s", e)
        return UpdateResult(success=False, message=f"pipx invocation failed: {e}")

    out = (result.stdout or "") + (result.stderr or "")
    last_line = next((ln for ln in reversed(out.splitlines()) if ln.strip()), "")
    log.info("pipx exit=%d last=%s", result.returncode, last_line[:200])
    if result.returncode != 0:
        return UpdateResult(
            success=False,
            message=f"pipx exited with code {result.returncode}:\n{out.strip()[:400]}",
        )
    low = out.lower()
    # pipx prints "upgraded package refrain from X to Y" on a real
    # upgrade, and "refrain is already at latest version" on a no-op
    # (which can happen while PyPI's CDN lags the GitHub release).
    if "already at latest version" in low and "upgraded" not in low:
        return UpdateResult(
            success=False,
            message=(
                "pipx reports Refrain is already at the latest version — "
                "usually PyPI's CDN is lagging the new release by a few "
                "minutes. Wait a moment and try again, or force it:\n\n"
                "    pipx upgrade --force refrain"
            ),
        )
    return UpdateResult(
        success=True,
        message="pipx upgrade complete. Restart Refrain to load the new version.",
        needs_restart=True,
    )


def _apply_pip() -> UpdateResult:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "refrain"]
    if sys.prefix == sys.base_prefix and _in_user_site():
        cmd.insert(4, "--user")
    log.info("pip update: invoking %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as e:
        log.warning("pip update invocation failed: %s", e)
        return UpdateResult(success=False, message=f"pip invocation failed: {e}")

    # Log the meaningful tail line of pip's stdout so the live log
    # shows "Successfully installed refrain-X.Y.Z" or
    # "Requirement already satisfied: refrain==X.Y.Z" — without this
    # the user sees `pip exit=0` and assumes the upgrade worked when
    # PyPI's index might still have a stale latest pinned, leaving
    # the venv at the previous version after pip claimed success.
    last_line = next(
        (line for line in reversed(result.stdout.splitlines()) if line.strip()),
        "",
    )
    log.info(
        "pip exit=%d stdout_len=%d last=%s",
        result.returncode,
        len(result.stdout),
        last_line[:200],
    )
    if result.returncode != 0:
        log.warning("pip stderr: %s", result.stderr.strip()[:400])
        return UpdateResult(
            success=False,
            message=f"pip exited with code {result.returncode}:\n{result.stderr.strip()}",
        )
    # Distinguish a real upgrade from pip's "already-satisfied" no-op.
    # PyPI's CDN can lag the GitHub Releases API by minutes after a
    # publish, so an update-check that just succeeded ("0.2.5
    # available") may still see 0.2.4 as latest from pip's side and
    # exit-zero without changing anything. Without this branch we'd
    # show "Update complete — restart Refrain" and the user would
    # restart to find themselves on the same version.
    stdout_lower = result.stdout.lower()
    if "successfully installed" in stdout_lower:
        return UpdateResult(
            success=True,
            message="pip upgrade complete. Restart Refrain to load the new version.",
            needs_restart=True,
        )
    if "requirement already satisfied" in stdout_lower:
        return UpdateResult(
            success=False,
            message=(
                "pip considered the package already up to date — usually "
                "PyPI's CDN cache is lagging the new release by a few "
                "minutes. Wait a moment and try again, or force the "
                "refresh manually:\n\n"
                "    pip install --upgrade --force-reinstall refrain"
            ),
        )
    # Pip exited 0 but neither marker is present — unusual, surface
    # the tail of stdout so the user has *something* to act on.
    return UpdateResult(
        success=True,
        message=(
            "pip exited successfully. Restart Refrain to load the new "
            "version. (pip output: " + (last_line[:120] or "<empty>") + ")"
        ),
        needs_restart=True,
    )
