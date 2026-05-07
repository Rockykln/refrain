"""Discord IPC client wrapper.

Wraps `pypresence.Presence` with:
- exponential backoff on connection failures (no busy-loop)
- silent no-op when Discord isn't running
- automatic reconnect on update errors
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from pypresence import ActivityType, Presence
from pypresence import exceptions as ppx

log = logging.getLogger(__name__)


def _bridge_sandboxed_ipc_socket() -> None:
    """Symlink Snap/Flatpak Discord's IPC socket into ``$XDG_RUNTIME_DIR``.

    pypresence (and the Discord RPC docs) require the socket at
    ``$XDG_RUNTIME_DIR/discord-ipc-N``. Snap and Flatpak Discord builds
    place the socket inside their sandbox tree instead, so a stock
    Refrain → pypresence connection fails on those installs even
    though Discord is running. We probe a handful of known sandbox
    locations and symlink the first match.

    No-op when ``$XDG_RUNTIME_DIR`` isn't set, when a standard socket
    already exists (Discord installed via .deb or pacman), or when
    nothing matches.
    """
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not xdg_runtime:
        return
    runtime_dir = Path(xdg_runtime)
    if not runtime_dir.is_dir():
        return
    # Sweep stale symlinks from a previous bridge run whose target has
    # since vanished (Discord uninstalled, Flatpak removed, host
    # reboot). Path.exists() returns False on a broken symlink, so
    # without this we'd never replace it and pypresence would keep
    # failing on connect.
    for n in range(10):
        link = runtime_dir / f"discord-ipc-{n}"
        if link.is_symlink() and not link.exists():
            with contextlib.suppress(OSError):
                link.unlink()
                log.debug("Removed stale Discord IPC symlink: %s", link)
    # If the standard path already has any working discord-ipc-N socket,
    # leave things alone — pypresence will find it on its own.
    for n in range(10):
        if (runtime_dir / f"discord-ipc-{n}").exists():
            return
    candidates = [
        # Flatpak (newer): per-app instance dir under XDG_RUNTIME_DIR
        runtime_dir / "app" / "com.discordapp.Discord",
        # Flatpak (older / config-dir layout)
        Path.home() / ".var" / "app" / "com.discordapp.Discord" / "config" / "discord",
        # Snap
        Path.home() / "snap" / "discord" / "current" / ".config" / "discord",
    ]
    for root in candidates:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            name = entry.name
            if not name.startswith("discord-ipc-"):
                continue
            try:
                if not entry.is_socket():
                    continue
            except OSError:
                continue
            target = runtime_dir / name
            try:
                target.symlink_to(entry)
                log.info("Bridged sandboxed Discord IPC socket: %s → %s", target, entry)
            except FileExistsError:
                # Race with Discord creating the standard socket itself,
                # or with a stale symlink we just couldn't unlink — give
                # up on this one rather than risk clobbering a real file.
                pass
            except OSError as e:
                log.debug("Could not symlink Discord IPC socket %s: %s", target, e)
            return


class DiscordRPC:
    def __init__(self, client_id: str):
        self.client_id = (client_id or "").strip()
        self._presence: Presence | None = None
        self._next_retry_ts: float = 0.0
        self._backoff_s: float = 2.0
        # Memoise the last payload we sent so we don't hammer Discord
        # with identical updates on every poll. Discord rate-limits
        # presence updates to ~5 per 20 s; the daemon ticks at 500 ms,
        # so without this we'd be sending 4× the cap and pypresence
        # would silently queue/drop most of them. Only the "start"
        # key meaningfully changes between consecutive ticks (and only
        # on a drift-resync), so most ticks would otherwise be
        # entirely redundant.
        self._last_payload: dict | None = None
        # Cap retry backoff at 15 s instead of 60 s — autostart launches
        # refrain before Discord is ready, and a 60 s ceiling means the
        # user can sit there for almost a minute after Discord finishes
        # loading before refrain notices and connects. 15 s keeps the
        # exponential ramp short enough to feel responsive while still
        # avoiding tight retry loops when Discord isn't installed.
        self._max_backoff_s: float = 15.0
        if not self.client_id:
            log.info(
                "Discord RPC disabled — no client_id configured. "
                "Set one in Settings → General to enable Discord status."
            )

    def is_connected(self) -> bool:
        return self._presence is not None

    def _ensure_connected(self) -> bool:
        if self._presence is not None:
            return True
        if not self.client_id:
            return False
        if time.monotonic() < self._next_retry_ts:
            return False
        # Sandbox-aware socket bridging — cheap (a few stat calls when
        # the standard path already works) and handles the
        # Snap/Flatpak Discord case without per-user manual symlinks.
        # Wrap defensively: a permission error on iterdir() of one of
        # the sandbox candidate dirs (rare but possible on locked-down
        # systems) shouldn't crash the connect path on every tick.
        try:
            _bridge_sandboxed_ipc_socket()
        except Exception as e:
            log.debug("Sandboxed-IPC bridge failed: %s", e)
        try:
            p = Presence(self.client_id)
            p.connect()
            self._presence = p
            self._backoff_s = 2.0
            log.info("Discord RPC connected")
            return True
        except (
            ppx.DiscordNotFound,
            ppx.PipeClosed,
            ConnectionRefusedError,
            FileNotFoundError,
            OSError,
        ) as e:
            log.debug("Discord RPC connect failed: %s", e)
        except Exception:
            log.exception("Discord RPC connect unexpected error")
        self._presence = None
        self._schedule_retry()
        return False

    def update(self, **payload) -> None:
        if not self._ensure_connected():
            return
        # Default to Discord's "Listening" activity type so the status renders
        # as "Listening to <song>" — matching what users expect from a music
        # RPC and what Spotify / other Discord music apps show. Without this,
        # Discord defaults to type=PLAYING ("Playing Refrain"), which looks
        # wrong for a music status and is what made early v0.1.x feel like
        # "Discord status missing" even though the RPC was sending data.
        payload.setdefault("activity_type", ActivityType.LISTENING)
        # Skip when the payload is byte-for-byte identical to what we
        # already pushed — Discord's rate-limit (5/20 s) drops most of
        # them anyway, but the IPC write + json-encode + Discord-side
        # state recompute is wasted work. The cache key intentionally
        # round-trips through dict-equality so any field change (start
        # drift-resync, cover URL arrival, button URL change) re-pushes.
        if payload == self._last_payload:
            return
        try:
            self._presence.update(**payload)
            self._last_payload = dict(payload)
        except Exception as e:
            log.warning("Discord RPC update failed: %s", e)
            self._presence = None
            self._last_payload = None
            self._schedule_retry()

    def clear(self) -> None:
        if self._presence is None:
            return
        # Whatever the user just listened to is no longer current; the
        # next update() must push (don't dedupe against a previous
        # identical payload).
        self._last_payload = None
        try:
            self._presence.clear()
        except Exception as e:
            log.debug("Discord RPC clear failed: %s", e)
            self._presence = None
            self._schedule_retry()

    def close(self) -> None:
        if self._presence is None:
            return
        with contextlib.suppress(Exception):
            self._presence.close()
        self._presence = None
        self._last_payload = None

    def _schedule_retry(self) -> None:
        self._next_retry_ts = time.monotonic() + self._backoff_s
        self._backoff_s = min(self._backoff_s * 2.0, self._max_backoff_s)
