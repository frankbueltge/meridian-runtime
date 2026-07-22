"""Integration tests for ``mrr release supersede``/``mrr release status``
(task-packets/E8-T05.yaml), driven end to end through the REAL console-
script entry point (``mrr.services.cli.main.main``), against a real,
throwaway PostgreSQL test schema AND a real ``mrr.adapters.object_store
.local.LocalFilesystemArtifactStore`` — mirrors ``tests/integration/services
/test_release_cli_create_and_verify.py``'s own ``postgres_url``/fixture-
factory shape (duplicated here, trimmed and extended, per that module's own
documented "this codebase's own established convention of duplicating this
exact shape per integration test module").

``_seed_graph`` is a byte-for-byte reuse of the E8-T04 module's own helper
(a source record, an evidence anchor, a claim, a run manifest, and a sealed
``EvidenceCrate`` — through the actual E3/E2 services this codebase ships).
``_record_correction`` is this module's own new addition, recording a real
``CorrectionEvent`` via ``CorrectionImpactService.record`` against a claim
inside a release's own exported closure — needed for AT2's
corrections_affect_this_release flow.

Acceptance-test mapping (task-packets/E8-T05.yaml):

- AT1 -> ``test_supersede_then_status_reports_superseded_and_current``.
- AT2 -> ``test_correction_after_release_against_a_claim_in_closure_flips_
  status_to_corrections_affect``, ``test_correction_before_release_does_not_
  flip_status``, ``test_correction_against_an_unrelated_object_does_not_
  flip_status``.
- AT4 -> ``test_verify_bundle_dir_against_pre_supersession_bundle_still_
  matches_after_superseding``.
- AT5 -> the tests under "Refusal matrix" below (already-superseded,
  non-person approver, unknown --supersedes id — each run through the real
  CLI; self-supersede is unit-tested directly against ``ReleaseService``
  instead — see ``tests/unit/services/release/test_supersede_and_status
  .py`` — since the CLI can never construct a self-supersede call in
  practice, the new release's id is always freshly minted). E8-T01…T04's
  own suites are confirmed passing UNMODIFIED alongside this module as part
  of the full ``test-integration`` tier run — see this task's own delivery
  report for the exact command and result, mirroring E8-T04's own identical
  confirmation for ITS OWN predecessors.
- The reviewer_resolution (2) intermediate-state / duplicate-unsuperseded
  anomaly -> ``test_duplicate_unsuperseded_releases_anomaly_is_detected``.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import (
    Claim,
    CorrectionEvent,
    EvidenceAnchor,
    RunManifest,
    SourceRecord,
    TaskBundle,
)
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as bind_claim_edge_uow
from mrr.services.claim.service import bind_unit_of_work as bind_claim_uow
from mrr.services.cli.main import main as mrr_main
from mrr.services.correction.service import CorrectionImpactService
from mrr.services.correction.service import bind_unit_of_work as bind_correction_uow
from mrr.services.evidence.service import EvidenceAnchorService, SourceRecordService
from mrr.services.evidence.service import bind_unit_of_work as bind_evidence_uow
from mrr.services.node_runtime.evidence_crate import EvidenceCrateSealer
from mrr.services.node_runtime.evidence_crate import bind_unit_of_work as bind_crate_uow
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from mrr.services.node_runtime.run_manifest import bind_unit_of_work as bind_manifest_uow
from sqlalchemy import Engine

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"
ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"

_POLICY_VERSION = "policy-e8-t05-supersede-test"


def _require_test_database_url_or_skip() -> str:
    base_url = os.environ.get(_TEST_DATABASE_URL_ENV_VAR)
    if base_url:
        return base_url
    if os.environ.get("CI"):
        pytest.fail(
            f"{_TEST_DATABASE_URL_ENV_VAR} is unset in CI — an integration test run without a "
            "real PostgreSQL database must never look green."
        )
    pytest.skip(reason=f"no PostgreSQL available ({_TEST_DATABASE_URL_ENV_VAR} unset)")


def _schema_scoped_url(base_url: str, schema: str) -> str:
    options_value = quote(f"-c search_path={schema}", safe="")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={options_value}"


def _run_alembic_upgrade_head(database_url: str) -> None:
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_cfg.attributes[_ATTRIBUTES_URL_KEY] = database_url
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
def postgres_url() -> Iterator[str]:
    base_url = _require_test_database_url_or_skip()
    schema = f"mrr_test_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(base_url)
    try:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        scoped_url = _schema_scoped_url(base_url, schema)
        _run_alembic_upgrade_head(scoped_url)
        yield scoped_url
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


# ---------------------------------------------------------------------------
# Fixture factories — a byte-for-byte reuse of test_release_cli_create_and_
# verify.py's own helpers (E8-T04), trimmed to this module's own needs, plus
# this module's own new `_record_correction`.
# ---------------------------------------------------------------------------


def _kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actor": new_urn("agent"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def _source_record(**overrides: Any) -> SourceRecord:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("source-record"),
        "api_version": "mrr/v1alpha1",
        "kind": "SourceRecord",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "a" * 64,
        "identifiers": {"doi": "10.1234/e8-t05.fixture"},
        "title": "E8-T05 supersede-test fixture source",
        "creators": ["Example Research Collective"],
        "publication_date": "2026-01-15",
        "version": "1.0",
        "retrieval_timestamp": now,
        "retrieval_method": "HTTP GET, publisher API",
        "snapshot_artifact_hash": "sha256:" + "6" * 64,
        "source_type": "journal-article",
        "primary_secondary_derived": "primary",
        "source_family_id": None,
        "derivation_evidence": None,
        "accessibility": {"access_type": "open_access"},
        "licensing": {"license_id": "CC-BY-4.0"},
    }
    data.update(overrides)
    return SourceRecord.model_validate(data)


def _evidence_anchor(*, source_record_id: str, **overrides: Any) -> EvidenceAnchor:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "8" * 64,
        "relation": "supports",
        "anchor_kind": "text",
        "extraction_method": "manual quotation with paragraph locator",
        "extractor_id": new_urn("agent"),
        "anchor_validation_status": "validated",
        "anchor_unavailable_reason": None,
        "source_record_id": source_record_id,
        "snapshot_hash": "sha256:" + "6" * 64,
        "locator": {"page": 4, "section": "Results", "paragraph": 2},
        "quoted_fragment_hash": "sha256:" + "5" * 64,
        "run_id": None,
        "output_artifact": None,
        "selector": None,
        "transformation_chain": [],
        "recomputation_status": None,
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


def _claim(**overrides: Any) -> Claim:
    data: dict[str, Any] = {
        "id": new_urn("claim"),
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "assertion": "The fixture claim's own assertion text, for E8-T05's supersede test.",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "source_family_ids": [],
        "uncertainty": [],
        "known_unknowns": [],
        "proposer_id": new_urn("agent-role"),
        "verification_ids": [],
        "correction_ids": [],
    }
    data.update(overrides)
    return Claim.model_validate(data)


def _claim_service_for(engine: Engine) -> ClaimService:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)
    claim_record = bind_claim_uow(engine, object_repository, event_log)
    claim_record_edge = bind_claim_edge_uow(engine, event_log)
    return ClaimService(
        object_repository, event_log, edge_repository, claim_record, claim_record_edge
    )


def _bundle(*, revision: int = 1) -> TaskBundle:
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
    )


def _execution_result(bundle: TaskBundle, *, output_hash: str) -> ExecutionResult:
    return ExecutionResult(
        outcome="completed",
        output=b"x",
        output_hash=output_hash,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=0.5),
        detail=None,
    )


def _run_manifest_for(engine: Engine, bundle: TaskBundle, result: ExecutionResult) -> RunManifest:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_manifest_uow(engine, object_repository, event_log)
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
        "node_key_id": "node-key-e8-t05",
        "signer_practice_id": new_urn("practice"),
        "actor": new_urn("executor"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


@dataclass(frozen=True, slots=True)
class SeededGraph:
    crate_id: str
    claim_id: str


def _seed_graph(postgres_url: str) -> SeededGraph:
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)

        evidence_record = bind_evidence_uow(engine, object_repository, event_log)
        source_service = SourceRecordService(evidence_record)
        anchor_service = EvidenceAnchorService(evidence_record)
        source_record = _source_record()
        source_service.create(source_record, **_kwargs())

        anchor = _evidence_anchor(source_record_id=source_record.id)
        anchor_service.create(anchor, **_kwargs())

        claim_service = _claim_service_for(engine)
        claim = _claim()
        claim_service.create(claim, **_kwargs())
        claim_service.add_evidence_edge(claim.id, anchor.id, "supports", **_kwargs())

        bundle = _bundle()
        result = _execution_result(bundle, output_hash="sha256:" + "e" * 64)
        run_manifest = _run_manifest_for(engine, bundle, result)

        crate_record = bind_crate_uow(engine, object_repository, event_log)
        sealer = EvidenceCrateSealer(crate_record)
        stored_crate = sealer.seal(
            run_manifest,
            result,
            bundle,
            source_records=[source_record.id],
            evidence_anchors=[anchor.id],
            proposed_claims=[claim.id],
            **_seal_kwargs(),
        )
    finally:
        engine.dispose()

    return SeededGraph(crate_id=stored_crate.id, claim_id=claim.id)


def _record_correction(postgres_url: str, *, target_claim_id: str, created_at: datetime) -> str:
    """Record a real ``CorrectionEvent`` against ``target_claim_id`` (E8-T05's
    own new fixture addition, for AT2's corrections_affect_this_release
    flow) via the real ``CorrectionImpactService.record`` — the same service
    E3-T06's own suite already exercises. Returns the correction's own id.
    """
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)
        edge_repository = PostgresEdgeRepository(engine)
        claim_service = _claim_service_for(engine)
        record = bind_correction_uow(engine, object_repository, event_log)
        service = CorrectionImpactService(
            object_repository, edge_repository, claim_service, event_log, record
        )
        correction = CorrectionEvent.model_validate(
            {
                "id": new_urn("correction"),
                "api_version": "mrr/v1alpha1",
                "kind": "CorrectionEvent",
                "practice_id": new_urn("practice"),
                "revision": 1,
                "created_at": created_at,
                "created_by": new_urn("agent-role"),
                "content_hash": "sha256:" + "a" * 64,
                "affected_objects": [{"id": target_claim_id, "content_hash": "sha256:" + "a" * 64}],
                "correction_type": "numeric_error",
                "severity": "critical",
                "reason": "The underlying percentage was recomputed and found materially wrong.",
                "evidence_refs": [],
                "originator_id": new_urn("agent-role"),
                "requested_action": "Recompute the affected claim and re-verify it.",
                "status": "OPEN",
                "impact_objects": [],
            }
        )
        stored = service.record(correction, **_kwargs())
    finally:
        engine.dispose()
    return stored.id


def _create_args(
    *,
    postgres_url: str,
    artifact_root: Path,
    crate_id: str,
    output_dir: Path,
    approved_by: str,
    approval_statement_file: Path,
    disclosure: str = "internal",
) -> list[str]:
    return [
        "release",
        "create",
        "--database-url",
        postgres_url,
        "--artifact-root",
        str(artifact_root),
        "--crate-id",
        crate_id,
        "--disclosure",
        disclosure,
        "--output-dir",
        str(output_dir),
        "--policy-version",
        _POLICY_VERSION,
        "--approved-by",
        approved_by,
        "--approval-statement-file",
        str(approval_statement_file),
        "--approval-mode",
        "single_human",
    ]


def _supersede_args(
    *,
    postgres_url: str,
    artifact_root: Path,
    crate_id: str,
    output_dir: Path,
    supersedes: str,
    approved_by: str | None,
    approval_statement_file: Path | None,
    disclosure: str = "internal",
) -> list[str]:
    argv = [
        "release",
        "supersede",
        "--database-url",
        postgres_url,
        "--artifact-root",
        str(artifact_root),
        "--crate-id",
        crate_id,
        "--disclosure",
        disclosure,
        "--output-dir",
        str(output_dir),
        "--policy-version",
        _POLICY_VERSION,
        "--supersedes",
        supersedes,
    ]
    if approved_by is not None:
        argv.extend(["--approved-by", approved_by])
    if approval_statement_file is not None:
        argv.extend(["--approval-statement-file", str(approval_statement_file)])
    argv.extend(["--approval-mode", "single_human"])
    return argv


def _status_args(*, postgres_url: str, release_id: str) -> list[str]:
    return ["release", "status", "--database-url", postgres_url, "--release-id", release_id]


def _verify_args(*, postgres_url: str, release_id: str, bundle_dir: Path) -> list[str]:
    return [
        "release",
        "verify",
        "--database-url",
        postgres_url,
        "--release-id",
        release_id,
        "--bundle-dir",
        str(bundle_dir),
    ]


def _write_statement(tmp_path: Path, text: str = "Approving this release.") -> Path:
    statement_file = tmp_path / f"statement-{uuid.uuid4().hex}.txt"
    statement_file.write_text(text, encoding="utf-8")
    return statement_file


# ---------------------------------------------------------------------------
# AT1: supersede then status, both sides.
# ---------------------------------------------------------------------------


def test_supersede_then_status_reports_superseded_and_current(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-a",
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    release_a_id = json.loads(capsys.readouterr().out)["release_id"]

    approver_b = new_urn("person")
    exit_code = mrr_main(
        _supersede_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-b",
            supersedes=release_a_id,
            approved_by=approver_b,
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    supersede_result = json.loads(capsys.readouterr().out)
    release_b_id = supersede_result["release_id"]
    assert supersede_result["supersedes"] == release_a_id
    assert supersede_result["supersedes_status"] == "superseded"
    assert supersede_result["supersedes_revision"] == 2

    # --- Direct DB check: A's latest revision, and the release.superseded
    #     event's own actor (ADR-0011-style verification, mirroring
    #     E8-T04's own "actor equals approver" direct check).
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)
        stored_a = object_repository.get_latest(release_a_id)
        assert stored_a.body["status"] == "superseded"
        assert stored_a.body["labels"]["superseded_by"] == release_b_id

        superseded_events = [
            appended
            for appended in event_log.read_all()
            if appended.event.event_type == "release.superseded"
            and appended.event.object_id == release_a_id
        ]
        assert len(superseded_events) == 1
        assert superseded_events[0].event.actor == approver_b
    finally:
        engine.dispose()

    # --- status: A -> superseded naming B; B -> current.
    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_a_id))
    assert exit_code == 0
    status_a = json.loads(capsys.readouterr().out)
    assert status_a["verdict"] == "superseded"
    assert status_a["superseded_by"] == release_b_id
    assert status_a["release_id"] == release_a_id

    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_b_id))
    assert exit_code == 0
    status_b = json.loads(capsys.readouterr().out)
    assert status_b["verdict"] == "current"
    assert "superseded_by" not in status_b


# ---------------------------------------------------------------------------
# AT2: corrections_affect_this_release flow.
# ---------------------------------------------------------------------------


def test_correction_after_release_against_a_claim_in_closure_flips_status_to_corrections_affect(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release",
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    release_id = json.loads(capsys.readouterr().out)["release_id"]

    correction_id = _record_correction(
        postgres_url,
        target_claim_id=graph.claim_id,
        created_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_id))
    assert exit_code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["verdict"] == "corrections_affect_this_release"
    assert len(status["affecting_corrections"]) == 1
    row = status["affecting_corrections"][0]
    assert row["correction_id"] == correction_id
    assert row["intersecting_object_ids"] == [graph.claim_id]


def test_correction_before_release_does_not_flip_status(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    # Record the correction FIRST, then release — so its created_at is
    # necessarily earlier than the release's own.
    correction_created_at = datetime.now(UTC)
    _record_correction(
        postgres_url, target_claim_id=graph.claim_id, created_at=correction_created_at
    )

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release",
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    release_id = json.loads(capsys.readouterr().out)["release_id"]

    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_id))
    assert exit_code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["verdict"] == "current"
    assert "affecting_corrections" not in status


def test_correction_against_an_unrelated_object_does_not_flip_status(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    unrelated_graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release",
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    release_id = json.loads(capsys.readouterr().out)["release_id"]

    _record_correction(
        postgres_url,
        target_claim_id=unrelated_graph.claim_id,
        created_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_id))
    assert exit_code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["verdict"] == "current"


# ---------------------------------------------------------------------------
# AT4: historical-bundle immutability.
# ---------------------------------------------------------------------------


def test_verify_bundle_dir_against_pre_supersession_bundle_still_matches_after_superseding(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir_a = tmp_path / "release-a"

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir_a,
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    create_result = json.loads(capsys.readouterr().out)
    release_a_id = create_result["release_id"]
    original_root_hash = create_result["root_hash"]

    # Capture every content byte under output_dir_a BEFORE superseding.
    pre_supersession_bytes = {
        str(path.relative_to(output_dir_a)): path.read_bytes()
        for path in sorted(output_dir_a.rglob("*"))
        if path.is_file()
    }

    exit_code = mrr_main(
        _supersede_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-b",
            supersedes=release_a_id,
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    capsys.readouterr()  # discard the supersede command's own JSON line

    # The bundle directory's own bytes are untouched — mechanically.
    post_supersession_bytes = {
        str(path.relative_to(output_dir_a)): path.read_bytes()
        for path in sorted(output_dir_a.rglob("*"))
        if path.is_file()
    }
    assert post_supersession_bytes == pre_supersession_bytes

    exit_code = mrr_main(
        _verify_args(postgres_url=postgres_url, release_id=release_a_id, bundle_dir=output_dir_a)
    )
    assert exit_code == 0
    verify_result = json.loads(capsys.readouterr().out)
    assert verify_result["matched"] is True
    assert verify_result["root_hash"] == original_root_hash


# ---------------------------------------------------------------------------
# Duplicate-unsuperseded-releases anomaly (reviewer_resolution (2)).
# ---------------------------------------------------------------------------


def test_duplicate_unsuperseded_releases_anomaly_is_detected(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    release_ids = []
    for i in range(2):
        exit_code = mrr_main(
            _create_args(
                postgres_url=postgres_url,
                artifact_root=artifact_root,
                crate_id=graph.crate_id,
                output_dir=tmp_path / f"release-{i}",
                approved_by=new_urn("person"),
                approval_statement_file=_write_statement(tmp_path),
            )
        )
        assert exit_code == 0
        release_ids.append(json.loads(capsys.readouterr().out)["release_id"])

    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_ids[0]))
    assert exit_code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["anomaly"] == "duplicate_unsuperseded_releases"


# ---------------------------------------------------------------------------
# Refusal matrix (AT5).
# ---------------------------------------------------------------------------


def test_supersede_with_agent_role_approver_is_refused_naming_mrr_fr_102(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-a",
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    release_a_id = json.loads(capsys.readouterr().out)["release_id"]

    exit_code = mrr_main(
        _supersede_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-b",
            supersedes=release_a_id,
            approved_by=new_urn("agent-role"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "MRR-FR-102" in err
    assert not (tmp_path / "release-b").exists()

    # Nothing was superseded — A is still "released".
    exit_code = mrr_main(_status_args(postgres_url=postgres_url, release_id=release_a_id))
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "current"


def test_supersede_with_unknown_supersedes_id_names_the_intermediate_state(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    unknown_release_id = new_urn("release-record")

    exit_code = mrr_main(
        _supersede_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-new",
            supersedes=unknown_release_id,
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "ONE known inconsistent state" in err
    assert unknown_release_id in err
    # The new release WAS persisted and its bundle directory WAS written —
    # this is the point of the named intermediate state.
    assert (tmp_path / "release-new").exists()
    assert (tmp_path / "release-new" / "release-record.json").is_file()


def test_supersede_against_an_already_superseded_release_names_the_intermediate_state(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-a",
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0
    release_a_id = json.loads(capsys.readouterr().out)["release_id"]

    exit_code = mrr_main(
        _supersede_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-b",
            supersedes=release_a_id,
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 0

    # A second attempt to supersede the NOW-already-superseded A: the new
    # release C is created (step 1 succeeds), then step 2 fails because A
    # is already superseded — exactly the one named intermediate state.
    exit_code = mrr_main(
        _supersede_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=tmp_path / "release-c",
            supersedes=release_a_id,
            approved_by=new_urn("person"),
            approval_statement_file=_write_statement(tmp_path),
        )
    )
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "ONE known inconsistent state" in err
    assert (tmp_path / "release-c").exists()


def test_status_of_an_unknown_release_id_is_refused(
    postgres_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = mrr_main(
        _status_args(postgres_url=postgres_url, release_id=new_urn("release-record"))
    )
    assert exit_code == 3
