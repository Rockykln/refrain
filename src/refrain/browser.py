"""Open a song's page in the browser that played it.

Apple Music plays in a browser tab, and that browser is the one signed
in to Apple Music — often not the desktop's default browser. So a link
from the history goes to the browser the song played in, as a new tab
of the running instance, as long as that browser is still open.
Anything else — a Bluetooth play, a browser that has since closed, a
sandboxed Refrain — goes to the default browser like any other link.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Browser:
    keyword: str  # matched against the player's MPRIS Identity, lower-cased
    executables: tuple[str, ...]
    processes: tuple[str, ...]  # as in /proc/<pid>/comm, cut at 15 characters


# More specific names first: Zen, LibreWolf and Floorp are Firefox
# underneath, and "chrome" would otherwise catch nothing but Chrome.
_BROWSERS = (
    _Browser("zen", ("zen-browser", "zen"), ("zen-bin", "zen")),
    _Browser("librewolf", ("librewolf",), ("librewolf",)),
    _Browser("floorp", ("floorp",), ("floorp",)),
    _Browser("firefox", ("firefox",), ("firefox", "firefox-bin")),
    _Browser("chromium", ("chromium", "chromium-browser"), ("chromium", "chromium-browse")),
    _Browser("brave", ("brave", "brave-browser"), ("brave",)),
    _Browser("vivaldi", ("vivaldi-stable", "vivaldi"), ("vivaldi-bin",)),
    _Browser("edge", ("microsoft-edge-stable", "microsoft-edge"), ("msedge",)),
    _Browser("opera", ("opera",), ("opera",)),
    _Browser("chrome", ("google-chrome-stable", "google-chrome"), ("chrome",)),
)

# Belong to the Refrain process, not to a browser it starts: a Qt plugin
# path pointing into PySide6 is enough to break a Qt-based Chromium.
_PRIVATE_ENV = ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONPATH", "PYTHONHOME")


def running_processes(proc: Path = Path("/proc")) -> set[str]:
    names: set[str] = set()
    for comm in proc.glob("[0-9]*/comm"):
        try:
            names.add(comm.read_text(encoding="utf-8").strip())
        except OSError:
            continue  # the process ended between the glob and the read
    return names


def browser_for(
    player: str,
    running: Iterable[str],
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """The browser executable to hand a link to, or None for the default one."""
    hay = player.lower()
    running = set(running)
    for browser in _BROWSERS:
        if browser.keyword not in hay:
            continue
        if not running.intersection(browser.processes):
            return None
        for exe in browser.executables:
            path = which(exe)
            if path:
                return path
        return None
    return None


def _sandboxed() -> bool:
    # An AppImage's library path or a Flatpak's sandbox would leak into,
    # or stop, a browser started from here; the portal-backed default
    # route works in both.
    return bool(
        os.environ.get("APPIMAGE")
        or os.environ.get("FLATPAK_ID")
        or os.environ.get("container") == "flatpak"  # noqa: SIM112
    )


def open_url(url: str, player: str = "") -> bool:
    """Open ``url`` — in ``player``'s browser while it runs, else the default one."""
    if not url.startswith("https://"):
        # Everything the history links to is https. Anything else can only
        # come from a hand-edited history file, and has no business on a
        # browser's command line.
        log.warning("Not opening %r — not an https link", url)
        return False

    from PySide6.QtCore import QProcess, QProcessEnvironment, QUrl
    from PySide6.QtGui import QDesktopServices

    exe = browser_for(player, running_processes()) if player and not _sandboxed() else None
    if exe:
        env = QProcessEnvironment.systemEnvironment()
        for key in _PRIVATE_ENV:
            env.remove(key)
        proc = QProcess()
        proc.setProgram(exe)
        proc.setArguments([url])
        proc.setProcessEnvironment(env)
        # Detached, so the browser's short-lived launcher (it hands the URL
        # to the running instance and exits) is never left as a zombie.
        started = proc.startDetached()
        if started[0] if isinstance(started, tuple) else started:
            log.info("Opened %s in %s", url, os.path.basename(exe))
            return True
        log.warning("Could not start %s — using the default browser", exe)

    if QDesktopServices.openUrl(QUrl(url)):
        log.info("Opened %s in the default browser", url)
        return True
    log.warning("Could not open %s", url)
    return False
