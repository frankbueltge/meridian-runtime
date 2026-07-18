"""Integration tests for
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer``
(task-packets/E2-T06.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ``bind_unit_of_work`` closing over all three.
The ``RunManifest`` each test seals is itself recorded through the real
E2-T05 ``RunManifestRecorder`` against the same database, so these tests
exercise the full node-runtime chain: execute -> record manifest -> seal
crate, exactly as production wiring would.

Acceptance-test mapping (task-packets/E2-T06.yaml, integration tier):

- "sealing persists one revision + one event atomically (integration, real
  PostgreSQL)" -> ``test_seal_persists_revision_one_and_exactly_one_event_atomically``.
- "a materially failed run also seals" ->
  ``test_seal_failed_result_persists_that_terminal_state_and_failures``.
- "the crate read back is schema-valid, immutable, and its signature still
  verifies from the database" ->
  ``test_crate_read_back_from_database_is_schema_and_pydantic_valid``,
  ``test_sealer_has_no_update_method_and_a_second_seal_call_is_a_new_object``,
  ``test_signature_verifies_from_the_database``.
- event provenance straight from the database (MRR-NFR-001) ->
  ``test_event_provenance_straight_from_database``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import ArtifactRef, EvidenceCrate, FailureEntry, RunManifest, TaskBundle
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.identity import new_urn, parse_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.evidence_crate import bind_unit_of_work as bind_crate_unit_of_work
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage, TerminalOutcome
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from mrr.services.node_runtime.run_manifest import bind_unit_of_work as bind_manifest_unit_of_work
from sqlalchemy import Engine

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_POLICY_VERSION = "policy-2026-07-01"


def _sealer_for(
    engine: Engine,
) -> tuple[EvidenceCrateSealer, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_crate_unit_of_work(engine, object_repository, event_log)
    return EvidenceCrateSealer(record), object_repository, event_log


def _bundle(*, revision: int = 1, declared_inputs: list[ArtifactRef] | None = None) -> TaskBundle:
    now = datetime.now(UTC)
    return TaskBundle.model_validate(
        {
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
            "research_score_revision": 2,
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
    )


def _artifact_ref(content_hash: str = "sha256:" + "d" * 64) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=new_urn("artifact"), content_hash=content_hash, classification="PUBLIC"
    )


def _execution_result(
    bundle: TaskBundle,
    outcome: TerminalOutcome,
    *,
    output_hash: str | None = None,
    wall_time_seconds: float = 0.5,
) -> ExecutionResult:
    return ExecutionResult(
        outcome=outcome,
        output=b"x" if output_hash else None,
        output_hash=output_hash,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=wall_time_seconds),
        detail=None if outcome == "completed" else "reference executor detail",
    )


def _run_manifest_for(engine: Engine, bundle: TaskBundle, result: ExecutionResult) -> RunManifest:
    """Record a real, sealed ``RunManifest`` for ``bundle``/``result`` via
    the E2-T05 ``RunManifestRecorder`` against the same database, exactly as
    production wiring would chain the two node-runtime services."""
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_manifest_unit_of_work(engine, object_repository, event_log)
    recorder = RunManifestRecorder(record)
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


def test_seal_persists_revision_one_and_exactly_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    sealer, object_repository, event_log = _sealer_for(postgres_engine)
    bundle = _bundle()
    result = _execution_result(bundle, "completed", output_hash="sha256:" + "e" * 64)
    run_manifest = _run_manifest_for(postgres_engine, bundle, result)
    artifact_ref = _artifact_ref()

    stored = sealer.seal(
        run_manifest, result, bundle, artifact_refs=[artifact_ref], **_seal_kwargs()
    )

    assert stored.revision == 1
    persisted = object_repository.get_latest(stored.id)
    assert persisted.revision == 1
    assert persisted.body["sealed"] is True
    assert persisted.body["run_state"] == "completed"
    assert persisted.body["run_id"] == run_manifest.id
    assert persisted.body["task_id"] == bundle.id
    assert persisted.body["artifacts"] == [artifact_ref.model_dump(mode="json")]

    events = [
        appended for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert len(events) == 1
    assert events[0].event.event_type == "evidence_crate.sealed"


@pytest.mark.parametrize("outcome", ["failed", "timed_out", "cancelled", "policy_denied"])
def test_seal_failed_result_persists_that_terminal_state_and_failures(
    postgres_engine: Engine, outcome: TerminalOutcome
) -> None:
    sealer, object_repository, _ = _sealer_for(postgres_engine)
    bundle = _bundle()
    result = _execution_result(bundle, outcome)
    run_manifest = _run_manifest_for(postgres_engine, bundle, result)
    failures = [
        FailureEntry(
            code="E_RUN_FAILED", category="execution_error", message="run did not complete"
        )
    ]
    known_unknowns = ["whether a retry would succeed"]

    stored = sealer.seal(
        run_manifest,
        result,
        bundle,
        **_seal_kwargs(failures=failures, known_unknowns=known_unknowns),
    )

    persisted = object_repository.get_latest(stored.id)
    assert persisted.body["run_state"] == outcome
    assert persisted.body["sealed"] is True
    assert persisted.body["artifacts"] == []
    assert persisted.body["failures"] == [f.model_dump(mode="json") for f in failures]
    assert persisted.body["known_unknowns"] == known_unknowns


def test_crate_read_back_from_database_is_schema_and_pydantic_valid(
    postgres_engine: Engine,
) -> None:
    sealer, object_repository, _ = _sealer_for(postgres_engine)
    input_ref = ArtifactRef(artifact_id=new_urn("artifact"), content_hash="sha256:" + "1" * 64)
    bundle = _bundle(declared_inputs=[input_ref])
    result = _execution_result(bundle, "completed", output_hash="sha256:" + "2" * 64)
    run_manifest = _run_manifest_for(postgres_engine, bundle, result)

    stored = sealer.seal(run_manifest, result, bundle, **_seal_kwargs())
    persisted = object_repository.get_latest(stored.id)

    crate = EvidenceCrate.model_validate(persisted.body)
    assert crate.environment.input_hashes == [input_ref.content_hash]

    entity, _ulid = parse_urn(crate.id)
    assert entity == "evidence-crate"

    schema = json.loads((SCHEMAS_DIR / "evidence-crate.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(persisted.body)


def test_sealer_has_no_update_method_and_a_second_seal_call_is_a_new_object(
    postgres_engine: Engine,
) -> None:
    """A sealed evidence crate is immutable — there is no method to mutate an
    already-sealed crate. Calling ``seal`` again (e.g. for a second,
    unrelated run) creates an entirely new object identity, never a new
    revision of the first.
    """
    public_methods = {
        name
        for name in dir(EvidenceCrateSealer)
        if not name.startswith("_") and callable(getattr(EvidenceCrateSealer, name))
    }
    assert public_methods == {"seal"}

    sealer, object_repository, _ = _sealer_for(postgres_engine)
    bundle_a = _bundle()
    result_a = _execution_result(bundle_a, "completed")
    manifest_a = _run_manifest_for(postgres_engine, bundle_a, result_a)
    bundle_b = _bundle()
    result_b = _execution_result(bundle_b, "completed")
    manifest_b = _run_manifest_for(postgres_engine, bundle_b, result_b)

    first = sealer.seal(manifest_a, result_a, bundle_a, **_seal_kwargs())
    second = sealer.seal(manifest_b, result_b, bundle_b, **_seal_kwargs())

    assert first.id != second.id
    assert object_repository.get_latest(first.id).revision == 1
    assert object_repository.get_latest(second.id).revision == 1


def test_signature_verifies_from_the_database(postgres_engine: Engine) -> None:
    sealer, object_repository, _ = _sealer_for(postgres_engine)
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(postgres_engine, bundle, result)
    node_signing_key = Ed25519PrivateKey.generate()

    stored = sealer.seal(
        run_manifest, result, bundle, **_seal_kwargs(node_signing_key=node_signing_key)
    )
    persisted = object_repository.get_latest(stored.id)

    crate = EvidenceCrate.model_validate(persisted.body)
    verify_object_signature(
        node_signing_key.public_key(),
        crate.model_dump(mode="json"),
        crate.signature.value,
        algorithm=crate.signature.algorithm,
    )  # must not raise, straight from the database


def test_event_provenance_straight_from_database(postgres_engine: Engine) -> None:
    sealer, _, event_log = _sealer_for(postgres_engine)
    bundle = _bundle()
    result = _execution_result(bundle, "completed")
    run_manifest = _run_manifest_for(postgres_engine, bundle, result)
    actor = new_urn("executor")
    correlation_id = new_urn("research-run")

    stored = sealer.seal(
        run_manifest,
        result,
        bundle,
        **_seal_kwargs(actor=actor, correlation_id=correlation_id),
    )

    events = [
        appended for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert len(events) == 1
    event = events[0].event
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["run_id"] == run_manifest.id
