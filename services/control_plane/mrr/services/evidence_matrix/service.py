"""``EvidenceMatrixService`` (task-packets/K1-T03.yaml): persists
``EvidenceMatrix`` objects and drives them through
``mrr.domain.lifecycles.EVIDENCE_MATRIX_LIFECYCLE`` (``draft -> active ->
frozen -> superseded``), recording a domain event on every transition
(MRR-MTH-019). Mirrors ``mrr.services.method_profile.service.
MethodProfileService``'s EXACT shape (one ``bind_unit_of_work``, one
``RecordRevisionWithEvent``-typed local callable, one service class,
create-then-transition via a shared ``_transition`` helper, a minimal local
``_EventJournal`` Protocol for causal-chain lookup) — the template
task-packets/K1-T03.yaml names explicitly for the three new K1-T03 services.

Only ``create``/``activate``/``freeze`` are implemented — driving a matrix to
``superseded`` is not needed by ``run_synthesis_evidence_loop`` (this
packet's own only caller) and is left, like ``MethodProfileService``'s own
undrawn superseded-outgoing-edge open question, for a future task that
actually needs it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import EvidenceMatrix, EvidenceMatrixStatus, Urn
from mrr.domain.exceptions import (
    EvidenceMatrixNotFoundError,
    InvalidTransitionError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import EVIDENCE_MATRIX_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: The create/draft-publication event — additive (no
#: docs/spec/03_API_AND_EVENTS.md section 5.2 entry exists for it),
#: mirroring ``MethodProfileService``'s own identical "floor not ceiling"
#: precedent for its own ``method_profile.proposed`` event.
_CREATED_EVENT_TYPE = "evidence_matrix.created"
_ACTIVATED_EVENT_TYPE = "evidence_matrix.activated"
_FROZEN_EVENT_TYPE = "evidence_matrix.frozen"

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``create()`` with a non-draft initial status — mirrors
#: ``ResearchScoreService``'s/``ClaimService``'s/``MethodProfileService``'s
#: identical ``_NEW_*_SENTINEL_STATE`` convention. Never a member of
#: ``EVIDENCE_MATRIX_LIFECYCLE.states``.
_NEW_MATRIX_SENTINEL_STATE = "<new>"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments are
#: bound — a local copy, not a shared import, across separate service
#: modules (see ``mrr.services.source_family.service``'s own module
#: docstring for why).
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical rationale to ``mrr.services.method_profile.service._EventJournal``
    (causal-chain lookup only; deliberately smaller than the generic
    ``mrr.provenance.log.EventLog[TTx]`` Protocol).
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
    ``EvidenceMatrixService`` depends on for its atomic writes. Production
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


def _matrix_to_stored_object(matrix: EvidenceMatrix) -> StoredObject:
    """Convert an already-valid ``EvidenceMatrix`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip —
    no added keys — matching every other service's own
    ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(matrix.model_dump_json(exclude_none=True))
    return StoredObject(
        id=matrix.id,
        api_version=matrix.api_version,
        kind=matrix.kind,
        practice_id=matrix.practice_id,
        revision=matrix.revision,
        created_at=matrix.created_at,
        created_by=matrix.created_by,
        content_hash=matrix.content_hash,
        supersedes=matrix.supersedes,
        labels=matrix.labels,
        body=body,
    )


class EvidenceMatrixService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``EvidenceMatrix``,
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
        matrix: EvidenceMatrix,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``matrix`` as revision 1, plus an ``evidence_matrix.created``
        event, atomically. Rejects any initial status other than
        ``EVIDENCE_MATRIX_LIFECYCLE.initial_state`` (``"draft"``).

        ``matrix`` must already be a fully valid ``EvidenceMatrix`` — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller; ``matrix.revision`` must be ``1``.
        """
        if matrix.status != EVIDENCE_MATRIX_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                EVIDENCE_MATRIX_LIFECYCLE.name, _NEW_MATRIX_SENTINEL_STATE, matrix.status
            )
        if matrix.revision != 1:
            raise ValueError(
                f"EvidenceMatrix.revision must be 1 for create(), got {matrix.revision!r}"
            )

        obj = _matrix_to_stored_object(matrix)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_CREATED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=matrix.id,
            object_revision=1,
            payload={"protocol_id": matrix.protocol_id, "row_count": len(matrix.rows)},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def activate(
        self, matrix_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``draft -> active``."""
        return self._transition(
            matrix_id,
            "active",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_ACTIVATED_EVENT_TYPE,
        )

    def freeze(
        self, matrix_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``active -> frozen`` (MTH-020's own crate-sealing precondition:
        this run's matrix is frozen before the crate seals).
        """
        return self._transition(
            matrix_id,
            "frozen",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_FROZEN_EVENT_TYPE,
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, matrix_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(matrix_id)
        except ObjectNotFoundError:
            raise EvidenceMatrixNotFoundError(matrix_id) from None

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
        matrix_id: Urn,
        to_status: EvidenceMatrixStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
    ) -> StoredObject:
        """Shared implementation for every ``EVIDENCE_MATRIX_LIFECYCLE`` edge
        method — identical structure to ``MethodProfileService._transition``.
        """
        latest = self._get_latest_or_raise(matrix_id)
        from_status = latest.body["status"]
        EVIDENCE_MATRIX_LIFECYCLE.assert_transition(from_status, to_status)

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
            causation_id=self._last_event_id_for(matrix_id),
            correlation_id=correlation_id,
            object_id=matrix_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored
