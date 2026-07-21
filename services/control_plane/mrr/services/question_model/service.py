"""``QuestionModelService`` (task-packets/K1-T04.yaml): the application-layer
service that creates versioned ``QuestionModel`` objects and drives them
through ``mrr.domain.lifecycles.QUESTION_MODEL_LIFECYCLE`` (``draft ->
accepted -> superseded``), recording a domain event on every transition it
implements.

--- Why this exists only now, not in K1-T03 -----------------------------

K1-T01's own ``specification_gaps`` named this explicitly: "This packet does
not build QuestionModelService/ConceptCharterService/a WRITE-side
MethodProtocolService (create/submit/lock/amend) — it only reads
already-accepted/locked instances via the generic ObjectRepository. If
task-packets/K1-T04.yaml ... discovers it needs one of these services, that
is K1-T04's own scope to add" — this packet's first real run must actually
CREATE and ACCEPT a real ``QuestionModel`` for the model-collapse question,
rather than bypassing its lifecycle with a raw, test-only ``_seed_generic``
insert (task-packets/K1-T04.yaml objective (b)).

--- Wiring shape: mirrors MethodProfileService EXACTLY ---------------------

Copied deliberately from
``mrr.services.method_profile.service.MethodProfileService`` (the packet's
own named template, task-packets/K1-T04.yaml derived_decisions (f)): one
``bind_unit_of_work``, one local ``RecordRevisionWithEvent``-typed callable (a
local copy, not a shared import, per that module's own established
precedent), ``propose`` (draft, revision 1, event
``question_model.proposed``) + ``accept`` (draft -> accepted, event
``question_model.accepted``). Only these two transitions are implemented —
this run's own single, first-time confirmatory pass needs no ``supersede``
call, mirroring ``EvidenceMatrixService``'s own disclosed "only implement
what this run's own caller needs" restraint, applied here to
``QUESTION_MODEL_LIFECYCLE``'s undrawn ``superseded``-transition instead.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import QuestionModel, QuestionModelStatus, Urn
from mrr.domain.exceptions import (
    InvalidTransitionError,
    ObjectNotFoundError,
    QuestionModelNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import QUESTION_MODEL_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: The create/draft-publication event — additive (no
#: docs/spec/03_API_AND_EVENTS.md section 5.2 entry exists for it), mirroring
#: ``MethodProfileService``'s own identical "floor not ceiling" precedent for
#: its own ``method_profile.proposed`` event.
_PROPOSED_EVENT_TYPE = "question_model.proposed"

#: MRR-MTH-001's own "a question addressed through the runtime MUST be
#: represented as an accepted QuestionModel" reading: acceptance is the event
#: this packet's own additive choice records (mirroring
#: ``MethodProfileService``'s "profile activation" reading of MRR-MTH-019 for
#: its own ``.accepted`` event).
_ACCEPTED_EVENT_TYPE = "question_model.accepted"

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``propose()`` with a non-draft initial status — mirrors
#: ``MethodProfileService``'s/``EvidenceMatrixService``'s identical
#: ``_NEW_*_SENTINEL_STATE`` convention. Never a member of
#: ``QUESTION_MODEL_LIFECYCLE.states``.
_NEW_QUESTION_MODEL_SENTINEL_STATE = "<new>"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments are
#: bound — a local copy, not a shared import, across separate service
#: modules (see ``mrr.services.source_family.service``'s own module docstring
#: for why).
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical rationale to ``mrr.services.method_profile.service._EventJournal``
    (causal-chain lookup only).
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
    ``QuestionModelService`` depends on for its atomic writes. Production
    wiring and integration tests call this once; DB-free unit tests pass
    their own trivial callable of the same shape, backed by in-memory fakes.
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


def _question_model_to_stored_object(question_model: QuestionModel) -> StoredObject:
    """Convert an already-valid ``QuestionModel`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip —
    matching every other service's own ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(question_model.model_dump_json(exclude_none=True))
    return StoredObject(
        id=question_model.id,
        api_version=question_model.api_version,
        kind=question_model.kind,
        practice_id=question_model.practice_id,
        revision=question_model.revision,
        created_at=question_model.created_at,
        created_by=question_model.created_by,
        content_hash=question_model.content_hash,
        supersedes=question_model.supersedes,
        labels=question_model.labels,
        body=body,
    )


class QuestionModelService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``QuestionModel``
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
        question_model: QuestionModel,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``question_model`` as revision 1, plus a
        ``question_model.proposed`` event, atomically. Rejects any initial
        status other than ``QUESTION_MODEL_LIFECYCLE.initial_state``
        (``"draft"``).

        ``question_model`` must already be a fully valid ``QuestionModel`` —
        its own ``id``/``content_hash``/``created_at``/``created_by`` are
        minted by the caller; ``question_model.revision`` must be ``1``.
        """
        if question_model.status != QUESTION_MODEL_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                QUESTION_MODEL_LIFECYCLE.name,
                _NEW_QUESTION_MODEL_SENTINEL_STATE,
                question_model.status,
            )
        if question_model.revision != 1:
            raise ValueError(
                f"QuestionModel.revision must be 1 for propose(), got {question_model.revision!r}"
            )

        obj = _question_model_to_stored_object(question_model)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_PROPOSED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=question_model.id,
            object_revision=1,
            payload={"status": question_model.status},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def accept(
        self, question_model_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``draft -> accepted`` (MRR-MTH-001: an accepted ``QuestionModel``
        is the prerequisite before an executor task for it is negotiated).
        """
        return self._transition(
            question_model_id,
            "accepted",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_ACCEPTED_EVENT_TYPE,
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, question_model_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(question_model_id)
        except ObjectNotFoundError:
            raise QuestionModelNotFoundError(question_model_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — identical rationale to
        ``MethodProfileService``'s own ``_last_event_id_for``.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _transition(
        self,
        question_model_id: Urn,
        to_status: QuestionModelStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
    ) -> StoredObject:
        """Shared implementation for every ``QUESTION_MODEL_LIFECYCLE`` edge
        method — identical structure to ``MethodProfileService._transition``.
        """
        latest = self._get_latest_or_raise(question_model_id)
        from_status = latest.body["status"]
        QUESTION_MODEL_LIFECYCLE.assert_transition(from_status, to_status)

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
            causation_id=self._last_event_id_for(question_model_id),
            correlation_id=correlation_id,
            object_id=question_model_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored
