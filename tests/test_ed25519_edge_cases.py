"""Ed25519 verifier hardening against forgeable and malformed inputs.

Extends tests/test_ed25519.py with cases specific to small-order points and
cross-checks every case against `cryptography` (skipped if not installed;
requirements-dev.lock pulls it in for CI).
"""

from __future__ import annotations

import pytest

from refrain import ed25519
from tests.release_signing import release_key

cryptography_ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _cryptography_accepts(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        key = cryptography_ed25519.Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError:
        return False
    try:
        key.verify(signature, message)
        return True
    except InvalidSignature:
        return False


def _signed_message() -> tuple[bytes, bytes, bytes]:
    seed = bytes(range(1, 33))
    public = release_key.public_key(seed)
    message = b"SHA256SUMS payload for edge-case tests"
    signature = release_key.sign(seed, message)
    return public, message, signature


# The curve's 8-element torsion subgroup: the identity plus its 2-, 4- and
# 8-order points, compressed. y=0 gives an order-4 point directly from the
# curve equation (recover_x(0, 0) == sqrt(-1)), which is why 32 zero bytes
# -- an otherwise "empty" public key -- decode to a valid, non-identity
# point. The order-8 point has no such shortcut; it was found by taking a
# random curve point Q and computing L*Q (the group has order 8*L, so this
# always lands in the 8-torsion subgroup) until the result had exact order 8.
IDENTITY = ed25519.compress((0, 1, 1, 0))
ORDER_2 = (ed25519.P - 1).to_bytes(32, "little")
ORDER_4_ALL_ZERO = bytes(32)
ORDER_8 = bytes.fromhex("26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05")

SMALL_ORDER_PUBLIC_KEYS = {
    "identity": IDENTITY,
    "order-2": ORDER_2,
    "order-4 (all-zero bytes)": ORDER_4_ALL_ZERO,
    "order-8": ORDER_8,
}


@pytest.mark.parametrize("name", SMALL_ORDER_PUBLIC_KEYS)
def test_small_order_public_key_cannot_forge_a_signature(name):
    """A small-order public key A makes h*A vanish whenever ord(A) | h, so a
    single precomputed (R, s) = (s*B, s) then verifies for any message with
    that property -- for the identity, every message. Both `cryptography`
    and this module's unguarded verify() used to accept these; try enough
    messages that a surviving forgery would show up.
    """
    public_key = SMALL_ORDER_PUBLIC_KEYS[name]
    for i in range(32):
        message = f"forged update #{i}".encode()
        s = 1000 + i
        r = ed25519.scalar_mult(s, ed25519.BASE)
        signature = ed25519.compress(r) + s.to_bytes(32, "little")
        assert ed25519.verify(public_key, message, signature) is False


def test_cryptography_accepts_what_the_small_order_guard_rejects():
    """Documents the intentional divergence: RFC 8032 doesn't ask
    implementations to reject small-order keys, and `cryptography` doesn't --
    the identity key here forges every one of these messages under
    `cryptography` too. Our guard is deliberately stricter.
    """
    hits = 0
    for i in range(16):
        message = f"forged update #{i}".encode()
        s = 2000 + i
        r = ed25519.scalar_mult(s, ed25519.BASE)
        signature = ed25519.compress(r) + s.to_bytes(32, "little")
        if _cryptography_accepts(IDENTITY, message, signature):
            hits += 1
    assert hits == 16


def test_all_zero_signature_is_rejected():
    public, message, _ = _signed_message()
    signature = bytes(64)
    assert ed25519.verify(public, message, signature) is False
    assert _cryptography_accepts(public, message, signature) is False


def test_non_canonical_s_variants_are_rejected():
    public, message, signature = _signed_message()
    valid_s = int.from_bytes(signature[32:], "little")
    for bad_s in (ed25519.L, ed25519.L + valid_s, 2 * ed25519.L, 2**256 - 1):
        bad_signature = signature[:32] + (bad_s % 2**256).to_bytes(32, "little")
        assert ed25519.verify(public, message, bad_signature) is False
        assert _cryptography_accepts(public, message, bad_signature) is False


@pytest.mark.parametrize("name", SMALL_ORDER_PUBLIC_KEYS)
def test_small_order_r_is_rejected_against_a_real_key(name):
    """R degenerate doesn't help a forger against a real (unknown discrete
    log) key -- solving s*B = R + h*A for s still needs A's discrete log --
    but it must not be accepted by accident either.
    """
    public, message, signature = _signed_message()
    bad_r = SMALL_ORDER_PUBLIC_KEYS[name]
    for s in (0, 1, int.from_bytes(signature[32:], "little")):
        bad_signature = bad_r + s.to_bytes(32, "little")
        assert ed25519.verify(public, message, bad_signature) is False
        assert _cryptography_accepts(public, message, bad_signature) is False


def test_wrong_length_public_key_and_signature_cross_checked():
    public, message, signature = _signed_message()
    for bad_public in (b"", public[:31], public + b"\0", bytes(64)):
        assert ed25519.verify(bad_public, message, signature) is False
        assert _cryptography_accepts(bad_public, message, signature) is False
    for bad_signature in (b"", signature[:63], signature + b"\0"):
        assert ed25519.verify(public, message, bad_signature) is False
        assert _cryptography_accepts(public, message, bad_signature) is False


def test_flipped_message_bit_cross_checked():
    public, message, signature = _signed_message()
    flipped = bytearray(message)
    flipped[0] ^= 1
    flipped = bytes(flipped)
    assert ed25519.verify(public, flipped, signature) is False
    assert _cryptography_accepts(public, flipped, signature) is False
    # sanity: the un-flipped message must still be accepted by both
    assert ed25519.verify(public, message, signature) is True
    assert _cryptography_accepts(public, message, signature) is True
