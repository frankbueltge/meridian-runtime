"""Integration tests for the MRR-MTH-018 CLI/composition passthrough
(task-packets/K1-T04c.yaml): ``mrr.services.cli.synthesis_setup.
establish_and_run_synthesis``'s new, additive, default-``None``
``sensitivity_variation_parameters`` keyword. Run against a real PostgreSQL
via the ``postgres_engine`` fixture (tests/integration/conftest.py — a
FRESH, uniquely-named schema per test, dropped afterward), mirroring
``tests/integration/services/cli/test_synthesis_setup.py``'s own style and
small, synthetic fixture set (NOT the real atlases; that is the e2e tier's
own headline test, tests/e2e/test_k1_t04_first_real_run.py).

This file NEVER reads from, writes to, or otherwise touches the sealed
``mrr_k1t04_real_run_v2`` schema — every test here gets its own fresh schema
via ``postgres_engine``.

Acceptance-test mapping (task-packets/K1-T04c.yaml):

- "[integration, passthrough absent]" ->
  ``test_omitted_sensitivity_variation_parameters_completes_with_no_new_keys``,
  ``test_explicit_none_sensitivity_variation_parameters_completes_with_no_new_keys``.
- "[integration, passthrough supplied]" ->
  ``test_supplied_sensitivity_variation_parameters_overwrites_the_placeholder_protocol_lock``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.persistence.repositories import PostgresObjectRepository
from mrr.services.cli.synthesis_setup import establish_and_run_synthesis
from sqlalchemy import Engine

_TEST_CODE_REVISION = "git:k1-t04c-test-fixture"


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def _question_model_body() -> dict[str, Any]:
    return {
        "raw_question": "Do the fixture works instantiate the mechanism or just reference it?",
        "claim_type_sought": "interpretive",
        "scope": {"population": "test-fixture works", "conditions": []},
        "load_bearing_terms": ["mechanism", "instantiate", "reference"],
    }


def _concept_charter_body() -> dict[str, Any]:
    return {
        "entries": [
            {
                "entry_id": "instantiate-vs-reference-v1",
                "term": "instantiate",
                "definition": "test definition of instantiate",
                "scope_note": None,
            }
        ]
    }


def _method_protocol_body(*, sensitivity_variations: list[str]) -> dict[str, Any]:
    return {
        "extraction_fields": ["claim_relevant_finding"],
        "inclusion_criteria": ["catalogued"],
        "exclusion_criteria": ["derived"],
        "sensitivity_variations": sensitivity_variations,
        "planned_analyses": ["fixture-analysis"],
        "kill_conditions": ["fewer than 2 -> stop_insufficient_evidence"],
    }


def _corpus_entry(entry_id: str, *, applies_to_analysis: str) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "applies_to_analysis": applies_to_analysis,
        "claim_type": "interpretive",
        "evidence_relation": "supports",
        "verification_status": "verified",
        "unverifiable_reason": None,
        "claim_relevant_finding": f"Finding for {entry_id}.",
        "extraction": {},
        "source_family_id": None,
        "title": f"Fixture source {entry_id}",
        "creators": ["Fixture Author"],
        "retrieval_timestamp": "2026-07-21T12:00:00Z",
        "retrieval_method": "test-fixture-direct-read",
        "source_type": "test-fixture-artifact",
        "primary_secondary_derived": "primary",
    }


def _fixture_corpus() -> list[dict[str, Any]]:
    return [
        _corpus_entry("entry-1", applies_to_analysis="fixture-analysis"),
        _corpus_entry("entry-2", applies_to_analysis="fixture-analysis"),
    ]


def _fixture_protocol_parameters() -> dict[str, Any]:
    return {
        "protocol_id": "placeholder",
        "protocol_lock_content_hash": "placeholder",
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 2}},
        "non_applicability_conditions": ["Applies only to the fixture corpus."],
    }


def _variation_params(
    variation_entry_id: str,
    *,
    protocol_id: str = "placeholder",
    protocol_lock_content_hash: str = "placeholder",
) -> dict[str, Any]:
    """Deliberately wrong/placeholder ``protocol_id``/
    ``protocol_lock_content_hash`` values by default — mirroring
    ``task-packets/K1-T04b.yaml``'s own sidecar convention (derived_decisions
    (b)/(d)): no caller can know the real, just-locked protocol's own
    id/content_hash in advance, so ``establish_and_run_synthesis`` itself
    must overwrite both fields before the mapping reaches
    ``run_synthesis_evidence_loop``.
    """
    return {
        "protocol_id": protocol_id,
        "protocol_lock_content_hash": protocol_lock_content_hash,
        "variation_entry_id": variation_entry_id,
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 2}},
        "source_family_overrides": {},
    }


def test_omitted_sensitivity_variation_parameters_completes_with_no_new_keys(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """``sensitivity_variation_parameters`` OMITTED entirely, against a
    protocol fixture declaring ``sensitivity_variations: []`` — completes,
    and neither the TaskBundle's own ``instructions`` nor the persisted
    EvidenceMatrix carries any new key.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

    result = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_question_model_body(),
        concept_charter=_concept_charter_body(),
        method_protocol=_method_protocol_body(sensitivity_variations=[]),
        corpus_entries=_fixture_corpus(),
        protocol_parameters=_fixture_protocol_parameters(),
        # sensitivity_variation_parameters omitted entirely — the default.
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"

    task_bundle = object_repository.get_latest(result.task_id)
    assert "sensitivity_variation_artifact_ids" not in task_bundle.body["instructions"]

    assert result.evidence_matrix_id is not None
    matrix = object_repository.get_latest(result.evidence_matrix_id)
    assert "sensitivity_analysis_results" not in matrix.body


