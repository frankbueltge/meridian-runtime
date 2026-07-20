"""End-to-end inbound test (task-packets/E5-T05.yaml acceptance test): a
sealed ``EvidenceCrate`` wrapped in an E5-T03 ``NodeMessageEnvelope`` is
accepted by ``mrr.domain.envelope_validation.validate_inbound_envelope`` AND
its carried crate then verifies via
``mrr.domain.crate_trust.resolve_trusted_crate_key`` — proving that the
result's OWN signature is checked, not merely
``payload_content_hash`` (the exact E5-T03 module-docstring note this task
closes for results: "an envelope's payload_content_hash is a consistency
check only; a carried EvidenceCrate's OWN signature must be verified by the
result flow").

Both ``validate_inbound_envelope`` and ``resolve_trusted_crate_key`` are
pure, DB-free domain functions, so this end-to-end demonstration runs at the
unit tier (no PostgreSQL, no network, no mTLS — a real transport is E5-T03's
own completed scope, not rebuilt here). The crate itself is sealed by the
REAL E2-T06 ``EvidenceCrateSealer`` (against a DB-free fake unit-of-work),
matching this file's sibling ``tests/unit/domain/test_crate_trust.py``'s own
happy-path fixture, deliberately duplicated locally rather than imported
across test modules (this codebase's established per-test-tier convention).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts import RunManifest
from mrr.contracts.evidence_crate import EvidenceCrate
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.contracts.task_bundle import TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.keys import derive_key_id, encode_public_key
from mrr.domain.crate_trust import resolve_trusted_crate_key
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.exceptions import EnvelopePayloadContentHashMismatchError
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
        "description": "Fixture practice for the envelope->crate inbound e2e test.",
        "keys": keys,
        "governance_contacts": ["mailto:governance@fixture.invalid"],
        "supported_policy_versions": ["policy-2026-07-01"],
        "disclosure": {"max_disclosure": "PUBLIC", "trust_statement": "fixture"},
    }
    return Practice.model_validate(data)


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


def _seal_real_crate(
    *, node_signing_key: Ed25519PrivateKey, node_key_id: str, signer_practice_id: str
) -> EvidenceCrate:
    """Build+seal a genuine ``EvidenceCrate`` via the real E2-T06
    ``EvidenceCrateSealer`` (DB-free fake unit-of-work), signed by
    ``node_signing_key``/``node_key_id`` under ``signer_practice_id``.
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


def _envelope_carrying(
    crate: EvidenceCrate,
    *,
    sender_node_id: str,
    sender_practice_id: str,
    recipient_node_id: str,
    key_id: str,
) -> NodeMessageEnvelope:
    """Wrap ``crate`` as the payload of a ``NodeMessageEnvelope`` — the
    ``payload_content_hash`` is the crate's own ``content_hash``, per
    ``validate_inbound_envelope``'s condition 3 (a consistency check
    against whatever hash the carried payload itself already declares).
    """
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": sender_node_id,
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": _NOW - timedelta(minutes=1),
        "expires_at": _NOW + timedelta(minutes=5),
        "payload_kind": "EvidenceCrate",
        "payload_content_hash": crate.content_hash,
        "payload": json.loads(crate.model_dump_json(exclude_none=True)),
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": key_id,
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    return NodeMessageEnvelope.model_validate(data)


