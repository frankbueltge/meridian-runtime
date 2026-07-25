"""Contract tests for ``mrr.contracts.run_manifest.ArtifactStoreReference``/
``RunManifest.artifact_store_reference`` (task-packets/A2-T01.yaml, "Teil 1
— Aufzeichnen").

Four concerns, task-packets/A2-T01.yaml's own acceptance criteria, in order:

1. The status/root biconditional is enforced IN THE MODEL, not merely
   documented — both violating shapes raise on construction.
2. ``schemas/run-manifest.schema.json``'s ``artifact_store_reference``
   property agrees with the Pydantic model on every shape tried — the two
   are cross-checked here directly with ``jsonschema``, not merely by
   ``scripts/check_contracts.py``'s example-driven round trip (which never
   exercises a "recorded" instance at all, since neither the shipped
   example nor any committed archive dump has one).
3. Backward compatibility: both dumps committed to ``archive/dumps/`` still
   parse unchanged through ``mrr.domain.archive_dump`` (untouched by this
   packet — ``forbidden_changes``), and the three ``RunManifest`` objects
   inside them validate against the CURRENT contract, carrying
   ``status="not_recorded"`` as the true statement about them.
4. ``mrr.services.node_runtime.run_manifest.RunManifestRecorder.record``'s
   new ``artifact_root`` keyword actually produces a "recorded"/
   "not_recorded" ``ArtifactStoreReference`` as documented. The fixture
   helpers below are a DELIBERATE LOCAL DUPLICATE of
   tests/unit/services/node_runtime/test_run_manifest.py's own — that
   module is outside this packet's own allowed_paths, and this repository's
   own established convention is exactly this (see that module's own
   docstring note on ``_bundle()``: "deliberate local duplicate, not a
   shared import").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mrr.contracts import RunManifest, TaskBundle
from mrr.contracts.run_manifest import ArtifactStoreReference
from mrr.domain.archive_dump import parse_objects_copy_block
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from pydantic import ValidationError

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
DUMPS_DIR = REPO_ROOT / "archive" / "dumps"
_POLICY_VERSION = "policy-2026-07-26"


# ---------------------------------------------------------------------------
# 1. The biconditional is enforced in the model.
# ---------------------------------------------------------------------------


def test_recorded_with_root_is_valid() -> None:
    ref = ArtifactStoreReference(status="recorded", root="/data/artifacts")
    assert ref.status == "recorded"
    assert ref.root == "/data/artifacts"


def test_not_recorded_without_root_is_valid_and_the_default() -> None:
    ref = ArtifactStoreReference(status="not_recorded")
    assert ref.root is None


def test_recorded_without_root_raises() -> None:
    with pytest.raises(ValidationError):
        ArtifactStoreReference(status="recorded")


def test_recorded_with_explicit_null_root_raises() -> None:
    with pytest.raises(ValidationError):
        ArtifactStoreReference(status="recorded", root=None)


def test_not_recorded_with_a_root_raises() -> None:
    with pytest.raises(ValidationError):
        ArtifactStoreReference(status="not_recorded", root="/data/artifacts")


def test_run_manifest_defaults_artifact_store_reference_to_not_recorded() -> None:
    example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
    manifest = RunManifest.model_validate(example)
    assert manifest.artifact_store_reference == ArtifactStoreReference(status="not_recorded")


def test_run_manifest_accepts_an_explicitly_recorded_reference() -> None:
    example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
    example["artifact_store_reference"] = {"status": "recorded", "root": "/data/artifacts"}
    manifest = RunManifest.model_validate(example)
    assert manifest.artifact_store_reference.status == "recorded"
    assert manifest.artifact_store_reference.root == "/data/artifacts"


def test_run_manifest_rejects_a_biconditional_violating_reference() -> None:
    example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
    example["artifact_store_reference"] = {"status": "recorded"}
    with pytest.raises(ValidationError):
        RunManifest.model_validate(example)


def test_run_manifest_round_trip_preserves_the_recorded_reference() -> None:
    example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
    example["artifact_store_reference"] = {"status": "recorded", "root": "/data/artifacts"}
    manifest = RunManifest.model_validate(example)
    dumped = json.loads(manifest.model_dump_json(exclude_none=True))
    assert dumped["artifact_store_reference"] == {"status": "recorded", "root": "/data/artifacts"}
    assert RunManifest.model_validate(dumped) == manifest


# ---------------------------------------------------------------------------
# 2. Contract and schema agree — cross-checked directly, not merely via the
#    example-driven round trip (which never exercises a "recorded" shape).
# ---------------------------------------------------------------------------


def _validate_artifact_store_reference_against_schema(reference: dict[str, Any] | None) -> None:
    schema = json.loads((SCHEMAS_DIR / "run-manifest.schema.json").read_text())
    registry = build_registry()
    validator = build_validator_for_schema(schema, registry)

    example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
    if reference is None:
        example.pop("artifact_store_reference", None)
    else:
        example["artifact_store_reference"] = reference
    validator.validate(example)


@pytest.mark.parametrize(
    "reference",
    [
        None,
        {"status": "not_recorded"},
        {"status": "not_recorded", "root": None},
        {"status": "recorded", "root": "/data/artifacts"},
    ],
)
def test_schema_accepts_every_shape_the_model_accepts(reference: dict[str, Any] | None) -> None:
    _validate_artifact_store_reference_against_schema(reference)
    if reference is None:
        # Absent entirely — the model's own default kicks in; both agree
        # this is valid.
        example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
        RunManifest.model_validate(example)
    else:
        example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
        example["artifact_store_reference"] = reference
        RunManifest.model_validate(example)


@pytest.mark.parametrize(
    "reference",
    [
        {"status": "recorded"},
        {"status": "recorded", "root": None},
        {"status": "not_recorded", "root": "/data/artifacts"},
        {"status": "bogus"},
        {"status": "not_recorded", "extra_key": "not allowed"},
    ],
)
def test_schema_and_model_both_reject_every_violating_shape(reference: dict[str, Any]) -> None:
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

    with pytest.raises(JsonSchemaValidationError):
        _validate_artifact_store_reference_against_schema(reference)

    example = json.loads((REPO_ROOT / "examples" / "run-manifest.example.json").read_text())
    example["artifact_store_reference"] = reference
    with pytest.raises(ValidationError):
        RunManifest.model_validate(example)


def test_artifact_store_reference_not_in_top_level_required_list() -> None:
    """Deliberate: the field is absent from every RunManifest body committed
    before this packet (task-packets/A2-T01.yaml explicitly_not: no
    back-filling). Making it schema-required would break every one of
    them — see the module docstring's "Backward compatibility" concern.
    """
    schema = json.loads((SCHEMAS_DIR / "run-manifest.schema.json").read_text())
    required = schema["allOf"][1]["required"]
    assert "artifact_store_reference" not in required
    assert "artifact_store_reference" in schema["allOf"][1]["properties"]


# ---------------------------------------------------------------------------
# 3. Backward compatibility: both committed dumps parse unchanged, and the
#    three existing RunManifest objects inside them validate, carrying
#    not_recorded as the true statement about them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dump_name",
    ["mrr_k1t04_real_run_v2.sql", "mrr_run2_corroboration_floor_v1.sql"],
)
def test_both_committed_dumps_still_parse_unchanged(dump_name: str) -> None:
    dump_text = (DUMPS_DIR / dump_name).read_text(encoding="utf-8")
    objects = parse_objects_copy_block(dump_text)
    assert len(objects) > 0
    assert any(obj.kind == "RunManifest" for obj in objects)


def test_both_existing_run_manifests_validate_as_not_recorded() -> None:
    run_manifest_ids: list[str] = []
    for dump_name in ("mrr_k1t04_real_run_v2.sql", "mrr_run2_corroboration_floor_v1.sql"):
        dump_text = (DUMPS_DIR / dump_name).read_text(encoding="utf-8")
        objects = parse_objects_copy_block(dump_text)
        for obj in objects:
            if obj.kind != "RunManifest":
                continue
            manifest = RunManifest.model_validate(obj.body)
            assert manifest.artifact_store_reference.status == "not_recorded"
            assert manifest.artifact_store_reference.root is None
            run_manifest_ids.append(manifest.id)

    # The two real dumps carry one and two RunManifest objects respectively
    # (docs/design/2026-07-26-a2-derivation-artifact-store-reference.md).
    assert len(run_manifest_ids) == 3
    assert len(set(run_manifest_ids)) == 3


# ---------------------------------------------------------------------------
# 4. RunManifestRecorder.record()'s new artifact_root keyword — a
#    deliberate local duplicate of tests/unit/services/node_runtime
#    /test_run_manifest.py's own fixture helpers (see the module docstring).
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


def _recorder() -> tuple[RunManifestRecorder, _FakeUnitOfWork]:
    uow = _FakeUnitOfWork()
    return RunManifestRecorder(uow), uow


def _bundle(**overrides: Any) -> TaskBundle:
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
        "research_score_revision": 3,
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


def _result(bundle: TaskBundle) -> ExecutionResult:
    return ExecutionResult(
        outcome="completed",
        output=b"output bytes",
        output_hash="sha256:" + "e" * 64,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=0.42),
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


def test_record_without_artifact_root_defaults_to_not_recorded() -> None:
    recorder, _uow = _recorder()
    bundle = _bundle()
    result = _result(bundle)

    stored = recorder.record(result, bundle, **_record_kwargs())

    assert stored.body["artifact_store_reference"] == {"status": "not_recorded"}


def test_record_with_artifact_root_records_it() -> None:
    recorder, _uow = _recorder()
    bundle = _bundle()
    result = _result(bundle)

    stored = recorder.record(
        result, bundle, artifact_root=Path("/var/data/artifacts"), **_record_kwargs()
    )

    assert stored.body["artifact_store_reference"] == {
        "status": "recorded",
        "root": "/var/data/artifacts",
    }


def test_record_with_artifact_root_as_a_string_records_it_verbatim() -> None:
    recorder, _uow = _recorder()
    bundle = _bundle()
    result = _result(bundle)

    stored = recorder.record(
        result, bundle, artifact_root="s3-shaped/not/a/real/path", **_record_kwargs()
    )

    assert stored.body["artifact_store_reference"] == {
        "status": "recorded",
        "root": "s3-shaped/not/a/real/path",
    }


def test_recorded_manifest_is_schema_and_pydantic_valid_with_a_recorded_root() -> None:
    recorder, _uow = _recorder()
    bundle = _bundle()
    result = _result(bundle)

    stored = recorder.record(
        result, bundle, artifact_root=Path("/var/data/artifacts"), **_record_kwargs()
    )

    schema = json.loads((SCHEMAS_DIR / "run-manifest.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(stored.body)
    manifest = RunManifest.model_validate(stored.body)
    assert manifest.artifact_store_reference.status == "recorded"
    assert manifest.artifact_store_reference.root == "/var/data/artifacts"
