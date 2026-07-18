"""Integration tests for ``mrr.services.source_family.service.SourceFamilyService``
(task-packets/E3-T03.yaml), run against a real PostgreSQL via the
``postgres_engine`` fixture in tests/integration/conftest.py — wired exactly
as production code would: ``PostgresObjectRepository``/``PostgresEventLog``
over the fixture's engine, matching
``tests/integration/services/evidence/test_service.py``'s own wiring.

Acceptance-test mapping (task-packets/E3-T03.yaml, integration tier):

- "creating a family persists one revision + one event atomically
  (integration, real PostgreSQL)" ->
  ``test_create_persists_revision_one_and_exactly_one_event_atomically``.
- "read back is schema-valid" ->
  ``test_read_back_from_database_is_schema_and_pydantic_valid``.
- event provenance straight from the database (MRR-NFR-001) ->
  ``test_event_provenance_straight_from_database``.
- "a family referencing member source urns leaves those SourceRecords
  untouched (no deletion/mutation) - representation is additive" ->
  ``test_creating_a_family_leaves_its_member_source_records_untouched``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import SourceFamily, SourceRecord
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.services.evidence.service import SourceRecordService
from mrr.services.evidence.service import bind_unit_of_work as bind_evidence_unit_of_work
from mrr.services.source_family.service import SourceFamilyService, bind_unit_of_work
from sqlalchemy import Engine

from scripts.check_contracts import SCHEMAS_DIR, build_registry, build_validator_for_schema

_POLICY_VERSION = "policy-2026-07-01"


def _service_for(
    engine: Engine,
) -> tuple[SourceFamilyService, PostgresObjectRepository, PostgresEventLog]:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    record = bind_unit_of_work(engine, object_repository, event_log)
    return SourceFamilyService(record), object_repository, event_log


def _source_family(**overrides: Any) -> SourceFamily:
    now = datetime.now(UTC)
    data: dict[str, Any] = {
        "id": new_urn("source-family"),
        "api_version": "mrr/v1alpha1",
        "kind": "SourceFamily",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": now,
        "created_by": new_urn("agent"),
        "content_hash": "sha256:" + "7" * 64,
        "origin_ref": "Benchmark fixture percentage tables dataset, 2026 edition",
        "member_source_ids": [new_urn("source-record"), new_urn("source-record")],
        "relationship_type": "shared_dataset",
        "confidence": 0.87,
        "rationale": (
            "Both records reproduce the same percentage table verbatim, including an "
            "identical rounding artifact, consistent with one shared upstream dataset."
        ),
        "detecting_method": "Automated text-similarity comparison (token_sort_ratio >= 0.92)",
        "reviewer_id": new_urn("person"),
    }
    data.update(overrides)
    return SourceFamily.model_validate(data)


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


def _kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actor": new_urn("agent"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def test_create_persists_revision_one_and_exactly_one_event_atomically(
    postgres_engine: Engine,
) -> None:
    service, object_repository, event_log = _service_for(postgres_engine)
    source_family = _source_family()

    stored = service.create(source_family, **_kwargs())

    assert stored.revision == 1
    persisted = object_repository.get_latest(stored.id)
    assert persisted.revision == 1
    assert persisted.body["relationship_type"] == "shared_dataset"
    assert persisted.body["member_source_ids"] == source_family.member_source_ids

    events = [
        appended for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert len(events) == 1
    assert events[0].event.event_type == "source_family.created"


def test_read_back_from_database_is_schema_and_pydantic_valid(postgres_engine: Engine) -> None:
    service, object_repository, _event_log = _service_for(postgres_engine)
    source_family = _source_family()

    stored = service.create(source_family, **_kwargs())
    persisted = object_repository.get_latest(stored.id)

    reconstructed = SourceFamily.model_validate(persisted.body)
    assert reconstructed.relationship_type == "shared_dataset"

    schema = json.loads((SCHEMAS_DIR / "source-family.schema.json").read_text())
    registry = build_registry()
    build_validator_for_schema(schema, registry).validate(persisted.body)


def test_event_provenance_straight_from_database(postgres_engine: Engine) -> None:
    service, _object_repository, event_log = _service_for(postgres_engine)
    actor = new_urn("agent")
    correlation_id = new_urn("research-run")

    stored = service.create(_source_family(), **_kwargs(actor=actor, correlation_id=correlation_id))

    events = [
        appended for appended in event_log.read_all() if appended.event.object_id == stored.id
    ]
    assert len(events) == 1
    event = events[0].event
    assert event.event_type == "source_family.created"
    assert event.actor == actor
    assert event.policy_version == _POLICY_VERSION
    assert event.correlation_id == correlation_id
    assert event.causation_id is None
    assert event.object_revision == 1


def test_service_has_no_update_method_and_a_second_create_call_is_a_new_object(
    postgres_engine: Engine,
) -> None:
    """SourceFamily is append-only — there is no method to mutate an
    already-recorded family. Calling ``create`` again creates an entirely
    new object identity, never a new revision of the first.
    """
    service, object_repository, _event_log = _service_for(postgres_engine)

    public_methods = {
        name
        for name in dir(SourceFamilyService)
        if not name.startswith("_") and callable(getattr(SourceFamilyService, name))
    }
    assert public_methods == {"create"}

    first = service.create(_source_family(), **_kwargs())
    second = service.create(_source_family(), **_kwargs())

    assert first.id != second.id
    assert object_repository.get_latest(first.id).revision == 1
    assert object_repository.get_latest(second.id).revision == 1


def test_creating_a_family_leaves_its_member_source_records_untouched(
    postgres_engine: Engine,
) -> None:
    """The headline invariant (task-packets/E3-T03.yaml): a SourceFamily
    references member SourceRecords by urn without ever mutating or deleting
    them. This seeds two real SourceRecords via SourceRecordService (E3-T01),
    then creates a SourceFamily referencing their ids, and asserts both
    member records are byte-for-byte unchanged afterward — still at
    revision 1, with their original body untouched.
    """
    family_service, object_repository, event_log = _service_for(postgres_engine)
    evidence_record = bind_evidence_unit_of_work(postgres_engine, object_repository, event_log)
    source_record_service = SourceRecordService(evidence_record)

    member_a = _source_record()
    member_b = _source_record()
    stored_a = source_record_service.create(member_a, **_kwargs())
    stored_b = source_record_service.create(member_b, **_kwargs())

    before_a = object_repository.get_latest(stored_a.id)
    before_b = object_repository.get_latest(stored_b.id)

    source_family = _source_family(member_source_ids=[stored_a.id, stored_b.id])
    family_service.create(source_family, **_kwargs())

    after_a = object_repository.get_latest(stored_a.id)
    after_b = object_repository.get_latest(stored_b.id)

    assert after_a.revision == before_a.revision == 1
    assert after_b.revision == before_b.revision == 1
    assert after_a.body == before_a.body
    assert after_b.body == before_b.body

    # No new event was appended against either member source's own id -
    # the only new event is the family's own creation.
    member_events_after = [
        appended
        for appended in event_log.read_all()
        if appended.event.object_id in {stored_a.id, stored_b.id}
    ]
    assert len(member_events_after) == 2  # each member's own source_record.created only
    assert {e.event.event_type for e in member_events_after} == {"source_record.created"}
