"""Unit tests for ``mrr.services.node_runtime.run_manifest.RunManifestRecorder``
(task-packets/E2-T05.yaml) — entirely DB-free, no PostgreSQL, no
``sqlalchemy.Engine``: the recorder needs only a bound
``RecordRevisionWithEvent`` callable (see the module's own docstring for why
it needs no ``ObjectRepository``/event-journal read dependency at all), so
the fake unit-of-work below is simpler than the other services' own
DB-free fakes (no revision bookkeeping needed — every call is a brand-new
object at revision 1).

Acceptance-test mapping (task-packets/E2-T05.yaml):

- "recording a completed ExecutionResult yields a sealed manifest carrying
  the terminal state and resource usage" (unit-level; the packet's own
  duplicate against real PostgreSQL is the integration tier) ->
  ``test_completed_result_builds_sealed_manifest_with_terminal_state_and_resource_usage``.
- "recording a failed/timed_out ExecutionResult yields a manifest with that
  terminal state" -> ``test_every_terminal_outcome_is_recorded_with_its_own_state``.
- "the recorded manifest reproduces the executor output hash and the task/
  score revisions exactly" ->
  ``test_completed_result_builds_sealed_manifest_with_terminal_state_and_resource_usage``,
  ``test_input_hashes_and_network_permitted_are_derived_from_task_bundle``.
- "a sealed run manifest is immutable — no public interface mutates it" ->
  ``test_recorder_exposes_no_mutate_method``.
- "recording a manifest writes exactly one domain event with full NFR-001
  provenance, atomically with the persisted revision" ->
  ``test_event_provenance_is_complete_and_causation_is_root``.
- RunManifest.id names "the run" (entity segment ``run``, not
  ``run-manifest``) -> ``test_manifest_id_uses_run_entity_segment``.
- fail-closed cross-checks (not in the packet's own acceptance list, this
  task's own addition, flagged in the PR) ->
  ``test_mismatched_task_id_raises``, ``test_mismatched_task_revision_raises``,
  ``test_ended_before_started_raises``.
- every manifest built is schema- and Pydantic-valid, not only the static
  example -> ``test_recorded_body_is_schema_and_pydantic_valid``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mrr.contracts import ArtifactRef, RunManifest, TaskBundle
from mrr.domain.identity import new_urn, parse_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage, TerminalOutcome
from mrr.services.node_runtime.run_manifest import RunManifestRecorder

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_POLICY_VERSION = "policy-2026-07-01"

# ---------------------------------------------------------------------------
# Fake unit-of-work: RunManifestRecorder never reads prior state (every call
# writes a brand-new object at revision 1), so this fake only needs to record
# what it was called with, unlike the richer FakeObjectRepository/FakeEventLog
# pairs the lifecycle-managing services' own test modules build.
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
            "RunManifestRecorder always writes a brand-new object at revision 1"
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


def _recorder() -> tuple[RunManifestRecorder, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return RunManifestRecorder(uow), uow


# ---------------------------------------------------------------------------
# TaskBundle fixture factory — same shape and convention as
# tests/unit/services/node_runtime/test_executor.py's own ``_bundle()``
# (deliberate local duplicate, not a shared import — see that module's own
# comment on this convention).
# ---------------------------------------------------------------------------


def _bundle(
    *,
    revision: int = 1,
    declared_inputs: list[ArtifactRef] | None = None,
    network_mode: str = "deny_all",
    network_allowlist: list[str] | None = None,
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
        "research_score_revision": 3,
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
        "network_policy": {"mode": network_mode, "allowlist": network_allowlist or []},
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
    return ArtifactRef(artifact_id=new_urn("artifact"), content_hash=content_hash)


def _result(
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
        detail=None,
    )


def _record_kwargs(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "practice_id": new_urn("practice"),
        "executor_id": new_urn("executor"),
        "executor_role": "reference-task-executor",
        "started_at": now,
        "ended_at": now + timedelta(seconds=1),
        "actor": new_urn("executor"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_completed_result_builds_sealed_manifest_with_terminal_state_and_resource_usage() -> None:
    recorder, uow = _recorder()
    input_ref = _artifact_ref()
    bundle = _bundle(revision=2, declared_inputs=[input_ref])
    output_hash = "sha256:" + "e" * 64
    result = _result(bundle, "completed", output=b"output bytes", output_hash=output_hash)
    kwargs = _record_kwargs()

    stored = recorder.record(result, bundle, **kwargs)

    body = stored.body
    assert body["sealed"] is True
    assert body["run_state"] == "completed"
    assert body["resource_usage"] == {"wall_seconds": 0.42}
    assert body["produced_artifact_hashes"] == [output_hash]
    assert body["task_id"] == bundle.id
    assert body["task_revision"] == 2
    assert body["research_score_id"] == bundle.research_score_id
    assert body["research_score_revision"] == 3
    assert body["image_digest"] == bundle.execution.image_digest
    assert body["code_commit"] == "git:abc123"
    assert body["parameters"] == bundle.instructions
    assert body["input_hashes"] == [input_ref.content_hash]
    assert body["tool_invocations"] == []
    assert body["model_invocations"] == []
    assert body["ended_at"] is not None
    assert stored.revision == 1

    assert len(uow.stored) == 1
    assert len(uow.events) == 1


@pytest.mark.parametrize(
    "outcome",
    ["completed", "failed", "cancelled", "timed_out", "policy_denied", "partial"],
)
def test_every_terminal_outcome_is_recorded_with_its_own_state(outcome: TerminalOutcome) -> None:
    """MRR-FR-043: every terminal outcome — not only ``completed`` — is
    recorded with its own explicit state, and every one is still sealed
    (this recorder only ever builds already-sealed manifests).
    """
    recorder, _ = _recorder()
    bundle = _bundle()
    result = _result(bundle, outcome)

    stored = recorder.record(result, bundle, **_record_kwargs())

    assert stored.body["run_state"] == outcome
    assert stored.body["sealed"] is True


def test_recorder_exposes_no_mutate_method() -> None:
    """A sealed run manifest is immutable (docs/spec/02_DOMAIN_MODEL.md
    section 2.6) — this class's only public callable is ``record``; there is
    no update/seal/correct method anywhere on it.
    """
    public_methods = {
        name
        for name in dir(RunManifestRecorder)
        if not name.startswith("_") and callable(getattr(RunManifestRecorder, name))
    }
    assert public_methods == {"record"}


def test_event_provenance_is_complete_and_causation_is_root() -> None:
    recorder, uow = _recorder()
    bundle = _bundle()
    result = _result(bundle, "completed", output=b"x", output_hash="sha256:" + "f" * 64)
    actor = new_urn("executor")
    correlation_id = new_urn("research-run")

    stored = recorder.record(
        result, bundle, **_record_kwargs(actor=actor, correlation_id=correlation_id)
    )

    assert len(uow.events) == 1
    event = uow.events[0]
    assert event.event_type == "run_manifest.recorded"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None  # a brand-new run identity has no prior event
    assert event.object_id == stored.id
    assert event.object_revision == 1
    assert event.occurred_at.tzinfo is not None
    assert event.payload["task_id"] == bundle.id
    assert event.payload["run_state"] == "completed"


def test_manifest_id_uses_run_entity_segment() -> None:
    """RunManifest.id is minted with URN entity "run" (not "run-manifest") —
    see mrr.contracts.run_manifest's own docstring for why this matches
    schemas/evidence-crate.schema.json's pre-existing run_id field.
    """
    recorder, _ = _recorder()
    bundle = _bundle()
    result = _result(bundle, "completed")

    stored = recorder.record(result, bundle, **_record_kwargs())

    entity, _ulid = parse_urn(stored.id)
    assert entity == "run"


def test_input_hashes_and_network_permitted_are_derived_from_task_bundle() -> None:
    recorder, _ = _recorder()
    ref_a = _artifact_ref("sha256:" + "1" * 64)
    ref_b = _artifact_ref("sha256:" + "2" * 64)
    bundle = _bundle(
        declared_inputs=[ref_a, ref_b],
        network_mode="allowlist",
        network_allowlist=["api.example.invalid"],
    )
    result = _result(bundle, "completed")

    stored = recorder.record(result, bundle, **_record_kwargs())

    assert stored.body["input_hashes"] == [ref_a.content_hash, ref_b.content_hash]
    assert stored.body["network_permitted"] == ["api.example.invalid"]
    assert stored.body["network_performed"] == []


def test_deny_all_network_policy_yields_no_permitted_hosts() -> None:
    recorder, _ = _recorder()
    bundle = _bundle(network_mode="deny_all", network_allowlist=[])
    result = _result(bundle, "completed")

    stored = recorder.record(result, bundle, **_record_kwargs())

    assert stored.body["network_permitted"] == []


def test_mismatched_task_id_raises() -> None:
    recorder, _ = _recorder()
    bundle = _bundle()
    other_bundle = _bundle()
    result = _result(other_bundle, "completed")

    with pytest.raises(ValueError, match="task_id"):
        recorder.record(result, bundle, **_record_kwargs())


def test_mismatched_task_revision_raises() -> None:
    recorder, _ = _recorder()
    bundle = _bundle(revision=1)
    # Build a result claiming a different revision of the SAME bundle id.
    result = ExecutionResult(
        outcome="completed",
        output=b"x",
        output_hash="sha256:" + "9" * 64,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=2,
        resource_usage=ResourceUsage(wall_time_seconds=0.1),
        detail=None,
    )

    with pytest.raises(ValueError, match="task_revision"):
        recorder.record(result, bundle, **_record_kwargs())


def test_ended_before_started_raises() -> None:
    recorder, _ = _recorder()
    bundle = _bundle()
    result = _result(bundle, "completed")
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="ended_at"):
        recorder.record(
            result,
            bundle,
            **_record_kwargs(started_at=now, ended_at=now - timedelta(seconds=1)),
        )


def test_recorded_body_is_schema_and_pydantic_valid() -> None:
    """Every manifest this recorder builds — not only the static example —
    validates against both the JSON Schema and the Pydantic model (E1-T03's
    invariant, extended to this seventh entity)."""
    recorder, _ = _recorder()
    bundle = _bundle(declared_inputs=[_artifact_ref()])
    result = _result(bundle, "completed", output=b"x", output_hash="sha256:" + "7" * 64)

    stored = recorder.record(result, bundle, **_record_kwargs())

    RunManifest.model_validate(stored.body)  # Pydantic validation

    schema = json.loads((SCHEMAS_DIR / "run-manifest.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(stored.body)

    # Belt: jsonschema's Draft202012Validator.check_schema on the entity
    # schema itself, matching scripts/check_contracts.py check 1.
    Draft202012Validator.check_schema(schema)
