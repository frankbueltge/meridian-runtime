"""Unit tests for mrr.domain.crate_trust (task-packets/E5-T05.yaml).

Covers the packet's named acceptance tests at the domain layer: an
executing node seals+signs an EvidenceCrate via the REAL E2-T06
``EvidenceCrateSealer`` with an active in-window key, and the origin
resolves the trusted key via the node practice's own ring
(``test_happy_path_resolves_the_trusted_verifying_key_for_a_really_sealed_crate``);
the full fail-closed matrix (signer mismatch, unknown kid, revoked, rotated,
expired, not-yet-valid, tampered crate), each a DISTINCT typed error; a key
valid at sealing but REVOKED by the evaluation instant is rejected while its
descriptor stays resolvable (docs/spec/04 section 8.4); and the
key-substitution attack (an attacker key claiming a trusted kid fails closed
with SignatureVerificationError, because verification is always against the
RING's key, never a key from the crate itself).

Deliberate local duplicate of tests/unit/domain/test_task_trust.py's own
fixture-building convention, adapted to EvidenceCrate (this codebase's
established convention for independent test tiers) — and, for the happy
path only, of tests/unit/services/node_runtime/test_evidence_crate.py's own
DB-free "fake unit-of-work + real EvidenceCrateSealer" fixture, so the
happy-path scenario is a crate the real production sealer actually produced,
not a hand-built stand-in (task-packets/E5-T05.yaml acceptance test's own
explicit "via the real EvidenceCrateSealer").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts import RunManifest
from mrr.contracts.evidence_crate import EvidenceCrate
from mrr.contracts.practice import Practice
from mrr.contracts.task_bundle import TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key, generate_ed25519_keypair
from mrr.domain.crate_trust import resolve_trusted_crate_key
from mrr.domain.exceptions import (
    CrateKeyNotValidError,
    CrateSignerMismatchError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage
from mrr.services.node_runtime.run_manifest import RunManifestRecorder

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_VALID_FROM = _NOW - timedelta(days=1)
_VALID_UNTIL = _NOW + timedelta(days=365)
_POLICY_VERSION = "policy-2026-07-01"

# ---------------------------------------------------------------------------
# Fixture builders — hand-built EvidenceCrate + Practice, for every scenario
# except the happy path (see the module docstring).
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
        "description": "Fixture practice for crate trust unit tests.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


def _crate(
    *,
    signer_practice_id: str,
    key_id: str,
    **overrides: Any,
) -> EvidenceCrate:
    now = _NOW
    data: dict[str, Any] = {
        "id": new_urn("evidence-crate"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceCrate",
        "practice_id": signer_practice_id,
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("executor"),
        "content_hash": "sha256:" + "a" * 64,
        "task_id": new_urn("task-bundle"),
        "run_id": new_urn("research-run"),
        "run_state": "completed",
        "artifacts": [],
        "source_records": [],
        "evidence_anchors": [],
        "proposed_claims": [],
        "failures": [],
        "known_unknowns": [],
        "environment": {
            "image_digest": "sha256:" + "c" * 64,
            "code_revision": "git:abc123",
            "input_hashes": [],
            "model_profiles": [],
        },
        "sealed": True,
        "signature": {
            "signer_practice_id": signer_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return EvidenceCrate.model_validate(data)


def _sign(crate: EvidenceCrate, private_key: Ed25519PrivateKey) -> EvidenceCrate:
    """Sign over the ADR-0004 ``exclude_none=True`` form — the same
    canonical body ``resolve_trusted_crate_key`` verifies against.
    """
    signature_value = sign_object(private_key, json.loads(crate.model_dump_json(exclude_none=True)))
    return crate.model_copy(
        update={"signature": crate.signature.model_copy(update={"value": signature_value})}
    )


def _trusted_scenario(
    *,
    key_state: str = "active",
    valid_from: datetime = _VALID_FROM,
    valid_until: datetime = _VALID_UNTIL,
) -> tuple[EvidenceCrate, Practice, Ed25519PrivateKey]:
    """A fully self-consistent scenario: a Practice with one key in
    ``key_state``, and an EvidenceCrate genuinely signed by that key, naming
    the practice as its signer.
    """
    private_key, public_key = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(public_key, valid_from=valid_from, valid_until=valid_until, state=key_state)
    practice = _practice(practice_id=practice_id, keys=[entry])
    crate = _crate(signer_practice_id=practice_id, key_id=entry["kid"])
    return _sign(crate, private_key), practice, private_key


# ---------------------------------------------------------------------------
# Real-sealer fixture (happy path only) — DB-free "fake unit-of-work" pattern
# duplicated locally from tests/unit/services/node_runtime/test_evidence_crate.py.
# ---------------------------------------------------------------------------


class _FakeUnitOfWork:
    def __init__(self) -> None:
        self.stored: list[StoredObject] = []
        self.events: list[DomainEvent] = []

    def __call__(
        self,
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        self.stored.append(obj)
        self.events.append(event)
        appended = AppendedEvent(
            event=event,
            sequence=len(self.events),
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=None,
        )
        return obj, appended


def _bundle_for_sealing() -> TaskBundle:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "origin_practice_id": new_urn("practice"),
        "target_node_id": new_urn("node"),
        "research_score_id": new_urn("research-score"),
        "research_score_revision": 1,
        "branch_id": new_urn("branch"),
        "capability": {"name": "reference.deterministic-transform", "version": "1.0.0"},
        "purpose": "Run the bounded, deterministic reference computation.",
        "instructions": {"operation": "percentage", "numerator": 42, "denominator": 100},
        "inputs": [],
        "data_access_mode": "none",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
            "code_revision": "git:abc123",
        },
        "resource_limits": {"cpu": 1.0, "memory_mb": 512, "disk_mb": 100, "timeout_seconds": 5},
        "network_policy": {"mode": "deny_all", "allowlist": []},
        "output_schema": "urn:mrr:schema:evidence-crate:1",
        "classification": "PUBLIC",
        "approval_requirement": "automatic",
        "expires_at": now + timedelta(days=1),
        "nonce": "n" * 16,
        "signature": {
            "signer_practice_id": new_urn("practice"),
            "key_id": "origin-key",
            "algorithm": "Ed25519",
            "signed_at": now,
            "value": "0" * 44,
        },
        "status": "RUNNING",
    }
    return TaskBundle.model_validate(data)


def seal_real_crate(
    *,
    node_signing_key: Ed25519PrivateKey,
    node_key_id: str,
    signer_practice_id: str,
) -> EvidenceCrate:
    """Build+seal a genuine ``EvidenceCrate`` via the real E2-T06
    ``EvidenceCrateSealer`` (against a DB-free fake unit-of-work, plus a
    genuinely-recorded ``RunManifest`` via the real E2-T05
    ``RunManifestRecorder``), signed by ``node_signing_key``/``node_key_id``
    under ``signer_practice_id``. Real cross-module reuse, not a hand-built
    dict that could drift from what the production sealer actually produces
    — this is what task-packets/E5-T05.yaml's happy-path acceptance test
    asks for.
    """
    bundle = _bundle_for_sealing()
    result = ExecutionResult(
        outcome="completed",
        output=b"deterministic reference output",
        output_hash="sha256:" + "e" * 64,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=0.42),
        detail=None,
    )
    now = datetime.now(UTC)
    manifest_stored = RunManifestRecorder(_FakeUnitOfWork()).record(
        result,
        bundle,
        practice_id=new_urn("practice"),
        executor_id=new_urn("executor"),
        executor_role="reference-task-executor",
        started_at=now,
        ended_at=now + timedelta(seconds=1),
        actor=new_urn("executor"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    run_manifest = RunManifest.model_validate(manifest_stored.body)

    sealer = EvidenceCrateSealer(_FakeUnitOfWork())
    stored = sealer.seal(
        run_manifest,
        result,
        bundle,
        node_signing_key=node_signing_key,
        node_key_id=node_key_id,
        signer_practice_id=signer_practice_id,
        actor=new_urn("executor"),
        policy_version=_POLICY_VERSION,
        correlation_id=new_urn("research-run"),
    )
    return EvidenceCrate.model_validate(stored.body)


# ---------------------------------------------------------------------------
# Happy path — via the REAL EvidenceCrateSealer.
# ---------------------------------------------------------------------------


def test_happy_path_resolves_the_trusted_verifying_key_for_a_really_sealed_crate() -> None:
    node_signing_key = Ed25519PrivateKey.generate()
    node_public_key = node_signing_key.public_key()
    node_practice_id = new_urn("practice")
    entry = _key_entry(node_public_key)
    node_practice = _practice(practice_id=node_practice_id, keys=[entry])

    crate = seal_real_crate(
        node_signing_key=node_signing_key,
        node_key_id=entry["kid"],
        signer_practice_id=node_practice_id,
    )
    ring = practice_key_ring(node_practice)

    resolved = resolve_trusted_crate_key(crate, node_practice_id, ring, at=datetime.now(UTC))

    assert resolved.public_bytes_raw() == node_public_key.public_bytes_raw()


# ---------------------------------------------------------------------------
# Fail-closed matrix: each a DISTINCT typed error, no key ever returned.
# ---------------------------------------------------------------------------


def test_unknown_kid_raises_unknown_key_id_error() -> None:
    crate, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = crate.model_copy(
        update={"signature": crate.signature.model_copy(update={"key_id": "kid:does-not-exist"})}
    )

    with pytest.raises(UnknownKeyIdError) as excinfo:
        resolve_trusted_crate_key(tampered, practice.id, ring, at=_NOW)
    assert excinfo.value.kid == "kid:does-not-exist"


def test_revoked_key_raises_crate_key_not_valid_error() -> None:
    crate, practice, _ = _trusted_scenario(key_state="revoked")
    ring = practice_key_ring(practice)

    with pytest.raises(CrateKeyNotValidError):
        resolve_trusted_crate_key(crate, practice.id, ring, at=_NOW)


def test_rotated_key_raises_crate_key_not_valid_error() -> None:
    crate, practice, _ = _trusted_scenario(key_state="rotated")
    ring = practice_key_ring(practice)

    with pytest.raises(CrateKeyNotValidError):
        resolve_trusted_crate_key(crate, practice.id, ring, at=_NOW)


def test_expired_key_raises_crate_key_not_valid_error() -> None:
    crate, practice, _ = _trusted_scenario(
        valid_from=_NOW - timedelta(days=10), valid_until=_NOW - timedelta(days=1)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(CrateKeyNotValidError):
        resolve_trusted_crate_key(crate, practice.id, ring, at=_NOW)


def test_not_yet_valid_key_raises_crate_key_not_valid_error() -> None:
    crate, practice, _ = _trusted_scenario(
        valid_from=_NOW + timedelta(days=1), valid_until=_NOW + timedelta(days=10)
    )
    ring = practice_key_ring(practice)

    with pytest.raises(CrateKeyNotValidError):
        resolve_trusted_crate_key(crate, practice.id, ring, at=_NOW)


def test_signer_mismatch_raises_crate_signer_mismatch_error() -> None:
    crate, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    other_practice_id = new_urn("practice")

    with pytest.raises(CrateSignerMismatchError) as excinfo:
        resolve_trusted_crate_key(crate, other_practice_id, ring, at=_NOW)
    assert excinfo.value.claimed_signer_practice_id == crate.signature.signer_practice_id
    assert excinfo.value.trusted_practice_id == other_practice_id


def test_tampered_crate_raises_signature_verification_error() -> None:
    crate, practice, _ = _trusted_scenario()
    ring = practice_key_ring(practice)
    tampered = crate.model_copy(update={"run_state": "failed"})

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_crate_key(tampered, practice.id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# Key-substitution attack: verification is against the RING's key, never a
# key the crate itself claims.
# ---------------------------------------------------------------------------


def test_key_substitution_attack_fails_closed_with_signature_verification_error() -> None:
    """An attacker without the trusted private key cannot forge acceptance
    by signing with their OWN key while claiming the victim's trusted kid:
    resolve_trusted_crate_key decodes and verifies against the RING's
    descriptor for that kid, never anything the crate itself carries, so
    the attacker's signature simply does not verify under the real key.
    """
    _, trusted_public_key = generate_ed25519_keypair()
    attacker_private_key, _ = generate_ed25519_keypair()
    practice_id = new_urn("practice")
    entry = _key_entry(trusted_public_key)
    practice = _practice(practice_id=practice_id, keys=[entry])

    # The attacker signs a crate claiming the trusted kid, but with their
    # OWN (different) private key.
    crate = _crate(signer_practice_id=practice_id, key_id=entry["kid"])
    forged = _sign(crate, attacker_private_key)
    ring = practice_key_ring(practice)

    with pytest.raises(SignatureVerificationError):
        resolve_trusted_crate_key(forged, practice_id, ring, at=_NOW)


# ---------------------------------------------------------------------------
# docs/spec/04 section 8.4: valid at sealing, revoked by the evaluation
# instant.
# ---------------------------------------------------------------------------


def test_key_valid_at_sealing_but_revoked_by_instant_is_rejected() -> None:
    """A crate signed while the key was active must still be rejected if
    the practice has since revoked that key by the evaluation instant —
    trust anchoring beyond raw signature validity. The revoked descriptor
    stays resolvable in the ring (historical attributability, E5-T01).
    """
    crate, practice, _ = _trusted_scenario()
    kid = crate.signature.key_id

    # Revoke the key in the practice's own ring, simulating time passing
    # between sealing and evaluation.
    revoked_practice = practice.model_copy(
        update={"keys": [practice.keys[0].model_copy(update={"state": "revoked"})]}
    )
    ring = practice_key_ring(revoked_practice)

    with pytest.raises(CrateKeyNotValidError):
        resolve_trusted_crate_key(crate, practice.id, ring, at=_NOW)

    # Still resolvable — historically attributable, not deleted.
    resolved_descriptor = ring.get(kid)
    assert resolved_descriptor is not None
    assert resolved_descriptor.state == "revoked"
