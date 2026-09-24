"""Discord IPC client around `pypresence.Presence`: backoff on connection failures,
silent no-op when Discord isn't running, payloads fitted to Discord's limits,
rate-limited writes and bounded waits on a client that stops answering."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
from collections import deque
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

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
# An unchanged status is still sent this often. Writing is the only way to
# notice that Discord restarted and the pipe is dead.
_RESEND_S = 30.0
# Discord takes about five activity changes per 20 s and queues or drops the
# rest. Each write spends a token that comes back 20 s later, so a song change
# goes out at once while tokens are left and no 20 s window holds more than five.
_WRITE_TOKENS = 5
_WRITE_WINDOW_S = 20.0
# pypresence waits 30 s to connect, 10 s for a reply and forever during the
# handshake; a frozen client would hold up the daemon tick that long.
_CONNECT_TIMEOUT_S = 2.0
_RESPONSE_TIMEOUT_S = 3.0
# A Discord that is still starting up ends the handshake with code 1000, the
# same answer a signed-out one gives, so the answer has to keep coming for a
# while before the user is told to log in. Asking again on a fixed short
# cadence instead of the growing backoff keeps that wait short and notices a
# login within seconds.
_SIGNED_OUT_RETRY_S = 2.0
_SIGNED_OUT_TRIES = 3
_SIGNED_OUT_GRACE_S = 20.0
_REFUSED_MEMORY = 64
# Discord answers a hiccup with the same "Unknown error" as a bad payload, so a
# refusal is given one more chance before the song is written off.
_REFUSED_RETRY_S = 20.0

_TEXT_FIELDS = frozenset({"name", "details", "state", "large_text", "small_text"})
_IMAGE_FIELDS = frozenset({"large_image", "small_image"})
_LINK_FIELDS = frozenset({"details_url", "state_url", "large_url", "small_url"})
_TEXT_MIN = 2
_TEXT_MAX = 128
_IMAGE_MAX = 256
_LINK_MAX = 256
_BUTTON_LABEL_MAX = 32
_BUTTON_URL_MAX = 512
_BUTTONS_MAX = 2
# Discord refuses text shorter than two characters. A braille blank is not
# whitespace, so trimming on Discord's side keeps it, and it renders empty.
_PAD = "\N{BRAILLE PATTERN BLANK}"
_ELLIPSIS = "…"

_TIMEOUTS = (TimeoutError, ppx.ConnectionTimeout, ppx.ResponseTimeout)
_CLEAR = object()


class RPCState(StrEnum):
    DISABLED = "disabled"
    NO_CLIENT = "no_client"
    REJECTED = "rejected"
    NOT_LOGGED_IN = "not_logged_in"
    CONNECTED_IDLE = "connected_idle"
    SHOWING = "showing"
    ERROR = "error"


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _fit(text: str, limit: int) -> str:
    """Shorten to ``limit`` UTF-16 units, the unit Discord counts in."""
    if _utf16_len(text) <= limit:
        return text
    # A surrogate pair cut in half is dropped by "ignore".
    kept = text.encode("utf-16-le")[: (limit - 1) * 2].decode("utf-16-le", "ignore")
    return kept.rstrip() + _ELLIPSIS


def _clean_text(value: object, limit: int, minimum: int = _TEXT_MIN) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    text = _fit(text, limit)
    return text + _PAD * max(0, minimum - _utf16_len(text))


def _clean_url(
    value: object, limit: int, schemes: tuple[str, ...] = ("https", "http")
) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or _utf16_len(url) > limit or any(ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in schemes or not parts.netloc:
        return None
    return url


def _clean_image(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    image = value.strip()
    if "://" in image:
        return _clean_url(image, _IMAGE_MAX)
    if not image or _utf16_len(image) > _IMAGE_MAX:
        return None
    return image


def _clean_buttons(value: object) -> list[dict] | None:
    if not isinstance(value, (list, tuple)):
        return None
    buttons = []
    for button in value:
        if not isinstance(button, dict):
            continue
        label = _clean_text(button.get("label"), _BUTTON_LABEL_MAX, minimum=1)
        url = _clean_url(button.get("url"), _BUTTON_URL_MAX, ("https",))
        if label and url:
            buttons.append({"label": label, "url": url})
    return buttons[:_BUTTONS_MAX] or None


def sanitize_activity(payload: dict) -> dict:
    """Fit an activity into Discord's limits; Discord refuses the whole
    payload over a single field that breaks them."""
    clean: dict = {}
    for key, value in payload.items():
        if key in _TEXT_FIELDS:
            value = _clean_text(value, _TEXT_MAX)
        elif key in _IMAGE_FIELDS:
            value = _clean_image(value)
        elif key in _LINK_FIELDS:
            value = _clean_url(value, _LINK_MAX)
        elif key == "buttons":
            value = _clean_buttons(value)
        if value is not None:
            clean[key] = value
    start, end = clean.get("start"), clean.get("end")
    if start is not None and end is not None and end <= start:
        del clean["end"]
    return clean


def _fingerprint(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _discard(presence: Presence) -> None:
    """Close the socket of a connection whose handshake did not finish."""
    loop = getattr(presence, "loop", None)
    writer = getattr(presence, "sock_writer", None)
    with contextlib.suppress(Exception):
        if writer is not None:
            writer.close()
            if loop is not None:
                loop.run_until_complete(writer.wait_closed())
    if loop is not None:
        with contextlib.suppress(Exception):
            loop.close()


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
    luck, and a ``discord-ipc-N`` left behind by a previous Discord
    session shadows the running one. Several clients at once
    (Discord plus Discord PTB, say) make it likelier still, because there
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
        # which is what running two Discord builds side by side needs — each
        # is a separate process with its own IPC socket, and a status sent
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
        self._last_sent_mono = 0.0
        self._cleared = False
        # What Discord should show next: a payload, _CLEAR, or None once sent.
        # A burst of changes collapses into whichever came last.
        self._pending: object = None
        self._writes: deque[float] = deque()
        # fingerprint → (first refusal, how often)
        self._refused: dict[str, tuple[float, int]] = {}
        # Set while something is wrong beyond "Discord is not running":
        # a refused payload or a client that stopped answering.
        self._fault = ""
        # Remembered so a changing client line-up is logged once, not per tick.
        self._last_live_pipes: list[int] = []
        # Why we are not connected, for the startup check and the tray.
        # "disabled" (no client_id) / "no_client" (nothing listening) /
        # "rejected" (Discord answered but refused the handshake — a bad
        # Application ID) / "not_logged_in" (Discord kept answering code 1000
        # long enough that the user really is signed out — recovers on its
        # own, unlike "rejected") / "connected".
        self.status: str = "disabled" if not self.client_id else "no_client"
        self.status_detail: str = ""
        # How long code 1000 has been the answer, in tries and in seconds.
        self._signed_out_tries = 0
        self._signed_out_since = 0.0
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

        A property for the single-connection call sites and the tests
        that inject a fake.
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

    @property
    def state(self) -> RPCState:
        if not self.client_id:
            return RPCState.DISABLED
        if not self._presences:
            if self.status == "rejected":
                return RPCState.REJECTED
            if self.status == "not_logged_in":
                return RPCState.NOT_LOGGED_IN
            return RPCState.ERROR if self._fault else RPCState.NO_CLIENT
        if self._fault:
            return RPCState.ERROR
        if self._last_payload is not None:
            return RPCState.SHOWING
        return RPCState.CONNECTED_IDLE

    @property
    def detail(self) -> str:
        """The reason behind ``state``, or the pipes served while all is well."""
        return self._fault or self.status_detail

    def _open(self, target: object) -> Presence:
        p = Presence(self.client_id) if target == "auto" else Presence(self.client_id, pipe=target)
        p.connection_timeout = _CONNECT_TIMEOUT_S
        p.response_timeout = _RESPONSE_TIMEOUT_S
        handshake = p.handshake

        async def bounded_handshake():
            await asyncio.wait_for(handshake(), _RESPONSE_TIMEOUT_S)

        p.handshake = bounded_handshake  # type: ignore[method-assign]  # bounds pypresence's own handshake
        try:
            p.connect()
        except Exception:
            _discard(p)
            raise
        return p

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
            # False here would freeze the status for everyone during the
            # backoff window, because update() bails on a False.
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
            # Everyone visible is already served. Push the next sweep
            # out so watching for newcomers does not run on every tick.
            self._next_retry_ts = time.monotonic() + _IPC_RESCAN_INTERVAL_S
            return True

        connected_now: list[object] = []
        last_error: Exception | None = None
        rejected = False
        not_logged_in = False
        for target in targets:
            try:
                self._presences[target] = self._open(target)
                connected_now.append(target)
            except ppx.DiscordError as e:
                if e.code == 1000:
                    # Discord is running and the app ID is fine, but it is not
                    # ready for us — signed out, or still starting. Unlike a bad
                    # Application ID this clears up on its own, so it never sets
                    # "rejected"; whether it is worth telling the user about is
                    # decided below, once the answer has had time to repeat.
                    log.debug("Discord RPC: %s is not ready (code 1000): %s", target, e)
                    not_logged_in = True
                    last_error = e
                    continue
                # Discord answered but refused the handshake — a bad
                # Application ID. Likely the same verdict from the clients
                # after it, so stop here instead of asking each one.
                log.info("Discord RPC handshake rejected on %s: %s", target, e)
                rejected = True
                if not self._presences:
                    self.status = "rejected"
                    self.status_detail = str(e)
                    self._fault = ""
                    self._backoff_s = self._max_backoff_s
                    self._schedule_retry()
                    return False
                break
            except Exception as e:
                last_error = e
                log.debug("Discord RPC connect failed on %s: %s", target, e)

        now = time.monotonic()
        if not_logged_in and not connected_now:
            if not self._signed_out_tries:
                self._signed_out_since = now
            self._signed_out_tries += 1
        else:
            self._signed_out_tries = 0
        signed_out = (
            self._signed_out_tries >= _SIGNED_OUT_TRIES
            and now - self._signed_out_since >= _SIGNED_OUT_GRACE_S
        )

        if rejected:
            # A client that accepted is served, so the status stays
            # "connected"; the one that refused is asked again later.
            self._next_retry_ts = now + self._max_backoff_s
        elif not_logged_in:
            # Same idea, but the ordinary cadence — it may log in any moment.
            self._next_retry_ts = now + self._backoff_s

        if connected_now:
            self._backoff_s = 2.0
            # A fresh IPC pipe carries no state on Discord's side, so the
            # dedup cache from a previous connection would wrongly skip
            # the first update on the new one (Discord would then keep
            # showing nothing until the daemon picks up a change).
            self._last_payload = None
            self._cleared = False
            self._fault = ""
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
            # Already serving someone; a newcomer is not ready or refused.
            self.status = "connected"
            if not rejected:
                self._schedule_retry()
            return True

        if signed_out and self.status != "not_logged_in":
            log.info("Discord RPC: Discord is open but not logged in: %s", last_error)
        self.status = "not_logged_in" if signed_out else "no_client"
        self.status_detail = str(last_error) if last_error else "no client reachable"
        self._fault = "Discord did not answer in time" if isinstance(last_error, _TIMEOUTS) else ""
        if not_logged_in and not signed_out:
            self._next_retry_ts = now + _SIGNED_OUT_RETRY_S
        else:
            self._schedule_retry()
        return False

    def update(self, **payload) -> None:
        if not self._ensure_connected():
            return
        # Without a type Discord renders "Playing Refrain" instead of
        # "Listening to <song>".
        payload.setdefault("activity_type", ActivityType.LISTENING)
        self._pending = sanitize_activity(payload)
        self.pump()

    def clear(self, force: bool = False) -> None:
        """``force`` skips the rate limit — used on the way out, where a
        held-back clear would leave the last song on the profile."""
        self._pending = _CLEAR
        self.pump(force=force)

    def pump(self, force: bool = False) -> None:
        """Send what Discord still owes the user once the rate limit allows.

        update() and clear() call it; calling it on every tick as well lets a
        held-back last state go out even when nothing new comes in.
        """
        pending = self._pending
        if pending is None or not self._presences:
            return
        now = time.monotonic()
        # An unchanged status is still written now and then: only a write
        # notices that Discord restarted and the pipe is dead.
        recent = now - self._last_sent_mono < _RESEND_S
        if pending is _CLEAR:
            if self._cleared and recent:
                self._pending = None
                return
        elif (pending == self._last_payload and recent) or self._written_off(pending, now):
            self._pending = None
            return
        if not force and not self._has_token(now):
            return
        if pending is _CLEAR:
            self._send_clear(now)
        elif isinstance(pending, dict):
            self._send_update(pending, now)

    def _written_off(self, payload: object, now: float) -> bool:
        seen = self._refused.get(_fingerprint(payload)) if isinstance(payload, dict) else None
        if seen is None:
            return False
        first, times = seen
        return times > 1 or now - first < _REFUSED_RETRY_S

    def _has_token(self, now: float) -> bool:
        while self._writes and now - self._writes[0] >= _WRITE_WINDOW_S:
            self._writes.popleft()
        return len(self._writes) < _WRITE_TOKENS

    def _send_update(self, payload: dict, now: float) -> None:
        # Fan out, and judge each connection on its own: one client being
        # closed mid-song must not drop the status from the others.
        failed: list[object] = []
        delivered = 0
        refused: Exception | None = None
        hung = False
        for target, presence in list(self._presences.items()):
            try:
                presence.update(**payload)
                delivered += 1
            except ppx.ServerError as e:
                # Discord read the payload and said no; the pipe is fine.
                refused = e
            except Exception as e:
                log.warning("Discord RPC update failed on %s: %s", target, e)
                failed.append(target)
                hung = hung or isinstance(e, _TIMEOUTS)
        for target in failed:
            with contextlib.suppress(Exception):
                self._presences.pop(target).close()
        if delivered or refused is not None:
            self._writes.append(now)
        if delivered:
            self._pending = None
            self._last_payload = dict(payload)
            self._last_sent_mono = now
            self._cleared = False
            self._fault = ""
        elif refused is not None:
            self._pending = None
            key = _fingerprint(payload)
            first, times = self._refused.get(key, (now, 0))
            self._refused[key] = (first, times + 1)
            while len(self._refused) > _REFUSED_MEMORY:
                del self._refused[next(iter(self._refused))]
            self._fault = f"Discord refused the status: {refused}"
            again = times + 1 > 1
            log.warning(
                "Discord refused the status (%s) — %s. Fields: %s",
                refused,
                "not sending it again"
                if again
                else f"trying once more in {_REFUSED_RETRY_S:.0f} s",
                ", ".join(sorted(payload)),
            )
            log.debug("Refused payload: %r", payload)
        else:
            self._last_payload = None
            if hung:
                self._fault = "Discord did not answer in time"
            self._schedule_retry()

    def _send_clear(self, now: float) -> None:
        # Whatever the user just listened to is no longer current; the next
        # update() must go out even if it repeats the last one.
        self._last_payload = None
        refused = False
        for target, presence in list(self._presences.items()):
            try:
                presence.clear()
            except ppx.ServerError as e:
                log.debug("Discord refused to clear the status on %s: %s", target, e)
                refused = True
            except Exception as e:
                log.debug("Discord RPC clear failed on %s: %s", target, e)
                with contextlib.suppress(Exception):
                    self._presences.pop(target).close()
        if not self._presences:
            # A closed pipe takes its activity with it; nothing left to clear.
            self._cleared = False
            self._schedule_retry()
            return
        self._writes.append(now)
        if refused:
            return
        self._pending = None
        self._cleared = True
        self._last_sent_mono = now

    def close(self) -> None:
        self._pending = None
        if not self._presences:
            return
        for presence in self._presences.values():
            with contextlib.suppress(Exception):
                presence.close()
        self._presences.clear()
        self._last_payload = None
        self._cleared = False

    def _schedule_retry(self) -> None:
        self._next_retry_ts = time.monotonic() + self._backoff_s
        self._backoff_s = min(self._backoff_s * 2.0, self._max_backoff_s)