def _sign_envelope(
    envelope: NodeMessageEnvelope, private_key: Ed25519PrivateKey
) -> NodeMessageEnvelope:
    signature_value = sign_object(
        private_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(message_id: str) -> bool:
    return False


def test_sealed_crate_carried_in_an_envelope_is_accepted_then_its_own_signature_verifies() -> None:
    """The full inbound path: the executing node's transport envelope is
    accepted by ``validate_inbound_envelope`` (E5-T03), and the crate it
    carries is then independently trust-anchored via
    ``resolve_trusted_crate_key`` (E5-T05) — the result's own signature is
    what is actually verified, not merely the envelope-level
    ``payload_content_hash`` consistency check.
    """
    node_signing_key = Ed25519PrivateKey.generate()
    node_public_key = node_signing_key.public_key()
    node_practice_id = new_urn("practice")
    node_entry = _key_entry(node_public_key)
    node_practice = _practice(practice_id=node_practice_id, keys=[node_entry])
    node_id = new_urn("node")
    origin_node_id = new_urn("node")

    crate = _seal_real_crate(
        node_signing_key=node_signing_key,
        node_key_id=node_entry["kid"],
        signer_practice_id=node_practice_id,
    )

    envelope = _envelope_carrying(
        crate,
        sender_node_id=node_id,
        sender_practice_id=node_practice_id,
        recipient_node_id=origin_node_id,
        key_id=node_entry["kid"],
    )
    signed_envelope = _sign_envelope(envelope, node_signing_key)
    ring = practice_key_ring(node_practice)

    # Step 1 (E5-T03): the transport envelope itself is accepted.
    validate_inbound_envelope(
        signed_envelope,
        this_node_id=origin_node_id,
        trusted_sender_practice_id=node_practice_id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )

    # Step 2 (E5-T05): the carried crate's OWN signature is independently
    # trust-anchored — not merely inferred from the envelope having been
    # accepted.
    carried_crate = EvidenceCrate.model_validate(signed_envelope.payload)
    resolved = resolve_trusted_crate_key(carried_crate, node_practice_id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == node_public_key.public_bytes_raw()


def test_envelope_accepting_does_not_substitute_for_the_crates_own_signature_check() -> None:
    """A crate tampered AFTER sealing but before being wrapped in the
    envelope, with the envelope's own ``payload_content_hash`` updated to
    match the tampered payload's forged ``content_hash``, still passes
    ``validate_inbound_envelope`` (it only checks internal envelope/payload
    hash CONSISTENCY, not the crate's cryptographic signature) — proving
    that skipping ``resolve_trusted_crate_key`` and trusting
    ``payload_content_hash`` alone would be unsafe. The forged crate's own
    signature then fails to verify when actually checked.
    """
    node_signing_key = Ed25519PrivateKey.generate()
    node_practice_id = new_urn("practice")
    node_entry = _key_entry(node_signing_key.public_key())
    node_practice = _practice(practice_id=node_practice_id, keys=[node_entry])
    node_id = new_urn("node")
    origin_node_id = new_urn("node")

    crate = _seal_real_crate(
        node_signing_key=node_signing_key,
        node_key_id=node_entry["kid"],
        signer_practice_id=node_practice_id,
    )

    # Tamper the crate's run_state after sealing (the signature no longer
    # covers this mutated content) and forge a matching content_hash so the
    # envelope-level consistency check alone would not catch it.
    tampered_body = json.loads(crate.model_dump_json(exclude_none=True))
    tampered_body["run_state"] = "failed"
    tampered_body["content_hash"] = "sha256:" + "9" * 64
    tampered_crate = EvidenceCrate.model_validate(tampered_body)

    envelope = _envelope_carrying(
        tampered_crate,
        sender_node_id=node_id,
        sender_practice_id=node_practice_id,
        recipient_node_id=origin_node_id,
        key_id=node_entry["kid"],
    )
    signed_envelope = _sign_envelope(envelope, node_signing_key)
    ring = practice_key_ring(node_practice)

    # The envelope itself still validates: payload_content_hash agrees with
    # the (forged) payload's own content_hash, and the transport signature
    # covers the envelope's own bytes, which were signed AFTER tampering.
    validate_inbound_envelope(
        signed_envelope,
        this_node_id=origin_node_id,
        trusted_sender_practice_id=node_practice_id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )

    # But the carried crate's OWN signature — computed at sealing time, over
    # the ORIGINAL content — no longer verifies over the tampered body.
    carried_crate = EvidenceCrate.model_validate(signed_envelope.payload)
    with pytest.raises(SignatureVerificationError):
        resolve_trusted_crate_key(carried_crate, node_practice_id, ring, at=_NOW)


def test_payload_content_hash_mismatch_is_rejected_at_the_envelope_layer() -> None:
    """A tampered ``payload_content_hash`` that disagrees with the carried
    payload's own ``content_hash`` fails at the envelope layer
    (``EnvelopePayloadContentHashMismatchError``), before
    ``resolve_trusted_crate_key`` is ever reached — the two checks are
    independent layers, not a single combined one.
    """
    node_signing_key = Ed25519PrivateKey.generate()
    node_practice_id = new_urn("practice")
    node_entry = _key_entry(node_signing_key.public_key())
    node_practice = _practice(practice_id=node_practice_id, keys=[node_entry])
    node_id = new_urn("node")
    origin_node_id = new_urn("node")

    crate = _seal_real_crate(
        node_signing_key=node_signing_key,
        node_key_id=node_entry["kid"],
        signer_practice_id=node_practice_id,
    )
    envelope = _envelope_carrying(
        crate,
        sender_node_id=node_id,
        sender_practice_id=node_practice_id,
        recipient_node_id=origin_node_id,
        key_id=node_entry["kid"],
    )
    mismatched = envelope.model_copy(update={"payload_content_hash": "sha256:" + "1" * 64})
    signed_envelope = _sign_envelope(mismatched, node_signing_key)
    ring = practice_key_ring(node_practice)

    with pytest.raises(EnvelopePayloadContentHashMismatchError):
        validate_inbound_envelope(
            signed_envelope,
            this_node_id=origin_node_id,
            trusted_sender_practice_id=node_practice_id,
            ring=ring,
            already_processed=_never_processed,
            at=_NOW,
        )
