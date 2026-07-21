"""Integration tests for
``mrr.services.cli.synthesis_setup.establish_and_run_synthesis``
(task-packets/K1-T04.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture (tests/integration/conftest.py).

Uses a SMALL, synthetic corpus fixture (not the real 87/214-entry atlases —
that is the e2e tier's own headline test,
tests/e2e/test_k1_t04_first_real_run.py) to prove the GOVERNANCE-OBJECT
LIFECYCLE wiring itself: a real QuestionModel/ConceptCharter/MethodProtocol
driven through their own real, event-emitting lifecycles rather than a raw
``_seed_generic`` insert.

Acceptance-test mapping (task-packets/K1-T04.yaml):

- "[capability-name guard]" ->
  ``test_capability_name_guard_raises_before_creating_anything``.
- "[governance-object lifecycle, integration tier]" ->
  ``test_establish_and_run_synthesis_persists_accepted_governance_objects``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.cli.synthesis_setup import (
    DEFAULT_METHOD_PROFILE_BODY,
    CapabilityNameNotDeclaredError,
    establish_and_run_synthesis,
)
from sqlalchemy import Engine

_TEST_CODE_REVISION = "git:k1-t04-test-fixture"


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


def _method_protocol_body() -> dict[str, Any]:
    return {
        "extraction_fields": ["claim_relevant_finding"],
        "inclusion_criteria": ["catalogued"],
        "exclusion_criteria": ["derived"],
        "sensitivity_variations": [],
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


def test_establish_and_run_synthesis_persists_accepted_governance_objects(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)
    edge_repository = PostgresEdgeRepository(postgres_engine)

    result = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_question_model_body(),
        concept_charter=_concept_charter_body(),
        method_protocol=_method_protocol_body(),
        corpus_entries=_fixture_corpus(),
        protocol_parameters=_fixture_protocol_parameters(),
        code_revision=_TEST_CODE_REVISION,
    )

    # A real, accepted MethodProfile.
    profile = object_repository.get_latest(result.method_profile_id)
    assert profile.body["status"] == "accepted"
    assert profile.body["profile_key"] == DEFAULT_METHOD_PROFILE_BODY["profile_key"]

    # A real, accepted QuestionModel.
    question_model = object_repository.get_latest(result.question_model_id)
    assert question_model.body["status"] == "accepted"
    assert question_model.revision == 2  # propose (rev 1) -> accept (rev 2)

    # A real, accepted ConceptCharter, with a real operationalizes edge to
    # the QuestionModel.
    concept_charter = object_repository.get_latest(result.concept_charter_id)
    assert concept_charter.body["status"] == "accepted"
    operationalizes_edges = edge_repository.edges_from(result.concept_charter_id, "operationalizes")
    assert len(operationalizes_edges) == 1
    assert operationalizes_edges[0].target_id == result.question_model_id

    # A real, locked MethodProtocol (locked_at/locked_by both set),
    # referencing the accepted MethodProfile.
    protocol = object_repository.get_latest(result.method_protocol_id)
    assert protocol.body["status"] == "locked"
    assert protocol.body["locked_at"] is not None
    assert protocol.body["locked_by"] is not None
    assert protocol.body["profile_id"] == result.method_profile_id
    assert protocol.revision == 3  # create (1) -> submit_for_review (2) -> lock (3)

    # Each transition's own domain event is present in the event log.

    event_log = PostgresEventLog(postgres_engine)
    event_types = {appended.event.event_type for appended in event_log.read_all()}
    assert "method_profile.proposed" in event_types
    assert "method_profile.accepted" in event_types
    assert "question_model.proposed" in event_types
    assert "question_model.accepted" in event_types
    assert "concept_charter.proposed" in event_types
    assert "concept_charter.accepted" in event_types
    assert "concept_charter.operationalizes_recorded" in event_types
    assert "method_protocol.created" in event_types
    assert "method_protocol.reviewed" in event_types
    assert "method_protocol.locked" in event_types

    # The run itself completed and produced a real sealed crate.
    assert result.run_state == "completed"
    crate = object_repository.get_latest(result.evidence_crate_id)
    assert crate.body["sealed"] is True


def test_capability_name_guard_raises_before_creating_anything(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

    mismatched_profile = dict(DEFAULT_METHOD_PROFILE_BODY)
    mismatched_profile["executor_task_family"] = ["mrr.method.some-other-capability/1"]

    with pytest.raises(CapabilityNameNotDeclaredError) as excinfo:
        establish_and_run_synthesis(
            engine=postgres_engine,
            artifact_store=store,
            origin_signing_key=origin_key,
            node_signing_key=node_key,
            method_profile=mismatched_profile,
            question_model=_question_model_body(),
            concept_charter=_concept_charter_body(),
            method_protocol=_method_protocol_body(),
            corpus_entries=_fixture_corpus(),
            protocol_parameters=_fixture_protocol_parameters(),
            code_revision=_TEST_CODE_REVISION,
        )

    assert excinfo.value.declared_capabilities == ["mrr.method.some-other-capability/1"]

    # The mismatched MethodProfile itself WAS accepted (the guard fires
    # right after resolving it, before anything else) — but no
    # QuestionModel/ConceptCharter/MethodProtocol exists anywhere.
    profile = object_repository.get_latest(excinfo.value.profile_id)
    assert profile.body["status"] == "accepted"

    event_log = PostgresEventLog(postgres_engine)
    event_types = {appended.event.event_type for appended in event_log.read_all()}
    assert "question_model.proposed" not in event_types
    assert "concept_charter.proposed" not in event_types
    assert "method_protocol.created" not in event_types
