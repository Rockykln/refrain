"""HTTPS finds the host's CA bundle when the bundled OpenSSL looks in the wrong place."""

from __future__ import annotations

import os
import ssl
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

import refrain.app as app  # noqa: E402


@pytest.fixture
def no_ssl_env(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def _defaults(monkeypatch, cafile, capath):
    monkeypatch.setattr(
        app.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(cafile=cafile, capath=capath),
    )


def test_a_missing_built_in_path_falls_back_to_the_hosts_bundle(no_ssl_env, monkeypatch, tmp_path):
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text("certs", encoding="utf-8")
    # Not /usr/lib/ssl: on the distributions that have it, the test would
    # be asserting what the host looks like rather than what the code does.
    missing = tmp_path / "no-openssl-here"
    _defaults(monkeypatch, str(missing / "cert.pem"), str(missing / "certs"))
    app.use_system_certificates((str(tmp_path / "missing.crt"), str(bundle)))
    assert os.environ["SSL_CERT_FILE"] == str(bundle)


def test_a_working_built_in_path_is_left_alone(no_ssl_env, monkeypatch, tmp_path):
    cafile = tmp_path / "cert.pem"
    cafile.write_text("certs", encoding="utf-8")
    _defaults(monkeypatch, str(cafile), None)
    app.use_system_certificates((str(cafile),))
    assert "SSL_CERT_FILE" not in os.environ


def test_a_user_setting_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/bundle.pem")
    _defaults(monkeypatch, None, None)
    app.use_system_certificates((str(tmp_path),))
    assert os.environ["SSL_CERT_FILE"] == "/custom/bundle.pem"


def test_the_real_default_context_still_builds():
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED
