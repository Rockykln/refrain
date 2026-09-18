"""Smoke imports for the modules that need no Qt, D-Bus or pypresence.
The others are covered by `python -m compileall` in CI."""

from __future__ import annotations


def test_lightweight_modules_import():
    import refrain
    import refrain.autostart
    import refrain.config
    import refrain.cover_art
    import refrain.logging_setup
    import refrain.paths
    import refrain.sources
    import refrain.sources.base

    assert refrain.__version__
