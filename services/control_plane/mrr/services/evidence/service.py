"""``SourceRecordService`` and ``EvidenceAnchorService`` (task-packets/
E3-T01.yaml): the evidence substrate for claims — persisting ``SourceRecord``
and ``EvidenceAnchor`` objects, each as an append-only revision-1 write plus
one domain event, atomically, via the existing E1-T06 unit-of-work
primitive. First task of Epic E3 (claim, evidence, correction kernel).

--- One module, two services -------------------------------------------------

Both services are collected in one module rather than two (task-packets/
E3-T01.yaml explicitly offers this: "(or one EvidenceService with both)").
``SourceRecord`` and ``EvidenceAnchor`` are the two halves of one coherent
concern ("the evidence substrate") and their services are structurally
identical: a single ``create`` method, no lifecycle, no read dependency at
all — exactly ``mrr.services.node_runtime.run_manifest.RunManifestRecorder``'s
shape (not ``mrr.services.research_score.service.ResearchScoreService``'s
richer one), since neither entity carries a status field or a multi-step
lifecycle to manage. Splitting them into two near-identical files would
duplicate the same docstring/import/``bind_unit_of_work`` boilerplate for no
structural benefit. ``mrr.services.task_bundle.service`` already establishes
a "two related service classes, one module" precedent in this codebase
(``TaskBundleService``/``NodeTaskDecisionService``), albeit for two ROLES on
the SAME entity rather than two sibling entities here; ``bind_unit_of_work``
below is shared between both services for the same reason that module's own
``bind_unit_of_work`` is shared — both ultimately write the same
``objects``/``domain_events`` tables.

--- Caller mints id/content_hash/created_* -----------------------------------

Unlike ``RunManifestRecorder`` (which mints identity/hash internally because
``RunManifest`` carries no signature — see that module's own "Deviation from
a literal 'caller mints id/content_hash/created_*'" docstring section for
why), ``SourceRecordService.create``/``EvidenceAnchorService.create`` follow
the MORE common convention in this codebase
(``ResearchScoreService.create``, ``CapabilityRegistry.register``,
``TaskBundleService.create``): the caller already built a fully valid,
schema-conformant ``SourceRecord``/``EvidenceAnchor`` — its ``id``,
``content_hash``, ``created_at``, ``created_by`` are already set — and this
service's only job is to validate ``revision == 1`` and persist it plus its
event, atomically. task-packets/E3-T01.yaml's own instructions name this
convention explicitly: "Caller mints id/content_hash/created_* (consistent
with the other services)".

--- No update/mutate method ---------------------------------------------------

Neither class offers anything beyond ``create``: both ``SourceRecord`` and
``EvidenceAnchor`` are append-only by construction (docs/spec/02_DOMAIN_MODEL.md
sections 2.8/2.9 name no lifecycle for either), matching
``RunManifestRecorder``'s own "no update/seal/correct method anywhere on this
class" precedent (task-packets/E3-T01.yaml: "No update/mutate beyond
append-only revisions"). A correction to an already-recorded ``SourceRecord``
or ``EvidenceAnchor`` would be a new revision written by a caller who already
holds the prior one — out of this task's scope.

--- Not wired to claim support here --------------------------------------------

This module provides the SUBSTRATE only. Nothing here writes a claim-graph
edge, resolves ``source_family_id`` against an actual ``SourceFamily`` object
(E3-T03, not yet implemented — the field is carried, unvalidated, as a bare
nullable URN), or validates an anchor by actually re-retrieving or
re-computing what it claims to resolve (E3-T04/T05 — "verification/review
records and the independence validator", this packet's own
``forbidden_changes``). Structural schema/Pydantic validation — including the
exact-anchor-or-explicit-reason rule, enforced at the contract layer before
this service ever sees the object (``mrr.contracts.evidence_anchor.
EvidenceAnchor._exact_resolution_or_explicit_reason``) — is the only
validation either service performs; wiring an anchor into claim support is
E3-T02.

--- No new typed errors -------------------------------------------------------

Both ``create`` methods raise a plain ``ValueError`` for the one sanity check
they perform (``revision != 1``), exactly matching
``ResearchScoreService.create``'s own equivalent check — not a new
``mrr.domain.exceptions`` type, since nothing beyond that one caller-mistake
case needs a typed error here (task-packets/E3-T01.yaml: "Typed errors
additive to mrr.domain.exceptions only if needed").
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import EvidenceAnchor, SourceRecord, Urn
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: task-packets/E3-T01.yaml invariant: "persisting a record or anchor writes
#: exactly one domain event with full NFR-001 provenance, atomically with
#: the revision" — one event type per entity, dot-separated, matching
#: "research_score.created"/"task_bundle.created"'s existing convention.
_EVENT_SOURCE_RECORD_CREATED = "source_record.created"
_EVENT_EVIDENCE_ANCHOR_CREATED = "evidence_anchor.created"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. Identical in shape to every other service's own
#: ``RecordRevisionWithEvent`` — see e.g.
#: ``mrr.services.research_score.service``'s own module docstring for why
#: this is a local copy, not a shared import, across separate service
#: modules.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple, producing the ``RecordRevisionWithEvent`` callable both
    ``SourceRecordService`` and ``EvidenceAnchorService`` depend on for their
    one atomic write each. Production wiring and integration tests call this
    ONCE and pass the same bound callable to both services (they ultimately
    write the same ``objects``/``domain_events`` tables — the same sharing
    ``mrr.services.task_bundle.service.bind_unit_of_work`` documents for
    ``TaskBundleService``/``NodeTaskDecisionService``); DB-free unit tests
    pass their own trivial callable of the same shape, backed by in-memory
    fakes, instead.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        return record_object_revision_with_event(
            engine, object_repository, event_log, obj, expected_current_revision, event
        )

    return _record


def _source_record_to_stored_object(source_record: SourceRecord) -> StoredObject:
    """Convert an already-valid ``SourceRecord`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip —
    no added keys — matching every other service's own
    ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(source_record.model_dump_json(exclude_none=True))
    return StoredObject(
        id=source_record.id,
        api_version=source_record.api_version,
        kind=source_record.kind,
        practice_id=source_record.practice_id,
        revision=source_record.revision,
        created_at=source_record.created_at,
        created_by=source_record.created_by,
        content_hash=source_record.content_hash,
        supersedes=source_record.supersedes,
        labels=source_record.labels,
        body=body,
    )


def _evidence_anchor_to_stored_object(anchor: EvidenceAnchor) -> StoredObject:
    """The ``EvidenceAnchor`` counterpart of ``_source_record_to_stored_object``."""
    body: dict[str, Any] = json.loads(anchor.model_dump_json(exclude_none=True))
    return StoredObject(
        id=anchor.id,
        api_version=anchor.api_version,
        kind=anchor.kind,
        practice_id=anchor.practice_id,
        revision=anchor.revision,
        created_at=anchor.created_at,
        created_by=anchor.created_by,
        content_hash=anchor.content_hash,
        supersedes=anchor.supersedes,
        labels=anchor.labels,
        body=body,
    )


class SourceRecordService:
    """docs/spec/02_DOMAIN_MODEL.md section 2.8, implemented per
    task-packets/E3-T01.yaml. Owns exactly one method, ``create`` — see the
    module docstring's "No update/mutate method" section.
    """

    def __init__(self, record: RecordRevisionWithEvent) -> None:
        self._record = record

    def create(
        self,
        source_record: SourceRecord,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``source_record`` as revision 1, plus a
        ``source_record.created`` event, atomically.

        ``source_record`` must already be a fully valid ``SourceRecord`` —
        its own ``id``/``content_hash``/``created_at``/``created_by`` are
        minted by the caller (this service does not generate identifiers or
        compute hashes on the caller's behalf, matching
        ``ResearchScoreService.create``'s own convention); ``revision`` must
        be ``1``.

        Raises:
            ValueError: ``source_record.revision`` is not ``1``.
        """
        if source_record.revision != 1:
            raise ValueError(
                f"SourceRecord.revision must be 1 for create(), got {source_record.revision!r}"
            )

        obj = _source_record_to_stored_object(source_record)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_SOURCE_RECORD_CREATED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=source_record.id,
            object_revision=1,
            payload={
                "source_type": source_record.source_type,
                "primary_secondary_derived": source_record.primary_secondary_derived,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored


class EvidenceAnchorService:
    """docs/spec/02_DOMAIN_MODEL.md section 2.9, implemented per
    task-packets/E3-T01.yaml. Owns exactly one method, ``create`` — see the
    module docstring's "No update/mutate method" section.
    """

    def __init__(self, record: RecordRevisionWithEvent) -> None:
        self._record = record

    def create(
        self,
        anchor: EvidenceAnchor,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``anchor`` as revision 1, plus an
        ``evidence_anchor.created`` event, atomically.

        ``anchor`` must already be a fully valid ``EvidenceAnchor`` — in
        particular, it has already passed
        ``EvidenceAnchor._exact_resolution_or_explicit_reason`` (the
        exact-anchor-or-explicit-reason invariant is enforced at
        construction time, before this service ever sees the object, not
        re-checked here) — its own ``id``/``content_hash``/``created_at``/
        ``created_by`` are minted by the caller; ``revision`` must be ``1``.

        Raises:
            ValueError: ``anchor.revision`` is not ``1``.
        """
        if anchor.revision != 1:
            raise ValueError(
                f"EvidenceAnchor.revision must be 1 for create(), got {anchor.revision!r}"
            )

        obj = _evidence_anchor_to_stored_object(anchor)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_EVIDENCE_ANCHOR_CREATED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=anchor.id,
            object_revision=1,
            payload={
                "relation": anchor.relation,
                "anchor_kind": anchor.anchor_kind,
                "anchor_validation_status": anchor.anchor_validation_status,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored
