"""Integration tests for the claim-graph-rooted export and report
(task-packets/E8-T06.yaml) — the packet's own R5, "the point of the packet":
an end-to-end reproduction of docs/design/2026-07-22-erste-nutzung-befunde
.md's Befund 2 (a sealed ``EvidenceCrate`` with EMPTY ``proposed_claims``/
``source_records``/``evidence_anchors``, exactly the real K1-T04 run's own
shape) followed by its fix — ``--all-claims``/``--claim-id`` export and
report, driven end to end through the REAL console-script entry point
(``mrr.services.cli.main.main``), against a real, throwaway PostgreSQL test
schema, mirroring ``tests/integration/services/test_export_cli_ro_crate.py``'s
own ``postgres_url``/fixture-factory conventions (duplicated here rather
than imported — this codebase's own established per-test-module precedent).

``_seed_real_shaped_graph`` builds the REAL-shaped graph through the actual
services this codebase ships: two claims (the "Hammond" claim — named after
the real K1-T04 claim this packet's own finding record and required_output
are about — carries ``evidence_relations``/``counterevidence_relations`` to
real anchors, and TWO verifications, a ``pass`` and a ``fail`` — the
disagreement; a second, simpler claim carries one ``pass`` verification),
every anchor's own ``run_id`` left EMPTY (the real K1-T04 fact), and a
SEALED crate whose OWN ``proposed_claims``/``source_records``/
``evidence_anchors`` arrays are ALL EMPTY — reproducing Befund 2 exactly.
``_seed_reachable_run_graph`` is R5's own SECOND, smaller fixture: one claim,
one anchor whose ``run_id`` IS populated, covering R1's "when non-empty"
branch.

Acceptance-test mapping (task-packets/E8-T06.yaml, integration tier):

- AT1 (the real-shaped fixture; crate-rooted = crate only, Befund 2
  reproduced; --all-claims = the full claim graph incl. both verifications)
  -> ``test_crate_rooted_export_of_the_real_shaped_run_yields_only_the_crate``,
  ``test_all_claims_export_yields_the_full_claim_graph_including_both_verifications``.
- R1's "when non-empty" branch (the second, smaller fixture) ->
  ``test_reachable_run_manifest_is_included_when_an_anchor_run_id_resolves``.
- AT2 (claim-rooted report renders the disagreement, both formats/both
  disclosures, header shows root/run/claim count/date) ->
  ``test_claim_rooted_report_marks_the_hammond_disagreement_internal_markdown``,
  ``test_claim_rooted_report_marks_the_hammond_disagreement_public_html``.
- AT3 (crate-rooted byte-identity for a graph whose crate DOES reference its
  claims) ->
  ``test_crate_rooted_and_claim_rooted_closures_agree_when_the_crate_references_its_claims``
  (also satisfies R6's unit-tier-deferred "crate seed and claim seed reach
  identical closures" requirement — see tests/unit/services/export
  /test_service.py's own module docstring for why THAT comparison lives
  here, not there: a sealed crate needs real signing/sealing machinery).
- AT4 (determinism; the only date is max(created_at)) ->
  ``test_two_claim_rooted_exports_of_the_same_schema_are_byte_identical``.
- AT5 (refusals) -> ``test_claim_id_naming_a_non_claim_is_refused_exit_3``,
  ``test_all_claims_over_a_schema_with_zero_claims_refuses_exit_3``.
"""

from __future__ import annotations

import hashlib
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
from mrr.contracts import VerificationResult as VerificationResultContract
from mrr.domain.hashing_policy import compute_content_hash
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
from mrr.services.node_runtime.executor import ExecutionResult, ResourceUsage, TerminalOutcome
from mrr.services.node_runtime.run_manifest import RunManifestRecorder
from mrr.services.node_runtime.run_manifest import bind_unit_of_work as bind_manifest_uow
from sqlalchemy import Engine

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"
ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"

_POLICY_VERSION = "policy-e8-t06-claim-rooted-test"


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
# Fixture factories — mirrors tests/integration/services/test_export_cli_ro
# _crate.py's own identical helpers (duplicated, not imported).
# ---------------------------------------------------------------------------


