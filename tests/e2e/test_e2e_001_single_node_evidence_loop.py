"""E2E-001 (E2 scope) — task-packets/E2-T07.yaml, docs/spec/
05_EVALUATION_AND_ACCEPTANCE.md section 6 ("E2E-001 Single-node evidence
loop"). Drives ``mrr.services.cli.orchestration.run_local_evidence_loop`` —
the SAME function the ``mrr`` console script calls — against a real
PostgreSQL (the ``postgres_engine`` fixture in this directory's own
``conftest.py``) and a real local content-addressed artifact store.

Scope: this is the E2 portion of the specified E2E-001 scenario — "Approve
Research Score" through "Seal Evidence Crate" (steps 1-5, with step 2
deliberately NOT generating a hypothesis-forest branch, per this task's
forbidden_changes: "the CLI references a branch_id but does not generate
branches"). Steps 6-9 (create a claim, run independent verification, mark
claim status, export a portable bundle) belong to E3/E8 and are out of scope
here — see this task's PR body for the full mapping.

Acceptance-test mapping (task-packets/E2-T07.yaml):

- "the whole loop runs with NO LLM ... completed via the deterministic
  executor and the result is_deterministic" ->
  ``test_complete_local_run_is_deterministic_with_no_llm``.
- "the sealed crate exists, is schema-valid, references the run manifest id
  and the task id, and its node signature verifies" + "every hash resolves"
  -> ``test_every_hash_and_signature_resolves``.
- "deterministic replay: running the loop twice with identical inputs yields
  the same executor output hash" ->
  ``test_deterministic_replay_same_inputs_yield_same_output_hash``.
- "an unapproved score aborts the run at the gate with the deterministic
  typed error" -> ``test_unapproved_score_aborts_at_the_gate``.
- "a policy-denied run ... seals an explicit FAILURE crate carrying that
  terminal run_state" -> ``test_policy_denied_run_seals_an_explicit_failure_crate``.
- "a timed-out run ... seals an explicit FAILURE crate carrying that terminal
  run_state" -> ``test_timed_out_run_seals_an_explicit_failure_crate``.
- MRR-NFR-012 / MRR-FR-053: a run started with no injected code revision
  (``code_revision=None``, the caller's own honest "unknown" — see
  ``run_local_evidence_loop``'s own docstring for why this is never derived
  by shelling out to ``git``) executes and records its Run Manifest as
  usual, then fails EXPLICITLY at sealing rather than fabricating a code
  revision -> ``test_run_without_code_revision_fails_explicitly_at_sealing``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.contracts import EvidenceCrate, RunManifest, TaskBundle
from mrr.crypto.hashing import content_hash
from mrr.domain.exceptions import ScoreNotApprovedError
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.services.cli.orchestration import run_local_evidence_loop
from mrr.services.node_runtime.executor import ReferenceTaskExecutor, default_reference_transform
from sqlalchemy import Engine

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

#: A fixed, caller-injected code revision for every test that needs a crate
#: to actually seal — mirrors what ``mrr.services.cli.main`` would resolve
#: from ``--code-revision``/``MRR_CODE_COMMIT`` in a real deployment. Never
#: derived from the real checked-out git commit (this test suite must not
#: depend on running inside a git working tree either).
_TEST_CODE_REVISION = "git:e2e-test-fixture"


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def test_complete_local_run_is_deterministic_with_no_llm(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """MRR-FR-044 / the task's own framing: there is no model/LLM anywhere in
    this loop — the executor is the deterministic reference implementation,
    and its result says so.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    result = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"
    assert result.is_deterministic is True
    assert result.output_hash is not None


