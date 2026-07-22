"""Integration tests for ``mrr export ro-crate`` (task-packets/E8-T01.yaml),
driven end to end through the REAL console-script entry point
(``mrr.services.cli.main.main``, exactly as
``tests/integration/services/test_verification_cli_recording.py`` already
exercises ``mrr verification record``) against a real, throwaway PostgreSQL
test schema AND a real ``mrr.adapters.object_store.local
.LocalFilesystemArtifactStore`` rooted at ``tmp_path``.

Uses a LOCAL ``postgres_url`` fixture — mirrors
``test_verification_cli_recording.py``'s own identically-shaped fixture (see
that module's docstring for why this codebase duplicates this exact fixture
per integration test module rather than sharing one).

``_seed_graph`` builds a small, REAL graph through the actual services this
codebase ships — never a raw insert, never a fake repository:

- ``SourceRecordService``/``EvidenceAnchorService`` (E3-T01) for the
  evidence substrate — the anchor's own ``source_record_id`` field points at
  the source record, a declared FIELD reference the R2(c) provenance BFS
  follows (``mrr.services.projection.service``'s own "field-vs-edge" design,
  ``ProvenanceEdge.via == "field"``);
- ``ClaimService`` (E3-T02) for the claim, EDGED to the anchor via
  ``add_evidence_edge(..., edge_type="supports")`` — a typed edge the SAME
  BFS also follows (``ProvenanceEdge.via == "edge"``), so this fixture
  exercises both provenance-hop kinds in one small graph;
- one real artifact, ``put()`` into a real ``LocalFilesystemArtifactStore``;
- ``RunManifestRecorder``/``EvidenceCrateSealer`` (E2-T05/T06) to seal a
  REAL, Ed25519-signed ``EvidenceCrate`` naming the source record, the
  anchor, the claim, and the artifact through its own arrays;
- ``mrr verification record`` — the REAL K1-T05 CLI path (not
  ``VerificationService`` called directly from Python) — to record a
  ``VerificationResult`` against the proposed claim, so AT2's closure
  includes it exactly as task-packets/E8-T01.yaml's own objective names.

Acceptance-test mapping (task-packets/E8-T01.yaml, integration tier):

- AT1 (offline verifiability) -> ``test_export_is_offline_verifiable``.
- AT2 (closure) -> ``test_exported_object_set_is_exactly_the_r2_closure``.
- AT3's refusal paths that genuinely need a real object store / real
  artifacts (unknown crate id, non-crate kind, missing artifact bytes) ->
  ``test_unknown_crate_id_produces_exit_3_naming_the_crate_id``,
  ``test_crate_id_resolving_to_a_non_crate_kind_is_refused``,
  ``test_missing_artifact_bytes_are_refused_naming_every_missing_hash``.
  AT3's DB-free halves (pre-existing output path, unreadable artifact root,
  unreachable database) live at the unit tier instead
  (``tests/unit/cli/test_export_cli_args.py``), mirroring
  ``tests/unit/cli/test_verification_cli_args.py``'s own identical tier
  split and its own documented rationale for that split.
- AT4 (determinism) ->
  ``test_two_exports_of_the_same_crate_are_byte_identical``.
- AT5 (layering) is asserted at the unit tier
  (``tests/unit/architecture/test_ro_crate_boundary.py``,
  ``tests/unit/architecture/test_export_cli_boundary.py``) and by the
  existing import-linter contract run
  (``tests/unit/architecture/test_import_boundaries.py``).
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
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.contracts import (
    ArtifactRef,
    Claim,
    EvidenceAnchor,
    RunManifest,
    SourceRecord,
    TaskBundle,
    VerificationResult,
)
from mrr.crypto.hashing import content_hash as compute_artifact_content_hash
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

_POLICY_VERSION = "policy-e8-t01-export-test"


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
    applied) — mirrors ``test_verification_cli_recording.py``'s own
    identically-named, identically-shaped fixture.
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
# Fixture factories — mirrors tests/integration/services/evidence/
# test_service.py's and tests/integration/services/node_runtime/
# test_evidence_crate.py's own helpers.
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
    """Recompute ``content_hash`` for real (``mrr.domain.hashing_policy
    .compute_content_hash``, the SAME RFC 8785 canonicalization every
    ``_*_to_stored_object`` service helper persists verbatim) instead of a
    placeholder "sha256:aaaa..." string. Every OTHER fixture in this
    codebase's own precedents (e.g. ``tests/integration/services/evidence/
    test_service.py``'s own ``_source_record``) uses a placeholder, because
    those tests never RECOMPUTE the hash from the persisted body — only
    THIS packet's own R4 does, so only here does the placeholder's dishonesty
    actually matter: AT1 asserts recomputing every exported object's content
    hash from the exported bytes alone reproduces the stored value, which is
    only a meaningful check against an honestly content-addressed fixture.
    """
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
        "identifiers": {"doi": "10.1234/e8-t01.fixture"},
        "title": "E8-T01 export-test fixture source",
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
        "node_key_id": "node-key-e8-t01",
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
        "rationale": "Fixture rationale for the E8-T01 export CLI integration test.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    payload.update(overrides)
    return _with_real_content_hash(VerificationResult, payload)


@dataclass(frozen=True, slots=True)
class SeededGraph:
    """Every id ``_seed_graph`` produces — the expected R2 closure (AT2)
    reads off this dataclass directly, rather than re-deriving it.
    """

    crate_id: str
    claim_id: str
    source_record_id: str
    evidence_anchor_id: str
    verification_id: str
    artifact_content_hash: str
    artifact_data: bytes


def _seed_graph(postgres_url: str, artifact_root: Path, tmp_path: Path) -> SeededGraph:
    """Build the full fixture graph described in this module's own
    docstring, through real services end to end. Returns every id the
    subsequent export/verification is checked against.
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

        artifact_store = LocalFilesystemArtifactStore(artifact_root)
        artifact_data = b"E8-T01 fixture artifact bytes for RO-Crate export testing."
        descriptor = artifact_store.put(
            artifact_data,
            media_type="text/plain",
            producer_run_id=new_urn("run-manifest"),
            classification="PUBLIC",
            created_at=datetime.now(UTC),
        )
        artifact_ref = ArtifactRef(
            artifact_id=new_urn("artifact"),
            content_hash=descriptor.content_hash,
            classification="PUBLIC",
        )

        bundle = _bundle()
        result = _execution_result(bundle, "completed", output_hash="sha256:" + "e" * 64)
        run_manifest = _run_manifest_for(engine, bundle, result)

        crate_record = bind_crate_uow(engine, object_repository, event_log)
        sealer = EvidenceCrateSealer(crate_record)
        stored_crate = sealer.seal(
            run_manifest,
            result,
            bundle,
            artifact_refs=[artifact_ref],
            source_records=[source_record.id],
            evidence_anchors=[anchor.id],
            proposed_claims=[claim.id],
            **_seal_kwargs(),
        )
    finally:
        engine.dispose()

    # Record a VerificationResult against the claim through the REAL
    # K1-T05 CLI path — `mrr verification record`, not VerificationService
    # called directly — so this closure includes it exactly as
    # task-packets/E8-T01.yaml's own objective describes.
    verification_id = new_urn("verification")
    reviewer_id = new_urn("person")
    payload = _verification_payload(target_id=claim.id, reviewer_id=reviewer_id, id=verification_id)
    verification_file = tmp_path / f"verification-{uuid.uuid4().hex}.json"
    verification_file.write_text(json.dumps(payload), encoding="utf-8")

    verification_exit_code = mrr_main(
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
    assert verification_exit_code == 0, "fixture setup: verification recording must succeed"

    return SeededGraph(
        crate_id=stored_crate.id,
        claim_id=claim.id,
        source_record_id=source_record.id,
        evidence_anchor_id=anchor.id,
        verification_id=verification_id,
        artifact_content_hash=descriptor.content_hash,
        artifact_data=artifact_data,
    )


def _export_args(
    *, postgres_url: str, artifact_root: Path, crate_id: str, output_dir: Path
) -> list[str]:
    return [
        "export",
        "ro-crate",
        "--database-url",
        postgres_url,
        "--artifact-root",
        str(artifact_root),
        "--crate-id",
        crate_id,
        "--output-dir",
        str(output_dir),
    ]


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# AT1: offline verifiability.
# ---------------------------------------------------------------------------


def test_export_is_offline_verifiable(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact_root = tmp_path / "artifact-root"
    output_dir = tmp_path / "export"
    graph = _seed_graph(postgres_url, artifact_root, tmp_path)
    capsys.readouterr()  # drain `_seed_graph`'s own `verification record` JSON line

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
        )
    )
    assert exit_code == 0

    result_line = json.loads(capsys.readouterr().out)
    assert result_line["crate_id"] == graph.crate_id
    assert result_line["output_dir"] == str(output_dir)
    assert result_line["object_count"] == 5  # crate + source record + anchor + claim + verification
    assert result_line["artifact_count"] == 1
    assert result_line["total_bytes"] > 0

    # --- From here on, READ ONLY output_dir — no database, no ArtifactStore. ---
    metadata = json.loads((output_dir / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    assert metadata["@context"][0] == "https://w3id.org/ro/crate/1.1/context"
    entities_by_id = {entity["@id"]: entity for entity in metadata["@graph"]}
    root = entities_by_id["./"]

    for ref in root["hasPart"]:
        relative_path = ref["@id"]
        assert (output_dir / relative_path).is_file(), (
            f"hasPart names a missing file: {relative_path}"
        )

    object_file_count = 0
    for relative_path, entity in entities_by_id.items():
        if entity.get("@type") != "File" or not relative_path.startswith("objects/"):
            continue
        object_file_count += 1
        raw_body = json.loads((output_dir / relative_path).read_bytes())
        recomputed_hash = compute_content_hash(raw_body)
        assert recomputed_hash == raw_body["content_hash"]
        assert recomputed_hash == entity["mrr:contentHash"]
    assert object_file_count == 5

    artifact_file_count = 0
    for relative_path, entity in entities_by_id.items():
        if entity.get("@type") != "File" or not relative_path.startswith("artifacts/"):
            continue
        artifact_file_count += 1
        raw_bytes = (output_dir / relative_path).read_bytes()
        recomputed_hash = compute_artifact_content_hash(raw_bytes)
        assert recomputed_hash == entity["mrr:contentHash"]
        assert relative_path == f"artifacts/{recomputed_hash.removeprefix('sha256:')}"
        assert raw_bytes == graph.artifact_data
    assert artifact_file_count == 1

    # Crate signature round trip: the exported crate object file's own
    # signature field equals the metadata's mrr:signature verbatim.
    crate_relative_path = f"objects/{graph.crate_id.replace(':', '_')}.json"
    crate_body = json.loads((output_dir / crate_relative_path).read_bytes())
    assert entities_by_id[graph.crate_id]["mrr:signature"] == crate_body["signature"]


# ---------------------------------------------------------------------------
# AT2: closure — exact URN-set equality, not a subset check.
# ---------------------------------------------------------------------------


def test_exported_object_set_is_exactly_the_r2_closure(postgres_url: str, tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact-root"
    output_dir = tmp_path / "export"
    graph = _seed_graph(postgres_url, artifact_root, tmp_path)

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=graph.crate_id,
            output_dir=output_dir,
        )
    )
    assert exit_code == 0

    metadata = json.loads((output_dir / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    exported_object_urns = {
        entity["mrr:urn"] for entity in metadata["@graph"] if "mrr:urn" in entity
    }

    expected_urns = {
        graph.crate_id,
        graph.source_record_id,
        graph.evidence_anchor_id,
        graph.claim_id,
        graph.verification_id,
    }
    assert exported_object_urns == expected_urns


# ---------------------------------------------------------------------------
# AT3 (the halves that need a real object store / real artifacts).
# ---------------------------------------------------------------------------


def test_unknown_crate_id_produces_exit_3_naming_the_crate_id(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "export"
    unknown_crate_id = new_urn("evidence-crate")

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=unknown_crate_id,
            output_dir=output_dir,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "ObjectNotFoundError" in err
    assert unknown_crate_id in err
    assert not output_dir.exists()


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

    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()
    output_dir = tmp_path / "export"

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=claim.id,
            output_dir=output_dir,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "Claim" in err
    assert not output_dir.exists()


def test_missing_artifact_bytes_are_refused_naming_every_missing_hash(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact_root = tmp_path / "artifact-root"
    artifact_root.mkdir()  # exists, but nothing was ever put() into it
    output_dir = tmp_path / "export"

    missing_hash_one = "sha256:" + "1" * 64
    missing_hash_two = "sha256:" + "2" * 64

    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        event_log = PostgresEventLog(engine)
        bundle = _bundle()
        result = _execution_result(bundle, "completed", output_hash="sha256:" + "e" * 64)
        run_manifest = _run_manifest_for(engine, bundle, result)
        crate_record = bind_crate_uow(engine, object_repository, event_log)
        sealer = EvidenceCrateSealer(crate_record)
        stored_crate = sealer.seal(
            run_manifest,
            result,
            bundle,
            artifact_refs=[
                ArtifactRef(
                    artifact_id=new_urn("artifact"),
                    content_hash=missing_hash_one,
                    classification="PUBLIC",
                ),
                ArtifactRef(
                    artifact_id=new_urn("artifact"),
                    content_hash=missing_hash_two,
                    classification="PUBLIC",
                ),
            ],
            **_seal_kwargs(),
        )
    finally:
        engine.dispose()

    exit_code = mrr_main(
        _export_args(
            postgres_url=postgres_url,
            artifact_root=artifact_root,
            crate_id=stored_crate.id,
            output_dir=output_dir,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert missing_hash_one in err
    assert missing_hash_two in err
    assert not output_dir.exists()


# ---------------------------------------------------------------------------
# AT4: determinism — two exports, two fresh target paths, byte-identical.
# ---------------------------------------------------------------------------


def test_two_exports_of_the_same_crate_are_byte_identical(
    postgres_url: str, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifact-root"
    graph = _seed_graph(postgres_url, artifact_root, tmp_path)

    output_dir_a = tmp_path / "export-a"
    output_dir_b = tmp_path / "export-b"
    for output_dir in (output_dir_a, output_dir_b):
        exit_code = mrr_main(
            _export_args(
                postgres_url=postgres_url,
                artifact_root=artifact_root,
                crate_id=graph.crate_id,
                output_dir=output_dir,
            )
        )
        assert exit_code == 0

    assert _hash_tree(output_dir_a) == _hash_tree(output_dir_b)
