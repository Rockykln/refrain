"""Smoke import tests.

Imports modules that don't pull Qt / D-Bus / pypresence to confirm they
don't depend on anything ambient. Modules that *do* require those (daemon,
ui, sources/mpris, sources/bluetooth, discord_rpc) are validated by
`python -m compileall` in CI instead.
"""

from __future__ import annotations


def test_lightweight_modules_import():
    import refrain
    import refrain.autostart  # noqa: F401
    import refrain.config  # noqa: F401
    import refrain.cover_art  # noqa: F401
    import refrain.logging_setup  # noqa: F401
    import refrain.paths  # noqa: F401
    import refrain.sources  # noqa: F401
    import refrain.sources.base  # noqa: F401

    assert refrain.__version__
