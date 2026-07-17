"""Unit tests for mrr.domain.hashing_policy (E1-T02).

Acceptance-test mapping: "hashing_policy exclusions (signature field never in
signed payload; content_hash not in hashed payload but in signed payload)"
(task-packets/E1-T02.yaml).
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.crypto.canonical import JSONValue
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.domain.hashing_policy import (
    compute_content_hash,
    prepare_for_hash,
    prepare_for_signature,
    sign_object,
    verify_object_signature,
)

_OBJECT: dict[str, JSONValue] = {
    "id": "urn:mrr:claim:01J00000000000000000000011",
    "api_version": "mrr/v1alpha1",
    "kind": "Claim",
    "content_hash": "sha256:" + "0" * 64,
    "signature": {
        "signer_practice_id": "urn:mrr:practice:01J00000000000000000000001",
        "key_id": "key-2026-01",
        "algorithm": "Ed25519",
        "signed_at": "2026-07-17T12:00:00Z",
        "value": "placeholder",
    },
    "assertion": "The recomputed value is 42 percent.",
}


def test_prepare_for_hash_excludes_content_hash_and_signature() -> None:
    hashed_payload = prepare_for_hash(_OBJECT)

    assert "content_hash" not in hashed_payload
    assert "signature" not in hashed_payload
    # Everything else survives.
    assert hashed_payload["id"] == _OBJECT["id"]
    assert hashed_payload["assertion"] == _OBJECT["assertion"]


def test_prepare_for_signature_excludes_only_signature_and_keeps_content_hash() -> None:
    signed_payload = prepare_for_signature(_OBJECT)

    assert "signature" not in signed_payload
    # content_hash IS part of the signed payload (section 1.3: "the signature
    # covers the canonical payload and content hash").
    assert signed_payload["content_hash"] == _OBJECT["content_hash"]


def test_prepare_for_hash_and_signature_do_not_exclude_a_plural_signatures_field() -> None:
    """schemas/common.schema.json defines only a singular `signature` field —
    no schema defines `signatures`. Excluding that name too would be inventing
    domain behavior (AGENTS.md rule 3) and would silently drop a future,
    legitimately-named `signatures` field out of hash coverage. This test
    documents the deliberate choice: an object carrying a `signatures` field
    keeps it in both the hashed and signed payloads, exactly like any other
    unrecognized field.
    """
    obj_with_plural_field = dict(_OBJECT)
    obj_with_plural_field["signatures"] = ["not-a-defined-schema-field"]

    hashed_payload = prepare_for_hash(obj_with_plural_field)
    signed_payload = prepare_for_signature(obj_with_plural_field)

    assert hashed_payload["signatures"] == obj_with_plural_field["signatures"]
    assert signed_payload["signatures"] == obj_with_plural_field["signatures"]


def test_prepare_for_hash_and_signature_do_not_mutate_input() -> None:
    original_keys = set(_OBJECT.keys())

    prepare_for_hash(_OBJECT)
    prepare_for_signature(_OBJECT)

    assert set(_OBJECT.keys()) == original_keys


def test_compute_content_hash_ignores_signature_field_changes() -> None:
    with_signature_a = dict(_OBJECT)
    with_signature_b = dict(_OBJECT)
    with_signature_b["signature"] = {"value": "a-completely-different-signature"}

    assert compute_content_hash(with_signature_a) == compute_content_hash(with_signature_b)


def test_compute_content_hash_changes_with_semantic_field() -> None:
    mutated = dict(_OBJECT)
    mutated["assertion"] = "A different assertion entirely."

    assert compute_content_hash(_OBJECT) != compute_content_hash(mutated)


def test_sign_object_and_verify_object_signature_round_trip() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    sealed = dict(_OBJECT)
    sealed["content_hash"] = compute_content_hash(_OBJECT)

    signature_value = sign_object(private_key, sealed)

    verify_object_signature(public_key, sealed, signature_value, algorithm="Ed25519")  # no raise


def test_verify_object_signature_rejects_tampered_content_hash() -> None:
    """content_hash is inside the signed payload, so tampering with it (while
    leaving the signature untouched) must invalidate the signature.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    sealed = dict(_OBJECT)
    sealed["content_hash"] = compute_content_hash(_OBJECT)
    signature_value = sign_object(private_key, sealed)

    tampered = dict(sealed)
    tampered["content_hash"] = "sha256:" + "1" * 64

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(public_key, tampered, signature_value, algorithm="Ed25519")