def _kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actor": new_urn("agent"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def _with_real_content_hash(model_cls: type[Any], data: dict[str, Any]) -> dict[str, Any]:
    draft = model_cls.model_validate({**data, "content_hash": "sha256:" + "0" * 64})
    body = json.loads(draft.model_dump_json(exclude_none=True))
    real_hash = compute_content_hash(body)
    return {**data, "content_hash": real_hash}


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
        "identifiers": {"doi": "10.1234/e8-t06.fixture"},
        "title": "E8-T06 claim-rooted-test fixture source",
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
    return SourceRecord.model_validate(_with_real_content_hash(SourceRecord, data))


def _evidence_anchor(
    *, source_record_id: str, run_id: str | None = None, **overrides: Any
) -> EvidenceAnchor:
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
        "run_id": run_id,
        "output_artifact": None,
        "selector": None,
        "transformation_chain": [],
        "recomputation_status": "reproduced" if run_id else None,
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(_with_real_content_hash(EvidenceAnchor, data))


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
        "assertion": "Does this fixture assertion satisfy the schema's minimum length rule?",
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
        # EMPTY, exactly like the real K1-T04 claims — verifications are
        # discovered via the event log, never via this declared array, in
        # the real data (see mrr.services.export.service's own "E8-T06"
        # fact-lock note).
        "verification_ids": [],
        "correction_ids": [],
    }
    data.update(overrides)
    return Claim.model_validate(_with_real_content_hash(Claim, data))


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


