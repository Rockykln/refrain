"""Comment-/unknown-key-preserving config writer.

Regression guard: the daily silent update-check stamps
`update.last_check_ts` and calls `Config.save()` — that must not wipe
user comments or keys a newer Refrain wrote.
"""

from __future__ import annotations

import tomllib

from refrain.config import Config, _merge_into_existing


def _load_text(path):
    return path.read_text(encoding="utf-8")


def test_merge_preserves_comments_blanks_and_unknown_keys():
    existing = (
        "# my hand-written note\n"
        "[discord]\n"
        'client_id = "OLD"  \n'
        "\n"
        "[advanced]\n"
        "# why I bumped this\n"
        "poll_interval_ms = 750\n"
        'future_key = "from a newer refrain"\n'
    )
    c = Config()
    c.discord.client_id = "NEW"
    c.advanced.poll_interval_ms = 750
    out = _merge_into_existing(existing, c.to_dict())

    assert "# my hand-written note" in out
    assert "# why I bumped this" in out
    assert 'future_key = "from a newer refrain"' in out  # unknown key kept
    assert 'client_id = "NEW"' in out  # owned value updated in place
    assert "OLD" not in out
    # Still valid TOML and the unknown key survives a parse.
    parsed = tomllib.loads(out)
    assert parsed["discord"]["client_id"] == "NEW"
    assert parsed["advanced"]["future_key"] == "from a newer refrain"


def test_merge_appends_missing_owned_key_inside_its_section():
    # An older config that predates the `language` field.
    existing = "[advanced]\npoll_interval_ms = 500\n\n[update]\nauto_check = true\n"
    out = _merge_into_existing(existing, Config().to_dict())
    parsed = tomllib.loads(out)
    # New key landed in [advanced], not leaked into [update].
    assert "language" in parsed["advanced"]
    assert "language" not in parsed["update"]


def test_merge_appends_wholly_missing_section():
    existing = '[discord]\nclient_id = "x"\n'
    out = _merge_into_existing(existing, Config().to_dict())
    parsed = tomllib.loads(out)
    # Every section is present even though only [discord] was in the file.
    for section in ("discord", "sources", "privacy", "behavior", "advanced", "update"):
        assert section in parsed
    assert parsed["discord"]["client_id"] == "x" or parsed["discord"]["client_id"] == ""


def test_merge_keeps_user_section_ordering():
    existing = (
        "[update]\nauto_check = false\nlast_check_ts = 0\n\n"
        '[discord]\nclient_id = "z"\n'
    )
    out = _merge_into_existing(existing, Config().to_dict())
    # [update] header still appears before [discord] — file order kept.
    assert out.index("[update]") < out.index("[discord]")


def test_save_then_load_roundtrip_keeps_comment(tmp_path):
    path = tmp_path / "config.toml"
    c = Config()
    c.discord.client_id = "abc123"
    c.save(path)  # no file yet → full serialize

    # User hand-edits in a comment.
    txt = _load_text(path)
    path.write_text("# please keep me\n" + txt, encoding="utf-8")

    # A later save (e.g. updater bumping last_check_ts).
    reloaded = Config.load(path)
    reloaded.update.last_check_ts = 1_700_000_000
    reloaded.save(path)

    final = _load_text(path)
    assert "# please keep me" in final
    parsed = tomllib.loads(final)
    assert parsed["discord"]["client_id"] == "abc123"
    assert parsed["update"]["last_check_ts"] == 1_700_000_000
    # Full Config round-trips with no loss.
    again = Config.load(path)
    assert again.discord.client_id == "abc123"
    assert again.update.last_check_ts == 1_700_000_000


def test_save_without_existing_file_uses_full_serialize(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    Config().save(path)
    parsed = tomllib.loads(_load_text(path))
    assert parsed["privacy"]["mode"] == "full"
