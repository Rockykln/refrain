"""What Discord and Last.fm are doing right now, as the tray and the Status window show it."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from PySide6.QtCore import QObject, Signal

from refrain.discord_rpc import RPCState
from refrain.sources.base import PlaybackStatus, TrackInfo


class DiscordStatus(StrEnum):
    STARTING = "starting"
    NOT_SET_UP = "not_set_up"
    NO_CLIENT = "no_client"
    REJECTED = "rejected"
    NOT_LOGGED_IN = "not_logged_in"
    ERROR = "error"
    READY = "ready"
    SHOWING = "showing"
    SHOWING_MINIMAL = "showing_minimal"
    PAUSED = "paused"
    PRIVACY_OFF = "privacy_off"


class LastfmStatus(StrEnum):
    OFF = "off"
    CONNECTED_OFF = "connected_off"
    NOT_CONNECTED = "not_connected"
    SCROBBLING = "scrobbling"
    WAITING = "waiting"
    EXPIRED = "expired"
    PAUSED = "paused"


def discord_status(rpc_state: RPCState, privacy_mode: str, track: TrackInfo) -> DiscordStatus:
    if privacy_mode == "off":
        return DiscordStatus.PRIVACY_OFF
    if rpc_state is RPCState.DISABLED:
        return DiscordStatus.NOT_SET_UP
    if rpc_state is RPCState.REJECTED:
        return DiscordStatus.REJECTED
    if rpc_state is RPCState.NOT_LOGGED_IN:
        return DiscordStatus.NOT_LOGGED_IN
    if rpc_state is RPCState.ERROR:
        return DiscordStatus.ERROR
    if rpc_state is RPCState.NO_CLIENT:
        return DiscordStatus.NO_CLIENT
    if track.status == PlaybackStatus.PLAYING and track.title.strip():
        if rpc_state is RPCState.SHOWING:
            return (
                DiscordStatus.SHOWING_MINIMAL
                if privacy_mode == "minimal"
                else DiscordStatus.SHOWING
            )
        return DiscordStatus.READY
    if track.has_track and track.status == PlaybackStatus.PAUSED:
        return DiscordStatus.PAUSED
    return DiscordStatus.READY


def lastfm_status(
    cfg, privacy_mode: str, session_invalid: bool, waiting: int
) -> tuple[LastfmStatus, str]:
    if not cfg.enabled:
        return (LastfmStatus.CONNECTED_OFF if cfg.session_key else LastfmStatus.OFF), ""
    if privacy_mode == "off":
        return LastfmStatus.PAUSED, ""
    if not (cfg.api_key and cfg.shared_secret and cfg.session_key):
        return LastfmStatus.NOT_CONNECTED, ""
    if session_invalid:
        return LastfmStatus.EXPIRED, ""
    if waiting > 0:
        return LastfmStatus.WAITING, str(waiting)
    return LastfmStatus.SCROBBLING, cfg.username


@dataclass(frozen=True)
class StatusSnapshot:
    discord: DiscordStatus = DiscordStatus.STARTING
    discord_detail: str = ""
    lastfm: LastfmStatus = LastfmStatus.OFF
    lastfm_detail: str = ""

    @property
    def needs_attention(self) -> frozenset[str]:
        """The states only the user can fix, by name, for showing each once."""
        found = set()
        if self.discord in (
            DiscordStatus.NOT_SET_UP,
            DiscordStatus.REJECTED,
            DiscordStatus.NOT_LOGGED_IN,
        ):
            found.add(f"discord:{self.discord}")
        if self.lastfm is LastfmStatus.EXPIRED:
            found.add("lastfm:expired")
        return frozenset(found)

    @property
    def discord_is_fine(self) -> bool:
        """Refrain is doing what was asked of it: showing the status,
        ready to, or quiet because the user asked for quiet. Nothing a
        menu row could add."""
        return self.discord in (
            DiscordStatus.STARTING,
            DiscordStatus.READY,
            DiscordStatus.SHOWING,
            DiscordStatus.SHOWING_MINIMAL,
            DiscordStatus.PAUSED,
            DiscordStatus.PRIVACY_OFF,
        )

    @property
    def lastfm_is_fine(self) -> bool:
        """As ``discord_is_fine``. Plays waiting in the queue are not
        fine: they explain why Last.fm shows nothing yet."""
        return self.lastfm in (
            LastfmStatus.OFF,
            LastfmStatus.CONNECTED_OFF,
            LastfmStatus.SCROBBLING,
        )

    @property
    def discord_blocked(self) -> bool:
        """Nothing is reaching Discord, and no song will change that.

        Wider than ``needs_attention``, which is only what the user can
        put right: "not answering" belongs here too. Narrower in one
        place — Discord simply being closed is an ordinary afternoon.
        """
        return self.discord in (
            DiscordStatus.NOT_SET_UP,
            DiscordStatus.REJECTED,
            DiscordStatus.NOT_LOGGED_IN,
            DiscordStatus.ERROR,
        )


class ServiceStatus(QObject):
    """Merges the daemon's live states with the startup check's Last.fm verdict."""

    changed = Signal(object)  # StatusSnapshot

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._live = StatusSnapshot()
        self._lastfm_rejected = False
        self._shown = self._merged()

    @property
    def snapshot(self) -> StatusSnapshot:
        return self._shown

    def set_discord(self, state: str, detail: str) -> None:
        self._live = replace(self._live, discord=DiscordStatus(state), discord_detail=detail)
        self._publish()

    def set_lastfm(self, state: str, detail: str) -> None:
        self._live = replace(self._live, lastfm=LastfmStatus(state), lastfm_detail=detail)
        self._publish()

    def set_startup_check(self, lastfm, _discord=None) -> None:
        from refrain.startup_check import INVALID

        self._lastfm_rejected = lastfm.state == INVALID
        self._publish()

    def forget_lastfm_check(self) -> None:
        """New credentials make the last verdict about the old ones."""
        self._lastfm_rejected = False
        self._publish()

    def _merged(self) -> StatusSnapshot:
        live = self._live
        if self._lastfm_rejected and live.lastfm in (LastfmStatus.SCROBBLING, LastfmStatus.WAITING):
            return replace(live, lastfm=LastfmStatus.EXPIRED, lastfm_detail="")
        return live

    def _publish(self) -> None:
        merged = self._merged()
        if merged != self._shown:
            self._shown = merged
            self.changed.emit(merged)
