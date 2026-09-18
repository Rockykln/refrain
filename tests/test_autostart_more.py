"""Autostart launcher fallbacks and the failure paths that must not break Apply."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from refrain import autostart


@pytest.fixture
def no_launcher_on_path(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(autostart.shutil, "which", lambda _name: None)


def test_exec_line_falls_back_to_the_script_that_launched_us(
    no_launcher_on_path, monkeypatch, tmp_path
):
    script = tmp_path / "bin" / "refrain-launcher"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script)])

    assert autostart.resolve_exec_line("--silent") == f"{script} --silent"


def test_exec_line_resolves_a_relative_argv0_to_an_absolute_path(
    no_launcher_on_path, monkeypatch, tmp_path
):
    (tmp_path / "refrain-launcher").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["refrain-launcher"])

    line = autostart.resolve_exec_line()
    assert Path(line).is_absolute()
    assert line == str((tmp_path / "refrain-launcher").resolve())


def test_exec_line_uses_python_module_when_argv0_is_no_file(no_launcher_on_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-c"])
    monkeypatch.setattr(sys, "executable", "/opt/python/bin/python3")

    assert autostart.resolve_exec_line("--silent") == "/opt/python/bin/python3 -m refrain --silent"


def test_exec_line_uses_python_module_with_an_empty_argv(no_launcher_on_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [])
    monkeypatch.setattr(sys, "executable", "/opt/python/bin/python3")

    assert autostart.resolve_exec_line() == "/opt/python/bin/python3 -m refrain"


def test_a_missing_appimage_file_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "gone.AppImage"))
    monkeypatch.setattr(autostart.shutil, "which", lambda _name: "/usr/bin/refrain")

    assert autostart.resolve_exec_line("--silent") == "/usr/bin/refrain --silent"


def test_enable_reports_failure_when_the_folder_cannot_be_created(xdg_tmp, caplog):
    (xdg_tmp["config"] / "autostart").write_text("not a folder", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="refrain.autostart"):
        assert autostart.enable() is False
    assert autostart.is_enabled() is False
    assert "Could not enable autostart" in caplog.text


def test_refresh_without_an_entry_reports_false_and_creates_nothing(xdg_tmp):
    assert autostart.refresh() is False
    assert not (xdg_tmp["config"] / "autostart" / "refrain.desktop").exists()


def test_refresh_reports_failure_when_the_entry_cannot_be_written(xdg_tmp, monkeypatch, caplog):
    entry = xdg_tmp["config"] / "autostart" / "refrain.desktop"
    entry.parent.mkdir()
    old = "[Desktop Entry]\nExec=/usr/bin/refrain --silent\nCategories=Audio;Network;\n"
    entry.write_text(old, encoding="utf-8")

    def refuse(self, *_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_text", refuse)
    with caplog.at_level(logging.WARNING, logger="refrain.autostart"):
        assert autostart.refresh() is False
    assert entry.read_bytes().decode() == old
    assert "Could not update the autostart entry" in caplog.text


def test_disable_reports_failure_when_the_entry_cannot_be_removed(xdg_tmp, caplog):
    blocker = xdg_tmp["config"] / "autostart" / "refrain.desktop"
    blocker.mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="refrain.autostart"):
        assert autostart.disable() is False
    assert blocker.exists()
    assert "Could not disable autostart" in caplog.text


def test_enable_and_disable_report_success(xdg_tmp):
    assert autostart.enable() is True
    assert autostart.disable() is True
    assert autostart.disable() is True