def test_explicit_none_sensitivity_variation_parameters_completes_with_no_new_keys(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """The SAME call, but with ``sensitivity_variation_parameters=None``
    passed explicitly — byte-for-byte the same outcome as the omitted case
    above (Python's keyword-only default-binding rule, not this function's
    own runtime logic; derived_decisions (e)).
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

    result = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_question_model_body(),
        concept_charter=_concept_charter_body(),
        method_protocol=_method_protocol_body(sensitivity_variations=[]),
        corpus_entries=_fixture_corpus(),
        protocol_parameters=_fixture_protocol_parameters(),
        sensitivity_variation_parameters=None,
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"

    task_bundle = object_repository.get_latest(result.task_id)
    assert "sensitivity_variation_artifact_ids" not in task_bundle.body["instructions"]

    assert result.evidence_matrix_id is not None
    matrix = object_repository.get_latest(result.evidence_matrix_id)
    assert "sensitivity_analysis_results" not in matrix.body


def test_supplied_sensitivity_variation_parameters_overwrites_the_placeholder_protocol_lock(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """A protocol fixture declaring ``sensitivity_variations: ["variant-x"]``,
    called with a variation-parameters mapping carrying deliberately WRONG,
    placeholder ``protocol_id``/``protocol_lock_content_hash`` values (the
    only way a caller CAN author one — the real ones do not exist until
    this same call creates and locks the protocol). The run still completes
    — itself the proof that ``establish_and_run_synthesis`` overwrote both
    placeholder fields with the real, just-locked protocol id/content_hash
    before handing the mapping to ``run_synthesis_evidence_loop``: an
    un-overwritten placeholder would instead fail the executor's own
    ``_check_protocol_lock`` (``ProtocolLockViolationError``), surfacing as
    ``run_state == "failed"``, not ``"completed"``.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

    result = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_question_model_body(),
        concept_charter=_concept_charter_body(),
        method_protocol=_method_protocol_body(sensitivity_variations=["variant-x"]),
        corpus_entries=_fixture_corpus(),
        protocol_parameters=_fixture_protocol_parameters(),
        sensitivity_variation_parameters={"variant-x": _variation_params("variant-x")},
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"

    task_bundle = object_repository.get_latest(result.task_id)
    sensitivity_variation_artifact_ids = task_bundle.body["instructions"][
        "sensitivity_variation_artifact_ids"
    ]
    assert set(sensitivity_variation_artifact_ids) == {"variant-x"}

    assert result.evidence_matrix_id is not None
    matrix = object_repository.get_latest(result.evidence_matrix_id)
    sensitivity_results = matrix.body["sensitivity_analysis_results"]
    assert sensitivity_results is not None
    assert any(entry["variation_entry_id"] == "variant-x" for entry in sensitivity_results)
