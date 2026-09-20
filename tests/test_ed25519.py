"""Ed25519 against RFC 8032 section 7.1, random round trips and malformed input."""

from __future__ import annotations

import os

import pytest

from refrain import ed25519
from tests.release_signing import release_key

# RFC 8032, section 7.1: secret key, public key, message, signature.
RFC8032_VECTORS = [
    pytest.param(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        (
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb88215"
            "90a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
        id="1",
    ),
    pytest.param(
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        (
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e4"
            "3e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
        id="2",
    ),
    pytest.param(
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        (
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b53"
            "8d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
        id="3",
    ),
    pytest.param(
        "f5e5767cf153319517630f226876b86c8160cc583bc013744c6bf255f5cc0ee5",
        "278117fc144c72340f67d0f2316e8386ceffbf2b2428c9c51fef7c597f1d426e",
        (
            "08b8b2b733424243760fe426a4b54908632110a66c2f6591eabd3345e3e4eb98fa6e264b"
            "f09efe12ee50f8f54e9f77b1e355f6c50544e23fb1433ddf73be84d879de7c0046dc4996"
            "d9e773f4bc9efe5738829adb26c81b37c93a1b270b20329d658675fc6ea534e0810a4432"
            "826bf58c941efb65d57a338bbd2e26640f89ffbc1a858efcb8550ee3a5e1998bd177e93a"
            "7363c344fe6b199ee5d02e82d522c4feba15452f80288a821a579116ec6dad2b3b310da9"
            "03401aa62100ab5d1a36553e06203b33890cc9b832f79ef80560ccb9a39ce767967ed628"
            "c6ad573cb116dbefefd75499da96bd68a8a97b928a8bbc103b6621fcde2beca1231d206b"
            "e6cd9ec7aff6f6c94fcd7204ed3455c68c83f4a41da4af2b74ef5c53f1d8ac70bdcb7ed1"
            "85ce81bd84359d44254d95629e9855a94a7c1958d1f8ada5d0532ed8a5aa3fb2d17ba70e"
            "b6248e594e1a2297acbbb39d502f1a8c6eb6f1ce22b3de1a1f40cc24554119a831a9aad6"
            "079cad88425de6bde1a9187ebb6092cf67bf2b13fd65f27088d78b7e883c8759d2c4f5c6"
            "5adb7553878ad575f9fad878e80a0c9ba63bcbcc2732e69485bbc9c90bfbd62481d9089b"
            "eccf80cfe2df16a2cf65bd92dd597b0707e0917af48bbb75fed413d238f5555a7a569d80"
            "c3414a8d0859dc65a46128bab27af87a71314f318c782b23ebfe808b82b0ce26401d2e22"
            "f04d83d1255dc51addd3b75a2b1ae0784504df543af8969be3ea7082ff7fc9888c144da2"
            "af58429ec96031dbcad3dad9af0dcbaaaf268cb8fcffead94f3c7ca495e056a9b47acdb7"
            "51fb73e666c6c655ade8297297d07ad1ba5e43f1bca32301651339e22904cc8c42f58c30"
            "c04aafdb038dda0847dd988dcda6f3bfd15c4b4c4525004aa06eeff8ca61783aacec57fb"
            "3d1f92b0fe2fd1a85f6724517b65e614ad6808d6f6ee34dff7310fdc82aebfd904b01e1d"
            "c54b2927094b2db68d6f903b68401adebf5a7e08d78ff4ef5d63653a65040cf9bfd4aca7"
            "984a74d37145986780fc0b16ac451649de6188a7dbdf191f64b5fc5e2ab47b57f7f7276c"
            "d419c17a3ca8e1b939ae49e488acba6b965610b5480109c8b17b80e1b7b750dfc7598d5d"
            "5011fd2dcc5600a32ef5b52a1ecc820e308aa342721aac0943bf6686b64b2579376504cc"
            "c493d97e6aed3fb0f9cd71a43dd497f01f17c0e2cb3797aa2a2f256656168e6c496afc5f"
            "b93246f6b1116398a346f1a641f3b041e989f7914f90cc2c7fff357876e506b50d334ba7"
            "7c225bc307ba537152f3f1610e4eafe595f6d9d90d11faa933a15ef1369546868a7f3a45"
            "a96768d40fd9d03412c091c6315cf4fde7cb68606937380db2eaaa707b4c4185c32eddcd"
            "d306705e4dc1ffc872eeee475a64dfac86aba41c0618983f8741c5ef68d3a101e8a3b8ca"
            "c60c905c15fc910840b94c00a0b9d0"
        ),
        (
            "0aab4c900501b3e24d7cdf4663326a3a87df5e4843b2cbdb67cbf6e460fec350aa5371b1"
            "508f9f4528ecea23c436d94b5e8fcd4f681e30a6ac00a9704a188a03"
        ),
        id="1024",
    ),
    pytest.param(
        "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        (
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a"
            "274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f"
        ),
        (
            "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9"
            "ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"
        ),
        id="SHA(abc)",
    ),
]


@pytest.mark.parametrize(("secret", "public", "message", "signature"), RFC8032_VECTORS)
def test_rfc8032_vectors(secret, public, message, signature):
    secret, public = bytes.fromhex(secret), bytes.fromhex(public)
    message, signature = bytes.fromhex(message), bytes.fromhex(signature)
    assert release_key.public_key(secret) == public
    assert release_key.sign(secret, message) == signature
    assert ed25519.verify(public, message, signature) is True


@pytest.mark.parametrize(("secret", "public", "message", "signature"), RFC8032_VECTORS)
def test_rfc8032_vectors_reject_any_changed_bit(secret, public, message, signature):
    public, message = bytes.fromhex(public), bytes.fromhex(message)
    signature = bytearray(bytes.fromhex(signature))
    signature[5] ^= 0x01
    assert ed25519.verify(public, message, bytes(signature)) is False
    signature[5] ^= 0x01
    assert ed25519.verify(public, message + b"x", bytes(signature)) is False


@pytest.mark.parametrize("length", [0, 1, 31, 64, 1000])
def test_random_round_trips(length):
    for _ in range(4):
        seed, message = os.urandom(32), os.urandom(length)
        public = release_key.public_key(seed)
        signature = release_key.sign(seed, message)
        assert ed25519.verify(public, message, signature) is True
        other = release_key.public_key(os.urandom(32))
        assert ed25519.verify(other, message, signature) is False


def _signed():
    seed = os.urandom(32)
    return release_key.public_key(seed), b"SHA256SUMS", release_key.sign(seed, b"SHA256SUMS")


def test_wrong_lengths_are_rejected():
    public, message, signature = _signed()
    assert ed25519.verify(public[:31], message, signature) is False
    assert ed25519.verify(public, message, signature[:63]) is False
    assert ed25519.verify(public, message, signature + b"\0") is False
    assert ed25519.verify(b"", message, b"") is False


def test_a_non_canonical_s_is_rejected():
    public, message, signature = _signed()
    s = int.from_bytes(signature[32:], "little") + ed25519.L
    assert s < 2**256
    assert ed25519.verify(public, message, signature[:32] + s.to_bytes(32, "little")) is False


def _not_on_curve() -> bytes:
    y = 2
    while ed25519.decompress(y.to_bytes(32, "little")) is not None:
        y += 1
    return y.to_bytes(32, "little")


def test_points_off_the_curve_are_rejected():
    public, message, signature = _signed()
    bad = _not_on_curve()
    assert ed25519.verify(bad, message, signature) is False
    assert ed25519.verify(public, message, bad + signature[32:]) is False


def test_decompress_edge_cases():
    assert ed25519.decompress(b"\0" * 31) is None
    assert ed25519.decompress(ed25519.P.to_bytes(32, "little")) is None
    assert ed25519.decompress((1).to_bytes(32, "little")) == (0, 1, 1, 0)
    assert ed25519.decompress((1 | 1 << 255).to_bytes(32, "little")) is None
    assert ed25519.compress(ed25519.BASE) == (4 * pow(5, -1, ed25519.P) % ed25519.P).to_bytes(
        32, "little"
    )


def test_agrees_with_an_independent_implementation():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization

    raw = serialization.Encoding.Raw, serialization.PublicFormat.Raw
    for length in (0, 17, 4096):
        seed, message = os.urandom(32), os.urandom(length)
        key = ed.Ed25519PrivateKey.from_private_bytes(seed)
        assert release_key.public_key(seed) == key.public_key().public_bytes(*raw)
        assert release_key.sign(seed, message) == key.sign(message)