def _execution_result(
    bundle: TaskBundle, outcome: TerminalOutcome, *, output_hash: str | None = None
) -> ExecutionResult:
    return ExecutionResult(
        outcome=outcome,
        output=b"x" if output_hash else None,
        output_hash=output_hash,
        is_deterministic=True,
        execution_attempt=1,
        task_id=bundle.id,
        task_revision=bundle.revision,
        resource_usage=ResourceUsage(wall_time_seconds=0.5),
        detail=None if outcome == "completed" else "reference executor detail",
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
        "node_key_id": "node-key-e8-t06",
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


def _verification_payload(*, target_id: str, reviewer_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": new_urn("verification"),
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
        "findings": [],
        "recommendation": "pass",
        "confidence": 0.8,
        "rationale": "Fixture rationale for the E8-T06 claim-rooted integration test.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    payload.update(overrides)
    return _with_real_content_hash(VerificationResultContract, payload)


def _record_verification(
    postgres_url: str, tmp_path: Path, *, target_id: str, recommendation: str
) -> str:
    """Records one VerificationResult against ``target_id`` through the REAL
    ``mrr verification record`` CLI path (not ``VerificationService`` called
    directly) — mirrors ``test_export_cli_ro_crate.py``'s own identical
    choice, "so this closure includes it exactly as ... the objective
    describes."
    """
    verification_id = new_urn("verification")
    reviewer_id = new_urn("person")
    payload = _verification_payload(
        target_id=target_id,
        reviewer_id=reviewer_id,
        id=verification_id,
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
            target_id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            _POLICY_VERSION,
        ]
    )
    assert exit_code == 0, "fixture setup: verification recording must succeed"
    return verification_id


@dataclass(frozen=True, slots=True)
class RealShapedGraph:
    """Every id ``_seed_real_shaped_graph`` produces — Befund 2's own real
    shape: a sealed crate whose OWN arrays are all empty, plus a claim graph
    that only the R1 declared-reference-field resolver, not
    ``build_provenance_map`` alone, can reach.
    """

    crate_id: str
    run_manifest_id: str
    hammond_claim_id: str
    other_claim_id: str
    support_anchor_id: str
    counter_anchor_id: str
    other_anchor_id: str
    support_source_id: str
    counter_source_id: str
    other_source_id: str
    pass_verification_id: str
    fail_verification_id: str
    other_verification_id: str


def _seed_real_shaped_graph(postgres_url: str, tmp_path: Path) -> RealShapedGraph:
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)

        evidence_record = bind_evidence_uow(engine, object_repository, event_log)
        source_service = SourceRecordService(evidence_record)
        anchor_service = EvidenceAnchorService(evidence_record)

        support_source = _source_record()
        counter_source = _source_record()
        other_source = _source_record()
        for source in (support_source, counter_source, other_source):
            source_service.create(source, **_kwargs())

        # Every anchor's own run_id is EMPTY — the real K1-T04 fact.
        support_anchor = _evidence_anchor(source_record_id=support_source.id, run_id=None)
        counter_anchor = _evidence_anchor(source_record_id=counter_source.id, run_id=None)
        other_anchor = _evidence_anchor(source_record_id=other_source.id, run_id=None)
        for anchor in (support_anchor, counter_anchor, other_anchor):
            anchor_service.create(anchor, **_kwargs())

        claim_service = _claim_service_for(engine)
        hammond_claim = _claim(
            evidence_relations=[support_anchor.id],
            counterevidence_relations=[counter_anchor.id],
        )
        other_claim = _claim(evidence_relations=[other_anchor.id])
        claim_service.create(hammond_claim, **_kwargs())
        claim_service.create(other_claim, **_kwargs())

        bundle = _bundle()
        result = _execution_result(bundle, "completed", output_hash="sha256:" + "e" * 64)
        run_manifest = _run_manifest_for(engine, bundle, result)

        # SEALED with EMPTY proposed_claims/source_records/evidence_anchors —
        # reproducing Befund 2's own real shape exactly: the crate seals the
        # run's inputs, the claim graph is a separate step that never links
        # back to it.
        crate_record = bind_crate_uow(engine, object_repository, event_log)
        sealer = EvidenceCrateSealer(crate_record)
        stored_crate = sealer.seal(
            run_manifest,
            result,
            bundle,
            artifact_refs=[],
            source_records=[],
            evidence_anchors=[],
            proposed_claims=[],
            **_seal_kwargs(),
        )
    finally:
        engine.dispose()

    pass_verification_id = _record_verification(
        postgres_url, tmp_path, target_id=hammond_claim.id, recommendation="pass"
    )
    fail_verification_id = _record_verification(
        postgres_url, tmp_path, target_id=hammond_claim.id, recommendation="fail"
    )
    other_verification_id = _record_verification(
        postgres_url, tmp_path, target_id=other_claim.id, recommendation="pass"
    )

    return RealShapedGraph(
        crate_id=stored_crate.id,
        run_manifest_id=run_manifest.id,
        hammond_claim_id=hammond_claim.id,
        other_claim_id=other_claim.id,
        support_anchor_id=support_anchor.id,
        counter_anchor_id=counter_anchor.id,
        other_anchor_id=other_anchor.id,
        support_source_id=support_source.id,
        counter_source_id=counter_source.id,
        other_source_id=other_source.id,
        pass_verification_id=pass_verification_id,
        fail_verification_id=fail_verification_id,
        other_verification_id=other_verification_id,
    )


@dataclass(frozen=True, slots=True)
class ReachableRunGraph:
    """R5's own SECOND, smaller fixture: one claim, one anchor whose
    ``run_id`` DOES resolve — covering R1's "when non-empty" branch.
    """

    claim_id: str
    anchor_id: str
    source_id: str
    run_manifest_id: str


def _seed_reachable_run_graph(postgres_url: str) -> ReachableRunGraph:
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)

        evidence_record = bind_evidence_uow(engine, object_repository, event_log)
        source_service = SourceRecordService(evidence_record)
        anchor_service = EvidenceAnchorService(evidence_record)

        source = _source_record()
        source_service.create(source, **_kwargs())

        bundle = _bundle()
        result = _execution_result(bundle, "completed", output_hash="sha256:" + "e" * 64)
        run_manifest = _run_manifest_for(engine, bundle, result)

        anchor = _evidence_anchor(source_record_id=source.id, run_id=run_manifest.id)
        anchor_service.create(anchor, **_kwargs())

        claim_service = _claim_service_for(engine)
        claim = _claim(evidence_relations=[anchor.id])
        claim_service.create(claim, **_kwargs())
    finally:
        engine.dispose()

    return ReachableRunGraph(
        claim_id=claim.id, anchor_id=anchor.id, source_id=source.id, run_manifest_id=run_manifest.id
    )


