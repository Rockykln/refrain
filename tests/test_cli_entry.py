"""The console script names a missing Qt system library instead of dying on the import."""

from __future__ import annotations

import builtins
import logging

import pytest

from refrain import cli

FONTCONFIG = "libfontconfig.so.1: cannot open shared object file: No such file or directory"


@pytest.fixture
def app_import_fails(monkeypatch):
    def fail_with(error: str) -> None:
        real = builtins.__import__

        def fake(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "refrain.app":
                raise ImportError(error)
            return real(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", fake)

    return fail_with


def test_a_working_import_runs_the_app(monkeypatch):
    monkeypatch.setattr("refrain.app.main", lambda: 7)
    assert cli.main() == 7


def test_a_qt_library_the_host_lacks_is_named(app_import_fails, monkeypatch, capsys):
    app_import_fails(FONTCONFIG)
    monkeypatch.setattr("refrain.qt_libraries.notify_without_qt", lambda title, text: None)
    assert cli.main() == 1
    assert "libfontconfig.so.1" in capsys.readouterr().err


def test_the_message_is_not_printed_twice(app_import_fails, monkeypatch, capsys):
    """With no handler anywhere, logging.lastResort would put the same text
    on stderr a second time."""
    app_import_fails(FONTCONFIG)
    monkeypatch.setattr("refrain.qt_libraries.notify_without_qt", lambda title, text: None)
    for name in ("", "refrain"):
        monkeypatch.setattr(logging.getLogger(name), "handlers", [])
    assert cli.main() == 1
    assert capsys.readouterr().err.count("libfontconfig.so.1") == 1


def test_any_other_import_error_is_left_alone(app_import_fails):
    app_import_fails("No module named 'nowhere'")
    with pytest.raises(ImportError, match="nowhere"):
        cli.main()
