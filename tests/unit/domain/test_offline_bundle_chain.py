"""End-to-end offline chain test (task-packets/E5-T06.yaml acceptance
test): a sealed ``EvidenceCrate`` wrapped in an E5-T03 ``NodeMessageEnvelope``,
carried inside an ``OfflineBundle``, survives import —
``validate_inbound_bundle`` verifies the batch, then the carried envelope
passes ``mrr.domain.envelope_validation.validate_inbound_envelope`` exactly
as an online-delivered one would, then the carried crate verifies via
``mrr.domain.crate_trust.resolve_trusted_crate_key`` — proving the offline
path REUSES every online verification rather than bypassing it (nothing
here re-implements per-envelope or per-crate validation).

Both ``validate_inbound_bundle``, ``validate_inbound_envelope``, and
``resolve_trusted_crate_key`` are pure, DB-free domain functions, so this
end-to-end demonstration runs at the unit tier (no PostgreSQL, no network,
no offline transport medium — a real air-gap transfer is out of this task's
scope). The crate itself is sealed by the REAL E2-T06
``EvidenceCrateSealer`` (against a DB-free fake unit-of-work), matching
this file's sibling ``tests/unit/domain/test_crate_trust_envelope_inbound.py``'s
own happy-path fixture, deliberately duplicated locally rather than
imported across test modules (this codebase's established per-test-tier
convention).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from mrr.contracts import RunManifest
from mrr.contracts.evidence_crate import EvidenceCrate
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.contracts.task_bundle import TaskBundle
from mrr.crypto.keys import derive_key_id, encode_public_key
from mrr.domain.crate_trust import resolve_trusted_crate_key
from mrr.domain.envelope_validation import validate_inbound_envelope
from mrr.domain.hashing_policy import sign_object
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import build_outbox_bundle, validate_inbound_bundle
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
_BUNDLE_CREATED_AT = _NOW - timedelta(minutes=1)
_BUNDLE_EXPIRES_AT = _NOW + timedelta(days=7)


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
        "description": "Fixture practice for the offline bundle chain e2e test.",
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
    ``EvidenceCrateSealer`` (against a DB-free fake unit-of-work), signed by
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
    node_signing_key: Ed25519PrivateKey,
) -> NodeMessageEnvelope:
    """Wrap ``crate`` as the payload of an already-signed
    ``NodeMessageEnvelope`` — the ``payload_content_hash`` is the crate's
    own ``content_hash``, per ``validate_inbound_envelope``'s condition 3
    (a consistency check against whatever hash the carried payload itself
    already declares).
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
    envelope = NodeMessageEnvelope.model_validate(data)
    signature_value = sign_object(
        node_signing_key, json.loads(envelope.model_dump_json(exclude_none=True))
    )
    return envelope.model_copy(
        update={"signature": envelope.signature.model_copy(update={"value": signature_value})}
    )


def _never_processed(identifier: str) -> bool:
    return False


def test_offline_chain_bundle_then_envelope_then_crate_reuses_every_online_verification() -> None:
    """The full offline path: a bundle carrying one envelope carrying one
    sealed result crate is accepted by ``validate_inbound_bundle`` (E5-T06),
    the carried envelope is then independently accepted by
    ``validate_inbound_envelope`` (E5-T03) exactly as it would be if
    delivered online, and the carried crate is then independently
    trust-anchored via ``resolve_trusted_crate_key`` (E5-T05) — proving the
    offline path reuses every online verification, not just batch
    integrity.
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
        node_signing_key=node_signing_key,
    )

    bundle = build_outbox_bundle(
        [envelope],
        bundle_id=new_urn("offline-bundle"),
        bundle_nonce="n" * 16,
        sender_node_id=node_id,
        sender_practice_id=node_practice_id,
        recipient_node_id=origin_node_id,
        created_at=_BUNDLE_CREATED_AT,
        expires_at=_BUNDLE_EXPIRES_AT,
        signing_key=node_signing_key,
        key_id=node_entry["kid"],
    )
    ring = practice_key_ring(node_practice)

    # Step 1 (E5-T06): the bundle's own batch integrity is verified —
    # recipient, validity window, replay, bundle signature, and every
    # entry's hash.
    verified_envelopes = validate_inbound_bundle(
        bundle,
        this_node_id=origin_node_id,
        trusted_sender_practice_id=node_practice_id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )
    assert len(verified_envelopes) == 1
    carried_envelope = verified_envelopes[0]

    # Step 2 (E5-T03): the carried envelope is independently validated,
    # exactly as an online-delivered one would be — validate_inbound_bundle
    # does NOT substitute for this.
    validate_inbound_envelope(
        carried_envelope,
        this_node_id=origin_node_id,
        trusted_sender_practice_id=node_practice_id,
        ring=ring,
        already_processed=_never_processed,
        at=_NOW,
    )

    # Step 3 (E5-T05): the carried crate's OWN signature is independently
    # trust-anchored — not merely inferred from the bundle or envelope
    # having been accepted.
    carried_crate = EvidenceCrate.model_validate(carried_envelope.payload)
    resolved = resolve_trusted_crate_key(carried_crate, node_practice_id, ring, at=_NOW)

    assert resolved.public_bytes_raw() == node_public_key.public_bytes_raw()
