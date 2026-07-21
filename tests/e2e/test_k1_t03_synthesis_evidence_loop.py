"""K1-T03 (systematic_evidence_synthesis v1 executor task family) — task-
packets/K1-T03.yaml. Drives
``mrr.services.cli.synthesis_orchestration.run_synthesis_evidence_loop`` end
to end against a real PostgreSQL (this directory's own ``postgres_engine``
fixture, ``tests/e2e/conftest.py``) and a real, tmp-path-backed
``LocalFilesystemArtifactStore`` — exactly like
``tests/e2e/test_e2e_001_single_node_evidence_loop.py``/
``tests/e2e/test_k0_t02_capability_dispatch.py``, but exercising the NEW
synthesis composition this task adds. Neither of those two files is
modified by this task at all (forbidden_changes) — they continue to pass
unmodified as part of the same test run.

This packet's own test fixtures are SMALL, synthetic/sample corpus excerpts
(a handful of entries shaped like the atlas records) — NOT the real atlases
(task-packets/K1-T04.yaml's job).

Acceptance-test mapping (task-packets/K1-T03.yaml):

- "[same fixture, through the full orchestration]" ->
  ``test_headline_corpus_persists_frozen_matrix_claims_rulings_and_governed_by_protocol_edges``.
  NOTE: per task-packets/K1-T03.yaml's own binding ``reviewer_resolution``
  (overriding the packet body's own wording, "two Claims ('supported' and
  'contested')"), the supported-track candidate is minted and left at
  ``Claim.status == "draft"`` (genuinely proposed, never self-verified by
  this run) — NOT promoted to ``"supported"`` — while the contested-track
  candidate IS driven to ``Claim.status == "contested"``. See
  ``mrr.services.cli.synthesis_orchestration``'s own module docstring for
  the full derivation.
- "[insufficient evidence, MRR-MTH-011]" ->
  ``test_insufficient_evidence_persists_research_decision_and_no_claim``.
- PR #49 review follow-up (integration test: a real ``ruled_by`` edge seeded
  through this loop, exercising ``ProjectionService.build_claim_table``, the
  render-time ceiling checkpoint) ->
  ``test_projection_service_build_claim_table_resolves_the_ceiling_chain_for_both_claims``.
- task-packets/E9-T00.yaml item 7 ("the sealed crate's source_records/
  evidence_anchors/proposed_claims now non-empty and matching the run's own
  persisted SourceRecord/EvidenceAnchor/Claim ids") -> assertions added
  in-place to
  ``test_headline_corpus_persists_frozen_matrix_claims_rulings_and_governed_by_protocol_edges``
  (this is the same fixture the K1-T03 acceptance test above already
  exercises; no new test function needed). ``tests/e2e/
  test_e2e_001_single_node_evidence_loop.py`` (E2E-001, ``run_local_
  evidence_loop``, a DIFFERENT, forbidden-to-touch call site) passes
  UNMODIFIED, still producing empty arrays.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.cli.synthesis_orchestration import run_synthesis_evidence_loop
from mrr.services.projection.service import ProjectionService
from sqlalchemy import Engine

_TEST_CODE_REVISION = "git:k1-t03-test-fixture"
_VALID_HASH = "sha256:" + "a" * 64


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def _seed_generic(
    object_repository: PostgresObjectRepository, *, kind: str, body: dict[str, Any]
) -> str:
    """Bypasses any service — mirrors
    tests/integration/services/claim/test_service.py's own identical
    ``_seed_generic`` helper for prerequisite objects this packet's own
    services do not own (QuestionModel/MethodProfile/MethodProtocol).

    Unlike that K1-T02 precedent (which never reads ``content_hash`` back
    out of a seeded body), this packet's own
    ``SystematicEvidenceSynthesisExecutor`` DOES — MRR-MTH-007's lock-hash
    check reads it directly from the serialized ``MethodProtocol`` body
    (``mrr.contracts.method_protocol``'s own "Lock hash IS
    baseObject.content_hash, not a new field" design). ``content_hash`` is
    therefore embedded in the body dict too, matching what every real
    ``_*_to_stored_object`` helper in this codebase already does
    (``model_dump_json()`` naturally includes every ``BaseObject`` field).
    """
    object_id = new_urn(kind.lower().replace("_", "-"))
    obj = StoredObject(
        id=object_id,
        api_version="mrr/v1alpha1",
        kind=kind,
        practice_id=new_urn("practice"),
        revision=1,
        created_at=datetime.now(UTC),
        created_by=new_urn("agent-role"),
        content_hash=_VALID_HASH,
        supersedes=None,
        labels=None,
        body={"id": object_id, "content_hash": _VALID_HASH, **body},
    )
    object_repository.insert_revision(obj, expected_current_revision=None)
    return object_id


def _seed_question_model(object_repository: PostgresObjectRepository) -> str:
    return _seed_generic(
        object_repository,
        kind="QuestionModel",
        body={
            "raw_question": "Do the fixture works instantiate the mechanism or just reference it?",
            "claim_type_sought": "interpretive",
            "scope": {"population": "test-fixture works", "conditions": []},
            "load_bearing_terms": ["mechanism"],
            "status": "accepted",
        },
    )


def _seed_locked_protocol(
    object_repository: PostgresObjectRepository,
    *,
    profile_max_ceiling: str = "associational_unadjusted",
) -> tuple[str, str]:
    """Seeds a MethodProfile + a LOCKED MethodProtocol referencing it.
    Returns ``(method_protocol_id, protocol_content_hash)``.
    """
    profile_id = _seed_generic(
        object_repository,
        kind="MethodProfile",
        body={"max_claim_ceiling": profile_max_ceiling},
    )
    protocol_id = _seed_generic(
        object_repository,
        kind="MethodProtocol",
        body={
            "profile_id": profile_id,
            "extraction_fields": ["sample_size", "methodology_notes"],
            "status": "locked",
        },
    )
    return protocol_id, _VALID_HASH


def _corpus_entry(
    entry_id: str,
    *,
    applies_to_analysis: str,
    claim_type: str = "interpretive",
    evidence_relation: str = "supports",
    verification_status: str = "verified",
    unverifiable_reason: str | None = None,
    source_family_id: str | None = None,
    primary_secondary_derived: str = "primary",
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "applies_to_analysis": applies_to_analysis,
        "claim_type": claim_type,
        "evidence_relation": evidence_relation,
        "verification_status": verification_status,
        "unverifiable_reason": unverifiable_reason,
        "claim_relevant_finding": f"Finding for {entry_id}.",
        "extraction": {},
        "source_family_id": source_family_id,
        "title": f"Test fixture source {entry_id}",
        "creators": ["Test Fixture Author"],
        "retrieval_timestamp": "2026-07-21T09:00:00Z",
        "retrieval_method": "test-fixture-direct-read",
        "source_type": "test-fixture-artifact",
        "primary_secondary_derived": primary_secondary_derived,
    }


def _headline_corpus() -> list[dict[str, Any]]:
    return [
        _corpus_entry(
            "entry-supported-1",
            applies_to_analysis="candidate-supported",
            source_family_id="family-supported-1",
        ),
        _corpus_entry(
            "entry-supported-2",
            applies_to_analysis="candidate-supported",
            source_family_id="family-supported-2",
        ),
        _corpus_entry(
            "entry-contested-support",
            applies_to_analysis="candidate-contested",
            evidence_relation="supports",
            source_family_id="family-contested-a",
        ),
        _corpus_entry(
            "entry-contested-contradict",
            applies_to_analysis="candidate-contested",
            evidence_relation="contradicts",
            source_family_id="family-contested-b",
        ),
        _corpus_entry(
            "entry-unverifiable",
            applies_to_analysis="candidate-supported",
            verification_status="unverifiable",
            unverifiable_reason="training provenance could not be confirmed",
        ),
        _corpus_entry(
            "entry-excluded",
            applies_to_analysis="candidate-supported",
            primary_secondary_derived="derived",
        ),
    ]


def _protocol_parameters(
    *, protocol_id: str, protocol_lock_content_hash: str, min_included_sources: int = 2
) -> dict[str, Any]:
    return {
        "protocol_id": protocol_id,
        "protocol_lock_content_hash": protocol_lock_content_hash,
        "inclusion_filter": {
            "primary_secondary_derived": {"allowed_values": ["primary", "secondary"]}
        },
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {
            "stop_insufficient_evidence": {"min_included_sources": min_included_sources}
        },
        "non_applicability_conditions": [
            "Applies only to catalogued works with disclosed training provenance."
        ],
    }


def test_headline_corpus_persists_frozen_matrix_claims_rulings_and_governed_by_protocol_edges(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)
    edge_repository = PostgresEdgeRepository(postgres_engine)

    question_model_id = _seed_question_model(object_repository)
    method_protocol_id, protocol_content_hash = _seed_locked_protocol(object_repository)

    result = run_synthesis_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model_id=question_model_id,
        method_protocol_id=method_protocol_id,
        corpus_entries=_headline_corpus(),
        protocol_parameters=_protocol_parameters(
            protocol_id=method_protocol_id, protocol_lock_content_hash=protocol_content_hash
        ),
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"
    assert result.is_deterministic is True
    assert result.evidence_matrix_id is not None

    # A frozen EvidenceMatrix with every included row (5 of 6 — the
    # inclusion-filter-excluded entry is absent), the unverifiable one
    # included.
    matrix = object_repository.get_latest(result.evidence_matrix_id)
    assert matrix.body["status"] == "frozen"
    row_ids = {row["row_id"] for row in matrix.body["rows"]}
    assert row_ids == {
        "entry-supported-1",
        "entry-supported-2",
        "entry-contested-support",
        "entry-contested-contradict",
        "entry-unverifiable",
    }
    unverifiable_row = next(
        row for row in matrix.body["rows"] if row["row_id"] == "entry-unverifiable"
    )
    assert unverifiable_row["verification_status"] == "unverifiable"
    assert unverifiable_row["unverifiable_reason"] is not None

    # Two claims: one left "draft" (supported-track, genuinely proposed —
    # reviewer_resolution), one driven to "contested".
    assert len(result.claim_ids) == 2
    statuses = {
        object_repository.get_latest(claim_id).body["status"] for claim_id in result.claim_ids
    }
    assert statuses == {"draft", "contested"}

    # Two issued MethodRulings, both ruled to associational_unadjusted.
    assert len(result.method_ruling_ids) == 2
    for ruling_id in result.method_ruling_ids:
        ruling = object_repository.get_latest(ruling_id)
        assert ruling.body["status"] == "issued"
        assert ruling.body["ruled_ceiling"] == "associational_unadjusted"

    # governed_by_protocol edges: matrix, each claim, and the crate, all ->
    # the same locked MethodProtocol.
    matrix_edges = edge_repository.edges_from(result.evidence_matrix_id, "governed_by_protocol")
    assert [e.target_id for e in matrix_edges] == [method_protocol_id]
    for claim_id in result.claim_ids:
        claim_edges = edge_repository.edges_from(claim_id, "governed_by_protocol")
        assert [e.target_id for e in claim_edges] == [method_protocol_id]
    crate_edges = edge_repository.edges_from(result.evidence_crate_id, "governed_by_protocol")
    assert [e.target_id for e in crate_edges] == [method_protocol_id]

    # Reverse direction, from the protocol's own point of view: every
    # object this run governed is enumerable by walking edges_to(protocol_id),
    # not only by walking edges_from each object individually.
    governed_object_ids = {
        e.source_id for e in edge_repository.edges_to(method_protocol_id, "governed_by_protocol")
    }
    assert governed_object_ids == {
        result.evidence_matrix_id,
        result.evidence_crate_id,
        *result.claim_ids,
    }

    # ruled_by edges: each claim -> its own MethodRuling.
    for claim_id in result.claim_ids:
        ruled_by_edges = edge_repository.edges_from(claim_id, "ruled_by")
        assert len(ruled_by_edges) == 1
        assert ruled_by_edges[0].target_id in result.method_ruling_ids

    # The sealed EvidenceCrate itself exists and is genuinely sealed.
    crate = object_repository.get_latest(result.evidence_crate_id)
    assert crate.body["sealed"] is True
    assert crate.body["run_state"] == "completed"

    # task-packets/E9-T00.yaml item 7: the sealed crate's own
    # source_records/evidence_anchors/proposed_claims are now non-empty and
    # match the run's own persisted SourceRecord/EvidenceAnchor/Claim ids —
    # independently derived from the frozen matrix's own rows (each row
    # already carries its own source_record_id/evidence_anchor_id), not
    # from any new field this task adds to the result.
    # ADR-0004 exclude_none=True: a row with no evidence_anchor_id omits the
    # key entirely rather than persisting it as JSON null.
    expected_source_record_ids = {row["source_record_id"] for row in matrix.body["rows"]}
    expected_evidence_anchor_ids = {
        row["evidence_anchor_id"]
        for row in matrix.body["rows"]
        if row.get("evidence_anchor_id") is not None
    }
    assert set(crate.body["source_records"]) == expected_source_record_ids
    assert set(crate.body["evidence_anchors"]) == expected_evidence_anchor_ids
    assert crate.body["proposed_claims"] == list(result.claim_ids)

    # No ResearchDecision for this fixture (no analysis was insufficient).
    assert result.research_decision_ids == ()


def test_insufficient_evidence_persists_research_decision_and_no_claim(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

    question_model_id = _seed_question_model(object_repository)
    method_protocol_id, protocol_content_hash = _seed_locked_protocol(object_repository)

    thin_corpus = [
        _corpus_entry(
            "entry-thin-1", applies_to_analysis="candidate-thin", source_family_id="family-thin-1"
        )
    ]

    result = run_synthesis_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model_id=question_model_id,
        method_protocol_id=method_protocol_id,
        corpus_entries=thin_corpus,
        protocol_parameters=_protocol_parameters(
            protocol_id=method_protocol_id,
            protocol_lock_content_hash=protocol_content_hash,
            min_included_sources=5,
        ),
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"  # NEVER "failed" for insufficient evidence (MRR-MTH-011)
    assert result.claim_ids == ()
    assert result.method_ruling_ids == ()
    assert len(result.research_decision_ids) == 1

    decision = object_repository.get_latest(result.research_decision_ids[0])
    assert decision.body["decision_type"] == "stop_insufficient_evidence"
    assert decision.body["applies_to_analysis"] == "candidate-thin"
    assert decision.body["status"] == "issued"

    # The matrix still exists, frozen, with the row present (MRR-MTH-011:
    # an honest record of what WAS found, not a hidden failure).
    assert result.evidence_matrix_id is not None
    matrix = object_repository.get_latest(result.evidence_matrix_id)
    assert matrix.body["status"] == "frozen"
    assert len(matrix.body["rows"]) == 1

    # The Run Manifest/Evidence Crate seal exactly as for any other
    # completed run.
    crate = object_repository.get_latest(result.evidence_crate_id)
    assert crate.body["sealed"] is True
    assert crate.body["run_state"] == "completed"


def test_projection_service_build_claim_table_resolves_the_ceiling_chain_for_both_claims(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """PR #49 review follow-up: a real ``ruled_by`` edge seeded through this
    loop, exercising ``ProjectionService.build_claim_table`` — the
    render-time ceiling checkpoint (MRR-MTH-004's "at ... projection
    rendering" half).
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)
    edge_repository = PostgresEdgeRepository(postgres_engine)
    event_log = PostgresEventLog(postgres_engine)

    question_model_id = _seed_question_model(object_repository)
    method_protocol_id, protocol_content_hash = _seed_locked_protocol(object_repository)

    result = run_synthesis_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model_id=question_model_id,
        method_protocol_id=method_protocol_id,
        corpus_entries=_headline_corpus(),
        protocol_parameters=_protocol_parameters(
            protocol_id=method_protocol_id, protocol_lock_content_hash=protocol_content_hash
        ),
        code_revision=_TEST_CODE_REVISION,
    )

    projection_service = ProjectionService(object_repository, edge_repository, event_log)
    table = projection_service.build_claim_table()

    our_rows = {row.claim_id: row for row in table if row.claim_id in result.claim_ids}
    assert len(our_rows) == 2
    for row in our_rows.values():
        assert row.ceiling_checked is True
        assert row.ceiling_violation is None
