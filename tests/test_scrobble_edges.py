"""scrobble.py edge cases: a non-https endpoint refused, a LastfmError passed through
unwrapped, and a drain that skips because the last one was too recent."""

from __future__ import annotations

import pytest

from refrain import scrobble
from refrain.scrobble import LastfmClient, LastfmError
from tests.test_scrobbler import _cfg, _scrobbler


def _client():
    return LastfmClient("12345apikey", "12345secret", session_key="12345session")


def test_a_non_https_api_root_is_refused(monkeypatch):
    """Defence in depth: _call must never send credentials over plain http."""
    monkeypatch.setattr(scrobble, "API_ROOT", "http://ws.audioscrobbler.com/2.0/")
    calls = []
    monkeypatch.setattr(scrobble.urllib.request, "urlopen", lambda *a, **kw: calls.append(a))
    with pytest.raises(LastfmError, match="non-HTTPS"):
        _client().update_now_playing("Wren & Ash", "Salt Flats")
    assert calls == []


def test_a_lastfm_error_from_the_transport_is_not_rewrapped(monkeypatch):
    """A LastfmError raised while talking to Last.fm keeps its own code and message."""
    original = LastfmError("session revoked mid-request", code=9)

    def boom(*_a, **_kw):
        raise original

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", boom)
    with pytest.raises(LastfmError) as ei:
        _client().update_now_playing("Wren & Ash", "Salt Flats")
    assert ei.value is original
    assert ei.value.code == 9


def test_a_drain_too_soon_after_the_last_one_is_skipped(tmp_path):
    sc, q = _scrobbler(tmp_path, cfg=_cfg())
    q.enqueue({"artist": "Wren & Ash", "track": "Salt Flats", "timestamp": 1000})
    sc._last_drain_mono = 1_000.0

    sc._maybe_drain_locked(1_000.0 + 10.0)  # well under _DRAIN_INTERVAL_S

    assert sc._drain_inflight is False
    assert sc._last_drain_mono == 1_000.0
    assert len(q) == 1
