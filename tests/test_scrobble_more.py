"""Last.fm client replies and Scrobbler failure paths that must never lose a play or crash."""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

from refrain import scrobble
from refrain.scrobble import LastfmClient, LastfmError, Scrobbler
from refrain.scrobble_queue import ScrobbleQueue
from tests.test_scrobble import _resp
from tests.test_scrobbler import T0, FakeClient, _cfg, _next_song, _OfflineClient, _run, _t


def _client():
    return LastfmClient("12345apikey", "12345secret", session_key="12345session")


def _capture(monkeypatch, payload):
    seen = []

    def fake(req, *a, **kw):
        seen.append(req)
        return _resp(payload)

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", fake)
    return seen


def _http_error(code, body: bytes):
    return urllib.error.HTTPError(scrobble.API_ROOT, code, "Forbidden", {}, io.BytesIO(body))


def test_missing_api_credentials_fail_before_any_request(monkeypatch):
    seen = _capture(monkeypatch, {})
    with pytest.raises(LastfmError, match="not configured"):
        LastfmClient("", "12345secret").get_token()
    with pytest.raises(LastfmError, match="not configured"):
        LastfmClient("12345apikey", "  ").get_token()
    assert seen == []


def test_an_http_error_carries_last_fm_s_own_error_code(monkeypatch):
    body = json.dumps({"error": 9, "message": "Invalid session key"}).encode()

    def refuse(*a, **kw):
        raise _http_error(403, body)

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", refuse)
    with pytest.raises(LastfmError) as ei:
        _client().update_now_playing("Neon Harbor", "Glass Tides")
    assert ei.value.invalid_session is True


def test_an_http_error_without_json_is_a_retryable_failure(monkeypatch):
    def refuse(*a, **kw):
        raise _http_error(503, b"<html>Service Unavailable</html>")

    monkeypatch.setattr(scrobble.urllib.request, "urlopen", refuse)
    with pytest.raises(LastfmError, match="HTTP 503") as ei:
        _client().scrobble([{"artist": "Neon Harbor", "track": "Glass Tides", "timestamp": T0}])
    assert ei.value.retryable is True


def test_a_non_object_reply_is_an_error(monkeypatch):
    _capture(monkeypatch, ["unexpected"])
    with pytest.raises(LastfmError, match="non-object"):
        _client().get_token()


def test_an_empty_token_is_an_error(monkeypatch):
    _capture(monkeypatch, {"token": "  "})
    with pytest.raises(LastfmError, match="no token"):
        _client().get_token()


def test_a_session_reply_without_a_key_keeps_the_old_session(monkeypatch):
    _capture(monkeypatch, {"session": {"name": "marlowvance"}})
    client = _client()
    with pytest.raises(LastfmError, match="no session key"):
        client.get_session("12345token")
    assert client.session_key == "12345session"


def test_validate_session_returns_the_user_name(monkeypatch):
    seen = _capture(monkeypatch, {"user": {"name": " marlowvance "}})
    assert _client().validate_session() == "marlowvance"
    assert "method=user.getInfo" in seen[0].full_url
    assert "sk=12345session" in seen[0].full_url


def test_validate_session_with_an_odd_reply_returns_no_name(monkeypatch):
    _capture(monkeypatch, {"user": None})
    assert _client().validate_session() == ""


def test_validate_session_needs_a_session_key(monkeypatch):
    seen = _capture(monkeypatch, {})
    with pytest.raises(LastfmError, match="not connected"):
        LastfmClient("12345apikey", "12345secret").validate_session()
    with pytest.raises(LastfmError, match="not connected"):
        LastfmClient("12345apikey", "12345secret").update_now_playing("Kite Theory", "Afterimage")
    assert seen == []


def test_now_playing_sends_album_and_length_only_when_known(monkeypatch):
    seen = _capture(monkeypatch, {"nowplaying": {}})
    client = _client()
    client.update_now_playing("Oskar Lind", "Ferrous", "Iron Garden", 231)
    client.update_now_playing("Oskar Lind", "Ferrous")
    full, bare = (req.data.decode() for req in seen)
    assert "album=Iron+Garden" in full and "duration=231" in full
    assert "album=" not in bare and "duration=" not in bare
    assert all(req.get_method() == "POST" for req in seen)


def test_an_empty_batch_sends_nothing(monkeypatch):
    seen = _capture(monkeypatch, {})
    assert _client().scrobble([]) == 0
    assert seen == []


def test_scrobble_sends_the_length_of_each_track(monkeypatch):
    seen = _capture(monkeypatch, {"scrobbles": {"@attr": {"accepted": 1}}})
    item = {"artist": "Wren & Ash", "track": "Salt Flats", "timestamp": T0, "duration": 198.7}
    assert _client().scrobble([item]) == 1
    assert "duration%5B0%5D=198" in seen[0].data.decode()


@pytest.mark.parametrize(
    "reply", [{"scrobbles": {"@attr": {"accepted": "many"}}}, {"scrobbles": "ok"}]
)
def test_an_unreadable_accepted_count_assumes_all_were_taken(monkeypatch, reply):
    _capture(monkeypatch, reply)
    items = [
        {"artist": "Velvet Static", "track": "Late Train Home", "timestamp": T0},
        {"artist": "Kite Theory", "track": "Overexposed", "timestamp": T0 + 240},
    ]
    assert _client().scrobble(items) == 2


