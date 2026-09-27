"""The source package is built from a list of what goes in, so a stray
file in a working tree cannot ride along — and nothing tracked is lost."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _sdist_config() -> dict:
    with (REPO / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["tool"]["hatch"]["build"]["targets"]["sdist"]


def _tracked_top_level() -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {name.split("/", 1)[0] for name in out.split("\0") if name}


def test_the_sdist_names_what_it_ships():
    """An exclude list would let anything new through, including working
    notes that are kept out of git rather than ignored."""
    assert _sdist_config()["include"]


def test_every_tracked_top_level_entry_is_shipped_or_deliberately_left_out():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    config = _sdist_config()
    shipped = set(config["include"])
    # docs/screenshots and the social preview are inside docs/, which is
    # included and then narrowed by `exclude`.
    left_out = {Path(p).parts[0] for p in config["exclude"]}
    missing = sorted(_tracked_top_level() - shipped - left_out)
    assert missing == [], f"not in the sdist include list: {missing}"
