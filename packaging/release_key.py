#!/usr/bin/env python3
"""Sign Refrain's release checksums with the owner's Ed25519 key (RFC 8032).

python packaging/release_key.py generate
python packaging/release_key.py sign FILE
python packaging/release_key.py verify FILE SIG PUBKEY
python packaging/release_key.py release TAG
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from refrain import ed25519  # noqa: E402

REPO = "Rockykln/refrain"
WORKFLOW = f"github.com/{REPO}/.github/workflows/release.yml"
SUMS = "SHA256SUMS"
DEFAULT_KEY = Path.home() / ".config" / "refrain-release" / "ed25519.key"
UPDATER = ROOT / "src" / "refrain" / "updater.py"
_TAG_RE = re.compile(r"v\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?")


class KeyFileError(Exception):
    pass


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return ed25519.compress(ed25519.scalar_mult(a, ed25519.BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    pub = ed25519.compress(ed25519.scalar_mult(a, ed25519.BASE))
    r = ed25519.hash_to_scalar(prefix, message)
    r_bytes = ed25519.compress(ed25519.scalar_mult(r, ed25519.BASE))
    s = (r + ed25519.hash_to_scalar(r_bytes, pub, message) * a) % ed25519.L
    return r_bytes + s.to_bytes(32, "little")


def encode_signature(signature: bytes) -> bytes:
    return base64.b64encode(signature) + b"\n"


def decode_signature(text: bytes) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except binascii.Error as e:
        raise ValueError(f"signature is not base64: {e}") from None
    if len(raw) != 64:
        raise ValueError(f"signature has {len(raw)} bytes, expected 64")
    return raw


def parse_public_key(text: str) -> bytes:
    text = text.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
        raise ValueError("public key must be 64 hex characters")
    return bytes.fromhex(text)


def write_new_key(path: Path) -> bytes:
    if path.exists():
        raise KeyFileError(f"{path} already exists; not overwriting it")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    seed = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(seed.hex() + "\n")
    return seed


def read_key(path: Path) -> bytes:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        raise KeyFileError(f"no key at {path}; run `generate` first") from None
    if mode & 0o077:
        raise KeyFileError(f"{path} is readable by others; run: chmod 600 {path}")
    text = path.read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise KeyFileError(f"{path} does not hold a 32-byte hex key")
    return bytes.fromhex(text)


def embedded_public_key() -> str:
    m = re.search(r'^RELEASE_PUBLIC_KEY = "([0-9a-fA-F]*)"', UPDATER.read_text(), re.M)
    return m.group(1).lower() if m else ""


def _run(argv: list[str]) -> None:
    print("+", " ".join(argv))
    subprocess.run(argv, check=True)


def cmd_generate(args) -> int:
    try:
        seed = write_new_key(args.key)
    except KeyFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Private key written to {args.key} (mode 600).")
    print(f"Public key: {public_key(seed).hex()}")
    print()
    print("Put the public key into RELEASE_PUBLIC_KEY in src/refrain/updater.py.")
    print("Back up the private key file offline now (e.g. password manager or an")
    print("encrypted USB stick). Without it, no AppImage can update itself again;")
    print("if it leaks, anyone can sign updates that Refrain will install.")
    return 0


def cmd_sign(args) -> int:
    try:
        seed = read_key(args.key)
    except KeyFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = Path(str(args.file) + ".sig")
    out.write_bytes(encode_signature(sign(seed, args.file.read_bytes())))
    print(f"Wrote {out}")
    return 0


def cmd_verify(args) -> int:
    try:
        pub = parse_public_key(args.pubkey)
        sig = decode_signature(args.sig.read_bytes())
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if ed25519.verify(pub, args.file.read_bytes(), sig):
        print("Signature OK")
        return 0
    print("Signature INVALID", file=sys.stderr)
    return 1


def cmd_release(args) -> int:
    tag = args.tag
    if not _TAG_RE.fullmatch(tag):
        print(f"error: {tag!r} is not a release tag like v1.2.3", file=sys.stderr)
        return 1
    try:
        seed = read_key(args.key)
    except KeyFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    pub = public_key(seed).hex()
    if embedded_public_key() != pub:
        print(
            f"error: RELEASE_PUBLIC_KEY in {UPDATER.relative_to(ROOT)} is not this key's "
            f"public key ({pub}); Refrain would refuse the signature.",
            file=sys.stderr,
        )
        return 1

    try:
        with tempfile.TemporaryDirectory() as tmp:
            sums = Path(tmp) / SUMS
            _run(
                ["gh", "release", "download", tag, "--repo", REPO, "--pattern", SUMS, "--dir", tmp]
            )
            if not args.skip_attestation:
                _run(
                    [
                        "gh",
                        "attestation",
                        "verify",
                        str(sums),
                        "--repo",
                        REPO,
                        "--signer-workflow",
                        WORKFLOW,
                        "--source-ref",
                        f"refs/tags/{tag}",
                    ]
                )
            data = sums.read_bytes()
            print(f"\n{SUMS} of {tag}:\n{data.decode('utf-8', 'replace')}")
            signature = sign(seed, data)
            if not ed25519.verify(bytes.fromhex(pub), data, signature):
                print("error: the new signature does not verify", file=sys.stderr)
                return 1
            sig_path = Path(tmp) / f"{SUMS}.sig"
            sig_path.write_bytes(encode_signature(signature))
            _run(["gh", "release", "upload", tag, str(sig_path), "--repo", REPO, "--clobber"])
    except subprocess.CalledProcessError as e:
        print(f"error: `{' '.join(e.cmd)}` failed with exit code {e.returncode}", file=sys.stderr)
        return 1

    print(f"\n{SUMS}.sig is uploaded to the draft release {tag}.")
    answer = input(f"Publish the release {tag} now? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print(
            f"Left as a draft. Publish later with:\n  gh release edit {tag} --repo {REPO} --draft=false"
        )
        return 0
    try:
        _run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false"])
    except subprocess.CalledProcessError as e:
        print(f"error: publishing failed with exit code {e.returncode}", file=sys.stderr)
        return 1
    print(f"Published {tag}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--key", type=Path, default=DEFAULT_KEY, help=f"private key file (default {DEFAULT_KEY})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("generate", help="create a new private key").set_defaults(func=cmd_generate)

    p = sub.add_parser("sign", help="write FILE.sig")
    p.add_argument("file", type=Path)
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("verify", help="check SIG over FILE against PUBKEY (hex)")
    p.add_argument("file", type=Path)
    p.add_argument("sig", type=Path)
    p.add_argument("pubkey")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("release", help="sign the draft release's SHA256SUMS, then offer to publish")
    p.add_argument("tag")
    p.add_argument(
        "--skip-attestation",
        action="store_true",
        help="do not check the build provenance of SHA256SUMS before signing",
    )
    p.set_defaults(func=cmd_release)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
