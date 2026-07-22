"""Integration tests for ``mrr report render`` (task-packets/E8-T03.yaml),
driven end to end through the REAL console-script entry point
(``mrr.services.cli.main.main``, exactly as
``tests/integration/services/test_export_cli_ro_crate.py`` already exercises
``mrr export ro-crate``) against a real, throwaway PostgreSQL test schema.

Uses a LOCAL ``postgres_url`` fixture and per-fixture-factory helpers — this
codebase's own established convention of duplicating this exact shape per
integration test module rather than sharing one (see
``test_export_cli_ro_crate.py``'s own docstring for the rationale). This
module's own ``_seed_graph`` builds a SMALLER graph than that module's own
(no PROV-specific derived_from/field-reference wiring is needed here — the
report never touches ``prov:`` at all) but EXTENDS it with exactly what
task-packets/E8-T03.yaml R6 asks this packet's own integration fixture to
add beyond the export precedent: a SECOND, disagreeing ``VerificationResult``
on the SAME claim (one ``pass``, one ``fail``, both via the REAL ``mrr
verification record`` CLI path — the K1-T05 precedent, not
``VerificationService`` called directly), and one unresolved CRITICAL
``CorrectionEvent`` naming that claim (via the REAL ``CorrectionImpactService
.record`` — E3-T06's own service, not a raw insert; ``record()`` alone is
sufficient, since correction DISCOVERY (``ProjectionService
._read_correction_bodies``) scans the event log for
``"correction.recorded"``, which ``record()`` always appends —
``propagate_impact()`` is not needed and is not called here). The crate also
carries one crate-level known unknown and one crate-level failure, so R1(6)/
(7) render something non-empty in this fixture too.

Acceptance-test mapping, integration tier (task-packets/E8-T03.yaml):

- AT1 (all four format x disclosure combinations render successfully, the
  disagreement is visible and marked in all four, the correction is visible
  in all four) -> ``test_all_four_format_disclosure_combinations_render_the_
  disagreement_and_correction``.
- AT2 (public + empty attestation shows structure and redaction markers,
  never a stored assertion/finding/summary byte; public + the claim
  attested PUBLIC shows its assertion while un-attested finding statements
  stay redacted) -> ``test_public_disclosure_with_empty_attestation_shows_
  structure_never_text``, ``test_public_disclosure_with_claim_attested_
  public_shows_assertion_but_not_unattested_findings``.
- AT5's refusal/dependency paths that genuinely need a real object store
  (unknown crate id, non-crate kind) -> ``test_unknown_crate_id_produces_
  exit_3_naming_the_crate_id``, ``test_crate_id_resolving_to_a_non_crate_
  kind_is_refused``. AT5's DB-free halves (pre-existing output, missing/
  forbidden classification file, unreachable database) live at the unit
  tier instead (``tests/unit/cli/test_report_cli_args.py``), mirroring
  ``test_export_cli_ro_crate.py``'s own identical tier split.
  ``test_export_cli_ro_crate.py``'s AND ``test_prov_mapping``-adjacent
  suites are asserted to pass UNMODIFIED separately (they are not touched by
  this file at all) — see this packet's own report for that confirmation.
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
    FailureEntry,
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

_POLICY_VERSION = "policy-e8-t03-report-test"


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
    applied) — mirrors ``test_export_cli_ro_crate.py``'s own identically-
    named, identically-shaped fixture.
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
# Fixture factories — mirrors test_export_cli_ro_crate.py's own helpers,
# trimmed to what this packet's own report fixture needs (no PROV wiring).
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
        "identifiers": {"doi": "10.1234/e8-t03.fixture"},
        "title": "E8-T03 report-test fixture source",
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
        "assertion": "The fixture claim's own assertion text, under test for redaction.",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "source_family_ids": [],
        "uncertainty": [{"kind": "sampling", "statement": "small sample size", "method": None}],
        "known_unknowns": ["Whether the effect replicates in a larger sample."],
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


def _correction_service_for(engine: Engine, claim_service: ClaimService) -> CorrectionImpactService:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)
    record = bind_correction_uow(engine, object_repository, event_log)
    return CorrectionImpactService(
        object_repository, edge_repository, claim_service, event_log, record
    )


def _correction(*, affected_object_id: str, content_hash: str, **overrides: Any) -> CorrectionEvent:
    data: dict[str, Any] = {
        "id": new_urn("correction"),
        "api_version": "mrr/v1alpha1",
        "kind": "CorrectionEvent",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("person"),
        "content_hash": "sha256:" + "b" * 64,
        "affected_objects": [{"id": affected_object_id, "content_hash": content_hash}],
        "correction_type": "numeric_error",
        "severity": "critical",
        "reason": "The fixture's own reported percentage was later shown to be miscalculated.",
        "evidence_refs": [new_urn("evidence-anchor")],
        "originator_id": new_urn("person"),
        "requested_action": "Recompute the affected claim and re-verify it independently.",
        "replacement_object_id": None,
        "status": "OPEN",
        "impact_objects": [],
    }
    data.update(overrides)
    return CorrectionEvent.model_validate(data)


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
        "node_key_id": "node-key-e8-t03",
        "signer_practice_id": new_urn("practice"),
        "actor": new_urn("executor"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def _independence_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "principal": new_urn("person"),
        "model_family": "human-reviewer (no model invoked)",
        "prompt_family": "n/a — manual review checklist v3",
        "retrieval_path": "independent re-fetch via publisher API, not the original crawl",
        "code_path": "independent recomputation script, not the original analysis notebook",
        "data_access_path": "read-only snapshot corpus, separate credential from the proposer's",
    }
    profile.update(overrides)
    return profile


def _verification_payload(
    *, verification_id: str, target_id: str, reviewer_id: str, recommendation: str, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": verification_id,
        "api_version": "mrr/v1alpha1",
        "kind": "VerificationResult",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": reviewer_id,
        "content_hash": "sha256:" + "b" * 64,
        "target_id": target_id,
        "target_kind": "claim",
        "reviewer_id": reviewer_id,
        "reviewer_role": "independent reviewer",
        "independence_profile": _independence_profile(),
        "verification_type": "skeptic",
        "checks_performed": ["Searched for counterevidence and alternative explanations"],
        "evidence_inspected": [],
        "numeric_recomputation": None,
        "findings": [{"severity": "minor", "statement": "A fixture finding under test."}],
        "recommendation": recommendation,
        "confidence": 0.7,
        "rationale": "Fixture rationale for the E8-T03 report CLI integration test.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    payload.update(overrides)
    return payload


@dataclass(frozen=True, slots=True)
class SeededGraph:
    """Every id ``_seed_graph`` produces."""

    crate_id: str
    claim_id: str
    source_record_id: str
    evidence_anchor_id: str
    verification_id_pass: str
    verification_id_fail: str
    correction_id: str


def _seed_graph(postgres_url: str, tmp_path: Path) -> SeededGraph:
    """Build the fixture graph described in this module's own docstring
    through real services end to end. No artifact store at all —
    ``EvidenceCrateSealer.seal``'s own ``artifact_refs`` defaults to ``()``
    (schema-valid: ``artifacts`` has no ``minItems``), matching ``mrr
    report render``'s own "never touches artifact bytes" design (see
    ``mrr.services.report.service``'s own module docstring).
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
            known_unknowns=["This crate's own run left the sampling frame under-documented."],
            failures=[
                FailureEntry(
                    code="partial-retrieval",
                    category="source_unavailable",
                    message="One secondary source could not be re-fetched at seal time.",
                )
            ],
            **_seal_kwargs(),
        )

        correction_service = _correction_service_for(engine, claim_service)
        correction = _correction(affected_object_id=claim.id, content_hash=claim.content_hash)
        correction_service.record(correction, **_kwargs())
    finally:
        engine.dispose()

    # Two disagreeing VerificationResults, both via the REAL `mrr
    # verification record` CLI path (K1-T05), targeting the SAME claim.
    verification_id_pass = new_urn("verification")
    verification_id_fail = new_urn("verification")
    for verification_id, recommendation in (
        (verification_id_pass, "pass"),
        (verification_id_fail, "fail"),
    ):
        reviewer_id = new_urn("person")
        payload = _verification_payload(
            verification_id=verification_id,
            target_id=claim.id,
            reviewer_id=reviewer_id,
            recommendation=recommendation,
        )
        verification_file = tmp_path / f"verification-{uuid.uuid4().hex}.json"
        verification_file.write_text(json.dumps(payload), encoding="utf-8")
        exit_code = mrr_main(
            [
                "verification",
                "record",
                "--database-url",
                postgres_url,
                "--verification-file",
                str(verification_file),
                "--claim-id",
                claim.id,
                "--actor",
                new_urn("agent"),
                "--policy-version",
                _POLICY_VERSION,
            ]
        )
        assert exit_code == 0, "fixture setup: verification recording must succeed"

    return SeededGraph(
        crate_id=stored_crate.id,
        claim_id=claim.id,
        source_record_id=source_record.id,
        evidence_anchor_id=anchor.id,
        verification_id_pass=verification_id_pass,
        verification_id_fail=verification_id_fail,
        correction_id=correction.id,
    )


