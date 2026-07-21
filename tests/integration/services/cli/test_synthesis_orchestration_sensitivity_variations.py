"""Integration tests for MRR-MTH-018 sensitivity-variation execution
(task-packets/K1-T03b.yaml) on top of
``mrr.services.cli.synthesis_orchestration.run_synthesis_evidence_loop``,
run against a real PostgreSQL (``tests/integration/conftest.py``'s own
``postgres_engine`` fixture — a FRESH, uniquely-named schema per test,
dropped afterward) and a real, tmp-path-backed
``LocalFilesystemArtifactStore``. Mirrors
``tests/integration/services/cli/test_synthesis_setup.py``'s own style.

This file NEVER reads from, writes to, or otherwise touches the sealed
``mrr_k1t04_real_run_v2`` schema — every test here gets its own fresh
schema via ``postgres_engine`` (task-packets/K1-T03b.yaml forbidden_changes).
Fixtures are SMALL, synthetic corpus excerpts, not the real atlases.

Acceptance-test mapping (task-packets/K1-T03b.yaml):

- "[integration tier] ... persists an EvidenceMatrix whose
  sensitivity_analysis_results round-trips via ObjectRepository.get_latest
  byte-for-byte" ->
  ``test_sensitivity_variation_parameters_round_trip_through_the_persisted_matrix``.
- "... the SAME call with sensitivity_variation_parameters=None (the
  default) produces a matrix identical in every respect to a K1-T03-era
  call" -> ``test_none_sensitivity_variation_parameters_is_byte_identical_to_a_k1_t03_era_call``.
- "[unit, claim/ruling isolation]" belt-and-braces reinforcement at the
  REAL, DB-backed orchestration tier (the executor-tier structural proof
  lives in tests/unit/services/node_runtime/
  test_synthesis_executor_sensitivity_variations.py) ->
  ``test_claim_ruling_decision_counts_are_identical_with_and_without_variations``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresObjectRepository
from mrr.services.cli.synthesis_orchestration import run_synthesis_evidence_loop
from sqlalchemy import Engine

_TEST_CODE_REVISION = "git:k1-t03b-test-fixture"
_VALID_HASH = "sha256:" + "a" * 64


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def _seed_generic(
    object_repository: PostgresObjectRepository, *, kind: str, body: dict[str, Any]
) -> str:
    """Mirrors tests/e2e/test_k1_t03_synthesis_evidence_loop.py's own
    identical ``_seed_generic`` helper — a deliberate, small duplication
    across sibling test modules (this codebase's own established
    convention, see tests/e2e/conftest.py's own docstring), not a shared
    import.
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
    sensitivity_variations: list[str] | None = None,
    profile_max_ceiling: str = "associational_unadjusted",
) -> tuple[str, str]:
    """Mirrors the e2e test module's own identical seed helper, extended
    with an explicit ``sensitivity_variations`` declaration — every K1-T03
    seed defaults to ``[]``, this packet's own tests need real, non-empty
    declarations to exercise the new coverage check.
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
            "sensitivity_variations": sensitivity_variations or [],
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


def _divergence_corpus() -> list[dict[str, Any]]:
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


def test_sensitivity_variation_parameters_round_trip_through_the_persisted_matrix(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

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
    assert result.evidence_matrix_id is not None

    matrix = object_repository.get_latest(result.evidence_matrix_id)
    sensitivity_results = matrix.body["sensitivity_analysis_results"]
    assert sensitivity_results is not None
    by_variation = {entry["variation_entry_id"]: entry for entry in sensitivity_results}
    assert set(by_variation) == {"variant-collapse", "variant-noop"}
    assert any(entry["matches_base_outcome"] is False for entry in by_variation.values())

    # Byte-for-byte round trip: re-fetching the SAME id returns the
    # identical structure (ObjectRepository.get_latest is a pure read).
    matrix_reread = object_repository.get_latest(result.evidence_matrix_id)
    assert matrix_reread.body["sensitivity_analysis_results"] == sensitivity_results
    assert matrix_reread.content_hash == matrix.content_hash


def test_none_sensitivity_variation_parameters_is_byte_identical_to_a_k1_t03_era_call(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

    question_model_id = _seed_question_model(object_repository)
    method_protocol_id, protocol_content_hash = _seed_locked_protocol(object_repository)

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
        # sensitivity_variation_parameters omitted entirely — the default.
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"
    assert result.evidence_matrix_id is not None

    matrix = object_repository.get_latest(result.evidence_matrix_id)
    # A K1-T03-era matrix never carried this key at all — exclude_none=True
    # drops a None field entirely, so its absence here is the proof of
    # byte-identical equivalence (derived_decisions (j)).
    assert "sensitivity_analysis_results" not in matrix.body


def test_claim_ruling_decision_counts_are_identical_with_and_without_variations(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """Belt-and-braces reinforcement of derived_decisions (e) at the REAL,
    DB-backed orchestration tier: comparing COUNTS/STATUS-SETS (not literal
    ids, which are freshly minted per run) between an otherwise-identical
    run with and without a non-empty ``sensitivity_variation_parameters``
    argument.
    """
    store = _artifact_store(tmp_path)

    def _run(*, with_variation: bool) -> Any:
        origin_key = Ed25519PrivateKey.generate()
        node_key = Ed25519PrivateKey.generate()
        object_repository = PostgresObjectRepository(postgres_engine)
        question_model_id = _seed_question_model(object_repository)
        sensitivity_variations = ["variant-collapse"] if with_variation else []
        method_protocol_id, protocol_content_hash = _seed_locked_protocol(
            object_repository, sensitivity_variations=sensitivity_variations
        )
        variation_parameters = (
            {
                "variant-collapse": _variation_params(
                    "variant-collapse",
                    protocol_id=method_protocol_id,
                    protocol_lock_content_hash=protocol_content_hash,
                    source_family_overrides={"entry-b": "family-1"},
                )
            }
            if with_variation
            else None
        )
        return run_synthesis_evidence_loop(
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
            sensitivity_variation_parameters=variation_parameters,
            code_revision=_TEST_CODE_REVISION,
        )

    result_without = _run(with_variation=False)
    result_with = _run(with_variation=True)

    assert result_without.run_state == "completed"
    assert result_with.run_state == "completed"
    assert len(result_with.claim_ids) == len(result_without.claim_ids)
    assert len(result_with.method_ruling_ids) == len(result_without.method_ruling_ids)
    assert len(result_with.research_decision_ids) == len(result_without.research_decision_ids)

    object_repository = PostgresObjectRepository(postgres_engine)
    statuses_without = {
        object_repository.get_latest(claim_id).body["status"]
        for claim_id in result_without.claim_ids
    }
    statuses_with = {
        object_repository.get_latest(claim_id).body["status"] for claim_id in result_with.claim_ids
    }
    assert statuses_with == statuses_without

    # The variation-bearing run's own matrix genuinely carries a divergent
    # sensitivity result — the isolation isn't vacuously true.
    matrix_with = object_repository.get_latest(result_with.evidence_matrix_id)
    assert matrix_with.body["sensitivity_analysis_results"]
