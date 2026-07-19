"""Unit tests for mrr.crypto.keys (task-packets/E5-T01.yaml).

Covers the packet's named acceptance tests: keygen returns a usable private
key to the caller and a public encoding containing only public material,
encode/decode round trip, and no-private-key-leak.
"""

from __future__ import annotations

import base64

import pytest
from mrr.crypto.exceptions import InvalidPublicKeyError
from mrr.crypto.keys import (
    decode_public_key,
    derive_key_id,
    encode_public_key,
    generate_ed25519_keypair,
)


def test_generate_ed25519_keypair_returns_a_matching_private_and_public_key() -> None:
    private_key, public_key = generate_ed25519_keypair()

    assert private_key.public_key().public_bytes_raw() == public_key.public_bytes_raw()
    # The private key is fully usable by the caller (e.g. to sign).
    private_key.sign(b"payload")


def test_generate_ed25519_keypair_produces_fresh_keys_each_call() -> None:
    private_a, public_a = generate_ed25519_keypair()
    private_b, public_b = generate_ed25519_keypair()

    assert public_a.public_bytes_raw() != public_b.public_bytes_raw()
    assert private_a.private_bytes_raw() != private_b.private_bytes_raw()


def test_encode_decode_public_key_round_trips() -> None:
    _, public_key = generate_ed25519_keypair()

    encoded = encode_public_key(public_key)
    decoded = decode_public_key(encoded)

    assert decoded.public_bytes_raw() == public_key.public_bytes_raw()


def test_encode_public_key_uses_standard_base64_with_padding() -> None:
    """ADR-0003's convention: standard base64 (RFC 4648 section 4, with
    padding), the same as ``mrr.crypto.signatures``' ``signature.value``.
    """
    _, public_key = generate_ed25519_keypair()

    encoded = encode_public_key(public_key)

    # Round-trips through the stdlib's own strict standard-base64 decoder.
    assert base64.b64decode(encoded, validate=True) == public_key.public_bytes_raw()


def test_decode_public_key_rejects_invalid_base64() -> None:
    with pytest.raises(InvalidPublicKeyError):
        decode_public_key("not valid base64!!")


def test_decode_public_key_rejects_wrong_length_key_material() -> None:
    too_short = base64.b64encode(b"short").decode("ascii")

    with pytest.raises(InvalidPublicKeyError):
        decode_public_key(too_short)


# ---------------------------------------------------------------------------
# kid derivation: deterministic, collision-sensitive, never label-derived.
# ---------------------------------------------------------------------------


def test_derive_key_id_is_deterministic_for_the_same_key() -> None:
    _, public_key = generate_ed25519_keypair()

    assert derive_key_id(public_key) == derive_key_id(public_key)


def test_derive_key_id_is_deterministic_across_independently_decoded_copies() -> None:
    """The same underlying key bytes, reconstructed via a fresh decode,
    yield the same kid -- proves the kid is a pure function of the raw key
    bytes, not of the in-memory object identity.
    """
    _, public_key = generate_ed25519_keypair()
    encoded = encode_public_key(public_key)
    reconstructed = decode_public_key(encoded)

    assert derive_key_id(public_key) == derive_key_id(reconstructed)


def test_derive_key_id_differs_for_different_keys() -> None:
    _, public_key_a = generate_ed25519_keypair()
    _, public_key_b = generate_ed25519_keypair()

    assert derive_key_id(public_key_a) != derive_key_id(public_key_b)


def test_derive_key_id_has_a_self_describing_prefix() -> None:
    _, public_key = generate_ed25519_keypair()

    kid = derive_key_id(public_key)

    assert kid.startswith("kid:")


# ---------------------------------------------------------------------------
# No private key material anywhere (AGENTS.md rule 11, docs/spec/04 section
# 4.1). generate_ed25519_keypair hands the private half to the caller only;
# nothing this module computes from the PUBLIC half can leak it.
# ---------------------------------------------------------------------------


def test_no_private_key_bytes_appear_in_the_public_encoding_or_kid() -> None:
    private_key, public_key = generate_ed25519_keypair()
    private_raw = private_key.private_bytes_raw()
    private_b64 = base64.b64encode(private_raw).decode("ascii")
    private_hex = private_raw.hex()

    encoded_public_key = encode_public_key(public_key)
    kid = derive_key_id(public_key)

    assert private_b64 not in encoded_public_key
    assert private_hex not in encoded_public_key
    assert private_b64 not in kid
    assert private_hex not in kid


def test_private_key_raw_bytes_differ_from_public_key_raw_bytes() -> None:
    """Baseline sanity check underpinning the leak assertions above: an
    Ed25519 private key's own raw bytes (the seed) are never equal to its
    public key's raw bytes (the derived point), so the "not in" assertions
    above are not vacuously true.
    """
    private_key, public_key = generate_ed25519_keypair()

    assert private_key.private_bytes_raw() != public_key.public_bytes_raw()
