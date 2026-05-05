"""iTunes Search API cover-art lookup.

Real HTTP is mocked at the urllib level so the suite is hermetic.
"""

from __future__ import annotations

import importlib
import io
import json

import pytest


@pytest.fixture
def cover_art(xdg_tmp):
    """Reload the cover_art + paths modules so they pick up the patched XDG env."""
    import refrain.paths

    importlib.reload(refrain.paths)
    import refrain.cover_art as ca

    importlib.reload(ca)
    return ca


def _fake_response(payload: dict):
    """Return a context-manager wrapper that mimics urllib.request.urlopen()."""

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


def test_lookup_upgrades_artwork_to_600(monkeypatch, cover_art):
    payload = {
        "results": [
            {
                "artworkUrl100": "https://is1.example/100x100bb.jpg",
                "trackViewUrl": "https://music.apple.com/us/album/x/1?i=2",
            }
        ]
    }
    monkeypatch.setattr(
        cover_art.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload)
    )
    info = cover_art.lookup_track_info("Artist", "Title", "Album")
    assert info.cover_url == "https://is1.example/600x600bb.jpg"
    assert info.song_url == "https://music.apple.com/us/album/x/1?i=2"


def test_lookup_returns_empty_for_blank_input(cover_art):
    empty = cover_art.TrackLookup()
    assert cover_art.lookup_track_info("", "Title") == empty
    assert cover_art.lookup_track_info("Artist", "") == empty


def test_lookup_caches_positive_result(monkeypatch, cover_art):
    payload = {"results": [{"artworkUrl100": "https://is1.example/100x100bb.jpg"}]}
    calls = {"n": 0}

    def _opener(*a, **kw):
        calls["n"] += 1
        return _fake_response(payload)

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _opener)

    a = cover_art.lookup_track_info("Artist", "Title", "Album")
    b = cover_art.lookup_track_info("Artist", "Title", "Album")
    assert a == b
    assert calls["n"] == 1, "second lookup must hit the cache, not the network"


def test_lookup_caches_negative_result(monkeypatch, cover_art):
    """Empty-results queries should also cache, to avoid re-hitting the API."""
    calls = {"n": 0}

    def _opener(*a, **kw):
        calls["n"] += 1
        return _fake_response({"results": []})

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _opener)

    a = cover_art.lookup_track_info("Nobody", "Nothing", "Nowhere")
    b = cover_art.lookup_track_info("Nobody", "Nothing", "Nowhere")
    assert a.cover_url == "" and a.song_url == ""
    assert b.cover_url == "" and b.song_url == ""
    assert calls["n"] == 1


def test_lookup_returns_empty_on_network_error(monkeypatch, cover_art):
    def _boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _boom)
    info = cover_art.lookup_track_info("X", "Y", "Z")
    assert info.cover_url == "" and info.song_url == ""


def test_cache_key_is_stable_and_lowercase(cover_art):
    a = cover_art._key("Drake", "One Dance", "Views")
    b = cover_art._key("DRAKE", "one dance", "VIEWS")
    assert a == b


def test_cache_file_format_is_two_lines(monkeypatch, cover_art):
    """The on-disk cache stores both URLs; legacy single-line files still load."""
    payload = {
        "results": [
            {
                "artworkUrl100": "https://is1.example/100x100bb.jpg",
                "trackViewUrl": "https://music.apple.com/us/album/x/1?i=2",
            }
        ]
    }
    monkeypatch.setattr(
        cover_art.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload)
    )
    cover_art.lookup_track_info("Drake", "One Dance", "Views")

    key = cover_art._key("Drake", "One Dance", "Views")
    cache_file = cover_art.cover_cache_dir() / f"{key}.txt"
    assert cache_file.exists()
    lines = cache_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(".jpg")
    assert lines[1].startswith("https://music.apple.com/")


def test_legacy_single_line_cache_still_loads(cover_art):
    """An older cache file with just the cover URL on one line is read with empty song_url."""
    cover_art.cover_cache_dir().mkdir(parents=True, exist_ok=True)
    key = cover_art._key("Old", "Track", "Album")
    (cover_art.cover_cache_dir() / f"{key}.txt").write_text("https://is1.example/600x600bb.jpg\n")
    info = cover_art.lookup_track_info("Old", "Track", "Album")
    assert info.cover_url == "https://is1.example/600x600bb.jpg"
    assert info.song_url == ""
