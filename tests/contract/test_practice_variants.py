"""Contract tests for ``Practice`` (task-packets/E5-T01.yaml) beyond the
generic example-driven checks tests/contract/test_examples.py already runs.

These cases are Pydantic-only semantic checks that JSON Schema cannot
express (no cross-field/cross-array constraint language for "this nested
object's field must equal that top-level field" or "this nested field must
be a member of that array's own field values") — mirroring
tests/contract/test_model_profile_variants.py's own precedent for a
model_validator-only rule. Because they are NOT also rejected by JSON
Schema, they are deliberately NOT placed under
tests/contract/fixtures/invalid/ (tests/contract/test_negative_fixtures.py
requires every fixture there to fail BOTH layers).

Shape-only invalid cases (empty keys, unknown key state, malformed
content_hash) are covered by tests/contract/fixtures/invalid/practice-*.json
via test_negative_fixtures.py, not duplicated here.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts.practice import Practice
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.identity import new_urn
from pydantic import ValidationError

_VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
_VALID_UNTIL = _VALID_FROM + timedelta(days=365)
_VALID_HASH = "sha256:" + "9" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    _, public_key = generate_ed25519_keypair()
    kid = derive_key_id(public_key)
    practice_id = "urn:mrr:practice:01J00000000000000000000060"

    document: dict[str, Any] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": "2026-07-19T10:00:00Z",
        "created_by": "urn:mrr:person:01J00000000000000000000002",
        "content_hash": _VALID_HASH,
        "name": "Variant Test Practice",
        "description": "A fixture practice for model_validator-only checks.",
        "keys": [
            {
                "kid": kid,
                "algorithm": "Ed25519",
                "encoded_public_key": encode_public_key(public_key),
                "valid_from": "2026-07-19T00:00:00Z",
                "valid_until": "2027-07-19T00:00:00Z",
                "state": "active",
            }
        ],
        "governance_contacts": [],
        "supported_policy_versions": [],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": ""},
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# PublicKeyDescriptor.kid must match derive_key_id(encoded_public_key).
# ---------------------------------------------------------------------------


def test_kid_matching_the_encoded_key_is_accepted() -> None:
    document = _base_document()

    practice = Practice.model_validate(document)

    assert practice.keys[0].kid == document["keys"][0]["kid"]


def test_kid_that_does_not_match_the_encoded_key_is_rejected() -> None:
    document = _base_document()
    document["keys"][0]["kid"] = "kid:definitely-not-the-real-derived-kid"

    with pytest.raises(ValidationError, match="does not match the deterministic kid"):
        Practice.model_validate(document)


def test_encoded_public_key_that_is_not_a_valid_ed25519_key_is_rejected() -> None:
    document = _base_document()
    # Valid base64, satisfies the schema's minLength (>= 40 chars) -- but 31
    # raw bytes, not Ed25519's required 32.
    document["keys"][0]["encoded_public_key"] = base64.b64encode(b"x" * 31).decode("ascii")

    with pytest.raises(ValidationError, match="not a valid Ed25519 public key"):
        Practice.model_validate(document)


def test_key_descriptor_valid_from_at_or_after_valid_until_is_rejected() -> None:
    document = _base_document()
    document["keys"][0]["valid_from"] = "2027-07-19T00:00:00Z"
    document["keys"][0]["valid_until"] = "2026-07-19T00:00:00Z"

    with pytest.raises(ValidationError, match="strictly before"):
        Practice.model_validate(document)


# ---------------------------------------------------------------------------
# signature (optional self-signature) consistency: signer_practice_id must
# equal this Practice's own id, and key_id must be one of its listed keys.
# ---------------------------------------------------------------------------


def _signature_for(
    document: dict[str, Any], *, signer_practice_id: str, key_id: str
) -> dict[str, Any]:
    return {
        "signer_practice_id": signer_practice_id,
        "key_id": key_id,
        "algorithm": "Ed25519",
        "signed_at": "2026-07-19T10:05:00Z",
        "value": "E" * 88,
    }


def test_practice_with_no_signature_is_valid() -> None:
    document = _base_document()

    practice = Practice.model_validate(document)

    assert practice.signature is None


def test_signature_with_matching_signer_and_key_id_is_accepted() -> None:
    document = _base_document()
    document["signature"] = _signature_for(
        document,
        signer_practice_id=document["id"],
        key_id=document["keys"][0]["kid"],
    )

    practice = Practice.model_validate(document)

    assert practice.signature is not None
    assert practice.signature.key_id == document["keys"][0]["kid"]


def test_signature_signer_practice_id_not_equal_to_own_id_is_rejected() -> None:
    document = _base_document()
    document["signature"] = _signature_for(
        document,
        signer_practice_id=new_urn("practice"),  # a DIFFERENT practice
        key_id=document["keys"][0]["kid"],
    )

    with pytest.raises(ValidationError, match="must equal this Practice's own id"):
        Practice.model_validate(document)


def test_signature_key_id_not_among_listed_keys_is_rejected() -> None:
    document = _base_document()
    document["signature"] = _signature_for(
        document,
        signer_practice_id=document["id"],
        key_id="kid:not-one-of-this-practices-own-keys",
    )

    with pytest.raises(ValidationError, match="is not one of this Practice's own listed keys"):
        Practice.model_validate(document)


# ---------------------------------------------------------------------------
# Optional fields: capability_registry_endpoint, trust_chain_ref, signature
# all absent -- the minimal practice.
# ---------------------------------------------------------------------------


def test_minimal_practice_with_no_optional_fields_succeeds() -> None:
    document = _base_document()

    practice = Practice.model_validate(document)

    assert practice.capability_registry_endpoint is None
    assert practice.trust_chain_ref is None
    assert practice.signature is None
