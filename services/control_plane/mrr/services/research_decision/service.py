"""``ResearchDecisionService`` (task-packets/K1-T03.yaml): persists
``ResearchDecision`` objects — an append-only revision-1 write plus one
domain event, atomically, via the existing E1-T06 unit-of-work primitive.
Mirrors ``mrr.services.source_family.service.SourceFamilyService``'s EXACT
shape (task-packets/K1-T03.yaml objective (b): "create only [issued,
revision 1, append-only — mirrors SourceFamilyService's own single-method
shape exactly, since RESEARCH_DECISION_LIFECYCLE has zero legal
transitions]").

``mrr.domain.lifecycles.RESEARCH_DECISION_LIFECYCLE`` is declared with
exactly one state (``"issued"``) and an EMPTY transition set — there is
structurally no lifecycle edge for this service to drive, so it exposes
exactly one method, ``create``, and never reads prior state (no
``_get_latest_or_raise``, no event-log dependency for causal chaining: a
brand-new ``ResearchDecision`` identity has no prior event to chain from,
exactly like ``SourceFamilyService``'s own identical situation).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import ResearchDecision, Urn
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: task-packets/K1-T03.yaml invariant (mirroring E3-T03's identical one):
#: persisting a decision writes exactly one domain event with full NFR-001
#: provenance, atomically with the revision.
_EVENT_RESEARCH_DECISION_CREATED = "research_decision.created"

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
    triple, producing the ``RecordRevisionWithEvent`` callable
    ``ResearchDecisionService`` depends on for its one atomic write.
    Production wiring and integration tests call this once; DB-free unit
    tests pass their own trivial callable of the same shape, backed by an
    in-memory fake, instead.
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


def _decision_to_stored_object(decision: ResearchDecision) -> StoredObject:
    """Convert an already-valid ``ResearchDecision`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    """
    body: dict[str, Any] = json.loads(decision.model_dump_json(exclude_none=True))
    return StoredObject(
        id=decision.id,
        api_version=decision.api_version,
        kind=decision.kind,
        practice_id=decision.practice_id,
        revision=decision.revision,
        created_at=decision.created_at,
        created_by=decision.created_by,
        content_hash=decision.content_hash,
        supersedes=decision.supersedes,
        labels=decision.labels,
        body=body,
    )


class ResearchDecisionService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``ResearchDecision``,
    implemented per task-packets/K1-T03.yaml. Owns exactly one method,
    ``create`` — see the module docstring.
    """

    def __init__(self, record: RecordRevisionWithEvent) -> None:
        self._record = record

    def create(
        self,
        decision: ResearchDecision,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``decision`` as revision 1, plus a
        ``research_decision.created`` event, atomically.

        ``decision`` must already be a fully valid ``ResearchDecision`` —
        its own ``id``/``content_hash``/``created_at``/``created_by`` are
        minted by the caller; ``revision`` must be ``1``.

        Raises:
            ValueError: ``decision.revision`` is not ``1``.
        """
        if decision.revision != 1:
            raise ValueError(
                f"ResearchDecision.revision must be 1 for create(), got {decision.revision!r}"
            )

        obj = _decision_to_stored_object(decision)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_RESEARCH_DECISION_CREATED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=decision.id,
            object_revision=1,
            payload={
                "decision_type": decision.decision_type,
                "protocol_id": decision.protocol_id,
                "applies_to_analysis": decision.applies_to_analysis,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored
