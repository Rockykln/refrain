"""packaging/release_key.py: key handling, sign/verify and the release flow (gh mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from refrain import ed25519
from tests.release_signing import SEED, release_key

SUMS = b"0" * 64 + b"  Refrain-9.9.9-x86_64.AppImage\n"


@pytest.fixture
def key(tmp_path):
    path = tmp_path / "ed25519.key"
    path.write_text(SEED.hex() + "\n")
    path.chmod(0o600)
    return path


def test_generate_writes_a_private_key_and_prints_the_public_one(tmp_path, capsys):
    path = tmp_path / "refrain-release" / "ed25519.key"
    assert release_key.main(["--key", str(path), "generate"]) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    seed = bytes.fromhex(path.read_text().strip())
    out = capsys.readouterr().out
    assert release_key.public_key(seed).hex() in out
    assert "Back up" in out


def test_generate_never_overwrites_a_key(key, capsys):
    before = key.read_text()
    assert release_key.main(["--key", str(key), "generate"]) == 1
    assert key.read_text() == before
    assert "already exists" in capsys.readouterr().err


def test_sign_then_verify(tmp_path, key, capsys):
    file = tmp_path / "SHA256SUMS"
    file.write_bytes(SUMS)
    assert release_key.main(["--key", str(key), "sign", str(file)]) == 0
    sig = tmp_path / "SHA256SUMS.sig"
    pub = release_key.public_key(SEED).hex()
    assert release_key.main(["verify", str(file), str(sig), pub]) == 0
    assert "Signature OK" in capsys.readouterr().out

    file.write_bytes(SUMS + b"extra\n")
    assert release_key.main(["verify", str(file), str(sig), pub]) == 1
    assert "INVALID" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("sig", "pub", "error"),
    [
        (b"!!!", "00" * 32, "not base64"),
        (b"QUJD", "00" * 32, "expected 64"),
        (b"A" * 88, "xyz", "64 hex"),
    ],
)
def test_verify_reports_malformed_input(tmp_path, capsys, sig, pub, error):
    (tmp_path / "f").write_bytes(b"x")
    (tmp_path / "f.sig").write_bytes(sig)
    assert release_key.main(["verify", str(tmp_path / "f"), str(tmp_path / "f.sig"), pub]) == 1
    assert error in capsys.readouterr().err


def test_a_key_others_can_read_is_refused(tmp_path, key, capsys):
    key.chmod(0o640)
    (tmp_path / "f").write_bytes(b"x")
    assert release_key.main(["--key", str(key), "sign", str(tmp_path / "f")]) == 1
    assert "chmod 600" in capsys.readouterr().err
    assert not (tmp_path / "f.sig").exists()


@pytest.mark.parametrize("content", [None, "not a key\n"])
def test_a_missing_or_broken_key_is_reported(tmp_path, capsys, content):
    path = tmp_path / "ed25519.key"
    if content is not None:
        path.write_text(content)
        path.chmod(0o600)
    (tmp_path / "f").write_bytes(b"x")
    assert release_key.main(["--key", str(path), "sign", str(tmp_path / "f")]) == 1
    assert "error:" in capsys.readouterr().err


def test_the_embedded_public_key_is_read_from_the_updater(tmp_path, monkeypatch):
    fake = tmp_path / "updater.py"
    fake.write_text('x = 1\nRELEASE_PUBLIC_KEY = "ABCD"\n')
    monkeypatch.setattr(release_key, "UPDATER", fake)
    assert release_key.embedded_public_key() == "abcd"
    fake.write_text("x = 1\n")
    assert release_key.embedded_public_key() == ""


class _Gh:
    """Stands in for `gh`: serves SHA256SUMS on download and records every call."""

    def __init__(self, fail=None):
        self.calls: list[list[str]] = []
        self.uploaded: bytes | None = None
        self.fail = fail

    def __call__(self, argv, check=False, **_kw):
        self.calls.append(list(argv))
        if self.fail and argv[1:3] == self.fail:
            raise subprocess.CalledProcessError(1, argv)
        if argv[1:3] == ["release", "download"]:
            Path(argv[argv.index("--dir") + 1], "SHA256SUMS").write_bytes(SUMS)
        elif argv[1:3] == ["release", "upload"]:
            self.uploaded = Path(argv[4]).read_bytes()
        return subprocess.CompletedProcess(argv, 0)

    def verbs(self):
        return [c[1:3] for c in self.calls]


@pytest.fixture
def gh(monkeypatch, key):
    monkeypatch.setattr(
        release_key, "embedded_public_key", lambda: release_key.public_key(SEED).hex()
    )

    def install(answer="n", fail=None):
        fake = _Gh(fail)
        monkeypatch.setattr(release_key.subprocess, "run", fake)
        monkeypatch.setattr("builtins.input", lambda _prompt: answer)
        return fake

    return install


def _release(key, *extra):
    return release_key.main(["--key", str(key), "release", "v9.9.9", *extra])


def test_release_signs_uploads_and_publishes_on_yes(gh, key):
    fake = gh("y")
    assert _release(key) == 0
    assert fake.verbs() == [
        ["release", "download"],
        ["attestation", "verify"],
        ["release", "upload"],
        ["release", "edit"],
    ]
    download, attest, upload, edit = fake.calls
    assert download[3] == "v9.9.9" and download[download.index("--pattern") + 1] == "SHA256SUMS"
    assert "--signer-workflow" in attest and "refs/tags/v9.9.9" in attest
    assert Path(upload[4]).name == "SHA256SUMS.sig" and "--clobber" in upload
    assert edit == [
        "gh",
        "release",
        "edit",
        "v9.9.9",
        "--repo",
        "Rockykln/refrain",
        "--draft=false",
    ]
    signature = release_key.decode_signature(fake.uploaded)
    assert ed25519.verify(release_key.public_key(SEED), SUMS, signature)


@pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
def test_release_stays_a_draft_unless_confirmed(gh, key, capsys, answer):
    fake = gh(answer)
    assert _release(key) == 0
    assert ["release", "edit"] not in fake.verbs()
    assert "--draft=false" in capsys.readouterr().out


def test_release_can_skip_the_attestation_check(gh, key):
    fake = gh("n")
    assert _release(key, "--skip-attestation") == 0
    assert ["attestation", "verify"] not in fake.verbs()


@pytest.mark.parametrize(
    "fail", [["release", "download"], ["attestation", "verify"], ["release", "upload"]]
)
def test_a_failed_step_stops_before_publishing(gh, key, capsys, fail):
    fake = gh("y", fail)
    assert _release(key) == 1
    assert ["release", "edit"] not in fake.verbs()
    assert "failed" in capsys.readouterr().err


def test_a_failed_publish_is_reported(gh, key, capsys):
    gh("y", ["release", "edit"])
    assert _release(key) == 1
    assert "publishing failed" in capsys.readouterr().err


def test_release_refuses_a_key_the_app_does_not_embed(gh, key, monkeypatch, capsys):
    fake = gh("y")
    monkeypatch.setattr(release_key, "embedded_public_key", lambda: "")
    assert _release(key) == 1
    assert fake.calls == []
    assert "RELEASE_PUBLIC_KEY" in capsys.readouterr().err


def test_release_refuses_a_missing_key(gh, tmp_path, capsys):
    fake = gh("y")
    assert _release(tmp_path / "nope") == 1
    assert fake.calls == []


@pytest.mark.parametrize("tag", ["9.9.9", "v9.9", "main", "v9.9.9; rm -rf ~"])
def test_release_refuses_odd_tags(gh, key, tag):
    fake = gh("y")
    assert release_key.main(["--key", str(key), "release", tag]) == 1
    assert fake.calls == []
