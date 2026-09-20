"""cover_art.py edge cases: a non-https search endpoint refused outright, and a cover
cache whose stray-file cleanup fails partway through."""

from __future__ import annotations

from tests.test_cover_art import cover_art  # noqa: F401


def test_a_non_https_search_endpoint_is_refused(monkeypatch, cover_art):  # noqa: F811
    """Defence in depth: _query must never call urlopen against a non-https URL."""
    monkeypatch.setattr(cover_art, "_ITUNES_SEARCH", "http://itunes.apple.com/search")
    calls = []
    monkeypatch.setattr(cover_art.urllib.request, "urlopen", lambda *a, **kw: calls.append(a))
    assert cover_art._query("term", "US", 10) == []
    assert calls == []


class _CleanupFailsDir:
    """A cache dir that can be checked but not listed, as if it vanished mid-loop."""

    def is_dir(self):
        return True

    def glob(self, _pattern):
        raise OSError("directory changed underneath us")


def test_cover_cleanup_survives_a_listing_failure(monkeypatch, cover_art, caplog):  # noqa: F811
    monkeypatch.setattr(cover_art, "cover_cache_dir", _CleanupFailsDir)
    with caplog.at_level("DEBUG", logger="refrain.cover_art"):
        cover_art.keep_cover_images(set(), [])
    assert any("Cover cleanup failed" in r.getMessage() for r in caplog.records)


def test_a_tagged_title_is_also_looked_up_without_its_tag():
    from refrain.cover_art import bare_title

    assert bare_title("HYPA HYPA - Remix") == "HYPA HYPA"
    assert bare_title("UP (HardTekk)") == "UP"
    assert bare_title("Glass Tides") == "Glass Tides"
    # Nothing left over means the tag was the whole title: keep it.
    assert bare_title("(Untitled)") == "(Untitled)"


def test_the_catalog_album_comes_back_with_the_lookup(monkeypatch, cover_art):  # noqa: F811
    from tests.test_cover_art import _install, _song, _Store

    _install(
        monkeypatch, cover_art, _Store(default=[_song("Oskar Lind", "Ferrous", "Iron Garden")])
    )
    found = cover_art.lookup_track_info("Oskar Lind", "Ferrous")
    assert found.album == "Iron Garden"
    # And again from the cache written for it.
    assert cover_art.lookup_track_info("Oskar Lind", "Ferrous").album == "Iron Garden"


def test_an_unknown_tag_is_found_via_its_bare_title(monkeypatch, cover_art):  # noqa: F811
    """A "(VIP Mix)" tag isn't a remaster/version/live/... tag clean_title knows, so only
    the broader bare_title fallback strips it before asking the catalog again."""
    from tests.test_cover_art import _install, _song, _Store

    hit = _song("Solstice", "Night Runner")
    store = _install(monkeypatch, cover_art, _Store({("Night Runner", "DE"): [hit]}))
    info = cover_art.lookup_track_info("Solstice", "Night Runner (VIP Mix)")
    assert info.cover_url
    assert ("Night Runner", "DE") in store.queries


def test_a_cache_entry_with_a_corrupted_number_is_looked_up_again(monkeypatch, cover_art):  # noqa: F811
    from tests.test_cover_art import _install, _song, _Store

    cover_art.cover_cache_dir().mkdir(parents=True, exist_ok=True)
    key = cover_art._key("Old", "Track", "Album")
    (cover_art.cover_cache_dir() / f"{key}.txt").write_text(
        f"url\nsong\nnot-a-number\n{cover_art._CACHE_VERSION}\nnot-a-number\nAlbum\n"
    )
    store = _install(monkeypatch, cover_art, _Store(default=[_song("Old", "Track", "Album")]))
    assert cover_art.lookup_track_info("Old", "Track", "Album").cover_url
    assert store.queries, "the corrupted cache entry must not be trusted"
