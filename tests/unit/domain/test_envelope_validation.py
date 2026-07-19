"""Unit tests for mrr.domain.envelope_validation (task-packets/E5-T03.yaml).

Covers the packet's named acceptance tests at the domain layer: a validly
signed envelope addressed to this node, within its validity window, whose
carried payload's content_hash matches payload_content_hash, whose message
id is unseen, and whose transport signature verifies under the sender's
valid trusted key, is ACCEPTED; the full fail-closed matrix (wrong
recipient, before sent_at, at-or-after expires_at, payload content_hash
mismatch, message id already processed, signer-practice mismatch, unknown
kid, key not valid at the instant, tampered signature), each a DISTINCT
typed error; and the coarse-reason mapping is total over every one of
those typed failures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.envelope_validation import (
    ENVELOPE_VALIDATION_FAILURES,
    EnvelopeRejectionReason,
    coarse_rejection_reason,
    validate_inbound_envelope,
)
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

# ---------------------------------------------------------------------------
# Fixture builders (deliberately local — this codebase's own convention of
# duplicating small fixture builders per test tier rather than sharing them,
# see tests/unit/domain/test_manifest_trust.py's own precedent).
# ---------------------------------------------------------------------------


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
        "description": "Fixture practice for envelope validation unit tests.",
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
    sent_at: datetime = _NOW - timedelta(minutes=1),
    expires_at: datetime = _NOW + timedelta(minutes=5),
    payload_content_hash: str = _PAYLOAD_HASH,
    payload: dict[str, Any] | None = None,
    **overrides: Any,
) -> NodeMessageEnvelope:
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": sent_at,
        "expires_at": expires_at,
        "payload_kind": "TaskBundle",
        "payload_content_hash": payload_content_hash,
        "payload": payload
        if payload is not None
        else {"kind": "TaskBundle", "content_hash": payload_content_hash},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return NodeMessageEnvelope.model_validate(data)


def _sign(envelope: NodeMessageEnvelope, private_key: Ed25519PrivateKey) -> NodeMessageEnvelope:
    """Sign over the ADR-0004 ``exclude_none=True`` form — the same
    canonical body ``validate_inbound_envelope`` verifies against.
    """
    signature_value = sign_object(
        private_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(message_id: str) -> bool:
    return False


def _trusted_scenario(
    *,
    key_state: str = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
    sent_at: datetime = _NOW - timedelta(minutes=1),
    expires_at: datetime = _NOW + timedelta(minutes=5),
) -> tuple[NodeMessageEnvelope, Practice, str, Ed25519PrivateKey]:
    """A fully self-consistent scenario: a Practice with one key in
    ``key_state``, and a NodeMessageEnvelope genuinely signed by that key,
    addressed to ``this_node_id``.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key, valid_from=valid_from, valid_until=valid_until, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
    envelope = _envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        sent_at=sent_at,
        expires_at=expires_at,
    )
    return _sign(envelope, private_key), practice, this_node_id, private_key


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_fully_valid_envelope_is_accepted() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    # Accepted means "returns normally" — validate_inbound_envelope's own
    # return type is None; a failing precondition would raise instead.
    validate_inbound_envelope(
        envelope,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )


# ---------------------------------------------------------------------------
# Fail-closed matrix: each a DISTINCT typed error, nothing ever accepted.
# ---------------------------------------------------------------------------


def test_wrong_recipient_raises_recipient_mismatch_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_node_id = new_urn("node")

    with pytest.raises(EnvelopeRecipientMismatchError) as excinfo:
        validate_inbound_envelope(
            envelope,
            this_node_id=other_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.recipient_node_id == envelope.recipient_node_id
    assert excinfo.value.this_node_id == other_node_id


def test_before_sent_at_raises_validity_window_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    with pytest.raises(EnvelopeNotWithinValidityWindowError):
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=envelope.sent_at - timedelta(seconds=1),
        )


def test_at_or_after_expires_at_raises_validity_window_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    with pytest.raises(EnvelopeNotWithinValidityWindowError):
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=envelope.expires_at,
        )


