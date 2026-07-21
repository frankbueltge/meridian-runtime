"""``MethodRulingService`` (task-packets/K1-T03.yaml): persists
``MethodRuling`` objects and drives them through
``mrr.domain.lifecycles.METHOD_RULING_LIFECYCLE`` (``pending -> issued ->
superseded``), recording a domain event on every transition (MRR-MTH-019:
"ruling issuance... MUST each emit a domain event"). Mirrors
``mrr.services.method_profile.service.MethodProfileService``'s EXACT shape —
see that module's own docstring for the full wiring rationale, reused
identically here.

Only ``create``/``issue`` are implemented — driving a ruling to
``superseded`` is not needed by ``run_synthesis_evidence_loop`` (this
packet's own only caller) and is left for a future task, mirroring
``EvidenceMatrixService``'s identical scope limitation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import MethodRuling, MethodRulingStatus, Urn
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MethodRulingNotFoundError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import METHOD_RULING_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

_CREATED_EVENT_TYPE = "method_ruling.created"
_ISSUED_EVENT_TYPE = "method_ruling.issued"

#: Sentinel "from" state — see ``mrr.services.evidence_matrix.service``'s
#: identical ``_NEW_MATRIX_SENTINEL_STATE`` for the full rationale. Never a
#: member of ``METHOD_RULING_LIFECYCLE.states``.
_NEW_RULING_SENTINEL_STATE = "<new>"

RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical rationale to ``mrr.services.evidence_matrix.service._EventJournal``.
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
    ``MethodRulingService`` depends on for its atomic writes. Production
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


def _ruling_to_stored_object(ruling: MethodRuling) -> StoredObject:
    """Convert an already-valid ``MethodRuling`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    """
    body: dict[str, Any] = json.loads(ruling.model_dump_json(exclude_none=True))
    return StoredObject(
        id=ruling.id,
        api_version=ruling.api_version,
        kind=ruling.kind,
        practice_id=ruling.practice_id,
        revision=ruling.revision,
        created_at=ruling.created_at,
        created_by=ruling.created_by,
        content_hash=ruling.content_hash,
        supersedes=ruling.supersedes,
        labels=ruling.labels,
        body=body,
    )


class MethodRulingService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``MethodRuling``,
    implemented per task-packets/K1-T03.yaml. See the module docstring for
    the full design rationale.
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

    def create(
        self,
        ruling: MethodRuling,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``ruling`` as revision 1, plus a ``method_ruling.created``
        event, atomically. Rejects any initial status other than
        ``METHOD_RULING_LIFECYCLE.initial_state`` (``"pending"``).

        ``ruling`` must already be a fully valid ``MethodRuling`` — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller; ``ruling.revision`` must be ``1``.
        """
        if ruling.status != METHOD_RULING_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                METHOD_RULING_LIFECYCLE.name, _NEW_RULING_SENTINEL_STATE, ruling.status
            )
        if ruling.revision != 1:
            raise ValueError(
                f"MethodRuling.revision must be 1 for create(), got {ruling.revision!r}"
            )

        obj = _ruling_to_stored_object(ruling)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_CREATED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=ruling.id,
            object_revision=1,
            payload={
                "protocol_id": ruling.protocol_id,
                "applies_to_analysis": ruling.applies_to_analysis,
                "ruled_ceiling": ruling.ruled_ceiling,
                "ruling_basis": ruling.ruling_basis,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def issue(
        self, ruling_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``pending -> issued`` (MRR-MTH-019: ruling issuance MUST emit a
        domain event).
        """
        return self._transition(
            ruling_id,
            "issued",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_ISSUED_EVENT_TYPE,
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, ruling_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(ruling_id)
        except ObjectNotFoundError:
            raise MethodRulingNotFoundError(ruling_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _transition(
        self,
        ruling_id: Urn,
        to_status: MethodRulingStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
    ) -> StoredObject:
        """Shared implementation for every ``METHOD_RULING_LIFECYCLE`` edge
        method — identical structure to ``EvidenceMatrixService._transition``.
        """
        latest = self._get_latest_or_raise(ruling_id)
        from_status = latest.body["status"]
        METHOD_RULING_LIFECYCLE.assert_transition(from_status, to_status)

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
            causation_id=self._last_event_id_for(ruling_id),
            correlation_id=correlation_id,
            object_id=ruling_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored
