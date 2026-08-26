"""notify-send argv builder + --print-id parsing.

Covers the cover-replace path: a brand-fallback notification fired when
iTunes is slow, then re-issued with `--replace-id` once the cover lands
so it swaps into the same bubble instead of stacking a second popup.
"""

from __future__ import annotations

from refrain.daemon import build_notify_argv, parse_notify_id


def test_argv_with_image_passes_it_twice():
    argv = build_notify_argv("notify-send", "/tmp/cover.jpg", "Song", "Artist — Album")
    assert argv[0] == "notify-send"
    assert "-i" in argv
    assert argv[argv.index("-i") + 1] == "/tmp/cover.jpg"
    # Same file also goes through the freedesktop image-path hint so KDE
    # doesn't flash the brand badge while loading it.
    assert "--hint" in argv
    assert "string:image-path:file:///tmp/cover.jpg" in argv
    # Title + body are the trailing positionals.
    assert argv[-2:] == ["Song", "Artist — Album"]


def test_argv_without_image_uses_themed_name_and_no_hint():
    argv = build_notify_argv("notify-send", None, "Song", "")
    assert argv[argv.index("-i") + 1] == "refrain"
    assert "--hint" not in argv
    assert argv[-2:] == ["Song", ""]


def test_argv_replace_id_and_print_id():
    argv = build_notify_argv("notify-send", "/c.jpg", "S", "B", replace_id=42, print_id=True)
    assert "--replace-id" in argv
    assert argv[argv.index("--replace-id") + 1] == "42"
    assert "--print-id" in argv


def test_argv_no_replace_id_by_default():
    argv = build_notify_argv("notify-send", None, "S", "B")
    assert "--replace-id" not in argv
    assert "--print-id" not in argv


def test_parse_id_plain():
    assert parse_notify_id("42\n") == 42


def test_parse_id_takes_first_line():
    assert parse_notify_id("17\nsome warning on stderr-merged\n") == 17


def test_parse_id_empty_is_none():
    assert parse_notify_id("") is None
    assert parse_notify_id("   \n") is None


def test_parse_id_non_numeric_is_none():
    # libnotify build without --print-id support, or a wrapper that
    # prints something else — degrade to "no later swap" not a crash.
    assert parse_notify_id("not-an-id") is None