def _export_args(*, postgres_url: str, output_dir: Path, extra: list[str]) -> list[str]:
    return [
        "export",
        "ro-crate",
        "--database-url",
        postgres_url,
        "--output-dir",
        str(output_dir),
        *extra,
    ]


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _exported_object_urns(output_dir: Path) -> set[str]:
    metadata = json.loads((output_dir / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    return {entity["mrr:urn"] for entity in metadata["@graph"] if "mrr:kind" in entity}


# ---------------------------------------------------------------------------
# AT1: crate-rooted = crate only (Befund 2 reproduced); --all-claims = the
# full claim graph.
# ---------------------------------------------------------------------------


def test_crate_rooted_export_of_the_real_shaped_run_yields_only_the_crate(
    postgres_url: str, tmp_path: Path
) -> None:
    graph = _seed_real_shaped_graph(postgres_url, tmp_path)
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "export-crate"

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            output_dir=output_dir,
            extra=["--crate-id", graph.crate_id, "--artifact-root", str(artifact_root)],
        )
    )

    assert exit_code == 0
    assert _exported_object_urns(output_dir) == {graph.crate_id}


def test_all_claims_export_yields_the_full_claim_graph_including_both_verifications(
    postgres_url: str, tmp_path: Path
) -> None:
    graph = _seed_real_shaped_graph(postgres_url, tmp_path)
    output_dir = tmp_path / "export-claims"

    exit_code = mrr_main(
        _export_args(postgres_url=postgres_url, output_dir=output_dir, extra=["--all-claims"])
    )

    assert exit_code == 0
    expected = {
        graph.hammond_claim_id,
        graph.other_claim_id,
        graph.support_anchor_id,
        graph.counter_anchor_id,
        graph.other_anchor_id,
        graph.support_source_id,
        graph.counter_source_id,
        graph.other_source_id,
        graph.pass_verification_id,
        graph.fail_verification_id,
        graph.other_verification_id,
    }
    exported = _exported_object_urns(output_dir)
    assert exported == expected
    # The crate itself, and the run manifest (every real anchor's own
    # run_id is empty), are HONESTLY absent — never fabricated.
    assert graph.crate_id not in exported
    assert graph.run_manifest_id not in exported


def test_claim_id_export_of_just_the_hammond_claim_excludes_the_other_claim(
    postgres_url: str, tmp_path: Path
) -> None:
    graph = _seed_real_shaped_graph(postgres_url, tmp_path)
    output_dir = tmp_path / "export-hammond-only"

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            output_dir=output_dir,
            extra=["--claim-id", graph.hammond_claim_id],
        )
    )

    assert exit_code == 0
    exported = _exported_object_urns(output_dir)
    assert graph.hammond_claim_id in exported
    assert graph.pass_verification_id in exported
    assert graph.fail_verification_id in exported
    assert graph.other_claim_id not in exported
    assert graph.other_verification_id not in exported


# ---------------------------------------------------------------------------
# R1's "when non-empty" branch.
# ---------------------------------------------------------------------------


def test_reachable_run_manifest_is_included_when_an_anchor_run_id_resolves(
    postgres_url: str, tmp_path: Path
) -> None:
    graph = _seed_reachable_run_graph(postgres_url)
    output_dir = tmp_path / "export-reachable-run"

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url, output_dir=output_dir, extra=["--claim-id", graph.claim_id]
        )
    )

    assert exit_code == 0
    exported = _exported_object_urns(output_dir)
    assert graph.run_manifest_id in exported
    assert graph.anchor_id in exported
    assert graph.source_id in exported


# ---------------------------------------------------------------------------
# AT2: the claim-rooted report renders the pass/fail disagreement, marked,
# in both formats and both disclosures.
# ---------------------------------------------------------------------------


