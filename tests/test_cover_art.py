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
    payload = {"results": [{"artworkUrl100": "https://is1.example/100x100bb.jpg"}]}
    monkeypatch.setattr(
        cover_art.urllib.request, "urlopen", lambda *a, **kw: _fake_response(payload)
    )
    url = cover_art.lookup_cover_url("Artist", "Title", "Album")
    assert url == "https://is1.example/600x600bb.jpg"


def test_lookup_returns_none_for_blank_input(cover_art):
    assert cover_art.lookup_cover_url("", "Title") is None
    assert cover_art.lookup_cover_url("Artist", "") is None


def test_lookup_caches_positive_result(monkeypatch, cover_art):
    payload = {"results": [{"artworkUrl100": "https://is1.example/100x100bb.jpg"}]}
    calls = {"n": 0}

    def _opener(*a, **kw):
        calls["n"] += 1
        return _fake_response(payload)

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _opener)

    a = cover_art.lookup_cover_url("Artist", "Title", "Album")
    b = cover_art.lookup_cover_url("Artist", "Title", "Album")
    assert a == b
    assert calls["n"] == 1, "second lookup must hit the cache, not the network"


def test_lookup_caches_negative_result(monkeypatch, cover_art):
    """Empty-results queries should also cache, to avoid re-hitting the API."""
    calls = {"n": 0}

    def _opener(*a, **kw):
        calls["n"] += 1
        return _fake_response({"results": []})

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _opener)

    a = cover_art.lookup_cover_url("Nobody", "Nothing", "Nowhere")
    b = cover_art.lookup_cover_url("Nobody", "Nothing", "Nowhere")
    assert a is None
    assert b is None
    assert calls["n"] == 1


def test_lookup_returns_none_on_network_error(monkeypatch, cover_art):
    def _boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _boom)
    assert cover_art.lookup_cover_url("X", "Y", "Z") is None


def test_cache_key_is_stable_and_lowercase(cover_art):
    a = cover_art._key("Drake", "One Dance", "Views")
    b = cover_art._key("DRAKE", "one dance", "VIEWS")
    assert a == b
