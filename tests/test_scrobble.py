"""Last.fm client + scrobble decision logic.

HTTP is mocked at the urllib level (same pattern as test_updater) so
the suite stays hermetic — no network, no real Last.fm account.
"""

from __future__ import annotations

import hashlib
import io
import json

import pytest

from refrain import scrobble
from refrain.scrobble import (
    LastfmClient,
    LastfmError,
    api_signature,
    should_scrobble,
)

# --------------------------------------------------------------------------- #
# api_signature                                                               #
# --------------------------------------------------------------------------- #


def test_api_signature_is_sorted_md5_with_secret():
    params = {"method": "auth.getToken", "api_key": "KEY"}
    expected = hashlib.md5(
        ("api_keyKEYmethodauth.getToken" + "SECRET").encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()
    assert api_signature(params, "SECRET") == expected


def test_api_signature_excludes_format_and_callback():
    base = {"method": "m", "api_key": "k"}
    with_fmt = {**base, "format": "json", "callback": "cb"}
    assert api_signature(base, "s") == api_signature(with_fmt, "s")


# --------------------------------------------------------------------------- #
# should_scrobble — Last.fm's documented rule                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "played_ms,duration_ms,expected",
    [
        (0, 200_000, False),  # nothing played
        (99_000, 200_000, False),  # < half
        (100_000, 200_000, True),  # exactly half
        (150_000, 200_000, True),  # past half
        (240_000, 10_000_000, True),  # 4 min rule on a very long track
        (239_000, 10_000_000, False),  # just under 4 min, < half of long track
        (20_000, 25_000, False),  # track ≤ 30 s never scrobbles
        (30_000, 30_000, False),  # exactly 30 s still excluded (> 30 s required)
        (-5, 200_000, False),  # defensive: negative played time
    ],
)
def test_should_scrobble_rule(played_ms, duration_ms, expected):
    assert should_scrobble(played_ms, duration_ms) is expected


# --------------------------------------------------------------------------- #
# LastfmClient — mocked transport                                             #
# --------------------------------------------------------------------------- #


def _resp(payload):
    class _R:
        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a, **kw):
            return self._buf.read(*a, **kw)

    return _R(json.dumps(payload).encode("utf-8"))


def _client():
    return LastfmClient("APIKEY", "SECRET", session_key="SK")


def test_get_token(monkeypatch):
    monkeypatch.setattr(
        scrobble.urllib.request, "urlopen", lambda *a, **kw: _resp({"token": "TOK"})
    )
    assert _client().get_token() == "TOK"


def test_authorize_url_contains_key_and_token():
    url = _client().authorize_url("TOK")
    assert url.startswith("https://www.last.fm/api/auth/?")
    assert "api_key=APIKEY" in url
    assert "token=TOK" in url


def test_get_session_returns_key_and_name(monkeypatch):
    monkeypatch.setattr(
        scrobble.urllib.request,
        "urlopen",
        lambda *a, **kw: _resp({"session": {"key": "NEWSK", "name": "alice"}}),
    )
    c = LastfmClient("K", "S")
    key, name = c.get_session("TOK")
    assert key == "NEWSK"
    assert name == "alice"
    assert c.session_key == "NEWSK"  # stored on the client too


def test_api_error_raises_with_code(monkeypatch):
    monkeypatch.setattr(
        scrobble.urllib.request,
        "urlopen",
        lambda *a, **kw: _resp({"error": 9, "message": "Invalid session key"}),
    )
    with pytest.raises(LastfmError) as ei:
        _client().update_now_playing("Artist", "Track")
    assert ei.value.code == 9
    assert ei.value.invalid_session is True
    assert ei.value.retryable is False


def test_network_failure_is_retryable(monkeypatch):
    def _boom(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", _boom)
    with pytest.raises(LastfmError) as ei:
        _client().scrobble([{"artist": "A", "track": "T", "timestamp": 1}])
    assert ei.value.code is None
    assert ei.value.retryable is True


def test_transient_service_error_is_retryable(monkeypatch):
    monkeypatch.setattr(
        scrobble.urllib.request,
        "urlopen",
        lambda *a, **kw: _resp({"error": 16, "message": "temporarily unavailable"}),
    )
    with pytest.raises(LastfmError) as ei:
        _client().scrobble([{"artist": "A", "track": "T", "timestamp": 1}])
    assert ei.value.retryable is True


def test_scrobble_batch_over_limit_raises(monkeypatch):
    items = [{"artist": "A", "track": str(i), "timestamp": i} for i in range(51)]
    with pytest.raises(LastfmError, match="batch too large"):
        _client().scrobble(items)


def test_scrobble_accepted_count(monkeypatch):
    captured = {}

    def _fake(req, *a, **kw):
        captured["data"] = req.data
        return _resp({"scrobbles": {"@attr": {"accepted": 2, "ignored": 0}}})

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", _fake)
    n = _client().scrobble(
        [
            {"artist": "A", "track": "T1", "timestamp": 100, "album": "Al"},
            {"artist": "B", "track": "T2", "timestamp": 200},
        ]
    )
    assert n == 2
    body = captured["data"].decode()
    assert "artist%5B0%5D=A" in body  # artist[0]=A url-encoded
    assert "track%5B1%5D=T2" in body
    assert "sk=SK" in body
    assert "api_sig=" in body


def test_scrobble_without_session_key_raises():
    c = LastfmClient("K", "S")  # no session key
    with pytest.raises(LastfmError, match="not connected"):
        c.scrobble([{"artist": "A", "track": "T", "timestamp": 1}])
