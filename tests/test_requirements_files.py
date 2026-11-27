"""The hand-written requirements files have to say what pyproject says.

Nothing in CI reads them, so they drifted: requirements-dev.txt was two
packages short and anyone following it skipped the signature tests
without being told.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _names(lines: list[str]) -> set[str]:
    """Package names only — the files pin the same way pyproject does."""
    out = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        out.add(line.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower())
    return out


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_requirements_txt_matches_the_runtime_dependencies():
    wanted = _names(_pyproject()["project"]["dependencies"])
    got = _names((ROOT / "requirements.txt").read_text().splitlines())
    assert got == wanted


def test_requirements_dev_txt_matches_the_dev_extra():
    wanted = _names(_pyproject()["project"]["optional-dependencies"]["dev"])
    got = _names((ROOT / "requirements-dev.txt").read_text().splitlines())
    assert got == wanted


def test_the_dev_file_pulls_in_the_runtime_one():
    first = (ROOT / "requirements-dev.txt").read_text().splitlines()[0].strip()
    assert first == "-r requirements.txt"
