"""Cover lookup edge cases: odd catalog answers, damaged cache files, failed disk writes."""

from __future__ import annotations

import io
import os

import pytest

from tests.test_cover_art import _install, _song, _Store, cover_art  # noqa: F401


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize(
    "env,country",
    [
        ({"LANG": "C.UTF-8"}, "US"),
        ({"LANG": ""}, "US"),
        ({"LANG": "de_DE.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8"}, "FR"),
        ({"LANG": "de_DE.UTF-8", "LC_ALL": "sv_SE.UTF-8"}, "SE"),
    ],
)
def test_store_country_follows_the_locale_or_defaults_to_us(
    cover_art,  # noqa: F811
    monkeypatch,
    env,
    country,
):
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert cover_art._store_country() == country


def test_a_c_locale_searches_only_the_us_store(cover_art, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LANG", "C.UTF-8")
    store = _install(monkeypatch, cover_art, _Store())
    cover_art.lookup_track_info("Marlow Vance", "Paper Satellites")
    assert [c for _t, c in store.queries] == ["US", "US"]


def test_a_broken_track_length_keeps_the_cover(cover_art, monkeypatch):  # noqa: F811
    hit = _song("Neon Harbor", "Glass Tides", "Low Light", ms="n/a")
    _install(monkeypatch, cover_art, _Store(default=[hit]))
    info = cover_art.lookup_track_info("Neon Harbor", "Glass Tides", "Low Light")
    assert info.cover_url == "https://is1.example/600x600bb.jpg"
    assert info.duration_ms == 0


def test_junk_results_are_skipped_not_fatal(cover_art, monkeypatch):  # noqa: F811
    junk = [
        "not a dict",
        {"kind": "music-video", "artistName": "Oskar Lind", "trackName": "Ferrous"},
        {"kind": "song", "artistName": "", "trackName": "Ferrous"},
        {"kind": "song", "artistName": "Oskar Lind", "trackName": None},
        _song("Oskar Lind", "Ferrous", "Iron Garden"),
    ]
    _install(monkeypatch, cover_art, _Store(default=junk))
    info = cover_art.lookup_track_info("Oskar Lind", "Ferrous", "Iron Garden")
    assert info.song_url == "https://music.apple.com/de/song/ferrous/1"


@pytest.mark.parametrize("payload", [["a", "list"], {"results": "none"}, "error"])
def test_an_answer_of_the_wrong_shape_is_a_miss(cover_art, monkeypatch, payload):  # noqa: F811
    import json

    monkeypatch.setattr(
        cover_art.urllib.request,
        "urlopen",
        lambda *a, **kw: _Resp(json.dumps(payload).encode()),
    )
    assert cover_art.lookup_track_info("Kite Theory", "Overexposed") == cover_art.TrackLookup()


def test_a_non_list_result_set_matches_nothing(cover_art):  # noqa: F811
    assert cover_art._best_match({"results": []}, "Wren & Ash", "Salt Flats", "") is None


def test_a_title_of_only_punctuation_matches_nothing(cover_art):  # noqa: F811
    results = [_song("Velvet Static", "...")]
    assert cover_art._best_match(results, "Velvet Static", "...", "") is None


@pytest.mark.parametrize(
    "content",
    [
        "https://is1.example/600x600bb.jpg\nx\nlong\nv2\n1700000000\n",
        "https://is1.example/600x600bb.jpg\nx\n215000\nv2\nyesterday\n",
    ],
)
def test_a_damaged_cache_entry_is_looked_up_again(cover_art, monkeypatch, content):  # noqa: F811
    key = cover_art._key("The Quiet Hours", "Northbound", "")
    d = cover_art.cover_cache_dir()
    d.mkdir(parents=True)
    (d / f"{key}.txt").write_text(content)
    store = _install(
        monkeypatch, cover_art, _Store(default=[_song("The Quiet Hours", "Northbound")])
    )
    assert cover_art.lookup_track_info("The Quiet Hours", "Northbound").duration_ms == 215000
    assert store.queries


def test_an_unreadable_cache_entry_is_looked_up_again(cover_art, monkeypatch):  # noqa: F811
    key = cover_art._key("Velvet Static", "Late Train Home", "")
    d = cover_art.cover_cache_dir()
    d.mkdir(parents=True)
    (d / f"{key}.txt").write_bytes(b"\xff\xfe\xfa not utf-8\n")
    store = _install(
        monkeypatch, cover_art, _Store(default=[_song("Velvet Static", "Late Train Home")])
    )
    assert cover_art.lookup_track_info("Velvet Static", "Late Train Home").cover_url
    assert store.queries


def test_a_cache_that_cannot_be_written_still_returns_the_answer(cover_art, monkeypatch):  # noqa: F811
    cache = cover_art.cover_cache_dir()
    cache.parent.mkdir(parents=True)
    cache.write_text("a file where the folder should be")
    _install(monkeypatch, cover_art, _Store(default=[_song("Ilse Moreau", "Silk Road Radio")]))
    info = cover_art.lookup_track_info("Ilse Moreau", "Silk Road Radio")
    assert info.cover_url == "https://is1.example/600x600bb.jpg"


def test_a_missing_history_cover_is_copied_from_the_first_source_that_has_it(
    cover_art,  # noqa: F811
    tmp_path,
):
    url = "https://is1.example/glass-tides/600x600bb.jpg"
    name = cover_art.image_name(url)
    empty, full = tmp_path / "empty", tmp_path / "full"
    empty.mkdir()
    full.mkdir()
    (empty / name).write_bytes(b"")
    (full / name).write_bytes(b"\xff\xd8cover")

    cover_art.keep_cover_images({url, ""}, [empty, full])

    dest = cover_art.image_path_for_url(url)
    assert dest.read_bytes() == b"\xff\xd8cover"
    assert os.stat(dest.parent).st_mode & 0o777 == 0o700
    assert not dest.with_suffix(".jpg.tmp").exists()


def test_a_failed_copy_leaves_no_partial_cover(cover_art, monkeypatch, tmp_path):  # noqa: F811
    url = "https://is1.example/ferrous/600x600bb.jpg"
    name = cover_art.image_name(url)
    source = tmp_path / "old-covers"
    source.mkdir()
    (source / name).write_bytes(b"\xff\xd8cover")

    def half_copy(src, dst):
        open(dst, "wb").write(b"\xff")
        raise OSError("disk full")

    monkeypatch.setattr(cover_art.shutil, "copyfile", half_copy)
    cover_art.keep_cover_images({url}, [source])
    assert list(cover_art.cover_cache_dir().iterdir()) == []


def test_keeping_covers_without_a_cache_folder_does_nothing(cover_art, tmp_path):  # noqa: F811
    cover_art.keep_cover_images({"https://is1.example/none.jpg"}, [tmp_path])
    assert not cover_art.cover_cache_dir().exists()


def test_a_failed_download_returns_none_and_writes_nothing(cover_art, tmp_path):  # noqa: F811
    dest = tmp_path / "covers" / "cover.jpg"
    dest.parent.mkdir()
    assert cover_art.download_cover_image("https://is1.example/c.jpg", dest) is None
    assert list(dest.parent.iterdir()) == []


def test_a_failed_image_write_leaves_no_temp_file(cover_art, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(cover_art.urllib.request, "urlopen", lambda *a, **kw: _Resp(b"\xff\xd8"))

    def refuse(_src, _dst):
        raise OSError("read-only")

    monkeypatch.setattr(cover_art.os, "replace", refuse)
    dest = tmp_path / "covers" / "cover.jpg"
    dest.parent.mkdir()
    assert cover_art.download_cover_image("https://is1.example/c.jpg", dest) is None
    assert list(dest.parent.iterdir()) == []
