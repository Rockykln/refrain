"""Bare-URL → Markdown-autolink preprocessing for release notes.

Tests refrain.updater.prepare_release_notes directly — no Qt needed.
"""

from __future__ import annotations

from refrain.updater import prepare_release_notes


def test_none_or_empty_returns_placeholder():
    assert prepare_release_notes(None) == "_No release notes provided._"
    assert prepare_release_notes("") == "_No release notes provided._"


def test_compare_url_with_triple_dot_gets_autolinked():
    """The exact pattern that broke before — bare GitHub compare URL."""
    body = "Full Changelog: https://github.com/x/y/compare/v0.1.0...v0.1.1"
    out = prepare_release_notes(body)
    assert "<https://github.com/x/y/compare/v0.1.0...v0.1.1>" in out


def test_existing_markdown_link_left_alone():
    body = "See [the diff](https://github.com/x/y/compare/v0.1.0...v0.1.1) for details."
    out = prepare_release_notes(body)
    assert out == body


def test_existing_autolink_left_alone():
    body = "<https://github.com/x/y/compare/v0.1.0...v0.1.1>"
    out = prepare_release_notes(body)
    assert out == body


def test_inline_code_url_left_alone():
    body = "Run `https://example.com/install.sh` first."
    out = prepare_release_notes(body)
    assert "<https://example.com/install.sh>" not in out


def test_multiple_bare_urls_all_wrapped():
    body = "First: https://a.example/x  Second: https://b.example/y"
    out = prepare_release_notes(body)
    assert out.count("<https://") == 2


def test_url_with_no_special_punctuation_still_wrapped():
    """Even simple URLs benefit from autolink wrapping for consistency."""
    body = "Visit https://example.com for details."
    out = prepare_release_notes(body)
    assert "<https://example.com>" in out
