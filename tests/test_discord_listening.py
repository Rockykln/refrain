"""Discord activity_type defaults to LISTENING (not PLAYING).

Without this, Discord renders the RPC payload as 'Playing Refrain' instead
of 'Listening to <song>', which made early v0.1.x feel like the Discord
status was missing entirely.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Stub pypresence — the test suite shouldn't need Discord IPC available.
# We replace the whole module before refrain.discord_rpc is imported.
_fake_pypresence = MagicMock()


class _FakeActivityType:
    PLAYING = 0
    LISTENING = 2
    WATCHING = 3
    COMPETING = 5


_fake_pypresence.ActivityType = _FakeActivityType
_fake_pypresence.exceptions = MagicMock()
sys.modules.setdefault("pypresence", _fake_pypresence)

from refrain.discord_rpc import DiscordRPC  # noqa: E402


def test_update_defaults_to_listening_activity_type():
    rpc = DiscordRPC("123456789012345678")
    fake_presence = MagicMock()
    rpc._presence = fake_presence  # pretend we're already connected

    rpc.update(details="Track Name", state="Artist • Album", start=1700000000)

    assert fake_presence.update.call_count == 1
    kwargs = fake_presence.update.call_args.kwargs
    assert kwargs["activity_type"] == _FakeActivityType.LISTENING, (
        "Discord renders this as 'Listening to <details>' instead of "
        "'Playing Refrain' — verifying the default isn't accidentally lost"
    )
    assert kwargs["details"] == "Track Name"
    assert kwargs["state"] == "Artist • Album"


def test_update_caller_can_override_activity_type():
    rpc = DiscordRPC("123456789012345678")
    fake_presence = MagicMock()
    rpc._presence = fake_presence

    rpc.update(details="Watching", activity_type=_FakeActivityType.WATCHING)

    kwargs = fake_presence.update.call_args.kwargs
    assert kwargs["activity_type"] == _FakeActivityType.WATCHING