def test_every_hash_and_signature_resolves(postgres_engine: Engine, tmp_path: Path) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    result = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        code_revision=_TEST_CODE_REVISION,
    )

    object_repository = PostgresObjectRepository(postgres_engine)

    # The sealed crate exists and is schema-valid.
    crate_stored = object_repository.get_latest(result.evidence_crate_id)
    schema = json.loads((SCHEMAS_DIR / "evidence-crate.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(crate_stored.body)
    crate = EvidenceCrate.model_validate(crate_stored.body)

    # It references the run manifest id and the task id.
    assert crate.run_id == result.run_manifest_id
    assert crate.task_id == result.task_id

    # crate.run_id resolves to the recorded RunManifest.
    manifest_stored = object_repository.get_latest(crate.run_id)
    run_manifest = RunManifest.model_validate(manifest_stored.body)
    assert run_manifest.task_id == result.task_id
    assert run_manifest.sealed is True
    assert run_manifest.run_state == "completed"

    # The task itself resolves, and the origin's signature over it still
    # verifies — over the persisted exclude_none=True body itself
    # (ADR-0004, task-packets/E5-T00.yaml), the same form
    # _authorize_and_verify uses in production.
    bundle_stored = object_repository.get_latest(result.task_id)
    task_bundle = TaskBundle.model_validate(bundle_stored.body)
    verify_object_signature(
        origin_key.public_key(),
        bundle_stored.body,
        task_bundle.signature.value,
        algorithm=task_bundle.signature.algorithm,
    )

    # Artifact hashes (input and output) recompute against the stored bytes.
    assert crate.artifacts, "a completed run must have produced at least one output artifact"
    for artifact_ref in crate.artifacts:
        stored_bytes = store.get(artifact_ref.content_hash)
        assert content_hash(stored_bytes) == artifact_ref.content_hash
    for declared_input_hash in run_manifest.input_hashes:
        stored_bytes = store.get(declared_input_hash)
        assert content_hash(stored_bytes) == declared_input_hash

    # The crate's node signature verifies — a local check only (no
    # production EvidenceCrate verify path exists yet; that is E5-T05's
    # scope), over the same exclude_none=True persisted body (ADR-0004,
    # task-packets/E5-T00.yaml).
    verify_object_signature(
        node_key.public_key(),
        crate_stored.body,
        crate.signature.value,
        algorithm=crate.signature.algorithm,
    )


def test_deterministic_replay_same_inputs_yield_same_output_hash(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """G-008 / task-packets/E2-T07.yaml's replay gate: two independent loop
    runs declaring the SAME input artifact (same id, same bytes) produce the
    same executor output hash, even though every OTHER id/timestamp each run
    mints (score, node, task, run manifest, crate) is fresh.

    ``default_reference_transform`` hashes ``"<artifact_id>:<sha256(bytes)>"``
    lines — it is deterministic in the full resolved ``inputs`` mapping (both
    keys and values), not merely in the byte content — so ``input_artifact_id``
    must be held constant across the two calls being compared, exactly like
    ``input_bytes``; two runs that mint two different random artifact ids for
    otherwise-identical bytes are, from the executor's own point of view, two
    different inputs (see ``run_local_evidence_loop``'s own docstring).
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    fixed_input = b"mrr-e2-t07-deterministic-replay-fixture"
    fixed_input_artifact_id = new_urn("artifact")

    first = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        input_bytes=fixed_input,
        input_artifact_id=fixed_input_artifact_id,
        code_revision=_TEST_CODE_REVISION,
    )
    second = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        input_bytes=fixed_input,
        input_artifact_id=fixed_input_artifact_id,
        code_revision=_TEST_CODE_REVISION,
    )

    assert first.run_state == "completed"
    assert second.run_state == "completed"
    assert first.output_hash == second.output_hash
    # Two independent runs, not one memoized call — distinct crates/manifests/tasks.
    assert first.evidence_crate_id != second.evidence_crate_id
    assert first.run_manifest_id != second.run_manifest_id
    assert first.task_id != second.task_id


def test_unapproved_score_aborts_at_the_gate(postgres_engine: Engine, tmp_path: Path) -> None:
    """MRR-FR-004: an unapproved score blocks the run before any Task Bundle
    is even created — the CLI refuses to start work, deterministically,
    with the typed ``ScoreNotApprovedError``, not a generic failure.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    with pytest.raises(ScoreNotApprovedError):
        run_local_evidence_loop(
            engine=postgres_engine,
            artifact_store=store,
            origin_signing_key=origin_key,
            node_signing_key=node_key,
            approve_score=False,
        )


def test_policy_denied_run_seals_an_explicit_failure_crate(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """MRR-FR-050: a policy-denied run is not a silent success — it seals an
    explicit failure crate carrying ``run_state == "policy_denied"``.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    executor = ReferenceTaskExecutor(policy_gate=lambda _bundle: False)

    result = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        executor=executor,
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "policy_denied"
    assert result.output_hash is None

    object_repository = PostgresObjectRepository(postgres_engine)
    crate = EvidenceCrate.model_validate(
        object_repository.get_latest(result.evidence_crate_id).body
    )
    assert crate.run_state == "policy_denied"
    assert crate.sealed is True
    assert crate.artifacts == []
    assert crate.failures, "a non-completed run must carry an explicit failure entry"

    run_manifest = RunManifest.model_validate(
        object_repository.get_latest(result.run_manifest_id).body
    )
    assert run_manifest.run_state == "policy_denied"
    assert run_manifest.sealed is True


def _slow_transform(inputs: Mapping[str, bytes]) -> bytes:
    time.sleep(3)
    return default_reference_transform(inputs)


def test_timed_out_run_seals_an_explicit_failure_crate(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """MRR-FR-050/MRR-FR-040: a timed-out run is likewise not a silent
    success — it seals an explicit failure crate carrying
    ``run_state == "timed_out"``.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    executor = ReferenceTaskExecutor(transform=_slow_transform)

    result = run_local_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        executor=executor,
        timeout_seconds=1,
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "timed_out"
    assert result.output_hash is None

    object_repository = PostgresObjectRepository(postgres_engine)
    crate = EvidenceCrate.model_validate(
        object_repository.get_latest(result.evidence_crate_id).body
    )
    assert crate.run_state == "timed_out"
    assert crate.sealed is True
    assert crate.failures, "a non-completed run must carry an explicit failure entry"

    run_manifest = RunManifest.model_validate(
        object_repository.get_latest(result.run_manifest_id).body
    )
    assert run_manifest.run_state == "timed_out"
    assert run_manifest.sealed is True


def test_run_without_code_revision_fails_explicitly_at_sealing(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """MRR-NFR-012 / MRR-FR-053: with no code revision injected (the honest
    "unknown" ``code_revision=None`` default — never a fabricated git-derived
    value, per ``run_local_evidence_loop``'s own docstring), the loop still
    executes and records a sealed Run Manifest, but ``EvidenceCrateSealer
    .seal`` then raises explicitly rather than sealing a crate with a
    fabricated code revision. This is a real gap in what this run can prove,
    surfaced loudly — not papered over with a placeholder value.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    with pytest.raises(ValueError, match="code_commit"):
        run_local_evidence_loop(
            engine=postgres_engine,
            artifact_store=store,
            origin_signing_key=origin_key,
            node_signing_key=node_key,
            # code_revision intentionally omitted -> None.
        )

    # The Run Manifest was still recorded before sealing failed — locate it
    # via the event log (the function raised before returning a
    # LocalEvidenceLoopResult, so there is no run_manifest_id to read here).
    event_log = PostgresEventLog(postgres_engine)
    object_repository = PostgresObjectRepository(postgres_engine)
    recorded_events = [
        appended
        for appended in event_log.read_all()
        if appended.event.event_type == "run_manifest.recorded"
    ]
    assert recorded_events, "the run manifest should still be recorded before sealing fails"
    run_manifest = RunManifest.model_validate(
        object_repository.get_latest(recorded_events[-1].event.object_id).body
    )
    assert run_manifest.code_commit is None
    assert run_manifest.run_state == "completed"
    assert run_manifest.sealed is True
