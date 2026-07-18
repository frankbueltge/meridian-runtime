"""Unit tests for ``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer``
(task-packets/E2-T06.yaml) — entirely DB-free, no PostgreSQL, no
``sqlalchemy.Engine``. Same "lightweight fake unit-of-work" pattern
``tests/unit/services/node_runtime/test_run_manifest.py`` uses, extended one
step further: these tests build a genuinely-recorded ``RunManifest`` via the
real E2-T05 ``RunManifestRecorder`` (also against a DB-free fake) rather than
hand-building a ``RunManifest`` dict, exercising the real cross-module reuse
this task's packet requires.

Acceptance-test mapping (task-packets/E2-T06.yaml):

- "a completed run seals into a schema-valid, signed crate that round-trips
  through the EvidenceCrate contract model" ->
  ``test_completed_result_seals_into_schema_valid_signed_crate_that_round_trips``,
  ``test_sealed_body_is_schema_and_pydantic_valid``.
- "a materially failed run also seals into a signed crate preserving its
  failures and known unknowns (MRR-FR-050 / MRR-FR-054)" ->
  ``test_failed_result_preserves_failures_and_known_unknowns``,
  ``test_every_terminal_outcome_seals_into_a_sealed_crate``.
- CRITICAL adversarial: "the sealed crate's node signature verifies;
  tampering any sealed field fails verification" ->
  ``test_sealed_crate_signature_verifies``,
  ``test_tampering_any_sealed_field_fails_signature_verification``,
  ``test_tampering_sealed_to_false_is_rejected_before_verification_even_runs``.
- "no mutate/unseal method exists on the sealer (MRR-FR-056)" ->
  ``test_sealer_exposes_no_mutate_method``.
- "an artifact reference's content hash recomputes to the stored value" ->
  ``test_artifact_refs_carry_content_hashes_that_recompute_from_the_object_store``.
- event provenance (MRR-NFR-001, "sealing records exactly one domain event
  ... atomically with the persisted revision" — DB-free half of this
  invariant; the integration tier covers real atomicity) ->
  ``test_event_provenance_is_complete_and_causation_is_root``.
- fail-closed cross-checks (this task's own addition, matching
  ``RunManifestRecorder``'s own precedent; flagged in the PR) ->
  ``test_mismatched_execution_result_task_id_raises``,
  ``test_mismatched_run_manifest_task_id_raises``,
  ``test_run_manifest_not_sealed_raises``,
  ``test_run_manifest_run_state_mismatch_raises``,
  ``test_missing_code_commit_raises``.
- identity conventions -> ``test_crate_id_uses_evidence_crate_entity_segment``.
- "empty is faithful, not a stub" (E3 scope) ->
  ``test_no_artifacts_supplied_yields_empty_list``,
  ``test_completed_result_seals_into_schema_valid_signed_crate_that_round_trips``
  (asserts ``source_records``/``evidence_anchors``/``proposed_claims`` are
  ``[]``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.contracts import ArtifactRef, EvidenceCrate, FailureEntry, RunManifest, TaskBundle
from mrr.crypto.exceptions import SignatureVerificationError
from mrr.crypto.hashing import content_hash as compute_raw_content_hash
from mrr.domain.hashing_policy import compute_content_hash, verify_object_signature
from mrr.domain.identity import new_urn, parse_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage, TerminalOutcome
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_POLICY_VERSION = "policy-2026-07-01"

# ---------------------------------------------------------------------------
# Fake unit-of-work — identical shape to test_run_manifest.py's own (both
# EvidenceCrateSealer and RunManifestRecorder only ever write a brand-new
# object at revision 1; deliberate local duplicate, not a shared import, per
# this codebase's established per-test-module convention).
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
        assert expected_current_revision is None, (
            "EvidenceCrateSealer always writes a brand-new object at revision 1"
        )
        self.stored.append(obj)
        self.events.append(event)
        appended = AppendedEvent(
            event=event,
            sequence=len(self.events),
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=None,
        )
        return obj, appended


def _sealer() -> tuple[EvidenceCrateSealer, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return EvidenceCrateSealer(uow), uow


# ---------------------------------------------------------------------------
# TaskBundle / ExecutionResult fixture factories — same shape and convention
# as tests/unit/services/node_runtime/test_run_manifest.py's own.
# ---------------------------------------------------------------------------


def _bundle(
    *,
    revision: int = 1,
    declared_inputs: list[ArtifactRef] | None = None,
    **overrides: Any,
) -> TaskBundle:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("task-bundle"),
        "api_version": "mrr/v1alpha1",
        "kind": "TaskBundle",
        "practice_id": new_urn("practice"),
        "revision": revision,
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
        "inputs": [ref.model_dump(mode="json") for ref in (declared_inputs or [])],
        "data_access_mode": "none",
        "execution": {
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
            "code_revision": "git:abc123",
        },
        "resource_limits": {
            "cpu": 1.0,
            "memory_mb": 512,
            "disk_mb": 100,
            "timeout_seconds": 5,
        },
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
    data.update(overrides)
    return TaskBundle.model_validate(data)


def _artifact_ref(content_hash: str = "sha256:" + "d" * 64) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=new_urn("artifact"), content_hash=content_hash, classification="PUBLIC"
    )


def _execution_result(
    bundle: TaskBundle,
    outcome: TerminalOutcome,
    *,
    output: bytes | None = None,
    output_hash: str | None = None,
    wall_time_seconds: float = 0.42,
) -> ExecutionResult:
    return ExecutionResult(
        outcome=outcome,
        output=output,
        output_hash=output_hash,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=wall_time_seconds),
        detail=None if outcome == "completed" else "reference executor detail",
    )


def _run_manifest_for(bundle: TaskBundle, result: ExecutionResult) -> RunManifest:
    """Build a genuinely-recorded, sealed ``RunManifest`` for ``bundle``/
    ``result`` via the real E2-T05 ``RunManifestRecorder`` (its own DB-free
    fake unit of work) — real cross-module reuse, not a hand-built dict that
    could drift from what the recorder actually produces.
    """
    recorder = RunManifestRecorder(_FakeUnitOfWork())
    now = datetime.now(UTC)
    stored = recorder.record(
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
    return RunManifest.model_validate(stored.body)


def _seal_kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "node_signing_key": Ed25519PrivateKey.generate(),
        "node_key_id": "node-key-2026-01",
        "signer_practice_id": new_urn("practice"),
        "actor": new_urn("executor"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_completed_result_seals_into_schema_valid_signed_crate_that_round_trips() -> None:
    sealer, uow = _sealer()
    artifact_ref = _artifact_ref()
    input_ref = _artifact_ref("sha256:" + "1" * 64)
    bundle = _bundle(declared_inputs=[input_ref])
    result = _execution_result(
        bundle, "completed", output=b"output bytes", output_hash="sha256:" + "e" * 64
    )
    run_manifest = _run_manifest_for(bundle, result)

    stored = sealer.seal(
        run_manifest, result, bundle, artifact_refs=[artifact_ref], **_seal_kwargs()
    )

    crate = EvidenceCrate.model_validate(stored.body)  # Pydantic round trip
    assert crate.task_id == bundle.id
    assert crate.run_id == run_manifest.id
    assert crate.run_state == "completed"
    assert crate.sealed is True
    assert crate.artifacts == [artifact_ref]
    assert crate.source_records == []
    assert crate.evidence_anchors == []
    assert crate.proposed_claims == []
    assert crate.failures == []
    assert crate.known_unknowns == []
    assert crate.environment.image_digest == run_manifest.image_digest
    assert crate.environment.code_revision == run_manifest.code_commit
    assert crate.environment.input_hashes == run_manifest.input_hashes
    assert crate.environment.model_profiles == []

    assert len(uow.stored) == 1
    assert stored.revision == 1


def test_failed_result_preserves_failures_and_known_unknowns() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "failed")
    run_manifest = _run_manifest_for(bundle, result)
    failures = [
        FailureEntry(
            code="E_REFERENCE_TRANSFORM",
            category="execution_error",
            message="reference transform raised during execution",
        )
    ]
    known_unknowns = ["whether a retry with a wider timeout would have succeeded"]

    stored = sealer.seal(
        run_manifest,
        result,
        bundle,
        **_seal_kwargs(failures=failures, known_unknowns=known_unknowns),
    )

    assert stored.body["run_state"] == "failed"
    assert stored.body["sealed"] is True
    assert stored.body["failures"] == [f.model_dump(mode="json") for f in failures]
    assert stored.body["known_unknowns"] == known_unknowns
    assert stored.body["artifacts"] == []


@pytest.mark.parametrize(
    "outcome",
    ["completed", "failed", "cancelled", "timed_out", "policy_denied", "partial"],
)
def test_every_terminal_outcome_seals_into_a_sealed_crate(outcome: TerminalOutcome) -> None:
    """MRR-FR-050: every terminal outcome — not only ``completed`` — produces
    a sealed, signed crate. Sealing is never skipped for a non-``completed``
    outcome.
    """
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, outcome)
    run_manifest = _run_manifest_for(bundle, result)

    stored = sealer.seal(run_manifest, result, bundle, **_seal_kwargs())

    assert stored.body["run_state"] == outcome
    assert stored.body["sealed"] is True
    assert stored.body["signature"]["value"]


def test_no_artifacts_supplied_yields_empty_list() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result)

    stored = sealer.seal(run_manifest, result, bundle, **_seal_kwargs())

    assert stored.body["artifacts"] == []


def test_sealer_exposes_no_mutate_method() -> None:
    """A sealed evidence crate is immutable (MRR-FR-056) — this class's only
    public callable is ``seal``; there is no update/unseal/correct method
    anywhere on it.
    """
    public_methods = {
        name
        for name in dir(EvidenceCrateSealer)
        if not name.startswith("_") and callable(getattr(EvidenceCrateSealer, name))
    }
    assert public_methods == {"seal"}


def test_event_provenance_is_complete_and_causation_is_root() -> None:
    sealer, uow = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result)
    actor = new_urn("executor")
    correlation_id = new_urn("research-run")

    stored = sealer.seal(
        run_manifest, result, bundle, **_seal_kwargs(actor=actor, correlation_id=correlation_id)
    )

    assert len(uow.events) == 1
    event = uow.events[0]
    assert event.event_type == "evidence_crate.sealed"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None  # a brand-new crate identity has no prior event
    assert event.object_id == stored.id
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["task_id"] == bundle.id
    assert event.payload["run_id"] == run_manifest.id
    assert event.payload["run_state"] == "completed"
    assert event.payload["sealed"] is True


def test_crate_id_uses_evidence_crate_entity_segment() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result)

    stored = sealer.seal(run_manifest, result, bundle, **_seal_kwargs())

    entity, _ulid = parse_urn(stored.id)
    assert entity == "evidence-crate"


def test_sealed_body_is_schema_and_pydantic_valid() -> None:
    """Every crate this sealer builds — not only the static example —
    validates against both the JSON Schema and the Pydantic model (E1-T03's
    invariant, extended to this sealing service)."""
    sealer, _ = _sealer()
    artifact_ref = _artifact_ref()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result)

    stored = sealer.seal(
        run_manifest, result, bundle, artifact_refs=[artifact_ref], **_seal_kwargs()
    )

    EvidenceCrate.model_validate(stored.body)

    schema = json.loads((SCHEMAS_DIR / "evidence-crate.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(stored.body)
    Draft202012Validator.check_schema(schema)


# ---------------------------------------------------------------------------
# CRITICAL adversarial: the node signature verifies against the sealed
# content, and tampering any sealed field fails verification.
# ---------------------------------------------------------------------------


def test_sealed_crate_signature_verifies() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed", output=b"x", output_hash="sha256:" + "f" * 64)
    run_manifest = _run_manifest_for(bundle, result)
    node_signing_key = Ed25519PrivateKey.generate()

    stored = sealer.seal(
        run_manifest, result, bundle, **_seal_kwargs(node_signing_key=node_signing_key)
    )

    crate = EvidenceCrate.model_validate(stored.body)
    verify_object_signature(
        node_signing_key.public_key(),
        crate.model_dump(mode="json"),
        crate.signature.value,
        algorithm=crate.signature.algorithm,
    )  # must not raise
    assert crate.content_hash == compute_content_hash(crate.model_dump(mode="json"))


def _sealed_body_and_key() -> tuple[dict[str, Any], Ed25519PrivateKey]:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed", output=b"x", output_hash="sha256:" + "f" * 64)
    run_manifest = _run_manifest_for(bundle, result)
    node_signing_key = Ed25519PrivateKey.generate()
    artifact = _artifact_ref()

    stored = sealer.seal(
        run_manifest,
        result,
        bundle,
        artifact_refs=[artifact],
        **_seal_kwargs(node_signing_key=node_signing_key),
    )
    return stored.body, node_signing_key


def _mutate_run_state(body: dict[str, Any]) -> None:
    body["run_state"] = "failed" if body["run_state"] != "failed" else "cancelled"


def _mutate_artifact_hash(body: dict[str, Any]) -> None:
    body["artifacts"][0]["content_hash"] = "sha256:" + "9" * 64


def _mutate_environment_image_digest(body: dict[str, Any]) -> None:
    body["environment"]["image_digest"] = "sha256:" + "8" * 64


def _mutate_task_id(body: dict[str, Any]) -> None:
    body["task_id"] = new_urn("task-bundle")


def _mutate_content_hash(body: dict[str, Any]) -> None:
    body["content_hash"] = "sha256:" + "7" * 64


def _mutate_known_unknowns(body: dict[str, Any]) -> None:
    body["known_unknowns"] = [*body["known_unknowns"], "a known unknown the node never recorded"]


@pytest.mark.parametrize(
    "mutate",
    [
        _mutate_run_state,
        _mutate_artifact_hash,
        _mutate_environment_image_digest,
        _mutate_task_id,
        _mutate_content_hash,
        _mutate_known_unknowns,
    ],
)
def test_tampering_any_sealed_field_fails_signature_verification(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    body, node_signing_key = _sealed_body_and_key()
    tampered = json.loads(json.dumps(body))  # deep copy
    mutate(tampered)
    tampered_crate = EvidenceCrate.model_validate(tampered)

    with pytest.raises(SignatureVerificationError):
        verify_object_signature(
            node_signing_key.public_key(),
            tampered_crate.model_dump(mode="json"),
            tampered_crate.signature.value,
            algorithm=tampered_crate.signature.algorithm,
        )


def test_tampering_sealed_to_false_is_rejected_before_verification_even_runs() -> None:
    """``sealed`` is a JSON Schema ``const: true`` / Pydantic ``Literal[True]``
    — flipping it is rejected by validation itself, before signature
    verification is even attempted. Defense in depth alongside the
    signature: two independent layers both refuse a desealed crate.
    """
    body, _node_signing_key = _sealed_body_and_key()
    tampered = json.loads(json.dumps(body))
    tampered["sealed"] = False

    with pytest.raises(ValidationError):
        EvidenceCrate.model_validate(tampered)


def test_artifact_refs_carry_content_hashes_that_recompute_from_the_object_store(
    tmp_path: Path,
) -> None:
    """Reuses the real E1-T07 ``LocalFilesystemArtifactStore`` — the
    artifact's content hash carried into the sealed crate is not merely
    caller-asserted, it recomputes from the actual stored bytes (stage 6
    acceptance)."""
    store = LocalFilesystemArtifactStore(tmp_path)
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result)

    data = b"deterministic reference output bytes"
    descriptor = store.put(
        data,
        media_type="application/octet-stream",
        producer_run_id=run_manifest.id,
        classification="PUBLIC",
        created_at=datetime.now(UTC),
    )
    artifact_ref = ArtifactRef(
        artifact_id=new_urn("artifact"),
        content_hash=descriptor.content_hash,
        classification="PUBLIC",
    )

    stored = sealer.seal(
        run_manifest, result, bundle, artifact_refs=[artifact_ref], **_seal_kwargs()
    )

    persisted_hash = stored.body["artifacts"][0]["content_hash"]
    assert persisted_hash == descriptor.content_hash
    assert persisted_hash == compute_raw_content_hash(store.get(persisted_hash))
    assert persisted_hash == compute_raw_content_hash(data)


# ---------------------------------------------------------------------------
# Fail-closed cross-checks.
# ---------------------------------------------------------------------------


def test_mismatched_execution_result_task_id_raises() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    other_bundle = _bundle()
    result = _execution_result(other_bundle, "completed")
    run_manifest = _run_manifest_for(other_bundle, result)

    with pytest.raises(ValueError, match="execution_result.task_id"):
        sealer.seal(run_manifest, result, bundle, **_seal_kwargs())


def test_mismatched_run_manifest_task_id_raises() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    other_bundle = _bundle()
    result = _execution_result(bundle, "completed")
    other_result = _execution_result(other_bundle, "completed")
    run_manifest = _run_manifest_for(other_bundle, other_result)

    with pytest.raises(ValueError, match="run_manifest.task_id"):
        sealer.seal(run_manifest, result, bundle, **_seal_kwargs())


def test_run_manifest_not_sealed_raises() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result).model_copy(update={"sealed": False})

    with pytest.raises(ValueError, match="not sealed"):
        sealer.seal(run_manifest, result, bundle, **_seal_kwargs())


def test_run_manifest_run_state_mismatch_raises() -> None:
    sealer, _ = _sealer()
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result).model_copy(update={"run_state": "failed"})

    with pytest.raises(ValueError, match="run_state"):
        sealer.seal(run_manifest, result, bundle, **_seal_kwargs())


def test_missing_code_commit_raises() -> None:
    sealer, _ = _sealer()
    bundle = _bundle(
        execution={
            "image_digest": "sha256:" + "c" * 64,
            "entrypoint": ["run.sh"],
            "code_revision": None,
        }
    )
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(bundle, result)
    assert run_manifest.code_commit is None

    with pytest.raises(ValueError, match="code_commit"):
        sealer.seal(run_manifest, result, bundle, **_seal_kwargs())
