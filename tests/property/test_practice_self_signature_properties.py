"""Property tests: a Practice built with a real Ed25519 signing key is
content-hashed and self-signed over the ADR-0004 ``exclude_none`` canonical
form via the EXISTING ``mrr.domain.hashing_policy`` (task-packets/
E5-T01.yaml).

Practice has no production sign/verify call site (no service is introduced
by this task — E5-T02+'s scope), so this mirrors
tests/property/test_canonical_signed_form_properties.py's own "local check
only" precedent for EvidenceCrate (which also had no production verify path
yet at the time it was written): a Practice is built directly in this
module, hashed and signed with ``mrr.domain.hashing_policy`` exactly as a
future recording service would, and ``verify_object_signature`` is called
directly as the acceptance test's own "local check".

No private key bytes are ever recorded on the Practice model or in its
JSON — proved directly at the bottom of this module.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from _json_strategies import json_text
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import (
    decode_public_key,
    derive_key_id,
    encode_public_key,
    generate_ed25519_keypair,
)
from mrr.domain.hashing_policy import compute_content_hash, sign_object, verify_object_signature
from mrr.domain.identity import new_urn

_VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
_VALID_UNTIL = _VALID_FROM + timedelta(days=365)
_SIGNED_AT = _VALID_FROM + timedelta(hours=1)


def _build_signed_practice(
    *, name: str, trust_statement: str, capability_registry_endpoint: str | None
) -> tuple[Practice, Ed25519PrivateKey]:
    """Build a Practice, self-sign it over the exclude_none persisted body
    using the EXISTING hashing_policy (never a bespoke hash/sign
    implementation), and return it alongside the signing PRIVATE key (for
    this module's own regression/leak checks only — never persisted on the
    Practice itself).

    Re-validates via ``Practice.model_validate`` at each stage rather than
    ``model_copy(update=...)``: Pydantic's ``model_copy`` does not run
    validators or coerce nested types, so a ``self_signature`` dict attached
    that way would stay a raw ``dict`` instead of becoming a typed
    ``Signature`` — and this module's own consistency validator
    (``_self_signature_signer_is_this_practice_with_a_listed_key``) would
    never run on the "signed" object at all.
    """
    signing_key, public_key = generate_ed25519_keypair()
    kid = derive_key_id(public_key)
    practice_id = new_urn("practice")

    document: dict[str, object] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": _VALID_FROM,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "0" * 64,
        "name": name,
        "description": "Property-test fixture practice.",
        "keys": [
            {
                "kid": kid,
                "algorithm": "Ed25519",
                "encoded_public_key": encode_public_key(public_key),
                "valid_from": _VALID_FROM,
                "valid_until": _VALID_UNTIL,
                "state": "active",
            }
        ],
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-19"],
        "capability_registry_endpoint": capability_registry_endpoint,
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": trust_statement},
    }

    # Seal content_hash over the exclude_none body with content_hash/signature absent.
    unsigned = Practice.model_validate(document)
    unsigned_body = json.loads(unsigned.model_dump_json(exclude_none=True))
    document["content_hash"] = compute_content_hash(unsigned_body)

    # Self-sign over the exclude_none body with content_hash present, signature absent.
    sealed = Practice.model_validate(document)
    body_to_sign = json.loads(sealed.model_dump_json(exclude_none=True))
    signature_value = sign_object(signing_key, body_to_sign)

    document["signature"] = {
        "signer_practice_id": practice_id,
        "key_id": kid,
        "algorithm": "Ed25519",
        "signed_at": _SIGNED_AT,
        "value": signature_value,
    }
    signed = Practice.model_validate(document)
    return signed, signing_key


_name_strategy = json_text(min_size=1)
_trust_statement_strategy = json_text()
_endpoint_strategy = st.one_of(st.none(), json_text(min_size=1))


@given(
    name=_name_strategy,
    trust_statement=_trust_statement_strategy,
    capability_registry_endpoint=_endpoint_strategy,
)
def test_practice_content_hash_equals_hash_of_its_own_exclude_none_body(
    name: str, trust_statement: str, capability_registry_endpoint: str | None
) -> None:
    practice, _signing_key = _build_signed_practice(
        name=name,
        trust_statement=trust_statement,
        capability_registry_endpoint=capability_registry_endpoint,
    )

    persisted_body = json.loads(practice.model_dump_json(exclude_none=True))
    without_signature = {k: v for k, v in persisted_body.items() if k != "signature"}

    assert practice.content_hash == compute_content_hash(without_signature)


@given(
    name=_name_strategy,
    trust_statement=_trust_statement_strategy,
    capability_registry_endpoint=_endpoint_strategy,
)
def test_practice_self_signature_verifies_over_the_exclude_none_persisted_body(
    name: str, trust_statement: str, capability_registry_endpoint: str | None
) -> None:
    practice, _signing_key = _build_signed_practice(
        name=name,
        trust_statement=trust_statement,
        capability_registry_endpoint=capability_registry_endpoint,
    )
    assert practice.signature is not None
    public_key = decode_public_key(practice.keys[0].encoded_public_key)

    persisted_body = json.loads(practice.model_dump_json(exclude_none=True))

    # Must not raise.
    verify_object_signature(
        public_key,
        persisted_body,
        practice.signature.value,
        algorithm=practice.signature.algorithm,
    )


def test_practice_old_null_including_signature_does_not_verify() -> None:
    """Regression proving the ADR-0004 discipline is real for Practice too:
    a signature produced over the OLD null-including ``model_dump(mode="json")``
    form does not verify against the exclude_none persisted body.
    """
    practice, signing_key = _build_signed_practice(
        name="Old Form Practice",
        trust_statement="",
        capability_registry_endpoint=None,
    )
    public_key = decode_public_key(practice.keys[0].encoded_public_key)
    persisted_body = json.loads(practice.model_dump_json(exclude_none=True))

    old_form_signature = sign_object(signing_key, practice.model_dump(mode="json"))

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(
            public_key,
            persisted_body,
            old_form_signature,
            algorithm="Ed25519",
        )


def test_tampering_a_signed_field_fails_closed() -> None:
    practice, _signing_key = _build_signed_practice(
        name="Tamper Target",
        trust_statement="original",
        capability_registry_endpoint=None,
    )
    assert practice.signature is not None
    public_key = decode_public_key(practice.keys[0].encoded_public_key)

    persisted_body = json.loads(practice.model_dump_json(exclude_none=True))
    tampered = dict(persisted_body)
    tampered["description"] = "a description the signer never approved"

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(
            public_key,
            tampered,
            practice.signature.value,
            algorithm=practice.signature.algorithm,
        )


# ---------------------------------------------------------------------------
# No private key material anywhere in the model or its JSON.
# ---------------------------------------------------------------------------


def test_no_private_key_bytes_anywhere_in_a_signed_practices_json() -> None:
    practice, signing_key = _build_signed_practice(
        name="Leak Check Practice",
        trust_statement="carries only public material",
        capability_registry_endpoint="https://fixture.invalid/capability-registry",
    )

    private_raw = signing_key.private_bytes_raw()
    private_b64 = base64.b64encode(private_raw).decode("ascii")
    private_hex = private_raw.hex()

    dumped = practice.model_dump_json(exclude_none=True)

    assert private_b64 not in dumped
    assert private_hex not in dumped
