"""K1-T03b (MRR-MTH-018 sensitivity-variation execution) — task-packets/
K1-T03b.yaml. Drives
``mrr.services.cli.synthesis_orchestration.run_synthesis_evidence_loop`` end
to end against a real PostgreSQL (this directory's own ``postgres_engine``
fixture, ``tests/e2e/conftest.py`` — a FRESH, uniquely-named schema per
test, dropped afterward) and a real, tmp-path-backed
``LocalFilesystemArtifactStore``, exercising the NEW
``sensitivity_variation_parameters`` keyword this packet adds.

Mirrors ``tests/e2e/test_k1_t03_synthesis_evidence_loop.py``'s own style —
that file is NOT modified by this packet at all (forbidden_changes) and
continues to pass unmodified as part of the same test run.

This file NEVER reads from, writes to, or executes against the sealed
``mrr_k1t04_real_run_v2`` schema — the ``postgres_engine`` fixture mints a
brand-new schema per test. Fixtures are a SMALL, synthetic corpus (2
entries) deliberately constructed so that one declared variation's own
``source_family_overrides`` genuinely flips the outcome relative to the
base run (derived_decisions (l)) — asserted structurally, never as a
hardcoded claim value.

Acceptance-test mapping (task-packets/K1-T03b.yaml):

- "[e2e tier, gated behind MRR_TEST_DATABASE_URL] ... a full
  run_synthesis_evidence_loop run over a small, synthetic, multi-variation
  fixture completes with run_state == 'completed', produces a sealed
  EvidenceCrate independently traceable via the existing
  governed_by_protocol edges, and the resulting EvidenceMatrix's
  sensitivity_analysis_results contains at least one
  matches_base_outcome == False entry" ->
  ``test_full_loop_with_a_divergent_sensitivity_variation``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEdgeRepository, PostgresObjectRepository
from mrr.services.cli.synthesis_orchestration import run_synthesis_evidence_loop
from sqlalchemy import Engine

_TEST_CODE_REVISION = "git:k1-t03b-e2e-test-fixture"
_VALID_HASH = "sha256:" + "a" * 64


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def _seed_generic(
    object_repository: PostgresObjectRepository, *, kind: str, body: dict[str, Any]
) -> str:
    """Mirrors tests/e2e/test_k1_t03_synthesis_evidence_loop.py's own
    identical ``_seed_generic`` helper (a deliberate, small duplication
    across sibling test modules; see that file's/tests/e2e/conftest.py's own
    docstrings for the established rationale).
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
    sensitivity_variations: list[str],
    profile_max_ceiling: str = "associational_unadjusted",
) -> tuple[str, str]:
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
            "sensitivity_variations": sensitivity_variations,
        },
    )
    return protocol_id, _VALID_HASH


def _corpus_entry(
    entry_id: str,
    *,
    applies_to_analysis: str,
    source_family_id: str | None,
) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "applies_to_analysis": applies_to_analysis,
        "claim_type": "interpretive",
        "evidence_relation": "supports",
        "verification_status": "verified",
        "unverifiable_reason": None,
        "claim_relevant_finding": f"Finding for {entry_id}.",
        "extraction": {},
        "source_family_id": source_family_id,
        "title": f"Test fixture source {entry_id}",
        "creators": ["Test Fixture Author"],
        "retrieval_timestamp": "2026-07-21T09:00:00Z",
        "retrieval_method": "test-fixture-direct-read",
        "source_type": "test-fixture-artifact",
        "primary_secondary_derived": "primary",
    }


def _divergence_corpus() -> list[dict[str, Any]]:
    """Two entries, both supporting the SAME applies_to_analysis group
    ('candidate-x'), each from a DISTINCT source_family_id. Two verified,
    independent families clear a min_independent_source_families: 2
    'supported' floor in the base run; a declared variation's own
    source_family_overrides collapses them into one family, dropping below
    that floor — the fixture's own DESIGN, not a hardcoded claim value.
    """
    return [
        _corpus_entry("entry-a", applies_to_analysis="candidate-x", source_family_id="family-1"),
        _corpus_entry("entry-b", applies_to_analysis="candidate-x", source_family_id="family-2"),
    ]


def _protocol_parameters(*, protocol_id: str, protocol_lock_content_hash: str) -> dict[str, Any]:
    return {
        "protocol_id": protocol_id,
        "protocol_lock_content_hash": protocol_lock_content_hash,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 0}},
        "non_applicability_conditions": ["Applies only to catalogued works."],
    }


def _variation_params(
    variation_entry_id: str,
    *,
    protocol_id: str,
    protocol_lock_content_hash: str,
    source_family_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_id": protocol_id,
        "protocol_lock_content_hash": protocol_lock_content_hash,
        "variation_entry_id": variation_entry_id,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 0}},
        "source_family_overrides": source_family_overrides or {},
    }


def test_full_loop_with_a_divergent_sensitivity_variation(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)
    edge_repository = PostgresEdgeRepository(postgres_engine)

    question_model_id = _seed_question_model(object_repository)
    method_protocol_id, protocol_content_hash = _seed_locked_protocol(
        object_repository, sensitivity_variations=["variant-collapse", "variant-noop"]
    )

    result = run_synthesis_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model_id=question_model_id,
        method_protocol_id=method_protocol_id,
        corpus_entries=_divergence_corpus(),
        protocol_parameters=_protocol_parameters(
            protocol_id=method_protocol_id, protocol_lock_content_hash=protocol_content_hash
        ),
        sensitivity_variation_parameters={
            "variant-collapse": _variation_params(
                "variant-collapse",
                protocol_id=method_protocol_id,
                protocol_lock_content_hash=protocol_content_hash,
                source_family_overrides={"entry-b": "family-1"},
            ),
            "variant-noop": _variation_params(
                "variant-noop",
                protocol_id=method_protocol_id,
                protocol_lock_content_hash=protocol_content_hash,
            ),
        },
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"
    assert result.is_deterministic is True
    assert result.evidence_matrix_id is not None

    matrix = object_repository.get_latest(result.evidence_matrix_id)
    assert matrix.body["status"] == "frozen"

    sensitivity_results = matrix.body["sensitivity_analysis_results"]
    assert sensitivity_results is not None
    assert {entry["variation_entry_id"] for entry in sensitivity_results} == {
        "variant-collapse",
        "variant-noop",
    }
    # The fixture's own DESIGN produces at least one divergent entry —
    # asserted structurally, never as a hardcoded claim value.
    assert any(entry["matches_base_outcome"] is False for entry in sensitivity_results)

    # The sealed EvidenceCrate is independently traceable via the existing
    # governed_by_protocol edges (unchanged machinery this packet never
    # modifies) — same structural proof K1-T03's own e2e test already
    # exercises for the base (no-variation) path.
    crate = object_repository.get_latest(result.evidence_crate_id)
    assert crate.body["sealed"] is True
    assert crate.body["run_state"] == "completed"

    matrix_edges = edge_repository.edges_from(result.evidence_matrix_id, "governed_by_protocol")
    assert [e.target_id for e in matrix_edges] == [method_protocol_id]
    crate_edges = edge_repository.edges_from(result.evidence_crate_id, "governed_by_protocol")
    assert [e.target_id for e in crate_edges] == [method_protocol_id]

    governed_object_ids = {
        e.source_id for e in edge_repository.edges_to(method_protocol_id, "governed_by_protocol")
    }
    assert result.evidence_matrix_id in governed_object_ids
    assert result.evidence_crate_id in governed_object_ids
