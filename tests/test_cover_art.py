"""iTunes Search API lookup: which result is taken, and what is cached."""

from __future__ import annotations

import importlib
import io
import json
import time
import urllib.parse

import pytest


@pytest.fixture
def cover_art(xdg_tmp, monkeypatch):
    """Reload cover_art + paths for the patched XDG env, with the store country pinned to DE."""
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
        monkeypatch, cover_art, _Store(default=[_song("Ilse Moréau", "Silk Road Radio")])
    )
    cover_art.lookup_track_info("Ilse Moréau", "Silk Road Radio")
    assert store.queries[0] == ("Ilse Moréau Silk Road Radio", "DE")


def test_a_result_by_someone_else_is_rejected(monkeypatch, cover_art):
    """A hit by a different artist is not taken just because it comes first."""
    wrong = [_song("黃霄雲", "星辰大海"), _song("Jazz Collective", "Rest in the Moonshine")]
    _install(monkeypatch, cover_art, _Store(default=wrong))
    assert cover_art.lookup_track_info("NA CHUI", "TILL THE END").cover_url == ""


def test_the_matching_result_wins_over_the_first(monkeypatch, cover_art):
    results = [
        _song(
            "Wren & Ash",
            "Close My Eyes (feat. Marlow Vance)",
            art="https://x/wrong/100x100bb.jpg",
        ),
        _song(
            "Wren & Ash & Oskar Lind",
            "Possible (feat. Marlow Vance)",
            art="https://x/right/100x100bb.jpg",
        ),
    ]
    _install(monkeypatch, cover_art, _Store(default=results))
    info = cover_art.lookup_track_info("Wren & Ash & Oskar Lind", "Possible (feat. Marlow Vance)")
    assert info.cover_url == "https://x/right/600x600bb.jpg"


def test_several_artists_fall_back_to_the_first(monkeypatch, cover_art):
    """MPRIS joins every credited artist; the catalog often lists fewer."""
    hit = _song("Wren & Ash", "Only If You Love Me")
    store = _install(
        monkeypatch,
        cover_art,
        _Store({("Wren & Ash Only If You Love Me", "DE"): [hit]}),
    )
    info = cover_art.lookup_track_info(
        "Wren & Ash, Kite Theory & Ilse Moreau", "Only If You Love Me"
    )
    assert info.cover_url
    assert store.queries[0] == ("Wren & Ash, Kite Theory & Ilse Moreau Only If You Love Me", "DE")


def test_feat_and_remaster_tags_leave_the_search(monkeypatch, cover_art):
    hit = _song("Velvet Static", "Late Train Home")
    store = _install(monkeypatch, cover_art, _Store(default=[hit]))
    assert cover_art.lookup_track_info(
        "Velvet Static", "Late Train Home (feat. Ilse Moreau)"
    ).cover_url
    assert store.queries[0][0] == "Velvet Static Late Train Home"


def test_falls_back_to_the_us_store(monkeypatch, cover_art):
    hit = _song("Some Band", "Only In America")
    store = _install(monkeypatch, cover_art, _Store({("Some Band Only In America", "US"): [hit]}))
    assert cover_art.lookup_track_info("Some Band", "Only In America").cover_url
    assert ("Some Band Only In America", "US") in store.queries


@pytest.mark.parametrize(
    "title,clean",
    [
        ("Late Train Home (feat. Ilse Moreau)", "Late Train Home"),
        ("Hey Jude - Remastered 2015", "Hey Jude"),
        ("Song [Radio Edit]", "Song"),
        ("Song (Live)", "Song"),
        ("Possible feat. Marlow Vance", "Possible"),
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
    """Offline must not be cached as "this song has no cover"."""

    def _boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(cover_art.urllib.request, "urlopen", _boom)
    with pytest.raises(LookupError):
        cover_art.lookup_track_info("X", "Y", "Z")
    key = cover_art._key("X", "Y", "Z")
    assert not (cover_art.cover_cache_dir() / f"{key}.txt").exists()


def test_cache_key_is_stable_and_lowercase(cover_art):
    a = cover_art._key("Oskar Lind", "Ferrous", "Iron Garden")
    b = cover_art._key("OSKAR LIND", "ferrous", "IRON GARDEN")
    assert a == b


def test_cache_file_format(monkeypatch, cover_art):
    """Cover URL, song URL, catalog length, format version, checked-at, album."""
    _install(
        monkeypatch, cover_art, _Store(default=[_song("Oskar Lind", "Ferrous", "Iron Garden")])
    )
    cover_art.lookup_track_info("Oskar Lind", "Ferrous", "Iron Garden")
    key = cover_art._key("Oskar Lind", "Ferrous", "Iron Garden")
    lines = (cover_art.cover_cache_dir() / f"{key}.txt").read_text().strip().splitlines()
    assert len(lines) == 6
    assert lines[0].endswith("600x600bb.jpg")
    assert lines[1].startswith("https://music.apple.com/")
    assert lines[2] == "215000"
    assert lines[3] == cover_art._CACHE_VERSION
    assert abs(int(lines[4]) - time.time()) < 60
    assert lines[5] == "Iron Garden"


def test_entries_from_the_old_matcher_are_looked_up_again(monkeypatch, cover_art):
    """Cache entries in the old matcher's format are looked up again."""
    cover_art.cover_cache_dir().mkdir(parents=True, exist_ok=True)
    key = cover_art._key("Old", "Track", "Album")
    (cover_art.cover_cache_dir() / f"{key}.txt").write_text("\n\n0\n")
    store = _install(monkeypatch, cover_art, _Store(default=[_song("Old", "Track")]))
    assert cover_art.lookup_track_info("Old", "Track", "Album").cover_url
    assert store.queries


def test_an_image_is_downloaded_to_the_given_file(monkeypatch, cover_art, tmp_path):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        cover_art.urllib.request, "urlopen", lambda *a, **kw: _Resp(b"\xff\xd8jpeg")
    )
    dest = tmp_path / "cover.jpg"
    assert cover_art.download_cover_image("https://example.com/c.jpg", dest) == dest
    assert dest.read_bytes() == b"\xff\xd8jpeg"
    assert not cover_art.cover_cache_dir().exists()
    assert cover_art.download_cover_image("http://example.com/c.jpg", tmp_path / "x.jpg") is None


def test_keeping_images_clears_out_everything_else_but_the_lookups(cover_art, tmp_path):
    """Images an older version cached by count go; lookup answers stay."""
    d = cover_art.cover_cache_dir()
    d.mkdir(parents=True)
    kept_url = "https://example.com/kept.jpg"
    kept = cover_art.image_path_for_url(kept_url)
    kept.write_bytes(b"kept")
    stray = [d / "0123456789abcdef01234567.jpg", d / "0123456789abcdef01234567.jpg.tmp"]
    for p in stray:
        p.write_bytes(b"old")
    lookup = d / "0123456789abcdef0123456789abcdef.txt"
    lookup.write_text("x\n")

    cover_art.keep_cover_images({kept_url}, [tmp_path])
    assert kept.read_bytes() == b"kept"
    assert not any(p.exists() for p in stray)
    assert lookup.exists()

    cover_art.keep_cover_images(set(), [tmp_path])
    assert not list(d.glob("*.jpg*"))
    assert lookup.exists()
