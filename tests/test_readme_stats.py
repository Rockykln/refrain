"""docs/stats.py: README block replacement and the counting helpers."""

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "docs" / "stats.py"
_spec = importlib.util.spec_from_file_location("readme_stats", _path)
stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stats)

BLOCK = stats.render("1.2.3", [("Commits", 1234), ("Languages", 3)])


def test_render_formats_table_with_thousands_separators():
    assert BLOCK.startswith(stats.START + "\n## Stats\n")
    assert "| Stat | Value |" in BLOCK
    assert "| Commits | 1,234 |" in BLOCK
    assert "| Languages | 3 |" in BLOCK
    assert "As of v1.2.3." in BLOCK
    assert BLOCK.endswith(stats.END)


def test_replace_block_appends_once_then_is_idempotent():
    readme = "# Title\n\nText.\n\n"
    once = stats.replace_block(readme, BLOCK)
    assert once == "# Title\n\nText.\n\n" + BLOCK + "\n"
    assert stats.replace_block(once, BLOCK) == once


def test_replace_block_only_touches_marked_part():
    old = stats.render("1.0.0", [("Commits", 1)])
    readme = "# Title\n\n" + old + "\n"
    assert stats.replace_block(readme, BLOCK) == "# Title\n\n" + BLOCK + "\n"


def test_count_code_lines_skips_blanks_comments_and_docstrings():
    source = '''"""Module docstring
spanning two lines."""

# a comment
import os  # trailing comment counts as code


class A:
    """Class doc."""

    def f(self):
        """Function doc."""
        text = """not a
docstring"""
        return text
'''
    # import, class, def, text = (2 lines), return
    assert stats.count_code_lines(source) == 6


def test_count_lines_handles_missing_final_newline():
    assert stats.count_lines(b"") == 0
    assert stats.count_lines(b"a\nb\n") == 2
    assert stats.count_lines(b"a\nb") == 2


def test_is_binary_detects_nul_bytes():
    assert stats.is_binary(b"\x89PNG\r\n\x1a\n\0\0")
    assert not stats.is_binary(b"<svg></svg>\n")


def test_count_test_defs():
    sources = ["def test_a():\n    pass\n\n\nasync def test_b():\n    pass\n\ndef helper(): pass\n"]
    assert stats.count_test_defs(sources) == 2


def test_parse_collected_reads_both_quiet_levels():
    assert stats.parse_collected("tests/test_a.py: 3\ntests/test_b.py: 12\n") == 15
    assert (
        stats.parse_collected("tests/test_a.py::test_x\ntests/test_a.py::test_y[1]\n\n2 tests\n")
        == 2
    )
    assert stats.parse_collected("no tests ran\n") == 0


def test_languages_counts_english_once():
    paths = [
        "src/refrain/i18n/refrain_en.ts",
        "src/refrain/i18n/refrain_de.ts",
        "src/refrain/i18n/refrain_de.qm",
        "src/refrain/i18n/refrain_zh_CN.ts",
        "README.md",
    ]
    assert stats.languages(paths) == 3
    assert stats.languages([]) == 1


def test_dependencies_reads_project_table():
    pyproject = b'[project]\nname = "x"\ndependencies = ["a>=1", "b"]\n\n[project.optional-dependencies]\ndev = ["c"]\n'
    assert stats.dependencies(pyproject) == 2


def test_words_are_runs_of_non_space():
    assert stats.count_words(b"one  two\nthree\t- four") == 5
    assert stats.count_words(b"") == 0
