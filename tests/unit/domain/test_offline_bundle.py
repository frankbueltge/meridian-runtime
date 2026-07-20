"""Unit tests for mrr.domain.offline_bundle (task-packets/E5-T06.yaml).

Covers the packet's named acceptance tests at the domain layer: the happy
path (``build_outbox_bundle`` assembles a bundle of two already-signed
envelopes for a recipient node with an active in-window sender key, and
``validate_inbound_bundle`` accepts it, returning the envelopes in order);
the full fail-closed matrix (wrong recipient, before/at-or-after the
validity window, already processed, signer-practice mismatch, unknown kid,
key not valid at the instant, tampered signature, entry hash mismatch),
each a DISTINCT typed error; the batch-tamper matrix (add/drop/reorder/
retarget an entry after signing breaks the bundle signature); the
key-substitution attack; revocation at the evaluation instant; and the
coarse-reason mapping is total over every one of those typed failures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.common import Signature
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.offline_bundle import BundleEncryption, BundleEntry, OfflineBundle
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
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
from mrr.domain.offline_bundle import (
    BUNDLE_VALIDATION_FAILURES,
    BundleRejectionReason,
    build_outbox_bundle,
    coarse_bundle_rejection_reason,
    validate_inbound_bundle,
)

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_BUNDLE_CREATED_AT = _NOW - timedelta(minutes=1)
_BUNDLE_EXPIRES_AT = _NOW + timedelta(days=7)

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
        "description": "Fixture practice for offline bundle unit tests.",
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


def _trusted_scenario(
    *,
    key_state: str = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
    created_at: datetime = _BUNDLE_CREATED_AT,
    expires_at: datetime = _BUNDLE_EXPIRES_AT,
    envelope_count: int = 2,
) -> tuple[OfflineBundle, Practice, str, list[NodeMessageEnvelope], Ed25519PrivateKey]:
    """A fully self-consistent scenario: a Practice with one key in
    ``key_state``, ``envelope_count`` already-signed envelopes addressed to
    ``this_node_id``, and an ``OfflineBundle`` genuinely assembled and
    signed by ``build_outbox_bundle`` for that same key.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(public_key, valid_from=valid_from, valid_until=valid_until, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
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
    bundle = build_outbox_bundle(
        envelopes,
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=new_urn("node"),
        sender_practice_id=practice_id,
        recipient_node_id=this_node_id,
        created_at=created_at,
        expires_at=expires_at,
        signing_key=private_key,
        key_id=entry["kid"],
    )
    return bundle, practice, this_node_id, envelopes, private_key


# ---------------------------------------------------------------------------
# Happy path — via the REAL build_outbox_bundle.
# ---------------------------------------------------------------------------


def test_happy_path_accepts_and_returns_envelopes_in_order() -> None:
    bundle, practice, this_node_id, envelopes, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    verified = validate_inbound_bundle(
        bundle,
        this_node_id=this_node_id,
        trusted_sender_practice_id=practice.id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )

    assert [envelope.message_id for envelope in verified] == [
        envelope.message_id for envelope in envelopes
    ]


# ---------------------------------------------------------------------------
# Fail-closed matrix: each a DISTINCT typed error, nothing ever accepted.
# ---------------------------------------------------------------------------


def test_wrong_recipient_raises_recipient_mismatch_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_node_id = new_urn("node")

    with pytest.raises(BundleRecipientMismatchError) as excinfo:
        validate_inbound_bundle(
            bundle,
            this_node_id=other_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.recipient_node_id == bundle.recipient_node_id
    assert excinfo.value.this_node_id == other_node_id


def test_before_created_at_raises_validity_window_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    with pytest.raises(BundleNotWithinValidityWindowError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=bundle.created_at - timedelta(seconds=1),
        )


def test_at_or_after_expires_at_raises_validity_window_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    with pytest.raises(BundleNotWithinValidityWindowError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=bundle.expires_at,
        )


def test_already_processed_bundle_id_raises_typed_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)

    def _always_processed(bundle_id: str) -> bool:
        assert bundle_id == bundle.bundle_id
        return True

    with pytest.raises(BundleAlreadyProcessedError) as excinfo:
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_always_processed,
            at=_NOW,
        )
    assert excinfo.value.bundle_id == bundle.bundle_id


def test_signer_practice_mismatch_raises_typed_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_practice_id = new_urn("practice")

    with pytest.raises(BundleSignerMismatchError) as excinfo:
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=other_practice_id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.claimed_signer_practice_id == bundle.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == other_practice_id


