"""Resolve a Discord Application ID to the name Discord will display.

The Client ID is the one setting nobody can verify by looking at it: it
is nineteen digits, and a wrong one fails silently — Refrain connects,
Discord accepts the socket and rejects the application, and the status
simply never appears. The name is the readable half of that ID, and it
is also the word that ends up next to "Listening to" on the card, so
seeing it confirms both that the ID is real and that it says what the
user meant it to say.

Discord exposes it without authentication at
``/api/v10/applications/{id}/rpc`` — the endpoint RPC clients use to
render an application they have only an ID for. The request carries the
Application ID and nothing else: no account, no token, no listening
data. It is public information by construction, since the same ID rides
along in every status Refrain publishes.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from refrain import __version__

log = logging.getLogger(__name__)

# How long a cached name is trusted. An application's name changes about
# never — and when it does, the user is the one who changed it and will
# see the new one within four hours or the moment they retype the ID.
NAME_TTL_S = 4 * 3600

_RPC_API = "https://discord.com/api/v10/applications"
_USER_AGENT = f"Refrain/{__version__} (+https://github.com/Rockykln/refrain)"
_TIMEOUT_S = 5

# Outcomes, kept as plain strings so the settings window can branch on
# them without importing anything from this module's transport layer.
FOUND = "found"
UNKNOWN_ID = "unknown_id"  # Discord answered, and has no such application
UNREACHABLE = "unreachable"  # network error, timeout, rate limit


def looks_like_application_id(client_id: str) -> bool:
    """Cheap local check, so an obvious typo costs no request at all.

    Discord snowflakes are 17-20 digits. Anything else cannot be one, and
    asking Discord about it would only spend a round-trip to be told so.
    """
    client_id = client_id.strip()
    return client_id.isdigit() and 17 <= len(client_id) <= 20


def fetch_application_name(client_id: str, timeout_s: float = _TIMEOUT_S) -> tuple[str, str]:
    """Look up the display name for ``client_id``.

    Returns ``(status, name)`` where status is one of ``FOUND``,
    ``UNKNOWN_ID`` or ``UNREACHABLE``, and name is the application's
    name when found and ``""`` otherwise.

    Never raises: a settings dialog must not blow up because the network
    is down, and the three outcomes are all the caller can act on
    anyway. The distinction that matters to a user is "Discord says this
    ID doesn't exist" — which they can fix — versus "we couldn't ask",
    which they can't.
    """
    client_id = client_id.strip()
    if not looks_like_application_id(client_id):
        return UNKNOWN_ID, ""

    url = f"{_RPC_API}/{client_id}/rpc"
    if not url.startswith("https://"):  # pragma: no cover - constant is https
        log.warning("Discord RPC API is not https — refusing to fetch: %s", url)
        return UNREACHABLE, ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:  # nosec B310
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # 404 is the documented answer for "no such application", and it
        # is the whole point of asking. Everything else — 429, 5xx — is
        # Discord being unavailable, not a verdict on the ID.
        if e.code == 404:
            log.debug("Discord has no application %s", client_id)
            return UNKNOWN_ID, ""
        log.info("Discord application lookup failed: HTTP %s", e.code)
        return UNREACHABLE, ""
    except Exception as e:
        log.info("Discord application lookup failed: %s", e)
        return UNREACHABLE, ""

    name = str(data.get("name", "")).strip()
    if not name:
        log.debug("Discord returned application %s with no name", client_id)
        return UNKNOWN_ID, ""
    return FOUND, name


def cached_name_is_fresh(client_id: str, cached_for_id: str, checked_ts: float, now: float) -> bool:
    """Can we show the cached name without asking Discord again?

    Three ways the answer is no: there is no ID to describe, the cache
    describes a *different* ID (the user retyped it, and a name from the
    old one would be actively misleading), or the last check has aged
    out. A `checked_ts` in the future — a clock that moved backwards —
    counts as aged out rather than as valid forever.
    """
    client_id = client_id.strip()
    if not client_id or cached_for_id.strip() != client_id:
        return False
    return 0 < checked_ts <= now and (now - checked_ts) < NAME_TTL_S


def remember_application_name(config, client_id: str, name: str) -> None:
    """Store a freshly confirmed name, and stamp when we confirmed it.

    Written straight to `config.toml` rather than waiting for Apply, the
    same way the silent update check stamps `update.last_check_ts`: the
    point of the cache is to survive a restart, and a name the user
    never clicked Apply for would not. A failed write is not worth
    surfacing — the cache simply misses next time.
    """
    config.discord.app_name = name
    config.discord.app_name_for_id = client_id.strip()
    config.discord.app_name_checked_ts = int(time.time())
    try:
        config.save()
    except OSError as e:
        log.debug("Could not persist the cached application name: %s", e)


def refresh_application_name(config) -> str:
    """Re-check the cached name if it has aged out. Returns the name.

    The whole job in one call, for the startup/periodic refresh: decides
    whether asking is warranted, asks, and persists. Returns whatever
    name we end up believing, or ``""`` when there is none — including
    when we deliberately did not ask.

    Skips silently when the user has switched the lookup off, when there
    is no ID to describe, when Privacy is Off (that switch means "do not
    talk to Discord", and an exception for a small request would make it
    mean less), and when the cache is still fresh.
    """
    client_id = config.discord.client_id.strip()
    if not client_id or not config.discord.resolve_app_name or config.privacy.mode == "off":
        return ""
    d = config.discord
    if d.app_name and cached_name_is_fresh(
        client_id, d.app_name_for_id, d.app_name_checked_ts, time.time()
    ):
        return d.app_name
    status, name = fetch_application_name(client_id)
    if status != FOUND:
        # Leave the cache alone. An unreachable Discord is not evidence
        # that the name changed, and dropping a good name over a flaky
        # network would show "no such application" for a valid ID.
        return d.app_name if d.app_name_for_id.strip() == client_id else ""
    if name != d.app_name or d.app_name_for_id.strip() != client_id:
        log.info("Discord application %s is named %r", client_id, name)
    remember_application_name(config, client_id, name)
    return name
