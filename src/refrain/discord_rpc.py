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
import socket
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


# How many discord-ipc-N slots the RPC spec defines.
_IPC_SLOTS = 10
# A live socket answers immediately; this only guards against a peer that
# accepts the connection but never completes it.
_IPC_PROBE_TIMEOUT_S = 0.5
# How often to look for a client that appeared after we connected. Only
# used with `all_clients`; the sweep costs one connect() per occupied
# slot, and the daemon ticks twice a second, so this must not ride the
# tick. Deliberately separate from the failure backoff: that one doubles
# up to 15 s and is about a client we could not reach, not about noticing
# a new one.
_IPC_RESCAN_INTERVAL_S = 5.0


def _runtime_dir() -> Path | None:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg)
    fallback = Path(f"/run/user/{os.getuid()}")
    return fallback if fallback.is_dir() else None


def _scan_ipc_pipes() -> tuple[list[int], list[int]]:
    """Return ``(live, stale)`` discord-ipc-N slot numbers.

    pypresence cannot do this for us. Its ``get_ipc_path`` probes each
    candidate with ``test_ipc_path``, which calls ``socket.connect()``
    with no exception handling — so the *first* dead socket it happens to
    touch raises ConnectionRefusedError straight out of the scan and the
    live socket behind it is never tried. The order comes from
    ``os.scandir``, i.e. the filesystem, so whether a connect succeeds is
    luck. That is the "Discord was already running but Refrain didn't
    connect" report: a ``discord-ipc-N`` left behind by a previous
    Discord session shadows the running one. Several clients at once
    (Discord plus Vencord/Vesktop) make it likelier still, because there
    are simply more sockets to trip over.
    """
    runtime_dir = _runtime_dir()
    if runtime_dir is None:
        return [], []
    live: list[int] = []
    stale: list[int] = []
    for n in range(_IPC_SLOTS):
        path = runtime_dir / f"discord-ipc-{n}"
        try:
            if not path.is_socket():
                continue
        except OSError:
            continue
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(_IPC_PROBE_TIMEOUT_S)
                probe.connect(str(path))
            live.append(n)
        except OSError:
            stale.append(n)
    return live, stale


