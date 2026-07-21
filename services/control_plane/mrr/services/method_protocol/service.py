"""``MethodProtocolService`` (task-packets/K1-T04.yaml): the application-layer
service that creates versioned ``MethodProtocol`` objects and drives them
through the first three edges of ``mrr.domain.lifecycles.
METHOD_PROTOCOL_LIFECYCLE`` — ``draft -> reviewed -> locked`` — recording a
domain event on every transition it implements (MRR-MTH-019/MRR-MTH-007).

--- Scope: exactly the three transitions this run's own caller needs -------

``METHOD_PROTOCOL_LIFECYCLE`` (task-packets/K1-T01.yaml, amended by commit
1d453bf) declares five edges: ``(draft, reviewed)``, ``(reviewed, locked)``,
``(locked, amended)``, ``(locked, executed)``, ``(amended, reviewed)``. This
service implements only the first two plus the create step —
``create``/``submit_for_review``/``lock`` — mirroring
``mrr.services.evidence_matrix.service.EvidenceMatrixService``'s own
disclosed "only `create`/`activate`/`freeze` implemented; `superseded` left
for a future task that actually needs it" restraint, applied here to
``amend``/``execute`` instead (task-packets/K1-T04.yaml forbidden_changes: "a
future task's job if this run is ever amended").

--- `lock` sets `locked_at`/`locked_by`, not just `status` ------------------

Unlike every other transition in this codebase's service layer (which flips
only ``status``), ``lock`` ALSO sets ``locked_at``/``locked_by`` on the new
revision's body — ``mrr.contracts.method_protocol.MethodProtocol``'s own
``_lock_fields_match_status`` validator requires both non-null exactly when
``status`` is ``locked``/``amended``/``executed`` (MRR-MTH-007: "Locking
binds the exact content hash, actor, and time"). The lock hash ITSELF is not
a separate field this service computes — it IS the locked revision's own
``content_hash``, already produced by ``compute_content_hash`` exactly like
every other transition (task-packets/K1-T01.yaml derived_decisions: "Lock
hash IS baseObject.content_hash, not a new field") — a caller resolves it by
reading the returned ``StoredObject.content_hash`` after ``lock`` returns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import MethodProtocol, Urn
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MethodProtocolNotFoundError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import METHOD_PROTOCOL_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.persistence.unit_of_work import (
    bind_unit_of_work as bind_unit_of_work,
)
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent

_CREATED_EVENT_TYPE = "method_protocol.created"
_REVIEWED_EVENT_TYPE = "method_protocol.reviewed"
_LOCKED_EVENT_TYPE = "method_protocol.locked"

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``create()`` with a non-draft initial status — mirrors
#: ``EvidenceMatrixService``'s/``QuestionModelService``'s identical
#: convention. Never a member of ``METHOD_PROTOCOL_LIFECYCLE.states``.
_NEW_PROTOCOL_SENTINEL_STATE = "<new>"


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    identical rationale to every other Layer-1 service's own
    ``_EventJournal``.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def _protocol_to_stored_object(protocol: MethodProtocol) -> StoredObject:
    """Convert an already-valid ``MethodProtocol`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    """
    body: dict[str, Any] = json.loads(protocol.model_dump_json(exclude_none=True))
    return StoredObject(
        id=protocol.id,
        api_version=protocol.api_version,
        kind=protocol.kind,
        practice_id=protocol.practice_id,
        revision=protocol.revision,
        created_at=protocol.created_at,
        created_by=protocol.created_by,
        content_hash=protocol.content_hash,
        supersedes=protocol.supersedes,
        labels=protocol.labels,
        body=body,
    )


class MethodProtocolService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``MethodProtocol``
    registry, scoped per task-packets/K1-T04.yaml to exactly the three
    transitions this run's own caller needs. See the module docstring for
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
        protocol: MethodProtocol,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``protocol`` as revision 1, plus a
        ``method_protocol.created`` event, atomically. Rejects any initial
        status other than ``METHOD_PROTOCOL_LIFECYCLE.initial_state``
        (``"draft"``).

        ``protocol`` must already be a fully valid ``MethodProtocol`` (its
        own ``locked_at``/``locked_by``/``amendment`` null, per the contract's
        own co-occurrence validators for ``status == "draft"``) — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller; ``protocol.revision`` must be ``1``.
        """
        if protocol.status != METHOD_PROTOCOL_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                METHOD_PROTOCOL_LIFECYCLE.name, _NEW_PROTOCOL_SENTINEL_STATE, protocol.status
            )
        if protocol.revision != 1:
            raise ValueError(
                f"MethodProtocol.revision must be 1 for create(), got {protocol.revision!r}"
            )

        obj = _protocol_to_stored_object(protocol)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_CREATED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=protocol.id,
            object_revision=1,
            payload={"status": protocol.status, "profile_id": protocol.profile_id},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def submit_for_review(
        self, protocol_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``draft -> reviewed``. Changes only ``status`` — no additional
        field co-occurrence to satisfy at this transition (the contract's
        own lock-fields validator only fires from ``locked`` onward).
        """
        latest = self._get_latest_or_raise(protocol_id)
        from_status = latest.body["status"]
        METHOD_PROTOCOL_LIFECYCLE.assert_transition(from_status, "reviewed")

        new_revision = latest.revision + 1
        now = datetime.now(UTC)
        new_body = dict(latest.body)
        new_body["status"] = "reviewed"
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
            event_type=_REVIEWED_EVENT_TYPE,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(protocol_id),
            correlation_id=correlation_id,
            object_id=protocol_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": "reviewed"},
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    def lock(
        self, protocol_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``reviewed -> locked`` (MRR-MTH-007: "Locking binds the exact
        content hash, actor, and time"). Sets ``locked_at``/``locked_by`` on
        the new revision's body IN ADDITION to ``status`` — the ONE
        transition this service implements that changes more than the status
        field alone. The lock hash itself is that revision's own
        ``content_hash``, computed by ``compute_content_hash`` exactly like
        every other transition — resolvable from the returned
        ``StoredObject.content_hash`` after this call returns.
        """
        latest = self._get_latest_or_raise(protocol_id)
        from_status = latest.body["status"]
        METHOD_PROTOCOL_LIFECYCLE.assert_transition(from_status, "locked")

        new_revision = latest.revision + 1
        now = datetime.now(UTC)
        new_body = dict(latest.body)
        new_body["status"] = "locked"
        new_body["locked_at"] = now.isoformat()
        new_body["locked_by"] = actor
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
            event_type=_LOCKED_EVENT_TYPE,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(protocol_id),
            correlation_id=correlation_id,
            object_id=protocol_id,
            object_revision=new_revision,
            payload={
                "from_status": from_status,
                "to_status": "locked",
                "locked_by": actor,
                "content_hash": new_content_hash,
            },
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, protocol_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(protocol_id)
        except ObjectNotFoundError:
            raise MethodProtocolNotFoundError(protocol_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None
