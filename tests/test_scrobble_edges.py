"""scrobble.py edge cases: a non-https endpoint refused, an answer that doesn't fit,
a LastfmError passed through unwrapped, and a drain that skips because the last one
was too recent."""

from __future__ import annotations

import urllib.error

import pytest

from refrain import https_only, scrobble
from refrain.scrobble import LastfmClient, LastfmError
from tests.test_scrobble import _resp
from tests.test_scrobbler import _cfg, _scrobbler


def _client():
    return LastfmClient("12345apikey", "12345secret", session_key="12345session")


def test_a_non_https_api_root_is_refused(monkeypatch):
    """Defence in depth: _call must never send credentials over plain http."""
    monkeypatch.setattr(scrobble, "API_ROOT", "http://ws.audioscrobbler.com/2.0/")
    calls = []
    monkeypatch.setattr(https_only.opener(), "open", lambda *a, **kw: calls.append(a))
    with pytest.raises(LastfmError, match="non-HTTPS"):
        _client().update_now_playing("Wren & Ash", "Salt Flats")
    assert calls == []


def test_a_refused_redirect_is_reported_as_a_failed_request(monkeypatch):
    """The opener stops a redirect to http before the scrobble leaves the machine."""

    def refuse(*_a, **_kw):
        raise urllib.error.URLError("refusing redirect to non-https URL http://ws.example/")

    monkeypatch.setattr(https_only.opener(), "open", refuse)
    with pytest.raises(LastfmError, match="refusing redirect"):
        _client().update_now_playing("Wren & Ash", "Salt Flats")


def test_an_answer_past_the_read_limit_fails_the_call(monkeypatch):
    monkeypatch.setattr(scrobble, "_MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr(https_only.opener(), "open", lambda *a, **kw: _resp({"nowplaying": {}}))
    with pytest.raises(LastfmError, match="larger than"):
        _client().update_now_playing("Wren & Ash", "Salt Flats")


def test_a_lastfm_error_from_the_transport_is_not_rewrapped(monkeypatch):
    """A LastfmError raised while talking to Last.fm keeps its own code and message."""
    original = LastfmError("session revoked mid-request", code=9)

    def boom(*_a, **_kw):
        raise original

    monkeypatch.setattr(https_only.opener(), "open", boom)
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