def _report_args(
    *,
    postgres_url: str,
    crate_id: str,
    output: Path,
    fmt: str,
    disclosure: str,
    classification_file: Path | None = None,
) -> list[str]:
    argv = [
        "report",
        "render",
        "--database-url",
        postgres_url,
        "--crate-id",
        crate_id,
        "--output",
        str(output),
        "--format",
        fmt,
        "--disclosure",
        disclosure,
    ]
    if classification_file is not None:
        argv.extend(["--classification-file", str(classification_file)])
    return argv


# ---------------------------------------------------------------------------
# AT1: all four format x disclosure combinations.
# ---------------------------------------------------------------------------


def test_all_four_format_disclosure_combinations_render_the_disagreement_and_correction(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url, tmp_path)
    capsys.readouterr()  # drain _seed_graph's own `verification record` JSON lines

    classification_file = tmp_path / "classification.json"
    classification_file.write_text(
        json.dumps(
            {
                graph.claim_id: "PUBLIC",
                graph.correction_id: "PUBLIC",
                graph.verification_id_pass: "PUBLIC",
                graph.verification_id_fail: "PUBLIC",
            }
        )
    )

    for fmt in ("md", "html"):
        for disclosure in ("internal", "public"):
            output = tmp_path / f"report-{fmt}-{disclosure}.out"
            args = _report_args(
                postgres_url=postgres_url,
                crate_id=graph.crate_id,
                output=output,
                fmt=fmt,
                disclosure=disclosure,
                classification_file=classification_file if disclosure == "public" else None,
            )
            exit_code = mrr_main(args)
            assert exit_code == 0, f"format={fmt} disclosure={disclosure}: exit {exit_code}"

            result_line = json.loads(capsys.readouterr().out)
            assert result_line["crate_id"] == graph.crate_id
            assert result_line["format"] == fmt
            assert result_line["disclosure"] == disclosure
            assert result_line["section_counts"]["claims"] == 1
            assert result_line["section_counts"]["corrections"] == 1

            rendered = output.read_text(encoding="utf-8")
            assert graph.verification_id_pass in rendered
            assert graph.verification_id_fail in rendered
            assert graph.correction_id in rendered
            assert "UNRESOLVED CRITICAL" in rendered
            disagreement_marker = (
                "DISAGREEMENT ON RECORD" if fmt == "md" else "disagreement on record"
            )
            assert rendered.count(disagreement_marker) == 2, (
                f"expected both disagreeing rows marked, format={fmt} disclosure={disclosure}"
            )