def _report_args(
    *, postgres_url: str, output: Path, fmt: str, disclosure: str, extra: list[str]
) -> list[str]:
    argv = [
        "report",
        "render",
        "--database-url",
        postgres_url,
        "--output",
        str(output),
        "--format",
        fmt,
        "--disclosure",
        disclosure,
    ]
    argv.extend(extra)
    return argv


def test_claim_rooted_report_marks_the_hammond_disagreement_internal_markdown(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    graph = _seed_real_shaped_graph(postgres_url, tmp_path)
    capsys.readouterr()  # drain _seed_real_shaped_graph's own `verification record` JSON lines
    output = tmp_path / "report.md"

    exit_code = mrr_main(
        _report_args(
            postgres_url=postgres_url,
            output=output,
            fmt="md",
            disclosure="internal",
            extra=["--all-claims"],
        )
    )

    assert exit_code == 0
    result_line = json.loads(capsys.readouterr().out)
    assert result_line["root"] == "claims"
    assert result_line["crate_id"] is None
    assert sorted(result_line["claim_ids"]) == sorted(
        [graph.hammond_claim_id, graph.other_claim_id]
    )

    rendered = output.read_text(encoding="utf-8")
    assert "# Research report — claim graph" in rendered
    assert "**Root:** claim graph" in rendered
    assert "**Claims:** 2" in rendered
    assert graph.run_manifest_id not in rendered  # honestly unreachable
    assert f"### {graph.hammond_claim_id}" in rendered
    hammond_section = rendered.split(f"### {graph.hammond_claim_id}")[1].split("### ")[0]
    assert "DISAGREEMENT ON RECORD" in hammond_section
    assert graph.pass_verification_id in hammond_section
    assert graph.fail_verification_id in hammond_section
    assert "Recommendation: pass" in hammond_section
    assert "Recommendation: fail" in hammond_section


def test_claim_rooted_report_marks_the_hammond_disagreement_public_html(
    postgres_url: str, tmp_path: Path
) -> None:
    graph = _seed_real_shaped_graph(postgres_url, tmp_path)
    output = tmp_path / "report.html"
    classification_file = tmp_path / "classification.json"
    classification_file.write_text(json.dumps({}), encoding="utf-8")

    exit_code = mrr_main(
        _report_args(
            postgres_url=postgres_url,
            output=output,
            fmt="html",
            disclosure="public",
            extra=[
                "--claim-id",
                graph.hammond_claim_id,
                "--classification-file",
                str(classification_file),
            ],
        )
    )

    assert exit_code == 0
    rendered = output.read_text(encoding="utf-8")
    assert "<h1>Research report — claim graph</h1>" in rendered
    assert "disagreement on record" in rendered
    # The claim's own assertion is fail-closed redacted (empty attestation
    # map, public disclosure) — the real fixture text never leaks. Every
    # other structural fact (ids, statuses, the disagreement itself) is
    # unaffected by redaction (never gated — see mrr.domain.research_report's
    # own "What is never redacted" section).
    real_assertion_text = "Does this fixture assertion satisfy the schema's minimum length rule?"
    assert real_assertion_text not in rendered
    assert graph.hammond_claim_id in rendered


# ---------------------------------------------------------------------------
# AT3: crate-rooted/claim-rooted agreement when the crate DOES reference its
# claims (also the R6 unit-tier-deferred "two seeds, identical closure"
# comparison — see this module's own docstring).
# ---------------------------------------------------------------------------


def test_crate_rooted_and_claim_rooted_closures_agree_when_the_crate_references_its_claims(
    postgres_url: str, tmp_path: Path
) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)

        evidence_record = bind_evidence_uow(engine, object_repository, event_log)
        source_service = SourceRecordService(evidence_record)
        anchor_service = EvidenceAnchorService(evidence_record)
        source = _source_record()
        source_service.create(source, **_kwargs())
        anchor = _evidence_anchor(source_record_id=source.id, run_id=None)
        anchor_service.create(anchor, **_kwargs())

        claim_service = _claim_service_for(engine)
        claim = _claim(evidence_relations=[anchor.id])
        claim_service.create(claim, **_kwargs())

        bundle = _bundle()
        result = _execution_result(bundle, "completed", output_hash="sha256:" + "e" * 64)
        run_manifest = _run_manifest_for(engine, bundle, result)

        crate_record = bind_crate_uow(engine, object_repository, event_log)
        sealer = EvidenceCrateSealer(crate_record)
        stored_crate = sealer.seal(
            run_manifest,
            result,
            bundle,
            artifact_refs=[],
            source_records=[source.id],
            evidence_anchors=[anchor.id],
            proposed_claims=[claim.id],
            **_seal_kwargs(),
        )
    finally:
        engine.dispose()

    crate_output = tmp_path / "export-crate"
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    assert (
        mrr_main(
            _export_args(
                postgres_url=postgres_url,
                output_dir=crate_output,
                extra=[
                    "--crate-id",
                    stored_crate.id,
                    "--artifact-root",
                    str(artifact_root),
                ],
            )
        )
        == 0
    )

    claim_output = tmp_path / "export-claim"
    assert (
        mrr_main(
            _export_args(
                postgres_url=postgres_url,
                output_dir=claim_output,
                extra=["--claim-id", claim.id],
            )
        )
        == 0
    )

    crate_urns = _exported_object_urns(crate_output)
    claim_urns = _exported_object_urns(claim_output)
    # The crate-rooted closure additionally carries the crate object itself;
    # every OTHER member — source record, anchor, claim — agrees exactly.
    assert crate_urns - {stored_crate.id} == claim_urns
    assert claim.id in claim_urns
    assert anchor.id in claim_urns
    assert source.id in claim_urns


