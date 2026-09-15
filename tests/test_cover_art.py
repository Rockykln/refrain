"""iTunes Search API lookup: which result is taken, and what is cached.

Real HTTP is mocked at the urllib level so the suite is hermetic.
"""

from __future__ import annotations

import importlib
import io
import json
import time
import urllib.parse

import pytest


@pytest.fixture
def cover_art(xdg_tmp, monkeypatch):
    """Reload cover_art + paths so they pick up the patched XDG env.

    The store country comes from the locale; pinned to Germany so the
    order of attempts is the same on every machine.
    """
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
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


def _song(artist, title, album="", *, art="https://is1.example/100x100bb.jpg", ms=215000):
    return {
        "kind": "song",
        "artistName": artist,
        "trackName": title,
        "collectionName": album,
        "artworkUrl100": art,
        "trackViewUrl": f"https://music.apple.com/de/song/{title.replace(' ', '-').lower()}/1",
        "trackTimeMillis": ms,
    }


class _Store:
    """Stand-in for the iTunes Search API: answers by (term, country)."""

    def __init__(self, answers=None, default=()):
        self.answers = answers or {}
        self.default = list(default)
        self.queries: list[tuple[str, str]] = []

    def __call__(self, req, *a, **kw):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        key = (q["term"][0], q.get("country", [""])[0])
        self.queries.append(key)
        return _fake_response({"results": self.answers.get(key, self.default)})


def _install(monkeypatch, cover_art, store):
    monkeypatch.setattr(cover_art.urllib.request, "urlopen", store)
    return store


# --------------------------------------------------------------------------- #
# Picking the right result                                                     #
# --------------------------------------------------------------------------- #


def test_lookup_upgrades_artwork_to_600(monkeypatch, cover_art):
    _install(monkeypatch, cover_art, _Store(default=[_song("Artist", "Title")]))
    info = cover_art.lookup_track_info("Artist", "Title", "Album")
    assert info.cover_url == "https://is1.example/600x600bb.jpg"
    assert info.song_url.startswith("https://music.apple.com/")
    assert info.duration_ms == 215000


def test_asks_the_local_store_first(monkeypatch, cover_art):
    store = _install(
        monkeypatch, cover_art, _Store(default=[_song("Die Ärzte", "Schrei nach Liebe")])
    )
    cover_art.lookup_track_info("Die Ärzte", "Schrei nach Liebe")
    assert store.queries[0] == ("Die Ärzte Schrei nach Liebe", "DE")


def test_a_result_by_someone_else_is_rejected(monkeypatch, cover_art):
    """The first hit used to be taken blindly — a Chinese ballad as the
    cover of a German rap track, a jazz collective for a pop song."""
    wrong = [_song("黃霄雲", "星辰大海"), _song("Jazz Collective", "Rest in the Moonshine")]
    _install(monkeypatch, cover_art, _Store(default=wrong))
    assert cover_art.lookup_track_info("NA CHUI", "TILL THE END").cover_url == ""


def test_the_matching_result_wins_over_the_first(monkeypatch, cover_art):
    results = [
        _song(
            "Darius & Finlay",
            "Close My Eyes (feat. Max Landry)",
            art="https://x/wrong/100x100bb.jpg",
        ),
        _song(
            "Darius & Finlay & Adam Bü",
            "Possible (feat. Max Landry)",
            art="https://x/right/100x100bb.jpg",
        ),
    ]
    _install(monkeypatch, cover_art, _Store(default=results))
    info = cover_art.lookup_track_info("Darius & Finlay & Adam Bü", "Possible (feat. Max Landry)")
    assert info.cover_url == "https://x/right/600x600bb.jpg"


def test_several_artists_fall_back_to_the_first(monkeypatch, cover_art):
    """MPRIS joins every credited artist; the catalog often lists fewer."""
    hit = _song("Darius & Finlay", "Only If You Love Me")
    store = _install(
        monkeypatch,
        cover_art,
        _Store({("Darius & Finlay Only If You Love Me", "DE"): [hit]}),
    )
    info = cover_art.lookup_track_info("Darius & Finlay, Lotus & Mougleta", "Only If You Love Me")
    assert info.cover_url
    assert store.queries[0] == ("Darius & Finlay, Lotus & Mougleta Only If You Love Me", "DE")


def test_feat_and_remaster_tags_leave_the_search(monkeypatch, cover_art):
    hit = _song("KYANU", "Talk Talk Talk")
    store = _install(monkeypatch, cover_art, _Store(default=[hit]))
    assert cover_art.lookup_track_info("KYANU", "Talk Talk Talk (feat. Lena Sue)").cover_url
    assert store.queries[0][0] == "KYANU Talk Talk Talk"


