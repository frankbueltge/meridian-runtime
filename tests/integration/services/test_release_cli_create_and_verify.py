"""Integration tests for ``mrr release create``/``mrr release verify``
(docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md /
task-packets/E8-T04.yaml), driven end to end through the REAL console-script
entry point (``mrr.services.cli.main.main``, exactly as
``tests/integration/services/test_export_cli_ro_crate.py``/
``test_report_cli_render.py`` already exercise ``mrr export ro-crate``/
``mrr report render``) against a real, throwaway PostgreSQL test schema AND a
real ``mrr.adapters.object_store.local.LocalFilesystemArtifactStore``.

Uses a LOCAL ``postgres_url`` fixture and per-fixture-factory helpers — this
codebase's own established convention of duplicating this exact shape per
integration test module (see ``test_export_cli_ro_crate.py``'s own docstring
for the rationale). ``_seed_graph`` builds a small, REAL graph — a source
record, an evidence anchor, a claim, a run manifest, and a sealed
``EvidenceCrate`` — through the actual E3/E2 services this codebase ships,
mirroring ``test_report_cli_render.py``'s own minimal fixture shape (no
disagreement/correction wiring is needed here — the release bundle carries
whatever ``ReportService`` renders, already proven correct by E8-T03's own
suite; this module tests the RELEASE mechanism, not the report content).
task-packets/E8-T04.yaml's own reviewer_resolution (1): every ``ReleaseRecord``
this module creates uses a SYNTHETIC person URN approver in a THROWAWAY
schema — nothing real is ever released.

Acceptance-test mapping (task-packets/E8-T04.yaml):

- AT1 -> ``test_create_with_full_synthetic_approval_then_verify_both_modes_match``.
- AT2 -> ``test_create_without_approved_by_is_refused_naming_mrr_fr_102``,
  ``test_create_without_approval_statement_file_is_refused_naming_mrr_fr_102``,
  ``test_create_with_agent_role_approver_is_refused_naming_mrr_fr_102``,
  ``test_create_with_empty_approval_statement_file_is_refused_naming_mrr_fr_102``.
- AT3 -> ``test_flipped_byte_is_detected_by_bundle_dir_mode_but_not_rebuild_mode``.
- AT4 -> ``test_two_creates_of_the_same_crate_are_content_deterministic``.
- AT5 (E8-T01/T02/T03 suites pass unmodified) is confirmed by running those
  test modules directly, unmodified, alongside this one — see this task's
  own delivery report for the exact command and result.
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
from mrr.contracts import Claim, EvidenceAnchor, RunManifest, SourceRecord, TaskBundle
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

_POLICY_VERSION = "policy-e8-t04-release-test"


def _require_test_database_url_or_skip() -> str:
    """A local copy of tests/integration/conftest.py's identical helper —
    see that module's own docstring for why this is duplicated rather than
    imported (this codebase's own established convention).
    """
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
    """Yields a schema-scoped ``--database-url`` STRING (migrations already
    applied) — mirrors ``test_export_cli_ro_crate.py``'s/
    ``test_report_cli_render.py``'s own identically-named, identically-
    shaped fixture. THROWAWAY schema, dropped afterward — reviewer_resolution
    (1): this records nothing real.
    """
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
# Fixture factories — trimmed from test_report_cli_render.py's own helpers
# to the minimum this module's own release-mechanism tests need.
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
        "identifiers": {"doi": "10.1234/e8-t04.fixture"},
        "title": "E8-T04 release-test fixture source",
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
        "assertion": "The fixture claim's own assertion text, for E8-T04's release-mechanism test.",
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
        "node_key_id": "node-key-e8-t04",
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
    """Build the minimal graph described in this module's own docstring
    through real services end to end. No artifact store at all —
    ``EvidenceCrateSealer.seal``'s own ``artifact_refs`` defaults to ``()``,
    matching ``mrr report render``'s own established "never touches
    artifact bytes" fixture precedent (``ExportService.export`` still
    accepts an empty artifact set fine; ``mrr release create``'s own
    ``--artifact-root`` just needs to exist as a readable, empty directory).
    """
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


def _create_args(
    *,
    postgres_url: str,
    artifact_root: Path,
    crate_id: str,
    output_dir: Path,
    approved_by: str | None,
    approval_statement_file: Path | None,
    approval_mode: str | None,
    disclosure: str = "internal",
) -> list[str]:
    argv = [
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
    ]
    if approved_by is not None:
        argv.extend(["--approved-by", approved_by])
    if approval_statement_file is not None:
        argv.extend(["--approval-statement-file", str(approval_statement_file)])
    if approval_mode is not None:
        argv.extend(["--approval-mode", approval_mode])
    return argv


def _verify_args(
    *,
    postgres_url: str,
    release_id: str,
    artifact_root: Path | None = None,
    bundle_dir: Path | None = None,
) -> list[str]:
    argv = ["release", "verify", "--database-url", postgres_url, "--release-id", release_id]
    if artifact_root is not None:
        argv.extend(["--artifact-root", str(artifact_root)])
    if bundle_dir is not None:
        argv.extend(["--bundle-dir", str(bundle_dir)])
    return argv


def _bundle_content_files(output_dir: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(output_dir)): path.read_bytes()
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name not in {"release-manifest.json", "release-record.json"}
    }


# ---------------------------------------------------------------------------
# AT1: full synthetic approval, then both verify modes.
# ---------------------------------------------------------------------------


def test_create_with_full_synthetic_approval_then_verify_both_modes_match(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "release"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text(
        "Reviewed the bundled report and RO-Crate export against the sealed crate; "
        "approving this synthetic, throwaway-schema release for E8-T04's own integration test.",
        encoding="utf-8",
    )
    approved_by = new_urn("person")

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
            approved_by=approved_by,
            approval_statement_file=statement_file,
            approval_mode="single_human",
        )
    )
    assert exit_code == 0

    result_line = json.loads(capsys.readouterr().out)
    assert result_line["crate_id"] == graph.crate_id
    assert result_line["disclosure"] == "internal"
    assert result_line["approval_mode"] == "single_human"
    assert result_line["revision"] == 1
    release_id = result_line["release_id"]

    # --- Bundle directory shape (R3).
    assert (output_dir / "ro-crate" / "ro-crate-metadata.json").is_file()
    assert (output_dir / "report.md").is_file()
    assert (output_dir / "report.html").is_file()
    assert (output_dir / "release-manifest.json").is_file()
    assert (output_dir / "release-record.json").is_file()

    manifest = json.loads((output_dir / "release-manifest.json").read_text())
    assert manifest["root_hash"] == result_line["root_hash"]
    assert manifest["files"] == sorted(manifest["files"], key=lambda f: f["path"])
    manifest_paths = {f["path"] for f in manifest["files"]}
    assert "release-manifest.json" not in manifest_paths
    assert "release-record.json" not in manifest_paths

    # --- release-record.json is the persisted record, verbatim (derived_decisions (c)).
    record_body = json.loads((output_dir / "release-record.json").read_text())
    assert record_body["id"] == release_id
    assert record_body["kind"] == "ReleaseRecord"
    assert record_body["crate_id"] == graph.crate_id
    assert record_body["approval"]["approved_by"] == approved_by
    assert record_body["approval"]["approval_mode"] == "single_human"
    assert record_body["status"] == "released"
    assert record_body["bundle"]["root_hash"] == result_line["root_hash"]

    # --- The release.approved event's own actor equals the approver (ADR-0011 decision 2).
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)
        stored = object_repository.get_latest(release_id)
        assert stored.body["approval"]["approved_by"] == approved_by

        release_approved_events = [
            appended
            for appended in event_log.read_all()
            if appended.event.event_type == "release.approved"
            and appended.event.object_id == release_id
        ]
        assert len(release_approved_events) == 1
        assert release_approved_events[0].event.actor == approved_by
    finally:
        engine.dispose()

    # --- verify: rebuild mode.
    exit_code = mrr_main(
        _verify_args(postgres_url=postgres_url, release_id=release_id, artifact_root=artifact_root)
    )
    assert exit_code == 0
    rebuild_result = json.loads(capsys.readouterr().out)
    assert rebuild_result["mode"] == "rebuild"
    assert rebuild_result["matched"] is True
    assert rebuild_result["root_hash"] == result_line["root_hash"]

    # --- verify: --bundle-dir mode.
    exit_code = mrr_main(
        _verify_args(postgres_url=postgres_url, release_id=release_id, bundle_dir=output_dir)
    )
    assert exit_code == 0
    bundle_dir_result = json.loads(capsys.readouterr().out)
    assert bundle_dir_result["mode"] == "bundle-dir"
    assert bundle_dir_result["matched"] is True
    assert bundle_dir_result["root_hash"] == result_line["root_hash"]


# ---------------------------------------------------------------------------
# AT2: refusal paths, each naming MRR-FR-102, nothing persisted, no directory.
# ---------------------------------------------------------------------------


def test_create_without_approved_by_is_refused_naming_mrr_fr_102(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "release"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving.", encoding="utf-8")

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
            approved_by=None,
            approval_statement_file=statement_file,
            approval_mode="single_human",
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_create_without_approval_statement_file_is_refused_naming_mrr_fr_102(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "release"

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
            approved_by=new_urn("person"),
            approval_statement_file=None,
            approval_mode="single_human",
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_create_with_agent_role_approver_is_refused_naming_mrr_fr_102(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "release"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving.", encoding="utf-8")

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
            approved_by=new_urn("agent-role"),
            approval_statement_file=statement_file,
            approval_mode="single_human",
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_create_with_empty_approval_statement_file_is_refused_naming_mrr_fr_102(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "release"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("   \n  \t ", encoding="utf-8")

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
            approved_by=new_urn("person"),
            approval_statement_file=statement_file,
            approval_mode="single_human",
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


# ---------------------------------------------------------------------------
# AT3: flipped byte detection.
# ---------------------------------------------------------------------------


def test_flipped_byte_is_detected_by_bundle_dir_mode_but_not_rebuild_mode(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "release"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this synthetic release.", encoding="utf-8")

    exit_code = mrr_main(
        _create_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
            approved_by=new_urn("person"),
            approval_statement_file=statement_file,
            approval_mode="single_human",
        )
    )
    assert exit_code == 0
    release_id = json.loads(capsys.readouterr().out)["release_id"]

    target = output_dir / "report.md"
    original = target.read_bytes()
    target.write_bytes(bytes([original[0] ^ 0x01]) + original[1:])
    flipped_path = "report.md"

    exit_code = mrr_main(
        _verify_args(postgres_url=postgres_url, release_id=release_id, bundle_dir=output_dir)
    )
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "MISMATCH" in err
    assert flipped_path in err

    exit_code = mrr_main(
        _verify_args(postgres_url=postgres_url, release_id=release_id, artifact_root=artifact_root)
    )
    assert exit_code == 0
    rebuild_result = json.loads(capsys.readouterr().out)
    assert rebuild_result["matched"] is True


# ---------------------------------------------------------------------------
# AT4: determinism.
# ---------------------------------------------------------------------------


def test_two_creates_of_the_same_crate_are_content_deterministic(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this synthetic release, twice.", encoding="utf-8")

    output_dir_a = tmp_path / "release-a"
    output_dir_b = tmp_path / "release-b"
    # Same approval INPUTS for both creates (task-packets/E8-T04.yaml AT4:
    # "two creates of the same crate/disclosure/attestation") — only the
    # record's own minted identity/timestamp/hash may differ between them.
    approved_by = new_urn("person")

    results = []
    for output_dir in (output_dir_a, output_dir_b):
        exit_code = mrr_main(
            _create_args(
                postgres_url=postgres_url,
                artifact_root=artifact_root,
                crate_id=graph.crate_id,
                output_dir=output_dir,
                approved_by=approved_by,
                approval_statement_file=statement_file,
                approval_mode="single_human",
            )
        )
        assert exit_code == 0
        results.append(json.loads(capsys.readouterr().out))

    assert results[0]["root_hash"] == results[1]["root_hash"]
    assert results[0]["release_id"] != results[1]["release_id"]

    content_a = _bundle_content_files(output_dir_a)
    content_b = _bundle_content_files(output_dir_b)
    assert content_a == content_b

    manifest_a = json.loads((output_dir_a / "release-manifest.json").read_bytes())
    manifest_b = json.loads((output_dir_b / "release-manifest.json").read_bytes())
    assert manifest_a == manifest_b

    record_a = json.loads((output_dir_a / "release-record.json").read_bytes())
    record_b = json.loads((output_dir_b / "release-record.json").read_bytes())
    identity_fields = {"id", "created_at", "content_hash"}
    assert {k: v for k, v in record_a.items() if k not in identity_fields} == {
        k: v for k, v in record_b.items() if k not in identity_fields
    }
    for field in identity_fields:
        assert record_a[field] != record_b[field]