def test_payload_content_hash_mismatch_raises_typed_error() -> None:
    """Payload tampered after the envelope was signed: the payload dict is
    swapped for one whose own content_hash no longer matches
    payload_content_hash — the payload itself is not part of the check
    performed by signature verification (it is carried, not itself
    resigned), so this must be its own distinct check.
    """
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered_payload = dict(envelope.payload)
    tampered_payload["content_hash"] = "sha256:" + "9" * 64
    tampered = envelope.model_copy(update={"payload": tampered_payload})

    with pytest.raises(EnvelopePayloadContentHashMismatchError) as excinfo:
        validate_inbound_envelope(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.declared == envelope.payload_content_hash
    assert excinfo.value.actual == "sha256:" + "9" * 64


def test_missing_payload_content_hash_raises_typed_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = envelope.model_copy(update={"payload": {"kind": "TaskBundle"}})

    with pytest.raises(EnvelopePayloadContentHashMismatchError) as excinfo:
        validate_inbound_envelope(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.actual is None


def test_already_processed_message_id_raises_typed_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    def _always_processed(message_id: str) -> bool:
        assert message_id == envelope.message_id
        return True

    with pytest.raises(EnvelopeAlreadyProcessedError) as excinfo:
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_always_processed,
            at=_NOW,
        )
    assert excinfo.value.message_id == envelope.message_id


def test_signer_practice_mismatch_raises_typed_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_practice_id = new_urn("practice")

    with pytest.raises(EnvelopeSignerMismatchError) as excinfo:
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=other_practice_id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.claimed_signer_practice_id == envelope.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == other_practice_id


def test_unknown_kid_raises_unknown_key_id_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    # Bypass NodeMessageEnvelope's own signer/sender consistency check by
    # tampering only the signature's key_id, not its signer_practice_id.
    tampered = envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"key_id": "kid:unknown"})}
    )

    with pytest.raises(UnknownKeyIdError) as excinfo:
        validate_inbound_envelope(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.kid == "kid:unknown"


def test_revoked_key_raises_envelope_key_not_valid_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario(key_state="revoked")
    ring = practice_key_ring(practice)

    with pytest.raises(EnvelopeKeyNotValidError):
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_expired_key_raises_envelope_key_not_valid_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(EnvelopeKeyNotValidError):
        validate_inbound_envelope(
            envelope,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_tampered_envelope_field_raises_signature_verification_error() -> None:
    envelope, practice, this_node_id, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = envelope.model_copy(update={"payload_kind": "EvidenceCrate"})

    with pytest.raises(SignatureVerificationError):
        validate_inbound_envelope(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


# ---------------------------------------------------------------------------
# Coarse rejection reason (docs/spec/04 section 8.3): total over every
# typed failure raised by validate_inbound_envelope.
# ---------------------------------------------------------------------------


def _instantiate(error_type: type[Exception]) -> Exception:
    """Build one instance of each of validate_inbound_envelope's typed
    failures, so the coarse-reason mapping can be exercised against a real
    instance of every type without duplicating each constructor's exact
    argument shape inline at every call site below.
    """
    if error_type is EnvelopeRecipientMismatchError:
        return EnvelopeRecipientMismatchError("msg-id", "node-a", "node-b")
    if error_type is EnvelopeNotWithinValidityWindowError:
        return EnvelopeNotWithinValidityWindowError("msg-id", _NOW, _NOW + timedelta(1), _NOW)
    if error_type is EnvelopePayloadContentHashMismatchError:
        return EnvelopePayloadContentHashMismatchError("msg-id", _PAYLOAD_HASH, None)
    if error_type is EnvelopeAlreadyProcessedError:
        return EnvelopeAlreadyProcessedError("msg-id")
    if error_type is EnvelopeSignerMismatchError:
        return EnvelopeSignerMismatchError(
            claimed_signer_practice_id="practice-a", trusted_practice_id="practice-b"
        )
    if error_type is UnknownKeyIdError:
        return UnknownKeyIdError("kid:unknown")
    if error_type is EnvelopeKeyNotValidError:
        return EnvelopeKeyNotValidError("kid:x", at=_NOW)
    if error_type is SignatureVerificationError:
        return SignatureVerificationError("bad signature")
    raise AssertionError(f"no instantiation recipe for {error_type!r}")  # pragma: no cover


@pytest.mark.parametrize(
    "error_type", [t for t in ENVELOPE_VALIDATION_FAILURES if t is not UnsupportedAlgorithmError]
)
def test_coarse_rejection_reason_is_defined_for_every_failure_type(
    error_type: type[Exception],
) -> None:
    instance = _instantiate(error_type)
    reason = coarse_rejection_reason(instance)
    assert reason in get_args(EnvelopeRejectionReason)


def test_coarse_rejection_reason_maps_signature_and_algorithm_errors_the_same_way() -> None:
    assert coarse_rejection_reason(SignatureVerificationError("bad")) == coarse_rejection_reason(
        UnsupportedAlgorithmError("bad algorithm")
    )


def test_coarse_rejection_reason_raises_key_error_for_an_unrelated_exception() -> None:
    with pytest.raises(KeyError):
        coarse_rejection_reason(ValueError("not one of validate_inbound_envelope's failures"))