# ---------------------------------------------------------------------------
# AT2: public disclosure fail-closed granularity.
# ---------------------------------------------------------------------------


def test_public_disclosure_with_empty_attestation_shows_structure_never_text(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url, tmp_path)
    capsys.readouterr()

    classification_file = tmp_path / "empty-classification.json"
    classification_file.write_text(json.dumps({}))
    output = tmp_path / "report-public-empty.md"

    exit_code = mrr_main(
        _report_args(
            postgres_url=postgres_url,
            crate_id=graph.crate_id,
            output=output,
            fmt="md",
            disclosure="public",
            classification_file=classification_file,
        )
    )
    assert exit_code == 0
    rendered = output.read_text(encoding="utf-8")

    # Structure: always visible.
    assert graph.claim_id in rendered
    assert graph.correction_id in rendered
    assert "OPEN" in rendered
    assert "UNRESOLVED CRITICAL" in rendered

    # Free text: never visible under empty attestation.
    assert "The fixture claim's own assertion text" not in rendered
    assert "A fixture finding under test" not in rendered
    assert "later shown to be miscalculated" not in rendered
    assert "Recompute the affected claim" not in rendered
    assert "[redacted: not attested PUBLIC]" in rendered


def test_public_disclosure_with_claim_attested_public_shows_assertion_but_not_unattested_findings(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_graph(postgres_url, tmp_path)
    capsys.readouterr()

    # The claim's own assertion is fail-closed on BOTH its own id AND every one
    # of its unresolved_correction_ids (mrr.domain.public_correction_view
    # .build_public_claim_row's own documented rule) — the claim is flagged by
    # graph.correction_id, so unlocking the assertion requires attesting BOTH,
    # not the claim id alone. The verifications' own ids are deliberately left
    # UN-attested, so their finding statements stay redacted — proving the
    # fail-closed check is granular per object id, not "any one attestation
    # unlocks everything" (AT2).
    classification_file = tmp_path / "partial-classification.json"
    classification_file.write_text(
        json.dumps({graph.claim_id: "PUBLIC", graph.correction_id: "PUBLIC"})
    )
    output = tmp_path / "report-public-partial.md"

    exit_code = mrr_main(
        _report_args(
            postgres_url=postgres_url,
            crate_id=graph.crate_id,
            output=output,
            fmt="md",
            disclosure="public",
            classification_file=classification_file,
        )
    )
    assert exit_code == 0
    rendered = output.read_text(encoding="utf-8")

    assert "The fixture claim's own assertion text" in rendered
    # The verifications' own ids are not attested PUBLIC -> finding statements stay redacted.
    assert "A fixture finding under test" not in rendered
    # The correction's own reason/requested_action ARE now attested PUBLIC too.
    assert "later shown to be miscalculated" in rendered
    assert "[redacted: not attested PUBLIC]" in rendered


# ---------------------------------------------------------------------------
# AT5 (the halves that need a real object store).
# ---------------------------------------------------------------------------


def test_unknown_crate_id_produces_exit_3_naming_the_crate_id(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    unknown_crate_id = new_urn("evidence-crate")

    exit_code = mrr_main(
        _report_args(
            postgres_url=postgres_url,
            crate_id=unknown_crate_id,
            output=output,
            fmt="md",
            disclosure="internal",
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "ObjectNotFoundError" in err
    assert unknown_crate_id in err
    assert not output.exists()


def test_crate_id_resolving_to_a_non_crate_kind_is_refused(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        claim_service = _claim_service_for(engine)
        claim = _claim()
        claim_service.create(claim, **_kwargs())
    finally:
        engine.dispose()

    output = tmp_path / "report.md"

    exit_code = mrr_main(
        _report_args(
            postgres_url=postgres_url,
            crate_id=claim.id,
            output=output,
            fmt="md",
            disclosure="internal",
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "Claim" in err
    assert not output.exists()
