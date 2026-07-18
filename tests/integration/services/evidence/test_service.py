"""Integration tests for
``mrr.services.evidence.service.SourceRecordService``/``EvidenceAnchorService``
(task-packets/E3-T01.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, with ONE ``bind_unit_of_work`` call shared by both
services (see the service module's own docstring for why that sharing is
sound).

Acceptance-test mapping (task-packets/E3-T01.yaml, integration tier):

- "creating a source record and an anchor each persists one revision + one
  event atomically (integration, real PostgreSQL)" ->
  ``test_source_record_create_persists_revision_one_and_exactly_one_event_atomically``,
  ``test_evidence_anchor_create_persists_revision_one_and_exactly_one_event_atomically``.
- "read back is schema-valid" ->
  ``test_source_record_read_back_from_database_is_schema_and_pydantic_valid``,
  ``test_evidence_anchor_read_back_from_database_is_schema_and_pydantic_valid``.
- event provenance straight from the database (MRR-NFR-001) ->
  ``test_event_provenance_straight_from_database``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import EvidenceAnchor, SourceRecord
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.services.evidence.service import (
    EvidenceAnchorService,
    SourceRecordService,
    bind_unit_of_work,
)
from sqlalchemy import Engine

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_POLICY_VERSION = "policy-2026-07-01"


def _services_for(
    engine: Engine,
) -> tuple[SourceRecordService, EvidenceAnchorService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    return (
        SourceRecordService(record),
        EvidenceAnchorService(record),
        object_repository,
        event_log,
    )


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
        "identifiers": {"doi": "10.1234/example.42"},
        "title": "Benchmark fixture percentage tables",
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


def _evidence_anchor(**overrides: Any) -> EvidenceAnchor:
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
        "source_record_id": new_urn("source-record"),
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


def _kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actor": new_urn("agent"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def test_source_record_create_persists_revision_one_and_exactly_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    source_service, _anchor_service, object_repository, event_log = _services_for(postgres_engine)
    source_record = _source_record()

    stored = source_service.create(source_record, **_kwargs())

    assert stored.revision == 1
    persisted = object_repository.get_latest(stored.id)
    assert persisted.revision == 1
    assert persisted.body["source_type"] == "journal-article"
    assert persisted.body["primary_secondary_derived"] == "primary"

    events = [
        appended for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert len(events) == 1
    assert events[0].event.event_type == "source_record.created"


def test_evidence_anchor_create_persists_revision_one_and_exactly_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    _source_service, anchor_service, object_repository, event_log = _services_for(postgres_engine)
    anchor = _evidence_anchor()

    stored = anchor_service.create(anchor, **_kwargs())

    assert stored.revision == 1
    persisted = object_repository.get_latest(stored.id)
    assert persisted.revision == 1
    assert persisted.body["relation"] == "supports"
    assert persisted.body["anchor_kind"] == "text"

    events = [
        appended for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert len(events) == 1
    assert events[0].event.event_type == "evidence_anchor.created"


def test_source_record_read_back_from_database_is_schema_and_pydantic_valid(
    postgres_engine: Engine,
) -> None:
    source_service, _anchor_service, object_repository, _event_log = _services_for(postgres_engine)
    source_record = _source_record()

    stored = source_service.create(source_record, **_kwargs())
    persisted = object_repository.get_latest(stored.id)

    reconstructed = SourceRecord.model_validate(persisted.body)
    assert reconstructed.title == source_record.title

    schema = json.loads((SCHEMAS_DIR / "source-record.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(persisted.body)


def test_evidence_anchor_read_back_from_database_is_schema_and_pydantic_valid(
    postgres_engine: Engine,
) -> None:
    _source_service, anchor_service, object_repository, _event_log = _services_for(postgres_engine)
    anchor = _evidence_anchor()

    stored = anchor_service.create(anchor, **_kwargs())
    persisted = object_repository.get_latest(stored.id)

    reconstructed = EvidenceAnchor.model_validate(persisted.body)
    assert reconstructed.relation == "supports"

    schema = json.loads((SCHEMAS_DIR / "evidence-anchor.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(persisted.body)


def test_evidence_anchor_computational_variant_persists(postgres_engine: Engine) -> None:
    _source_service, anchor_service, object_repository, _event_log = _services_for(postgres_engine)
    anchor = _evidence_anchor(
        anchor_kind="computational",
        source_record_id=None,
        snapshot_hash=None,
        locator=None,
        quoted_fragment_hash=None,
        run_id=new_urn("run"),
        recomputation_status="reproduced",
    )

    stored = anchor_service.create(anchor, **_kwargs())
    persisted = object_repository.get_latest(stored.id)

    assert persisted.body["anchor_kind"] == "computational"
    assert persisted.body["recomputation_status"] == "reproduced"


def test_event_provenance_straight_from_database(postgres_engine: Engine) -> None:
    source_service, anchor_service, _object_repository, event_log = _services_for(postgres_engine)
    actor = new_urn("agent")
    correlation_id = new_urn("research-run")

    source_stored = source_service.create(
        _source_record(), **_kwargs(actor=actor, correlation_id=correlation_id)
    )
    anchor_stored = anchor_service.create(
        _evidence_anchor(), **_kwargs(actor=actor, correlation_id=correlation_id)
    )

    for object_id, expected_event_type in (
        (source_stored.id, "source_record.created"),
        (anchor_stored.id, "evidence_anchor.created"),
    ):
        events = [
            appended for appended in event_log.read_all() if appended.event.object_id == object_id
        ]
        assert len(events) == 1
        event = events[0].event
        assert event.event_type == expected_event_type
        assert event.actor == actor
        assert event.policy_version == _POLICY_VERSION
        assert event.correlation_id == correlation_id
        assert event.causation_id is None
        assert event.object_revision == 1


def test_services_have_no_update_method_and_a_second_create_call_is_a_new_object(
    postgres_engine: Engine,
) -> None:
    """SourceRecord/EvidenceAnchor are append-only — there is no method to
    mutate an already-recorded object. Calling ``create`` again creates an
    entirely new object identity, never a new revision of the first.
    """
    source_service, anchor_service, object_repository, _event_log = _services_for(postgres_engine)

    source_public_methods = {
        name
        for name in dir(SourceRecordService)
        if not name.startswith("_") and callable(getattr(SourceRecordService, name))
    }
    anchor_public_methods = {
        name
        for name in dir(EvidenceAnchorService)
        if not name.startswith("_") and callable(getattr(EvidenceAnchorService, name))
    }
    assert source_public_methods == {"create"}
    assert anchor_public_methods == {"create"}

    first = source_service.create(_source_record(), **_kwargs())
    second = source_service.create(_source_record(), **_kwargs())

    assert first.id != second.id
    assert object_repository.get_latest(first.id).revision == 1
    assert object_repository.get_latest(second.id).revision == 1