def test_falls_back_to_the_us_store(monkeypatch, cover_art):
    hit = _song("Some Band", "Only In America")
    store = _install(monkeypatch, cover_art, _Store({("Some Band Only In America", "US"): [hit]}))
    assert cover_art.lookup_track_info("Some Band", "Only In America").cover_url
    assert ("Some Band Only In America", "US") in store.queries


@pytest.mark.parametrize(
    "title,clean",
    [
        ("Talk Talk Talk (feat. Lena Sue)", "Talk Talk Talk"),
        ("Hey Jude - Remastered 2015", "Hey Jude"),
        ("Song [Radio Edit]", "Song"),
        ("Song (Live)", "Song"),
        ("Possible feat. Max Landry", "Possible"),
        ("(Don't Fear) The Reaper", "(Don't Fear) The Reaper"),
    ],
)
def test_clean_title(cover_art, title, clean):
    assert cover_art.clean_title(title) == clean


# --------------------------------------------------------------------------- #
# Cache                                                                        #
# --------------------------------------------------------------------------- #


def test_lookup_returns_empty_for_blank_input(cover_art):
    empty = cover_art.TrackLookup()
    assert cover_art.lookup_track_info("", "Title") == empty
    assert cover_art.lookup_track_info("Artist", "") == empty


def test_lookup_caches_positive_result(monkeypatch, cover_art):
    store = _install(monkeypatch, cover_art, _Store(default=[_song("Artist", "Title")]))
    a = cover_art.lookup_track_info("Artist", "Title", "Album")
    asked = len(store.queries)
    b = cover_art.lookup_track_info("Artist", "Title", "Album")
    assert a == b
    assert len(store.queries) == asked, "second lookup must hit the cache, not the network"


def test_a_miss_is_cached_but_expires(monkeypatch, cover_art):
    store = _install(monkeypatch, cover_art, _Store())
    assert cover_art.lookup_track_info("Nobody", "Nothing", "Nowhere").cover_url == ""
    asked = len(store.queries)
    cover_art.lookup_track_info("Nobody", "Nothing", "Nowhere")
    assert len(store.queries) == asked
    # Three days on, the catalog gets another chance.
    real = time.time
    monkeypatch.setattr(cover_art.time, "time", lambda: real() + cover_art._NEGATIVE_TTL_S + 1)
    cover_art.lookup_track_info("Nobody", "Nothing", "Nowhere")
    assert len(store.queries) > asked


def test_unreachable_is_an_error_not_a_miss(monkeypatch, cover_art):
    """Offline must not look like "this song has no cover" — that was
    remembered for the whole session and, on disk, for good."""

    def _boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _boom)
    with pytest.raises(LookupError):
        cover_art.lookup_track_info("X", "Y", "Z")
    key = cover_art._key("X", "Y", "Z")
    assert not (cover_art.cover_cache_dir() / f"{key}.txt").exists()


def test_cache_key_is_stable_and_lowercase(cover_art):
    a = cover_art._key("Drake", "One Dance", "Views")
    b = cover_art._key("DRAKE", "one dance", "VIEWS")
    assert a == b


def test_cache_file_format(monkeypatch, cover_art):
    """Cover URL, song URL, catalog length, format version, checked-at."""
    _install(monkeypatch, cover_art, _Store(default=[_song("Drake", "One Dance", "Views")]))
    cover_art.lookup_track_info("Drake", "One Dance", "Views")
    key = cover_art._key("Drake", "One Dance", "Views")
    lines = (cover_art.cover_cache_dir() / f"{key}.txt").read_text().strip().splitlines()
    assert len(lines) == 5
    assert lines[0].endswith("600x600bb.jpg")
    assert lines[1].startswith("https://music.apple.com/")
    assert lines[2] == "215000"
    assert lines[3] == cover_art._CACHE_VERSION
    assert abs(int(lines[4]) - time.time()) < 60


def test_entries_from_the_old_matcher_are_looked_up_again(monkeypatch, cover_art):
    """The old matcher took any first hit and cached misses forever, so
    its entries are neither trusted nor final."""
    cover_art.cover_cache_dir().mkdir(parents=True, exist_ok=True)
    key = cover_art._key("Old", "Track", "Album")
    (cover_art.cover_cache_dir() / f"{key}.txt").write_text("\n\n0\n")
    store = _install(monkeypatch, cover_art, _Store(default=[_song("Old", "Track")]))
    assert cover_art.lookup_track_info("Old", "Track", "Album").cover_url
    assert store.queries
