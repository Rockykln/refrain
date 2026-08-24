"""One-shot credential check at startup.

Both credentials used to fail silently and late. A Discord Application ID
that is malformed or belongs to a deleted app only announced itself the
first time the daemon had something to publish, and a Last.fm session key
that had been revoked only surfaced at the first scrobble — which can be
an hour in. Until then the UI happily claimed to be connected.

This runs the two checks once, shortly after startup, off the UI thread,
and reports the outcome through one signal. Every line it logs carries a
``[startup-check]`` marker so the result is greppable in ``refrain.log``:

    grep '\\[startup-check\\]' ~/.local/state/refrain/refrain.log

Nothing here blocks startup, and nothing here is fatal: a failed check is
information for the user, not a reason to stop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MARKER = "[startup-check]"

# Result states, shared by both services so the tray can style them alike.
OK = "ok"
DISABLED = "disabled"  # not configured — not a problem
UNREACHABLE = "unreachable"  # network/client down — probably temporary
INVALID = "invalid"  # credentials rejected — the user must act


@dataclass(frozen=True)
class CheckResult:
    state: str
    detail: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state == INVALID


def check_lastfm(cfg) -> CheckResult:
    """Validate the stored Last.fm session against the live API."""
    from refrain.scrobble import LastfmClient, LastfmError

    if not getattr(cfg, "enabled", False):
        log.info("%s Last.fm: disabled", MARKER)
        return CheckResult(DISABLED)
    if not (cfg.api_key and cfg.shared_secret and cfg.session_key):
        log.info("%s Last.fm: not connected (missing credentials)", MARKER)
        return CheckResult(DISABLED, "missing credentials")

    client = LastfmClient(cfg.api_key, cfg.shared_secret, cfg.session_key)
    try:
        user = client.validate_session()
    except LastfmError as e:
        if e.invalid_session:
            log.warning(
                "%s Last.fm: session REJECTED (%s) — reconnect in "
                "Settings → Last.fm; scrobbles will not be accepted",
                MARKER,
                e,
            )
            return CheckResult(INVALID, str(e))
        log.info("%s Last.fm: could not verify right now (%s)", MARKER, e)
        return CheckResult(UNREACHABLE, str(e))
    except Exception as e:  # pragma: no cover - defensive
        log.info("%s Last.fm: could not verify right now (%s)", MARKER, e)
        return CheckResult(UNREACHABLE, str(e))

    log.info("%s Last.fm: OK — authenticated as %s", MARKER, user or "?")
    return CheckResult(OK, user)


def check_discord(rpc) -> CheckResult:
    """Report on the Discord RPC connection attempt already made."""
    if not getattr(rpc, "client_id", ""):
        log.info("%s Discord: disabled (no Application ID configured)", MARKER)
        return CheckResult(DISABLED)

    status = getattr(rpc, "status", "no_client")
    detail = getattr(rpc, "status_detail", "")
    if status == "connected":
        log.info("%s Discord: OK — connected on %s", MARKER, detail or "?")
        return CheckResult(OK, detail)
    if status == "rejected":
        log.warning(
            "%s Discord: handshake REJECTED (%s) — check the Application ID "
            "in Settings → Discord, and that you are signed in",
            MARKER,
            detail,
        )
        return CheckResult(INVALID, detail)
    # The daemon only dials Discord when it has something to publish, so
    # "not connected" here usually means "nothing has played yet", not
    # "Discord is missing". Probe the sockets directly rather than
    # reporting a failure the user does not have.
    from refrain.discord_rpc import _scan_ipc_pipes

    live, stale = _scan_ipc_pipes()
    if live:
        note = f"discord-ipc-{live[0]}"
        if len(live) > 1:
            note = f"{len(live)} clients listening ({', '.join(f'discord-ipc-{n}' for n in live)})"
        if stale:
            note += f"; ignoring stale {', '.join(f'discord-ipc-{n}' for n in stale)}"
        log.info(
            "%s Discord: client is running (%s) — not dialled yet, "
            "the daemon connects once something is playing",
            MARKER,
            note,
        )
        return CheckResult(UNREACHABLE, note)

    log.info(
        "%s Discord: no client running (nothing listening on discord-ipc-0..9)%s",
        MARKER,
        f"; {len(stale)} stale socket(s) ignored" if stale else "",
    )
    return CheckResult(UNREACHABLE, detail or "no client running")


class StartupCheckWorker(QObject):
    """Runs both checks on a worker thread and emits the pair once."""

    finished = Signal(object, object)  # (lastfm: CheckResult, discord: CheckResult)

    def __init__(self, lastfm_cfg, rpc) -> None:
        super().__init__()
        self._lastfm_cfg = lastfm_cfg
        self._rpc = rpc

    def run(self) -> None:
        try:
            lastfm = check_lastfm(self._lastfm_cfg)
        except Exception as e:  # pragma: no cover - never take startup down
            log.debug("%s Last.fm check crashed: %s", MARKER, e)
            lastfm = CheckResult(UNREACHABLE, str(e))
        try:
            discord = check_discord(self._rpc)
        except Exception as e:  # pragma: no cover
            log.debug("%s Discord check crashed: %s", MARKER, e)
            discord = CheckResult(UNREACHABLE, str(e))
        self.finished.emit(lastfm, discord)