class DiscordRPC:
    def __init__(self, client_id: str, all_clients: bool = False):
        self.client_id = (client_id or "").strip()
        # One live Presence per Discord client we serve, keyed by the
        # discord-ipc-N slot ("auto" when pypresence picked the socket
        # itself). Normally holds exactly one entry; with `all_clients`
        # the same status is published to every client that is listening,
        # which is what a Discord + Vencord/Vesktop pair needs — each is
        # a separate process with its own IPC socket, and a status sent
        # to one is invisible in the other.
        self._presences: dict[object, Presence] = {}
        self.all_clients = all_clients
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
        # Remembered so a changing client line-up is logged once, not per tick.
        self._last_live_pipes: list[int] = []
        # Why we are not connected, for the startup check and the tray.
        # "disabled" (no client_id) / "no_client" (nothing listening) /
        # "rejected" (Discord answered but refused the handshake — a bad
        # Application ID or a signed-out client) / "connected".
        self.status: str = "disabled" if not self.client_id else "no_client"
        self.status_detail: str = ""
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

    @property
    def _presence(self) -> Presence | None:
        """The primary connection.

        Kept as a property so the single-connection call sites (and the
        tests that inject a fake) keep working now that several
        connections can be open at once.
        """
        return next(iter(self._presences.values()), None)

    @_presence.setter
    def _presence(self, value: Presence | None) -> None:
        if value is None:
            self._presences.clear()
        else:
            self._presences = {"primary": value}

    def is_connected(self) -> bool:
        return bool(self._presences)

    def _ensure_connected(self) -> bool:
        # With all_clients we keep looking for newcomers (a second client
        # started after us), but only on the retry cadence so a missing
        # one cannot turn every tick into a socket sweep.
        if self._presences and not self.all_clients:
            return True
        if not self.client_id:
            return False
        if time.monotonic() < self._next_retry_ts:
            # Still connected to whoever answered — the pending retry is
            # only about clients we have *not* reached yet. Answering
            # False here froze the status for everyone during the backoff
            # window, because update() bails on a False.
            return bool(self._presences)
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
        live, stale = _scan_ipc_pipes()
        if stale:
            log.debug(
                "Discord IPC: skipping stale socket(s) %s",
                ", ".join(f"discord-ipc-{n}" for n in stale),
            )
        if live != self._last_live_pipes:
            # Only on change: the sweep runs every few seconds, and an
            # unchanged set of clients has nothing new to say.
            if len(live) > 1:
                log.info(
                    "Discord IPC: %d clients listening (%s) — using discord-ipc-%d",
                    len(live),
                    ", ".join(f"discord-ipc-{n}" for n in live),
                    live[0],
                )
            self._last_live_pipes = live
        # Pin the pipes we just proved are alive. Left to itself,
        # pypresence rescans and can abort on a stale socket before
        # reaching a live one — see _scan_ipc_pipes. Falling back to its
        # own discovery keeps the Snap/Flatpak paths it knows about
        # working when nothing is visible in XDG_RUNTIME_DIR.
        targets: list[object] = list(live) if live else ["auto"]
        if not self.all_clients:
            targets = targets[:1]
        targets = [t for t in targets if t not in self._presences]
        if not targets:
            if self._presences:
                # Everyone visible is already served. Push the next sweep
                # out so watching for newcomers does not run on every
                # tick — that was a socket sweep twice a second.
                self._next_retry_ts = time.monotonic() + _IPC_RESCAN_INTERVAL_S
                return True
            self._schedule_retry()
            return False

        connected_now: list[object] = []
        last_error: Exception | None = None
        for target in targets:
            try:
                p = (
                    Presence(self.client_id)
                    if target == "auto"
                    else Presence(self.client_id, pipe=target)
                )
                p.connect()
                self._presences[target] = p
                connected_now.append(target)
            except ppx.DiscordError as e:
                # Discord answered but refused the handshake — a bad
                # Application ID or a signed-out client. Same verdict for
                # every client, so stop here instead of retrying it once
                # per socket.
                self.status = "rejected"
                self.status_detail = str(e)
                log.info("Discord RPC handshake rejected: %s", e)
                self._backoff_s = self._max_backoff_s
                self._schedule_retry()
                return bool(self._presences)
            except Exception as e:
                last_error = e
                log.debug("Discord RPC connect failed on %s: %s", target, e)

        if connected_now:
            self._backoff_s = 2.0
            # A fresh IPC pipe carries no state on Discord's side, so the
            # dedup cache from a previous connection would wrongly skip
            # the first update on the new one (Discord would then keep
            # showing nothing until the daemon picks up a change).
            self._last_payload = None
            self.status = "connected"
            self.status_detail = ", ".join(
                "auto" if t == "auto" else f"discord-ipc-{t}"
                for t in sorted(self._presences, key=str)
            )
            log.info(
                "Discord RPC connected (%s)%s",
                self.status_detail,
                f" — {len(self._presences)} clients" if len(self._presences) > 1 else "",
            )
            return True

        if self._presences:
            # Already serving someone; a newcomer simply is not ready.
            self._schedule_retry()
            return True

        self.status = "no_client"
        self.status_detail = str(last_error) if last_error else "no client reachable"
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
        # Fan out, and judge each connection on its own: one client being
        # closed mid-song must not drop the status from the others.
        failed: list[object] = []
        delivered = 0
        for target, presence in list(self._presences.items()):
            try:
                presence.update(**payload)
                delivered += 1
            except Exception as e:
                log.warning("Discord RPC update failed on %s: %s", target, e)
                failed.append(target)
        for target in failed:
            with contextlib.suppress(Exception):
                self._presences.pop(target).close()
        if delivered:
            self._last_payload = dict(payload)
        else:
            self._last_payload = None
            self._schedule_retry()

    def clear(self) -> None:
        if not self._presences:
            return
        # Whatever the user just listened to is no longer current; the
        # next update() must push (don't dedupe against a previous
        # identical payload).
        self._last_payload = None
        for target, presence in list(self._presences.items()):
            try:
                presence.clear()
            except Exception as e:
                log.debug("Discord RPC clear failed on %s: %s", target, e)
                with contextlib.suppress(Exception):
                    self._presences.pop(target).close()
        if not self._presences:
            self._schedule_retry()

    def close(self) -> None:
        if not self._presences:
            return
        for presence in self._presences.values():
            with contextlib.suppress(Exception):
                presence.close()
        self._presences.clear()
        self._last_payload = None

    def _schedule_retry(self) -> None:
        self._next_retry_ts = time.monotonic() + self._backoff_s
        self._backoff_s = min(self._backoff_s * 2.0, self._max_backoff_s)
