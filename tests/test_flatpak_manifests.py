"""The manifest that is built locally has to grant what the shipped one does.

The local variant is not in the repository (`.gitignore`: it swaps the
git-tag source for a plain directory), so nothing in CI can see it and
these checks skip there. They are here for the machine that actually
builds it — the local copy sat without the Secret Service name while the
shipped one had it, which is exactly the case a sandbox build proves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

FLATPAK = Path(__file__).resolve().parent.parent / "packaging" / "flatpak"
SHIPPED = FLATPAK / "io.github.Rockykln.Refrain.yaml"
LOCAL = FLATPAK / "io.github.Rockykln.Refrain.test.yaml"

needs_local = pytest.mark.skipif(not LOCAL.exists(), reason="no local build manifest here")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_sandbox_can_reach_the_keyring():
    """Without it the Last.fm secret falls back to a file inside the sandbox."""
    assert "--talk-name=org.freedesktop.secrets" in _load(SHIPPED)["finish-args"]


def test_every_discord_socket_is_reachable():
    """Discord numbers its sockets 0-9, and PTB often holds one of the later ones."""
    granted = set(_load(SHIPPED)["finish-args"])
    for slot in range(10):
        assert f"--filesystem=xdg-run/discord-ipc-{slot}" in granted


@needs_local
def test_the_local_manifest_grants_everything_the_shipped_one_does():
    missing = set(_load(SHIPPED)["finish-args"]) - set(_load(LOCAL)["finish-args"])
    assert missing == set()


@needs_local
@pytest.mark.parametrize("key", ["runtime", "runtime-version", "sdk"])
def test_both_manifests_build_against_the_same_runtime(key):
    assert _load(SHIPPED)[key] == _load(LOCAL)[key]