def test_unknown_kid_raises_unknown_key_id_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"key_id": "kid:unknown"})}
    )

    with pytest.raises(UnknownKeyIdError) as excinfo:
        validate_inbound_bundle(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.kid == "kid:unknown"


def test_revoked_key_raises_bundle_key_not_valid_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario(key_state="revoked")
    ring = practice_key_ring(practice)

    with pytest.raises(BundleKeyNotValidError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_rotated_key_raises_bundle_key_not_valid_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario(key_state="rotated")
    ring = practice_key_ring(practice)

    with pytest.raises(BundleKeyNotValidError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_expired_key_raises_bundle_key_not_valid_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(BundleKeyNotValidError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_not_yet_valid_key_raises_bundle_key_not_valid_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario(
        valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(BundleKeyNotValidError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_tampered_bundle_field_raises_signature_verification_error() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = bundle.model_copy(update={"bundle_nonce": "z" * 16})

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_entry_hash_mismatch_raises_typed_error() -> None:
    """A bundle honestly signed by the real private key, but whose own
    entries list already declares a WRONG ``envelope_content_hash`` at
    signing time (not a post-signing tamper) — the signature verifies (it
    covers exactly this, including the wrong hash), so this is a distinct,
    later check than signature verification (see
    ``mrr.domain.exceptions.BundleEntryHashMismatchError``'s own docstring
    for why this is checked independently, defense-in-depth, even once the
    signature is known good).
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

    wrong_hash = "sha256:" + "9" * 64
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
                envelope_content_hash=wrong_hash,
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
    ring = practice_key_ring(practice)

    with pytest.raises(BundleEntryHashMismatchError) as excinfo:
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice_id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
    assert excinfo.value.declared == wrong_hash
    actual_hash = compute_content_hash(json.loads(envelope.model_dump_json(exclude_none=True)))
    assert excinfo.value.actual == actual_hash


# ---------------------------------------------------------------------------
# Batch-tamper matrix: add/drop/reorder/retarget an entry after signing.
# ---------------------------------------------------------------------------


def test_adding_an_entry_after_signing_breaks_the_bundle_signature() -> None:
    bundle, practice, this_node_id, envelopes, private_key = _trusted_scenario()
    ring = practice_key_ring(practice)
    extra_envelope = _signed_envelope(
        sender_practice_id=practice.id,
        key_id=bundle.signature.key_id,
        recipient_node_id=this_node_id,
        private_key=private_key,
        tag=99,
    )
    extra_entry = BundleEntry(
        message_id=extra_envelope.message_id,
        payload_kind=extra_envelope.payload_kind,
        envelope_content_hash=compute_content_hash(
            json.loads(extra_envelope.model_dump_json(exclude_none=True))
        ),
    )
    tampered = bundle.model_copy(
        update={
            "entries": [*bundle.entries, extra_entry],
            "envelopes": [*bundle.envelopes, extra_envelope],
        }
    )

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_dropping_an_entry_after_signing_breaks_the_bundle_signature() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = bundle.model_copy(
        update={"entries": bundle.entries[:-1], "envelopes": bundle.envelopes[:-1]}
    )

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_reordering_entries_after_signing_breaks_the_bundle_signature() -> None:
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    assert len(bundle.entries) == 2
    tampered = bundle.model_copy(
        update={
            "entries": list(reversed(bundle.entries)),
            "envelopes": list(reversed(bundle.envelopes)),
        }
    )

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


def test_retargeting_an_entrys_envelope_after_signing_breaks_the_bundle_signature() -> None:
    bundle, practice, this_node_id, envelopes, private_key = _trusted_scenario()
    ring = practice_key_ring(practice)
    substitute_envelope = _signed_envelope(
        sender_practice_id=practice.id,
        key_id=bundle.signature.key_id,
        recipient_node_id=this_node_id,
        private_key=private_key,
        tag=123,
    )
    tampered = bundle.model_copy(update={"envelopes": [substitute_envelope, bundle.envelopes[1]]})

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            tampered,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


# ---------------------------------------------------------------------------
# Key-substitution attack: verification is against the RING's key, never a
# key the bundle itself claims.
# ---------------------------------------------------------------------------


def test_key_substitution_attack_fails_closed_with_signature_verification_error() -> None:
    """An attacker without the trusted private key cannot forge acceptance
    by signing with their OWN key while claiming the victim's trusted kid:
    validate_inbound_bundle decodes and verifies against the RING's
    descriptor for that kid, never anything the bundle itself carries, so
    the attacker's signature simply does not verify under the real key.
    """
    _, trusted_public_key = generate_ed25519_keypair()
    attacker_private_key, _ = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    this_node_id = new_urn("node")
    entry = _key_entry(trusted_public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])
    envelope = _signed_envelope(
        sender_practice_id=practice_id,
        key_id=entry["kid"],
        recipient_node_id=this_node_id,
        private_key=attacker_private_key,
    )

    forged = build_outbox_bundle(
        [envelope],
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=new_urn("node"),
        sender_practice_id=practice_id,
        recipient_node_id=this_node_id,
        created_at=_BUNDLE_CREATED_AT,
        expires_at=_BUNDLE_EXPIRES_AT,
        signing_key=attacker_private_key,
        key_id=entry["kid"],
    )
    ring = practice_key_ring(practice)

    with pytest.raises(SignatureVerificationError):
        validate_inbound_bundle(
            forged,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice_id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )


# ---------------------------------------------------------------------------
# docs/spec/04 section 8.4: valid at creation, revoked by the evaluation
# instant.
# ---------------------------------------------------------------------------


def test_key_valid_at_creation_but_revoked_by_instant_is_rejected() -> None:
    """A bundle signed while the key was active must still be rejected if
    the practice has since revoked that key by the evaluation instant —
    trust anchoring beyond raw signature validity. The revoked descriptor
    stays resolvable in the ring (historical attributability, E5-T01).
    """
    bundle, practice, this_node_id, _, _ = _trusted_scenario()
    kid = bundle.signature.key_id

    revoked_practice = practice.model_copy(
        update={"keys": [practice.keys[0].model_copy(update={"state": "revoked"})]}
    )
    ring = practice_key_ring(revoked_practice)

    with pytest.raises(BundleKeyNotValidError):
        validate_inbound_bundle(
            bundle,
            this_node_id=this_node_id,
            trusted_sender_practice_id=practice.id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )

    resolved_descriptor = ring.get(kid)
    assert resolved_descriptor is not None
    assert resolved_descriptor.state == "revoked"


# ---------------------------------------------------------------------------
# Coarse rejection reason (docs/spec/04 section 8.3): total over every
# typed failure raised by validate_inbound_bundle.
# ---------------------------------------------------------------------------


def _instantiate(error_type: type[Exception]) -> Exception:
    if error_type is BundleRecipientMismatchError:
        return BundleRecipientMismatchError("bundle-id", "node-a", "node-b")
    if error_type is BundleNotWithinValidityWindowError:
        return BundleNotWithinValidityWindowError("bundle-id", _NOW, _NOW + timedelta(1), _NOW)
    if error_type is BundleAlreadyProcessedError:
        return BundleAlreadyProcessedError("bundle-id")
    if error_type is BundleSignerMismatchError:
        return BundleSignerMismatchError(
            claimed_signer_practice_id="practice-a", trusted_practice_id="practice-b"
        )
    if error_type is UnknownKeyIdError:
        return UnknownKeyIdError("kid:unknown")
    if error_type is BundleKeyNotValidError:
        return BundleKeyNotValidError("kid:x", at=_NOW)
    if error_type is SignatureVerificationError:
        return SignatureVerificationError("bad signature")
    if error_type is BundleEntryHashMismatchError:
        return BundleEntryHashMismatchError(
            "bundle-id", "msg-id", "sha256:" + "1" * 64, "sha256:" + "2" * 64
        )
    raise AssertionError(f"no instantiation recipe for {error_type!r}")  # pragma: no cover


@pytest.mark.parametrize(
    "error_type", [t for t in BUNDLE_VALIDATION_FAILURES if t is not UnsupportedAlgorithmError]
)
def test_coarse_rejection_reason_is_defined_for_every_failure_type(
    error_type: type[Exception],
) -> None:
    instance = _instantiate(error_type)
    reason = coarse_bundle_rejection_reason(instance)
    assert reason in get_args(BundleRejectionReason)


def test_coarse_rejection_reason_maps_signature_and_algorithm_errors_the_same_way() -> None:
    assert coarse_bundle_rejection_reason(
        SignatureVerificationError("bad")
    ) == coarse_bundle_rejection_reason(UnsupportedAlgorithmError("bad algorithm"))


def test_coarse_rejection_reason_raises_key_error_for_an_unrelated_exception() -> None:
    with pytest.raises(KeyError):
        coarse_bundle_rejection_reason(ValueError("not one of validate_inbound_bundle's failures"))
