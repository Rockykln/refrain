"""XDG-compliant runtime paths for Refrain."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def _xdg(env_var: str, default_subpath: str) -> Path:
    # The XDG spec says relative values are invalid and must be ignored.
    custom = os.environ.get(env_var)
    if custom and Path(custom).is_absolute():
        return Path(custom)
    return Path.home() / default_subpath


def make_private_dir(path: Path) -> Path:
    """Create ``path`` readable by its owner only, and return it.

    A directory an older Refrain left behind can be wider than that, and
    every atomic write below happens inside it. Only ever narrowed: a
    directory the owner locked down further keeps its own mode, and a
    write into it fails instead of quietly re-opening it.
    """
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(path, mode & 0o700)
    return path


def write_private(path: Path, data: str | bytes) -> None:
    """Replace ``path`` with ``data``, atomically and owner-only.

    The temporary file next to the target carries a predictable name, so it
    is opened with ``O_EXCL | O_NOFOLLOW``: a symlink planted there has to
    make the write fail rather than send it somewhere else. The mode is set
    at creation, never by a later ``chmod`` — between the two the file would
    be readable by anyone the umask allows.

    Raises ``OSError`` like any write; every caller already handles that.
    """
    make_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    with contextlib.suppress(FileNotFoundError):
        # Ours from a crashed write, or someone else's bait. Either way it
        # must not survive into the O_EXCL open below.
        os.unlink(tmp)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        os.write(fd, data.encode("utf-8") if isinstance(data, str) else data)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    os.close(fd)
    try:
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "refrain"


def config_path() -> Path:
    return config_dir() / "config.toml"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "refrain"


def log_path() -> Path:
    return state_dir() / "refrain.log"


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "refrain"


def cover_cache_dir() -> Path:
    return cache_dir() / "covers"


def autostart_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "refrain.desktop"


def desktop_entry() -> str:
    """Basename of the .desktop file this copy was installed with."""
    return os.environ.get("FLATPAK_ID") or "refrain"


def assets_dir() -> Path:
    """Bundled assets directory (lives inside the installed package)."""
    return Path(__file__).parent / "assets"