# ---------------------------------------------------------------------------
# AT4: determinism.
# ---------------------------------------------------------------------------


def test_two_claim_rooted_exports_of_the_same_schema_are_byte_identical(
    postgres_url: str, tmp_path: Path
) -> None:
    graph = _seed_real_shaped_graph(postgres_url, tmp_path)

    output_a = tmp_path / "export-a"
    output_b = tmp_path / "export-b"
    for output_dir in (output_a, output_b):
        exit_code = mrr_main(
            _export_args(postgres_url=postgres_url, output_dir=output_dir, extra=["--all-claims"])
        )
        assert exit_code == 0

    assert _hash_tree(output_a) == _hash_tree(output_b)

    metadata = json.loads((output_a / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    graph_by_id = {entity["@id"]: entity for entity in metadata["@graph"]}
    date_published = graph_by_id["./"]["datePublished"]
    all_created_at = {
        json.loads((output_a / f"objects/{urn.replace(':', '_')}.json").read_bytes())["created_at"]
        for urn in _exported_object_urns(output_a)
    }
    assert date_published == max(all_created_at)
    del graph  # only used to build the fixture; assertions are output-based


# ---------------------------------------------------------------------------
# AT5: refusals.
# ---------------------------------------------------------------------------


def test_claim_id_naming_a_non_claim_is_refused_exit_3(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        evidence_record = bind_evidence_uow(
            engine, PostgresObjectRepository(engine), PostgresEventLog(engine)
        )
        source_service = SourceRecordService(evidence_record)
        source = _source_record()
        source_service.create(source, **_kwargs())
    finally:
        engine.dispose()

    output_dir = tmp_path / "export"
    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url, output_dir=output_dir, extra=["--claim-id", source.id]
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "SourceRecord" in err
    assert not output_dir.exists()


def test_all_claims_over_a_schema_with_zero_claims_refuses_exit_3(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "export"
    exit_code = mrr_main(
        _export_args(postgres_url=postgres_url, output_dir=output_dir, extra=["--all-claims"])
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "no claims to export" in err
    assert not output_dir.exists()


def test_unknown_claim_id_produces_exit_3_naming_it(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unknown_claim_id = new_urn("claim")
    output_dir = tmp_path / "export"

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url, output_dir=output_dir, extra=["--claim-id", unknown_claim_id]
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert unknown_claim_id in err
    assert not output_dir.exists()
