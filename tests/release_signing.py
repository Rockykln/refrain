"""packaging/release_key.py loaded as a module, plus a throwaway test key."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[1] / "packaging" / "release_key.py"
_spec = importlib.util.spec_from_file_location("release_key", _TOOL)
release_key = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_key)

SEED = bytes(range(32))
PUBLIC_KEY_HEX = release_key.public_key(SEED).hex()


def signature_for(data: bytes, seed: bytes = SEED) -> bytes:
    return release_key.encode_signature(release_key.sign(seed, data))
