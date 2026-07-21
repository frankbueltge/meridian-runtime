"""``ConceptCharterService`` (task-packets/K1-T04.yaml): the application-layer
service that creates versioned ``ConceptCharter`` objects and drives them
through ``mrr.domain.lifecycles.CONCEPT_CHARTER_LIFECYCLE`` (``draft ->
accepted -> superseded``), recording a domain event on every transition it
implements.

Mirrors ``mrr.services.question_model.service.QuestionModelService``'s EXACT
shape (itself mirroring ``mrr.services.method_profile.service.
MethodProfileService``, task-packets/K1-T04.yaml derived_decisions (f)): one
``bind_unit_of_work``, one local ``RecordRevisionWithEvent``-typed callable (a
local copy, not a shared import), ``propose`` (draft, revision 1, event
``concept_charter.proposed``) + ``accept`` (draft -> accepted, event
``concept_charter.accepted``). Only these two transitions are implemented —
no ``supersede``, mirroring ``QuestionModelService``'s own identical
restraint.

The ``operationalizes`` edge (``ConceptCharter`` -> ``QuestionModel``,
docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3) is NOT written by this
service — it is a cross-cutting, run-level concern written by
``mrr.services.cli.synthesis_setup.establish_and_run_synthesis`` via a local
edge-writing helper, mirroring how ``governed_by_protocol``/``ruled_by``
edges are written by ``run_synthesis_evidence_loop`` rather than by any
single object's own service (task-packets/K1-T04.yaml derived_decisions (g)).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import ConceptCharter, ConceptCharterStatus, Urn
from mrr.domain.exceptions import (
    ConceptCharterNotFoundError,
    InvalidTransitionError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import CONCEPT_CHARTER_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

_PROPOSED_EVENT_TYPE = "concept_charter.proposed"
_ACCEPTED_EVENT_TYPE = "concept_charter.accepted"

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``propose()`` with a non-draft initial status — mirrors
#: ``QuestionModelService``'s identical convention. Never a member of
#: ``CONCEPT_CHARTER_LIFECYCLE.states``.
_NEW_CONCEPT_CHARTER_SENTINEL_STATE = "<new>"

#: A local copy, not a shared import, across separate service modules — see
#: ``mrr.services.source_family.service``'s own module docstring for why.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical rationale to ``mrr.services.question_model.service._EventJournal``.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple, producing the ``RecordRevisionWithEvent`` callable
    ``ConceptCharterService`` depends on for its atomic writes.
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


def _concept_charter_to_stored_object(concept_charter: ConceptCharter) -> StoredObject:
    """Convert an already-valid ``ConceptCharter`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    """
    body: dict[str, Any] = json.loads(concept_charter.model_dump_json(exclude_none=True))
    return StoredObject(
        id=concept_charter.id,
        api_version=concept_charter.api_version,
        kind=concept_charter.kind,
        practice_id=concept_charter.practice_id,
        revision=concept_charter.revision,
        created_at=concept_charter.created_at,
        created_by=concept_charter.created_by,
        content_hash=concept_charter.content_hash,
        supersedes=concept_charter.supersedes,
        labels=concept_charter.labels,
        body=body,
    )


class ConceptCharterService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``ConceptCharter``
    registry, implemented per task-packets/K1-T04.yaml. See the module
    docstring for the full design rationale.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record

    def propose(
        self,
        concept_charter: ConceptCharter,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``concept_charter`` as revision 1, plus a
        ``concept_charter.proposed`` event, atomically. Rejects any initial
        status other than ``CONCEPT_CHARTER_LIFECYCLE.initial_state``
        (``"draft"``).
        """
        if concept_charter.status != CONCEPT_CHARTER_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                CONCEPT_CHARTER_LIFECYCLE.name,
                _NEW_CONCEPT_CHARTER_SENTINEL_STATE,
                concept_charter.status,
            )
        if concept_charter.revision != 1:
            raise ValueError(
                f"ConceptCharter.revision must be 1 for propose(), got {concept_charter.revision!r}"
            )

        obj = _concept_charter_to_stored_object(concept_charter)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_PROPOSED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=concept_charter.id,
            object_revision=1,
            payload={"status": concept_charter.status, "entry_count": len(concept_charter.entries)},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def accept(
        self, concept_charter_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``draft -> accepted``."""
        return self._transition(
            concept_charter_id,
            "accepted",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_ACCEPTED_EVENT_TYPE,
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, concept_charter_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(concept_charter_id)
        except ObjectNotFoundError:
            raise ConceptCharterNotFoundError(concept_charter_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _transition(
        self,
        concept_charter_id: Urn,
        to_status: ConceptCharterStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
    ) -> StoredObject:
        """Shared implementation for every ``CONCEPT_CHARTER_LIFECYCLE`` edge
        method — identical structure to ``QuestionModelService._transition``.
        """
        latest = self._get_latest_or_raise(concept_charter_id)
        from_status = latest.body["status"]
        CONCEPT_CHARTER_LIFECYCLE.assert_transition(from_status, to_status)

        new_revision = latest.revision + 1
        now = datetime.now(UTC)

        new_body = dict(latest.body)
        new_body["status"] = to_status
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = actor
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash

        obj = StoredObject(
            id=latest.id,
            api_version=latest.api_version,
            kind=latest.kind,
            practice_id=latest.practice_id,
            revision=new_revision,
            created_at=now,
            created_by=actor,
            content_hash=new_content_hash,
            supersedes=latest.supersedes,
            labels=latest.labels,
            body=new_body,
        )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=event_type,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(concept_charter_id),
            correlation_id=correlation_id,
            object_id=concept_charter_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored
