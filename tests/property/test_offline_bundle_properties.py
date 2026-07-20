"""Property tests: ``mrr.domain.offline_bundle.validate_inbound_bundle``
never accepts (returns normally) for any single failing precondition, and
``mrr.domain.offline_bundle.build_outbox_bundle`` is deterministic — the
same envelopes and metadata always yield a byte-identical canonical
``exclude_none`` form and the same bundle content hash (task-packets/
E5-T06.yaml acceptance tests: the fail-closed matrix, and "round-trip
determinism").

Ed25519 keypairs have no seedable hypothesis strategy exposed by the
``cryptography`` library (mirroring
tests/property/test_envelope_validation_properties.py's own rationale), so
each example generates a fresh keypair directly; ``hypothesis`` drives
which of the twelve concrete fault kinds is injected into an otherwise
fully self-consistent, genuinely signed bundle. A positive control
(``test_validator_accepts_when_every_precondition_holds``) proves the same
construction, with no fault injected, DOES return normally.

Local, deliberate duplicate of tests/unit/domain/test_offline_bundle.py's
own fixture builders — this codebase's established convention for
independent test tiers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.common import Signature
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.offline_bundle import BundleEncryption, BundleEntry, OfflineBundle
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import (
    BundleAlreadyProcessedError,
    BundleEntryHashMismatchError,
    BundleKeyNotValidError,
    BundleNotWithinValidityWindowError,
    BundleRecipientMismatchError,
    BundleSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import compute_content_hash, sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import build_outbox_bundle, validate_inbound_bundle

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_BUNDLE_CREATED_AT = _NOW - timedelta(minutes=1)
_BUNDLE_EXPIRES_AT = _NOW + timedelta(days=7)

#: The twelve concrete ways ``validate_inbound_bundle`` must fail closed —
#: the packet's own named fail-closed matrix, minus the happy path. Four
#: distinct key-validity sub-cases all raise the same
#: ``BundleKeyNotValidError`` (mirroring
#: test_envelope_validation_properties.py's own four).
_FAULT_KINDS = (
    "wrong_recipient",
    "before_created_at",
    "at_or_after_expires_at",
    "already_processed",
    "signer_mismatch",
    "unknown_kid",
    "key_revoked",
    "key_rotated",
    "key_expired",
    "key_not_yet_valid",
    "tampered_signature",
    "entry_hash_mismatch",
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
        "description": "Fixture practice for offline bundle property tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _signed_envelope(
    *,
    sender_practice_id: str,
    key_id: str,
    recipient_node_id: str,
    private_key: Ed25519PrivateKey,
    tag: int = 0,
) -> NodeMessageEnvelope:
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": _NOW - timedelta(minutes=1),
        "expires_at": _NOW + timedelta(days=1),
        "payload_kind": "TaskBundle",
        "payload_content_hash": "sha256:" + "c" * 64,
        "payload": {"kind": "TaskBundle", "content_hash": "sha256:" + "c" * 64, "tag": tag},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    envelope = NodeMessageEnvelope.model_validate(data)
    signature_value = sign_object(
        private_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(bundle_id: str) -> bool:
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
    envelope = _signed_envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        private_key=private_key,
    )
    ring = practice_key_ring(practice)

    if fault == "entry_hash_mismatch":
        draft = OfflineBundle(
            bundle_id=new_urn("offline-bundle"),
            bundle_nonce="n" * 16,
            sender_node_id=new_urn("node"),
            sender_practice_id=practice_id,
            recipient_node_id=this_node_id,
            created_at=_BUNDLE_CREATED_AT,
            expires_at=_BUNDLE_EXPIRES_AT,
            entries=[
                BundleEntry(
                    message_id=envelope.message_id,
                    payload_kind=envelope.payload_kind,
                    envelope_content_hash="sha256:" + "9" * 64,
                )
            ],
            envelopes=[envelope],
            encryption=BundleEncryption(scheme="none"),
            signature=Signature(
                signer_practice_id=practice_id,
                key_id=entry["kid"],
                algorithm="Ed25519",
                signed_at=_NOW,
                value="0" * 44,
            ),
        )
        body = json.loads(draft.model_dump_json(exclude_none=True))
        body["signature"]["value"] = sign_object(private_key, body)
        bundle = OfflineBundle.model_validate(body)
    else:
        bundle = build_outbox_bundle(
            [envelope],
            bundle_id=new_urn("offline-bundle"),
            bundle_nonce="n" * 16,
            sender_node_id=new_urn("node"),
            sender_practice_id=practice_id,
            recipient_node_id=this_node_id,
            created_at=_BUNDLE_CREATED_AT,
            expires_at=_BUNDLE_EXPIRES_AT,
            signing_key=private_key,
            key_id=entry["kid"],
        )

    trusted_sender_practice_id = practice_id
    already_processed = _never_processed
    at = _NOW
    recipient_node_id = this_node_id

    expected_error: type[Exception]
    if fault == "wrong_recipient":
        recipient_node_id = new_urn("node")
        expected_error = BundleRecipientMismatchError
    elif fault == "before_created_at":
        at = bundle.created_at - timedelta(seconds=1)
        expected_error = BundleNotWithinValidityWindowError
    elif fault == "at_or_after_expires_at":
        at = bundle.expires_at
        expected_error = BundleNotWithinValidityWindowError
    elif fault == "already_processed":

        def _always_processed(bundle_id: str) -> bool:
            return True

        already_processed = _always_processed
        expected_error = BundleAlreadyProcessedError
    elif fault == "signer_mismatch":
        trusted_sender_practice_id = new_urn("practice")
        expected_error = BundleSignerMismatchError
    elif fault == "unknown_kid":
        bundle = bundle.model_copy(
            update={"signature": bundle.signature.model_copy(update={"key_id": "kid:unknown"})}
        )
        expected_error = UnknownKeyIdError
    elif fault in ("key_revoked", "key_rotated", "key_expired", "key_not_yet_valid"):
        expected_error = BundleKeyNotValidError
    elif fault == "tampered_signature":
        bundle = bundle.model_copy(update={"bundle_nonce": "z" * 16})
        expected_error = SignatureVerificationError
    elif fault == "entry_hash_mismatch":
        expected_error = BundleEntryHashMismatchError
    else:  # pragma: no cover - defensive; _FAULT_KINDS is the only source
        raise AssertionError(f"unhandled fault kind: {fault!r}")

    with pytest.raises(expected_error):
        validate_inbound_bundle(
            bundle,
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
    envelope = _signed_envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        private_key=private_key,
    )
    bundle = build_outbox_bundle(
        [envelope],
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=new_urn("node"),
        sender_practice_id=practice_id,
        recipient_node_id=this_node_id,
        created_at=_BUNDLE_CREATED_AT,
        expires_at=_BUNDLE_EXPIRES_AT,
        signing_key=private_key,
        key_id=entry["kid"],
    )
    ring = practice_key_ring(practice)

    verified = validate_inbound_bundle(
        bundle,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice_id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )
    assert [e.message_id for e in verified] == [envelope.message_id]


# ---------------------------------------------------------------------------
# Round-trip determinism (task-packets/E5-T06.yaml acceptance test): the
# same envelopes and metadata always yield a byte-identical canonical
# exclude_none form and the same bundle content hash.
# ---------------------------------------------------------------------------


@given(envelope_count=st.integers(min_value=1, max_value=4))
def test_build_outbox_bundle_is_deterministic_for_the_same_inputs(envelope_count: int) -> None:
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key)
    envelopes = [
        _signed_envelope(
            sender_practice_id=practice_id,
            key_id=entry["kid"],
            recipient_node_id=this_node_id,
            private_key=private_key,
            tag=i,
        )
        for i in range(envelope_count)
    ]
    bundle_id = new_urn("offline-bundle")
    sender_node_id = new_urn("node")

    kwargs: dict[str, Any] = {
        "bundle_id": bundle_id,
        "bundle_nonce": "n" * 16,
        "sender_node_id": sender_node_id,
        "sender_practice_id": practice_id,
        "recipient_node_id": this_node_id,
        "created_at": _BUNDLE_CREATED_AT,
        "expires_at": _BUNDLE_EXPIRES_AT,
        "signing_key": private_key,
        "key_id": entry["kid"],
    }

    first = build_outbox_bundle(envelopes, **kwargs)
    second = build_outbox_bundle(envelopes, **kwargs)

    first_body = first.model_dump_json(exclude_none=True)
    second_body = second.model_dump_json(exclude_none=True)
    assert first_body == second_body
    assert compute_content_hash(json.loads(first_body)) == compute_content_hash(
        json.loads(second_body)
    )
