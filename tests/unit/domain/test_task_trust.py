"""Unit tests for mrr.domain.task_trust (task-packets/E5-T04.yaml).

Covers the packet's named acceptance tests at the domain layer: a Practice
with an active in-window key trust-anchors and resolves a TaskBundle it
signed; the full fail-closed matrix (unknown kid, revoked, rotated, expired,
not-yet-valid, signer mismatch, tampered signature), each a DISTINCT typed
error; a key valid at signing but revoked by the evaluation instant is
rejected while its descriptor remains resolvable (docs/spec/04
section 8.4); the key-substitution attack (an attacker key claiming a
trusted kid fails closed with SignatureVerificationError, because
verification is always against the RING's key, never a key from the
bundle); and the resolver's symmetry — the identical function authenticates
an origin-signed task (trust the origin) and a node-signed modification
(trust the node).

Deliberate local duplicate of tests/unit/domain/test_manifest_trust.py's own
fixture-building convention, adapted to TaskBundle (this codebase's
established convention for independent test tiers).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts.practice import Practice
from mrr.contracts.task_bundle import TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.exceptions import (
    TaskKeyNotValidError,
    TaskSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.task_trust import practice_key_ring, resolve_trusted_task_key

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
        "description": "Fixture practice for task trust unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _bundle(
    *,
    signer_practice_id: str,
    key_id: str,
    origin_practice_id: str | None = None,
    **overrides: Any,
) -> TaskBundle:
    now = _NOW
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": origin_practice_id or signer_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": origin_practice_id or signer_practice_id,
        "target_node_id": new_urn("node"),
        "research_score_id": new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": "statistics.recompute", "version": "1.0.0"},
        "purpose": "Recompute the summary statistics for the confirmatory branch.",
        "instructions": {"note": "run the standard pipeline"},
        "inputs": [],
        "data_access_mode": "read_local",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
        },
        "resource_limits": {"cpu": 1.0, "memory_mb": 512, "disk_mb": 100, "timeout_seconds": 60},
        "network_policy": {"mode": "deny_all", "allowlist": []},
        "output_schema": "urn:mrr:schema:evidence-crate:1",
        "classification": "PUBLIC",
        "approval_requirement": "automatic",
        "expires_at": now + timedelta(days=1),
        "nonce": "n" * 16,
        "signature": {
            "signer_practice_id": signer_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "CREATED",
    }
    data.update(overrides)
    return TaskBundle.model_validate(data)


def _sign(bundle: TaskBundle, private_key: Ed25519PrivateKey) -> TaskBundle:
    """Sign over the ADR-0004 ``exclude_none=True`` form — the same
    canonical body ``resolve_trusted_task_key`` verifies against.
    """
    signature_value = sign_object(
        private_key, json.loads(bundle.model_dump_json(exclude_none=True))
    )
    return bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"value": signature_value})}
    )


def _trusted_scenario(
    *,
    key_state: str = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
) -> tuple[TaskBundle, Practice, Ed25519PrivateKey]:
    """A fully self-consistent scenario: a Practice with one key in
    ``key_state``, and a TaskBundle genuinely signed by that key, naming the
    practice as signer.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(public_key, valid_from=valid_from, valid_until=valid_until, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
    bundle = _bundle(signer_practice_id=practice_id, key_id=entry["kid"])
    return _sign(bundle, private_key), practice, private_key


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_happy_path_resolves_the_trusted_verifying_key() -> None:
    bundle, practice, private_key = _trusted_scenario()
    ring = practice_key_ring(practice)

    resolved = resolve_trusted_task_key(bundle, practice.id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == private_key.public_key().public_bytes_raw()


def test_resolver_is_symmetric_origin_or_node_practice_both_work() -> None:
    """The identical function authenticates either negotiation direction —
    trust anchored to whichever practice+ring the caller supplies. Here a
    NODE practice signs a "modification" bundle and the resolver, called
    with the NODE's own id+ring (the origin's perspective per MRR-FR-023),
    resolves it exactly the same way it would an origin-signed task.
    """
    node_private_key, node_public_key = generate_ed25519_keypair()
    node_practice_id = new_urn("practice")
    entry = _key_entry(node_public_key)
    node_practice = _practice(practice_id=node_practice_id, keys=[entry])
    modification = _bundle(
        signer_practice_id=node_practice_id,
        key_id=entry["kid"],
        origin_practice_id=new_urn("practice"),  # a DIFFERENT practice created the bundle
        status="OFFERED",
        revision=2,
    )
    signed_modification = _sign(modification, node_private_key)
    ring = practice_key_ring(node_practice)

    resolved = resolve_trusted_task_key(signed_modification, node_practice_id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == node_public_key.public_bytes_raw()


# ---------------------------------------------------------------------------
# Fail-closed matrix: each a DISTINCT typed error, no key ever returned.
# ---------------------------------------------------------------------------


def test_unknown_kid_raises_unknown_key_id_error() -> None:
    bundle, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = bundle.model_copy(
        update={"signature": bundle.signature.model_copy(update={"key_id": "kid:does-not-exist"})}
    )

    with pytest.raises(UnknownKeyIdError) as excinfo:
        resolve_trusted_task_key(tampered, practice.id, ring, at=_NOW)
    assert excinfo.value.kid == "kid:does-not-exist"


def test_revoked_key_raises_task_key_not_valid_error() -> None:
    bundle, practice, _ = _trusted_scenario(key_state="revoked")
    ring = practice_key_ring(practice)

    with pytest.raises(TaskKeyNotValidError):
        resolve_trusted_task_key(bundle, practice.id, ring, at=_NOW)


def test_rotated_key_raises_task_key_not_valid_error() -> None:
    bundle, practice, _ = _trusted_scenario(key_state="rotated")
    ring = practice_key_ring(practice)

    with pytest.raises(TaskKeyNotValidError):
        resolve_trusted_task_key(bundle, practice.id, ring, at=_NOW)


def test_expired_key_raises_task_key_not_valid_error() -> None:
    bundle, practice, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(TaskKeyNotValidError):
        resolve_trusted_task_key(bundle, practice.id, ring, at=_NOW)


def test_not_yet_valid_key_raises_task_key_not_valid_error() -> None:
    bundle, practice, _ = _trusted_scenario(
        valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(TaskKeyNotValidError):
        resolve_trusted_task_key(bundle, practice.id, ring, at=_NOW)


def test_signer_mismatch_raises_task_signer_mismatch_error() -> None:
    bundle, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_practice_id = new_urn("practice")

    with pytest.raises(TaskSignerMismatchError) as excinfo:
        resolve_trusted_task_key(bundle, other_practice_id, ring, at=_NOW)
    assert excinfo.value.claimed_signer_practice_id == bundle.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == other_practice_id


def test_tampered_bundle_raises_signature_verification_error() -> None:
    bundle, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = bundle.model_copy(update={"purpose": "not what was signed"})

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_task_key(tampered, practice.id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# Key-substitution attack: verification is against the RING's key, never a
# key the bundle itself claims — a bundle carries no such field to spoof
# directly (unlike NodeManifest.public_keys), but an attacker key signing
# under a trusted kid must still fail, because decode_public_key always
# decodes the RESOLVED descriptor, never anything derived from the bundle.
# ---------------------------------------------------------------------------


def test_key_substitution_attack_fails_closed_with_signature_verification_error() -> None:
    """An attacker without the trusted private key cannot forge acceptance
    by signing with their OWN key while claiming the victim's trusted kid:
    resolve_trusted_task_key decodes and verifies against the RING's
    descriptor for that kid, never anything the bundle itself carries, so
    the attacker's signature simply does not verify under the real key.
    """
    _, trusted_public_key = generate_ed25519_keypair()
    attacker_private_key, _ = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(trusted_public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])

    # The attacker signs a bundle claiming the trusted kid, but with their
    # OWN (different) private key.
    bundle = _bundle(signer_practice_id=practice_id, key_id=entry["kid"])
    forged = _sign(bundle, attacker_private_key)
    ring = practice_key_ring(practice)

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_task_key(forged, practice_id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# docs/spec/04 section 8.4: valid at signing, revoked by the evaluation
# instant.
# ---------------------------------------------------------------------------


def test_key_valid_at_signing_but_revoked_by_instant_is_rejected() -> None:
    """A bundle signed while the key was active must still be rejected if
    the practice has since revoked that key by the evaluation instant —
    trust anchoring beyond raw signature validity. The revoked descriptor
    stays resolvable in the ring (historical attributability, E5-T01).
    """
    bundle, practice, _ = _trusted_scenario()
    kid = bundle.signature.key_id

    # Revoke the key in the practice's own ring, simulating time passing
    # between signing and evaluation.
    revoked_practice = practice.model_copy(
        update={"keys": [practice.keys[0].model_copy(update={"state": "revoked"})]}
    )
    ring = practice_key_ring(revoked_practice)

    with pytest.raises(TaskKeyNotValidError):
        resolve_trusted_task_key(bundle, practice.id, ring, at=_NOW)

    # Still resolvable — historically attributable, not deleted.
    resolved_descriptor = ring.get(kid)
    assert resolved_descriptor is not None
    assert resolved_descriptor.state == "revoked"


# ---------------------------------------------------------------------------
# practice_key_ring is reused unchanged from mrr.domain.manifest_trust — a
# single behavioral smoke test that the re-export actually works, not a
# full duplicate of that module's own dedicated tests.
# ---------------------------------------------------------------------------


def test_practice_key_ring_reexport_matches_the_practices_own_keys() -> None:
    _, public_key = generate_ed25519_keypair()
    entry = _key_entry(public_key)
    practice = _practice(practice_id=new_urn("practice"), keys=[entry])

    ring = practice_key_ring(practice)

    assert set(ring.descriptors.keys()) == {entry["kid"]}
    assert ring.is_valid_at(_NOW, entry["kid"]) is True
