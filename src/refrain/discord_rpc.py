"""Discord IPC client wrapper.

Wraps `pypresence.Presence` with:
- exponential backoff on connection failures (no busy-loop)
- silent no-op when Discord isn't running
- automatic reconnect on update errors
"""

from __future__ import annotations

import logging
import time

from pypresence import Presence
from pypresence import exceptions as ppx

log = logging.getLogger(__name__)


class DiscordRPC:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self._presence: Presence | None = None
        self._next_retry_ts: float = 0.0
        self._backoff_s: float = 2.0
        self._max_backoff_s: float = 60.0

    def _ensure_connected(self) -> bool:
        if self._presence is not None:
            return True
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
        try:
            self._presence.close()
        except Exception:
            pass
        self._presence = None

    def _schedule_retry(self) -> None:
        self._next_retry_ts = time.monotonic() + self._backoff_s
        self._backoff_s = min(self._backoff_s * 2.0, self._max_backoff_s)
