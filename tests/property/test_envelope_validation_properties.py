"""Property test: ``mrr.domain.envelope_validation.validate_inbound_envelope``
never accepts (returns normally) for any single failing precondition
(task-packets/E5-T03.yaml acceptance test: "property/unit tests show the
validator returns for all-hold and raises for every single failing
precondition").

Ed25519 keypairs have no seedable hypothesis strategy exposed by the
``cryptography`` library (mirroring
tests/property/test_manifest_trust_properties.py's own rationale, itself
mirroring tests/property/test_key_derivation_properties.py), so each
example generates a fresh keypair directly; ``hypothesis`` drives which of
the twelve concrete fault kinds is injected into an otherwise fully
self-consistent, genuinely signed envelope. A positive control
(``test_validator_accepts_when_every_precondition_holds``) proves the same
construction, with no fault injected, DOES return normally — so the
property below is not vacuously true of a construction that always fails
regardless of input.

Local, deliberate duplicate of tests/unit/domain/test_envelope_validation.py's
own fixture builders — this codebase's established convention for
independent test tiers (see tests/unit/domain/test_manifest_trust.py's own
docstring precedent, and tests/property/test_manifest_trust_properties.py's
identical duplication of it).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.exceptions import (
    EnvelopeAlreadyProcessedError,
    EnvelopeKeyNotValidError,
    EnvelopeNotWithinValidityWindowError,
    EnvelopePayloadContentHashMismatchError,
    EnvelopeRecipientMismatchError,
    EnvelopeSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_PAYLOAD_HASH = "sha256:" + "c" * 64

#: The twelve concrete ways ``validate_inbound_envelope`` must fail closed —
#: the packet's own named fail-closed matrix, minus the happy path. Four
#: distinct key-validity sub-cases (mirroring
#: test_manifest_trust_properties.py's own four) all raise the same
#: ``EnvelopeKeyNotValidError``.
_FAULT_KINDS = (
    "wrong_recipient",
    "before_sent_at",
    "at_or_after_expires_at",
    "payload_hash_mismatch",
    "already_processed",
    "signer_mismatch",
    "unknown_kid",
    "key_revoked",
    "key_rotated",
    "key_expired",
    "key_not_yet_valid",
    "tampered_signature",
)


def _key_entry(
    public_key: Ed25519PublicKey,
    *,
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
    state: str = "active",
) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "state": state,
    }


def _practice(*, practice_id: str, keys: list[dict[str, Any]]) -> Practice:
    data: dict[str, Any] = {
        "id": practice_id,
        "api_version": "mrr/v1alpha1",
        "kind": "Practice",
        "practice_id": practice_id,
        "revision": 1,
        "created_at": _NOW,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "name": "Fixture Practice",
        "description": "Fixture practice for envelope validation property tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _envelope(
    *,
    sender_practice_id: str,
    key_id: str,
    recipient_node_id: str,
    sent_at: datetime,
    expires_at: datetime,
) -> NodeMessageEnvelope:
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": sent_at,
        "expires_at": expires_at,
        "payload_kind": "TaskBundle",
        "payload_content_hash": _PAYLOAD_HASH,
        "payload": {"kind": "TaskBundle", "content_hash": _PAYLOAD_HASH},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    return NodeMessageEnvelope.model_validate(data)


def _sign(envelope: NodeMessageEnvelope, private_key: Ed25519PrivateKey) -> NodeMessageEnvelope:
    signature_value = sign_object(
        private_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(message_id: str) -> bool:
    return False


@given(fault=st.sampled_from(_FAULT_KINDS))
def test_validator_never_accepts_for_a_failing_precondition(fault: str) -> None:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")

    if fault == "key_expired":
        entry = _key_entry(
            public_key, valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
        )
    elif fault == "key_not_yet_valid":
        entry = _key_entry(
            public_key, valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
        )
    elif fault == "key_revoked":
        entry = _key_entry(public_key, state="revoked")
    elif fault == "key_rotated":
        entry = _key_entry(public_key, state="rotated")
    else:
        entry = _key_entry(public_key)

    practice = _practice(practice_id=practice_id, keys=[entry])
    envelope = _envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        sent_at=_NOW - timedelta(minutes=1),
        expires_at=_NOW + timedelta(minutes=5),
    )
    signed = _sign(envelope, private_key)
    ring = practice_key_ring(practice)

    trusted_sender_practice_id = practice_id
    already_processed = _never_processed
    at = _NOW
    recipient_node_id = this_node_id

    expected_error: type[Exception]
    if fault == "wrong_recipient":
        recipient_node_id = new_urn("node")
        expected_error = EnvelopeRecipientMismatchError
    elif fault == "before_sent_at":
        at = signed.sent_at - timedelta(seconds=1)
        expected_error = EnvelopeNotWithinValidityWindowError
    elif fault == "at_or_after_expires_at":
        at = signed.expires_at
        expected_error = EnvelopeNotWithinValidityWindowError
    elif fault == "payload_hash_mismatch":
        signed = signed.model_copy(
            update={"payload": {"kind": "TaskBundle", "content_hash": "sha256:" + "9" * 64}}
        )
        expected_error = EnvelopePayloadContentHashMismatchError
    elif fault == "already_processed":

        def _always_processed(message_id: str) -> bool:
            return True

        already_processed = _always_processed
        expected_error = EnvelopeAlreadyProcessedError
    elif fault == "signer_mismatch":
        trusted_sender_practice_id = new_urn("practice")
        expected_error = EnvelopeSignerMismatchError
    elif fault == "unknown_kid":
        signed = signed.model_copy(
            update={"signature": signed.signature.model_copy(update={"key_id": "kid:unknown"})}
        )
        expected_error = UnknownKeyIdError
    elif fault in ("key_revoked", "key_rotated", "key_expired", "key_not_yet_valid"):
        expected_error = EnvelopeKeyNotValidError
    elif fault == "tampered_signature":
        signed = signed.model_copy(update={"payload_kind": "EvidenceCrate"})
        expected_error = SignatureVerificationError
    else:  # pragma: no cover - defensive; _FAULT_KINDS is the only source
        raise AssertionError(f"unhandled fault kind: {fault!r}")

    with pytest.raises(expected_error):
        validate_inbound_envelope(
            signed,
            this_node_id=recipient_node_id,
            trusted_sender_practice_id=trusted_sender_practice_id,
            ring=ring,
            already_processed=already_processed,
            at=at,
        )


def test_validator_accepts_when_every_precondition_holds() -> None:
    """Positive control for the property above: the identical construction,
    with no fault injected, returns normally — proving the fault branches
    are what causes rejection, not an always-failing construction.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    envelope = _envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        sent_at=_NOW - timedelta(minutes=1),
        expires_at=_NOW + timedelta(minutes=5),
    )
    signed = _sign(envelope, private_key)
    ring = practice_key_ring(practice)

    # Accepted means "returns normally" — validate_inbound_envelope's own
    # return type is None; a failing precondition would raise instead.
    validate_inbound_envelope(
        signed,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice_id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )
