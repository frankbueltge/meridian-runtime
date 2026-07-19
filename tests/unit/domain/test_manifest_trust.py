"""Unit tests for mrr.domain.manifest_trust (task-packets/E5-T02.yaml).

Covers the packet's named acceptance tests at the domain layer: a Practice
with an active in-window key trust-anchors and resolves a manifest it
signed; the full fail-closed matrix (unknown kid, revoked, rotated, expired,
not-yet-valid, signer mismatch, key not declared in manifest, tampered
signature), each a DISTINCT typed error; a key valid at signing but revoked
by the time of receipt is rejected while its descriptor remains resolvable
(docs/spec/04_SECURITY_AND_POLICY.md section 8.4); and the Practice ->
KeyRing helper builds a ring whose kids/descriptors match the practice's own
keys.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.node_manifest import NodeManifest
from mrr.contracts.practice import Practice
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import (
    ManifestKeyNotDeclaredError,
    ManifestKeyNotValidError,
    ManifestSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring, resolve_trusted_manifest_key

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
        "description": "Fixture practice for manifest trust unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _manifest(
    *,
    signer_practice_id: str,
    key_id: str,
    public_keys: list[str],
    **overrides: Any,
) -> NodeManifest:
    data: dict[str, Any] = {
        "id": new_urn("node-manifest"),
        "api_version": "mrr/v1alpha1",
        "kind": "NodeManifest",
        "practice_id": signer_practice_id,
        "revision": 1,
        "created_at": _NOW,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "node_id": new_urn("node"),
        "capabilities": [
            {
                "name": "literature.retrieve",
                "version": "1.0.0",
                "input_schema": "urn:mrr:schema:literature-query:1",
                "output_schema": "urn:mrr:schema:evidence-crate:1",
                "max_autonomy": "A2",
                "approval": "automatic",
                "network_profile": "none",
            }
        ],
        "restrictions": [],
        "accepted_classifications": ["PUBLIC"],
        "data_residency": "DE",
        "transport_modes": ["online"],
        "valid_from": _NOW - timedelta(days=1),
        "valid_until": _NOW + timedelta(days=365),
        "public_keys": public_keys,
        "signature": {
            "signer_practice_id": signer_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return NodeManifest.model_validate(data)


def _sign(manifest: NodeManifest, private_key: Ed25519PrivateKey) -> NodeManifest:
    """Sign over the ADR-0004 ``exclude_none=True`` form — the same
    canonical body ``resolve_trusted_manifest_key`` verifies against.
    """
    signature_value = sign_object(
        private_key, json.loads(manifest.model_dump_json(exclude_none=True))
    )
    return manifest.model_copy(
        update={"signature": manifest.signature.model_copy(update={"value": signature_value})}
    )


def _trusted_scenario(
    *,
    key_state: str = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
) -> tuple[NodeManifest, Practice, Ed25519PrivateKey]:
    """A fully self-consistent scenario: a Practice with one key in
    ``key_state``, and a NodeManifest genuinely signed by that key,
    declaring it in ``public_keys`` and naming the practice as signer.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(public_key, valid_from=valid_from, valid_until=valid_until, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
    manifest = _manifest(
        signer_practice_id=practice_id,
        key_id=entry["kid"],
        public_keys=[entry["encoded_public_key"]],
    )
    return _sign(manifest, private_key), practice, private_key


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_happy_path_resolves_the_trusted_verifying_key() -> None:
    manifest, practice, private_key = _trusted_scenario()
    ring = practice_key_ring(practice)

    resolved = resolve_trusted_manifest_key(manifest, practice.id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == private_key.public_key().public_bytes_raw()


# ---------------------------------------------------------------------------
# Fail-closed matrix: each a DISTINCT typed error, no key ever returned.
# ---------------------------------------------------------------------------


def test_unknown_kid_raises_unknown_key_id_error() -> None:
    manifest, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = manifest.model_copy(
        update={"signature": manifest.signature.model_copy(update={"key_id": "kid:does-not-exist"})}
    )

    with pytest.raises(UnknownKeyIdError) as excinfo:
        resolve_trusted_manifest_key(tampered, practice.id, ring, at=_NOW)
    assert excinfo.value.kid == "kid:does-not-exist"


def test_revoked_key_raises_key_not_valid_error() -> None:
    manifest, practice, _ = _trusted_scenario(key_state="revoked")
    ring = practice_key_ring(practice)

    with pytest.raises(ManifestKeyNotValidError):
        resolve_trusted_manifest_key(manifest, practice.id, ring, at=_NOW)


def test_rotated_key_raises_key_not_valid_error() -> None:
    manifest, practice, _ = _trusted_scenario(key_state="rotated")
    ring = practice_key_ring(practice)

    with pytest.raises(ManifestKeyNotValidError):
        resolve_trusted_manifest_key(manifest, practice.id, ring, at=_NOW)


def test_expired_key_raises_key_not_valid_error() -> None:
    manifest, practice, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(ManifestKeyNotValidError):
        resolve_trusted_manifest_key(manifest, practice.id, ring, at=_NOW)


def test_not_yet_valid_key_raises_key_not_valid_error() -> None:
    manifest, practice, _ = _trusted_scenario(
        valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(ManifestKeyNotValidError):
        resolve_trusted_manifest_key(manifest, practice.id, ring, at=_NOW)


def test_signer_mismatch_raises_manifest_signer_mismatch_error() -> None:
    manifest, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_practice_id = new_urn("practice")

    with pytest.raises(ManifestSignerMismatchError) as excinfo:
        resolve_trusted_manifest_key(manifest, other_practice_id, ring, at=_NOW)
    assert excinfo.value.claimed_signer_practice_id == manifest.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == other_practice_id


def test_key_not_declared_in_manifest_raises_manifest_key_not_declared_error() -> None:
    manifest, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    # The manifest never lists the key it actually signed with.
    stripped = manifest.model_copy(update={"public_keys": ["a-different-key-entirely"]})

    with pytest.raises(ManifestKeyNotDeclaredError):
        resolve_trusted_manifest_key(stripped, practice.id, ring, at=_NOW)


def test_tampered_manifest_raises_signature_verification_error() -> None:
    manifest, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = manifest.model_copy(update={"restrictions": ["not_what_was_signed"]})

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_manifest_key(tampered, practice.id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# docs/spec/04 section 8.4: valid at signing, revoked by receipt.
# ---------------------------------------------------------------------------


def test_key_valid_at_signing_but_revoked_by_receipt_is_rejected() -> None:
    """A manifest signed while the key was active must still be rejected if
    the practice has since revoked that key by the time it is received —
    trust anchoring beyond raw signature validity. The revoked descriptor
    stays resolvable in the ring (historical attributability, E5-T01).
    """
    manifest, practice, _ = _trusted_scenario()
    kid = manifest.signature.key_id

    # Revoke the key in the practice's own ring, simulating time passing
    # between signing and receipt.
    revoked_practice = practice.model_copy(
        update={"keys": [practice.keys[0].model_copy(update={"state": "revoked"})]}
    )
    ring = practice_key_ring(revoked_practice)

    with pytest.raises(ManifestKeyNotValidError):
        resolve_trusted_manifest_key(manifest, practice.id, ring, at=_NOW)

    # Still resolvable — historically attributable, not deleted.
    resolved_descriptor = ring.get(kid)
    assert resolved_descriptor is not None
    assert resolved_descriptor.state == "revoked"


# ---------------------------------------------------------------------------
# Practice -> KeyRing helper.
# ---------------------------------------------------------------------------


def test_practice_key_ring_matches_the_practices_own_keys() -> None:
    _, public_key_a = generate_ed25519_keypair()
    _, public_key_b = generate_ed25519_keypair()
    entry_a = _key_entry(public_key_a)
    entry_b = _key_entry(
        public_key_b,
        valid_from=_NOW - timedelta(days=2),
        valid_until=_NOW - timedelta(days=1),
        state="rotated",
    )
    practice = _practice(practice_id=new_urn("practice"), keys=[entry_a, entry_b])

    ring = practice_key_ring(practice)

    assert set(ring.descriptors.keys()) == {entry_a["kid"], entry_b["kid"]}
    resolved_a = ring.get(entry_a["kid"])
    assert resolved_a is not None
    assert resolved_a.encoded_public_key == entry_a["encoded_public_key"]
    assert resolved_a.state == "active"
    resolved_b = ring.get(entry_b["kid"])
    assert resolved_b is not None
    assert resolved_b.state == "rotated"


def test_practice_key_ring_round_trips_through_is_valid_at() -> None:
    _, public_key = generate_ed25519_keypair()
    entry = _key_entry(public_key)
    practice = _practice(practice_id=new_urn("practice"), keys=[entry])

    ring = practice_key_ring(practice)

    assert ring.is_valid_at(_NOW, entry["kid"]) is True
    assert ring.is_valid_at(entry["valid_until"], entry["kid"]) is False
