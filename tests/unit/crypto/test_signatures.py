"""Unit tests for mrr.crypto.signatures (E1-T02).

Covers the packet's named acceptance tests: valid signature verification,
invalid signature rejection (tampered payload, tampered signature, wrong
key), and unsupported-algorithm rejection.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
from mrr.crypto.signatures import SUPPORTED_ALGORITHMS, sign, verify


def _keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def test_supported_algorithms_contains_only_ed25519() -> None:
    assert frozenset({"Ed25519"}) == SUPPORTED_ALGORITHMS


def test_valid_signature_verifies() -> None:
    private_key, public_key = _keypair()
    payload = b"canonical payload bytes"

    signature_value = sign(private_key, payload)

    verify(public_key, payload, signature_value, algorithm="Ed25519")  # must not raise


def test_tampered_payload_fails_verification() -> None:
    private_key, public_key = _keypair()
    signature_value = sign(private_key, b"original payload")

    with pytest.raises(SignatureVerificationError):
        verify(public_key, b"tampered payload", signature_value, algorithm="Ed25519")


def test_tampered_signature_fails_verification() -> None:
    private_key, public_key = _keypair()
    payload = b"original payload"
    signature_value = sign(private_key, payload)

    tampered = ("A" if signature_value[0] != "A" else "B") + signature_value[1:]

    with pytest.raises(SignatureVerificationError):
        verify(public_key, payload, tampered, algorithm="Ed25519")


def test_malformed_base64_signature_fails_verification() -> None:
    _, public_key = _keypair()

    with pytest.raises(SignatureVerificationError):
        verify(public_key, b"payload", "not valid base64!!", algorithm="Ed25519")


def test_wrong_key_fails_verification() -> None:
    private_key, _ = _keypair()
    _, other_public_key = _keypair()
    payload = b"original payload"
    signature_value = sign(private_key, payload)

    with pytest.raises(SignatureVerificationError):
        verify(other_public_key, payload, signature_value, algorithm="Ed25519")


@pytest.mark.parametrize("algorithm", ["ed25519", "ED25519", "RSA", "", "Ed25519 "])
def test_unsupported_algorithm_is_rejected_before_verification(algorithm: str) -> None:
    """Fail closed: an unsupported (including wrongly-cased) algorithm string is
    rejected before any cryptographic call, even for an otherwise-valid signature.
    """
    private_key, public_key = _keypair()
    payload = b"payload"
    signature_value = sign(private_key, payload)

    with pytest.raises(UnsupportedAlgorithmError):
        verify(public_key, payload, signature_value, algorithm=algorithm)


def test_unsupported_algorithm_is_rejected_on_sign() -> None:
    private_key, _ = _keypair()

    with pytest.raises(UnsupportedAlgorithmError):
        sign(private_key, b"payload", algorithm="ed25519")
