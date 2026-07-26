"""Unit tests for mrr.domain.envelope_signing (task-packets/E5-T10.yaml).

Covers this module's own named acceptance criteria at the domain layer: a
payload carrying its own content_hash is built into a validly signed
NodeMessageEnvelope that the UNCHANGED
mrr.domain.envelope_validation.validate_inbound_envelope accepts; a payload
without one is a typed refusal and no envelope is built at all; a
payload_content_hash tampered with after signing makes the UNCHANGED
receiver fail its own condition 3 (proving the consistency check was not
weakened by this module); a single tampered byte elsewhere in the envelope
body makes the UNCHANGED receiver fail signature verification; identical
inputs (including message_id and sent_at) yield a byte-identical envelope
(no clock, no randomness anywhere in this module); and a SECOND, different
payload_kind travels through the exact same function, so payload-agnosticism
is demonstrated, not merely asserted.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain import envelope_signing
from mrr.domain.envelope_signing import build_signed_envelope
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.exceptions import (
    EnvelopePayloadContentHashMismatchError,
    EnvelopePayloadMissingContentHashError,
)
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from pydantic import ValidationError

_NOW = datetime(2026, 7, 26, 9, 0, 0, tzinfo=UTC)
_SENT_AT = _NOW - timedelta(minutes=1)
_EXPIRES_AT = _NOW + timedelta(days=1)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)

# ---------------------------------------------------------------------------
# Fixture builders (deliberately local — this codebase's own convention of
# duplicating small fixture builders per test tier rather than sharing them,
# see tests/unit/domain/test_envelope_validation.py's own precedent).
# ---------------------------------------------------------------------------


def _never_processed(message_id: str) -> bool:
    return False


def _key_entry(public_key: Ed25519PublicKey) -> dict[str, Any]:
    return {
        "kid": derive_key_id(public_key),
        "algorithm": "Ed25519",
        "encoded_public_key": encode_public_key(public_key),
        "valid_from": _VALID_FROM,
        "valid_until": _VALID_UNTIL,
        "state": "active",
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
        "description": "Fixture practice for envelope signing unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


class _Scenario:
    """A fully self-consistent scenario: one practice with one active key,
    a sender node id, and a recipient node id — enough to both BUILD and
    VALIDATE an envelope signed under this practice's own key, so tests can
    prove build_signed_envelope's output is actually accepted by the
    UNCHANGED validate_inbound_envelope, not merely well-typed.
    """

    def __init__(self) -> None:
        self.signing_key, public_key = generate_ed25519_keypair()
        entry = _key_entry(public_key)
        self.key_id = entry["kid"]
        self.practice = _practice(practice_id=new_urn("practice"), keys=[entry])
        self.sender_node_id = new_urn("node")
        self.recipient_node_id = new_urn("node")

    def build(
        self,
        payload: dict[str, Any],
        *,
        payload_kind: str = "VerificationResult",
        message_id: str | None = None,
        sent_at: datetime = _SENT_AT,
        expires_at: datetime = _EXPIRES_AT,
    ) -> Any:
        return build_signed_envelope(
            payload,
            payload_kind=payload_kind,
            message_id=message_id if message_id is not None else new_urn("node-message-envelope"),
            sender_node_id=self.sender_node_id,
            sender_practice_id=self.practice.id,
            recipient_node_id=self.recipient_node_id,
            sent_at=sent_at,
            expires_at=expires_at,
            signing_key=self.signing_key,
            key_id=self.key_id,
        )

    def accept(self, envelope: Any, *, at: datetime = _NOW) -> None:
        ring = practice_key_ring(self.practice)
        validate_inbound_envelope(
            envelope,
            this_node_id=self.recipient_node_id,
            trusted_sender_practice_id=self.practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=at,
        )


# ---------------------------------------------------------------------------
# Happy path: built, signed, and accepted by the UNCHANGED receiver.
# ---------------------------------------------------------------------------


def test_signed_envelope_is_accepted_by_unchanged_validate_inbound_envelope() -> None:
    scenario = _Scenario()
    payload = {"kind": "VerificationResult", "content_hash": "sha256:" + "b" * 64}

    envelope = scenario.build(payload)

    assert envelope.payload_kind == "VerificationResult"
    assert envelope.payload_content_hash == payload["content_hash"]
    assert envelope.payload == payload
    # Accepted means "returns normally" — validate_inbound_envelope's own
    # return type is None; a failing precondition would raise instead.
    scenario.accept(envelope)


# ---------------------------------------------------------------------------
# The hard rule: refuse what the receiver would reject.
# ---------------------------------------------------------------------------


def test_payload_missing_content_hash_key_is_refused_before_any_envelope_is_built() -> None:
    scenario = _Scenario()
    payload = {"kind": "VerificationResult"}  # no "content_hash" key at all

    with pytest.raises(EnvelopePayloadMissingContentHashError) as excinfo:
        scenario.build(payload)
    assert excinfo.value.payload_kind == "VerificationResult"


def test_payload_with_explicit_none_content_hash_is_also_refused() -> None:
    scenario = _Scenario()
    payload = {"kind": "VerificationResult", "content_hash": None}

    with pytest.raises(EnvelopePayloadMissingContentHashError):
        scenario.build(payload)


def test_tampering_payload_content_hash_after_signing_fails_receivers_condition_3() -> None:
    """A payload_content_hash tampered with AFTER signing must fail the
    UNCHANGED receiver's condition 3 (EnvelopePayloadContentHashMismatchError)
    — proving this module's read-not-recompute discipline did not weaken
    what the receiver checks.
    """
    scenario = _Scenario()
    payload = {"kind": "VerificationResult", "content_hash": "sha256:" + "c" * 64}
    envelope = scenario.build(payload)
    tampered = envelope.model_copy(update={"payload_content_hash": "sha256:" + "9" * 64})

    with pytest.raises(EnvelopePayloadContentHashMismatchError):
        scenario.accept(tampered)


def test_one_flipped_byte_in_envelope_body_fails_signature_verification() -> None:
    scenario = _Scenario()
    payload = {"kind": "VerificationResult", "content_hash": "sha256:" + "d" * 64}
    envelope = scenario.build(payload)
    tampered = envelope.model_copy(update={"payload_kind": envelope.payload_kind + "-tampered"})

    with pytest.raises(SignatureVerificationError):
        scenario.accept(tampered)


# ---------------------------------------------------------------------------
# Reproducibility: no clock, no randomness anywhere in this module.
# ---------------------------------------------------------------------------


def test_identical_inputs_yield_byte_identical_envelope() -> None:
    scenario = _Scenario()
    payload = {"kind": "VerificationResult", "content_hash": "sha256:" + "e" * 64}
    message_id = new_urn("node-message-envelope")

    envelope_a = scenario.build(dict(payload), message_id=message_id)
    envelope_b = scenario.build(dict(payload), message_id=message_id)

    assert envelope_a.model_dump_json(exclude_none=True) == envelope_b.model_dump_json(
        exclude_none=True
    )
    assert envelope_a.signature.value == envelope_b.signature.value


# ---------------------------------------------------------------------------
# Payload-agnosticism demonstrated, not asserted: a SECOND, different
# payload_kind through the exact same function.
# ---------------------------------------------------------------------------


def test_a_second_different_payload_kind_travels_through_the_same_function() -> None:
    scenario = _Scenario()
    payload = {
        "kind": "DissentNote",
        "content_hash": "sha256:" + "f" * 64,
        "note": "a payload kind build_signed_envelope has never special-cased",
    }

    envelope = scenario.build(payload, payload_kind="DissentNote")

    assert envelope.payload_kind == "DissentNote"
    assert envelope.payload_content_hash == payload["content_hash"]
    scenario.accept(envelope)


def test_module_imports_no_correction_notification_and_declares_no_closed_kind_set() -> None:
    """AGENTS.md rule 3 / task-packets/E5-T10.yaml explicitly_not: the
    signing twin, like the validating one, has no opinion on any specific
    payload kind. Checked against the module's own ACTUAL import
    statements (via ``ast``, not a substring scan — the module's own
    docstring legitimately names ``CorrectionNotification`` in prose, to
    explain that it is NOT imported, which a plain substring check would
    misread as a violation).
    """
    tree = ast.parse(inspect.getsource(envelope_signing))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_names.add(node.module)
            imported_names.update(alias.name for alias in node.names)

    assert not any("correction" in name.lower() for name in imported_names)
    assert "CorrectionNotification" not in imported_names


# ---------------------------------------------------------------------------
# Contract-level validation propagates unwrapped, never softened.
# ---------------------------------------------------------------------------


def test_expires_at_not_strictly_after_sent_at_raises_validation_error() -> None:
    scenario = _Scenario()
    payload = {"kind": "VerificationResult", "content_hash": "sha256:" + "1" * 64}

    with pytest.raises(ValidationError):
        scenario.build(payload, sent_at=_EXPIRES_AT, expires_at=_SENT_AT)
