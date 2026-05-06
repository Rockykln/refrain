"""Discord IPC client wrapper.

Wraps `pypresence.Presence` with:
- exponential backoff on connection failures (no busy-loop)
- silent no-op when Discord isn't running
- automatic reconnect on update errors
"""

from __future__ import annotations

import contextlib
import logging
import time

from pypresence import ActivityType, Presence
from pypresence import exceptions as ppx

log = logging.getLogger(__name__)


class DiscordRPC:
    def __init__(self, client_id: str):
        self.client_id = (client_id or "").strip()
        self._presence: Presence | None = None
        self._next_retry_ts: float = 0.0
        self._backoff_s: float = 2.0
        self._max_backoff_s: float = 60.0
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
        except Exception as e:
            log.warning("Discord RPC connect unexpected error: %s", e)
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
        try:
            self._presence.update(**payload)
        except Exception as e:
            log.warning("Discord RPC update failed: %s", e)
            self._presence = None
            self._schedule_retry()

    def clear(self) -> None:
        if self._presence is None:
            return
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

    def _schedule_retry(self) -> None:
        self._next_retry_ts = time.monotonic() + self._backoff_s
        self._backoff_s = min(self._backoff_s * 2.0, self._max_backoff_s)