# --------------------------------------------------------------------------- #
# Scrobbler                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides",
    [{"enabled": False}, {"api_key": ""}, {"shared_secret": ""}, {"session_key": ""}],
)
def test_an_incomplete_setup_has_no_client_and_queues_nothing(tmp_path, overrides):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(**overrides), queue=q, current_path=tmp_path / "current.json")
    assert sc._client is None
    clock = [float(T0), 1000.0]
    _run(sc, _t("Northbound", artist="The Quiet Hours"), 110, clock)
    _next_song(sc, clock)
    sc._executor.shutdown(wait=True)
    assert len(q) == 0


def test_a_failing_on_queued_hook_does_not_lose_the_scrobble(tmp_path):
    def broken(_artist, _title):
        raise RuntimeError("history is gone")

    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, on_queued=broken, current_path=tmp_path / "current.json")
    sc._client = _OfflineClient()
    clock = [float(T0), 1000.0]
    _run(sc, _t("Paper Satellites", artist="Marlow Vance"), 110, clock)
    _next_song(sc, clock)
    sc._executor.shutdown(wait=True)
    assert [p["track"] for p in q.pending()] == ["Paper Satellites"]


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2]",
        json.dumps({"key": "k", "artist": "Neon Harbor"}),
        json.dumps(
            {
                "key": "mpris|Glass Tides|Neon Harbor|",
                "artist": "Neon Harbor",
                "title": "Glass Tides",
                "started_unix": "soon",
                "played_ms": 60_000,
                "saved_at": T0,
            }
        ),
        json.dumps(
            {
                "key": "mpris|Glass Tides||",
                "artist": "",
                "title": "Glass Tides",
                "started_unix": T0,
                "played_ms": 60_000,
                "saved_at": T0,
            }
        ),
    ],
)
def test_a_damaged_saved_play_is_ignored(tmp_path, content):
    current = tmp_path / "current.json"
    current.write_text(content, encoding="utf-8")
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, current_path=current)
    sc._client = _OfflineClient()
    assert sc._resume is None
    clock = [float(T0) + 5, 1000.0]
    _run(sc, _t("Glass Tides", artist="Neon Harbor", album=""), 10, clock)
    sc._executor.shutdown(wait=True)
    assert len(q) == 0


def test_a_saved_play_that_cannot_be_written_still_queues_on_quit(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("a file where the folder should be")
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, current_path=blocker / "current.json")
    sc._client = _OfflineClient()
    clock = [float(T0), 1000.0]
    _run(sc, _t("Ferrous", artist="Oskar Lind"), 130, clock)
    sc.shutdown()
    assert [p["track"] for p in q.pending()] == ["Ferrous"]
    assert blocker.read_text() == "a file where the folder should be"


def test_a_saved_play_that_cannot_be_removed_is_only_logged(tmp_path, caplog):
    current = tmp_path / "current.json"
    current.mkdir()
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, current_path=current)
    sc._client = _OfflineClient()
    with caplog.at_level(logging.DEBUG, logger="refrain.scrobble"):
        sc.reconfigure(_cfg(username="wrenandash"))
    sc._executor.shutdown(wait=True)
    assert current.is_dir()
    assert "Could not remove" in caplog.text


def test_quitting_with_nothing_playing_clears_the_saved_play(tmp_path):
    current = tmp_path / "current.json"
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, current_path=current)
    sc._client = _OfflineClient()
    clock = [float(T0), 1000.0]
    _run(sc, _t("Salt Flats", artist="Wren & Ash"), 70, clock)
    assert current.exists()
    sc.update(_t(""), 0, privacy_off=False, now_wall=clock[0], now_mono=clock[1])
    sc.shutdown()
    assert not current.exists()


def test_a_rejected_session_on_now_playing_stops_further_now_playing(tmp_path, caplog):
    class _Rejecting(FakeClient):
        def update_now_playing(self, *a, **kw):
            super().update_now_playing(*a, **kw)
            raise LastfmError("Last.fm error 9: Invalid session key", code=9)

    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, current_path=tmp_path / "current.json")
    sc._client = client = _Rejecting()
    with caplog.at_level(logging.WARNING, logger="refrain.scrobble"):
        sc._do_now_playing("Ilse Moreau", "Silk Road Radio", "", 240)
    assert sc._session_invalid is True
    assert "session invalid" in caplog.text
    clock = [float(T0), 1000.0]
    _run(sc, _t("Silk Road Radio", artist="Ilse Moreau"), 4, clock)
    sc._executor.shutdown(wait=True)
    assert len(client.now_playing) == 1


def test_now_playing_survives_an_unexpected_client_error(tmp_path):
    class _Crashing(FakeClient):
        def update_now_playing(self, *a, **kw):
            raise ValueError("bad reply")

    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    sc = Scrobbler(_cfg(), queue=q, current_path=tmp_path / "current.json")
    sc._client = _Crashing()
    sc._do_now_playing("Kite Theory", "Afterimage", "Overexposed", 200)
    sc._client = None
    sc._do_now_playing("Kite Theory", "Afterimage", "Overexposed", 200)
    sc._executor.shutdown(wait=True)
    assert sc._session_invalid is False


def test_a_drain_after_disconnecting_keeps_the_queue(tmp_path):
    q = ScrobbleQueue(path=tmp_path / "q.jsonl")
    q.enqueue({"artist": "Velvet Static", "track": "Late Train Home", "timestamp": T0})
    sc = Scrobbler(_cfg(), queue=q, current_path=tmp_path / "current.json")
    sc._client = None
    sc._drain_inflight = True
    sc._do_drain()
    with pytest.raises(LastfmError, match="client gone"):
        sc._submit_batch(q.pending())
    sc._executor.shutdown(wait=True)
    assert sc._drain_inflight is False
    assert [p["track"] for p in q.pending()] == ["Late Train Home"]
