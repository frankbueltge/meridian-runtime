"""Unit tests for mrr.domain.correction_notification (task-packets/
E6-T03.yaml).

Covers the packet's named acceptance test — "signer/key fail-closed matrix
(adversarial), each a distinct typed error, mirroring resolve_trusted_task_
key's own four-condition test matrix exactly" — a Practice with an active
in-window key trust-anchors and resolves a CorrectionNotification it signed;
the full fail-closed matrix (unknown kid, revoked, rotated, expired,
not-yet-valid, signer mismatch, tampered signature), each a DISTINCT typed
error; a key valid at signing but revoked by the evaluation instant is
rejected while its descriptor remains resolvable (docs/spec/04 section 8.4);
and the key-substitution attack fails closed.

Deliberate local duplicate of tests/unit/domain/test_task_trust.py's own
fixture-building convention, adapted to CorrectionNotification (this
codebase's established convention for independent test tiers).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.correction_notification import CorrectionNotification
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.correction_notification import resolve_trusted_correction_notification_key
from mrr.domain.exceptions import (
    CorrectionNotificationKeyNotValidError,
    CorrectionNotificationSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)

# ---------------------------------------------------------------------------
# Fixture builders.
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
        "description": "Fixture practice for correction notification trust unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _notification(
    *,
    notifying_practice_id: str,
    key_id: str,
    **overrides: Any,
) -> CorrectionNotification:
    now = _NOW
    data: dict[str, Any] = {
        "notification_id": new_urn("correction-notification"),
        "correction_id": new_urn("correction"),
        "correction_revision": 1,
        "notifying_practice_id": notifying_practice_id,
        "recipient_practice_id": new_urn("practice"),
        "notified_object_ids": [new_urn("claim")],
        "correction_type": "numeric_error",
        "severity": "material",
        "reason": "Fixture reason: the denominator was later shown to be wrong.",
        "requested_action": "Mark dependent claims review_required and recompute.",
        "replacement_object_id": None,
        "content_hash": "sha256:" + "3" * 64,
        "nonce": "n" * 16,
        "sent_at": now,
        "expires_at": now + timedelta(minutes=5),
        "signature": {
            "signer_practice_id": notifying_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return CorrectionNotification.model_validate(data)


def _sign(
    notification: CorrectionNotification, private_key: Ed25519PrivateKey
) -> CorrectionNotification:
    """Sign over the ADR-0004 ``exclude_none=True`` form — the same
    canonical body ``resolve_trusted_correction_notification_key`` verifies
    against.
    """
    signature_value = sign_object(
        private_key, json.loads(notification.model_dump_json(exclude_none=True))
    )
    return notification.model_copy(
        update={"signature": notification.signature.model_copy(update={"value": signature_value})}
    )


def _trusted_scenario(
    *,
    key_state: str = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
) -> tuple[CorrectionNotification, Practice, Ed25519PrivateKey]:
    """A fully self-consistent scenario: a Practice with one key in
    ``key_state``, and a CorrectionNotification genuinely signed by that
    key, naming the practice as its notifying practice.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(public_key, valid_from=valid_from, valid_until=valid_until, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
    notification = _notification(notifying_practice_id=practice_id, key_id=entry["kid"])
    return _sign(notification, private_key), practice, private_key


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_happy_path_resolves_the_trusted_verifying_key() -> None:
    notification, practice, private_key = _trusted_scenario()
    ring = practice_key_ring(practice)

    resolved = resolve_trusted_correction_notification_key(notification, practice.id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == private_key.public_key().public_bytes_raw()


# ---------------------------------------------------------------------------
# Fail-closed matrix: each a DISTINCT typed error, no key ever returned.
# ---------------------------------------------------------------------------


def test_unknown_kid_raises_unknown_key_id_error() -> None:
    notification, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = notification.model_copy(
        update={
            "signature": notification.signature.model_copy(update={"key_id": "kid:does-not-exist"})
        }
    )

    with pytest.raises(UnknownKeyIdError) as excinfo:
        resolve_trusted_correction_notification_key(tampered, practice.id, ring, at=_NOW)
    assert excinfo.value.kid == "kid:does-not-exist"


def test_revoked_key_raises_correction_notification_key_not_valid_error() -> None:
    notification, practice, _ = _trusted_scenario(key_state="revoked")
    ring = practice_key_ring(practice)

    with pytest.raises(CorrectionNotificationKeyNotValidError):
        resolve_trusted_correction_notification_key(notification, practice.id, ring, at=_NOW)


def test_rotated_key_raises_correction_notification_key_not_valid_error() -> None:
    notification, practice, _ = _trusted_scenario(key_state="rotated")
    ring = practice_key_ring(practice)

    with pytest.raises(CorrectionNotificationKeyNotValidError):
        resolve_trusted_correction_notification_key(notification, practice.id, ring, at=_NOW)


def test_expired_key_raises_correction_notification_key_not_valid_error() -> None:
    notification, practice, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(CorrectionNotificationKeyNotValidError):
        resolve_trusted_correction_notification_key(notification, practice.id, ring, at=_NOW)


def test_not_yet_valid_key_raises_correction_notification_key_not_valid_error() -> None:
    notification, practice, _ = _trusted_scenario(
        valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(CorrectionNotificationKeyNotValidError):
        resolve_trusted_correction_notification_key(notification, practice.id, ring, at=_NOW)


def test_signer_mismatch_raises_correction_notification_signer_mismatch_error() -> None:
    notification, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_practice_id = new_urn("practice")

    with pytest.raises(CorrectionNotificationSignerMismatchError) as excinfo:
        resolve_trusted_correction_notification_key(notification, other_practice_id, ring, at=_NOW)
    assert excinfo.value.claimed_signer_practice_id == notification.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == other_practice_id


def test_tampered_notification_raises_signature_verification_error() -> None:
    notification, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = notification.model_copy(update={"severity": "critical"})

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_correction_notification_key(tampered, practice.id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# Key-substitution attack: verification is against the RING's key, never a
# key the notification itself claims.
# ---------------------------------------------------------------------------


def test_key_substitution_attack_fails_closed_with_signature_verification_error() -> None:
    """An attacker without the trusted private key cannot forge acceptance
    by signing with their OWN key while claiming the victim's trusted kid:
    resolve_trusted_correction_notification_key decodes and verifies against
    the RING's descriptor for that kid, never anything the notification
    itself carries, so the attacker's signature simply does not verify under
    the real key.
    """
    _, trusted_public_key = generate_ed25519_keypair()
    attacker_private_key, _ = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(trusted_public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])

    notification = _notification(notifying_practice_id=practice_id, key_id=entry["kid"])
    forged = _sign(notification, attacker_private_key)
    ring = practice_key_ring(practice)

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_correction_notification_key(forged, practice_id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# docs/spec/04 section 8.4: valid at signing, revoked by the evaluation
# instant.
# ---------------------------------------------------------------------------


def test_key_valid_at_signing_but_revoked_by_instant_is_rejected() -> None:
    """A notification signed while the key was active must still be
    rejected if the practice has since revoked that key by the evaluation
    instant — trust anchoring beyond raw signature validity. The revoked
    descriptor stays resolvable in the ring (historical attributability,
    E5-T01).
    """
    notification, practice, _ = _trusted_scenario()
    kid = notification.signature.key_id

    revoked_practice = practice.model_copy(
        update={"keys": [practice.keys[0].model_copy(update={"state": "revoked"})]}
    )
    ring = practice_key_ring(revoked_practice)

    with pytest.raises(CorrectionNotificationKeyNotValidError):
        resolve_trusted_correction_notification_key(notification, practice.id, ring, at=_NOW)

    resolved_descriptor = ring.get(kid)
    assert resolved_descriptor is not None
    assert resolved_descriptor.state == "revoked"
