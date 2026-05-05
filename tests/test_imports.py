"""Smoke import tests.

Imports modules that don't pull Qt / D-Bus / pypresence to confirm they
don't depend on anything ambient. Modules that *do* require those (daemon,
ui, sources/mpris, sources/bluetooth, discord_rpc) are validated by
`python -m compileall` in CI instead.
"""

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
