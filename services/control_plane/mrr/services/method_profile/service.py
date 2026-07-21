"""``MethodProfileService`` (task-packets/K0-T01.yaml): the application-layer
service that creates versioned ``MethodProfile`` objects and drives them
through ``mrr.domain.lifecycles.METHOD_PROFILE_LIFECYCLE`` (``draft ->
accepted -> superseded``), recording a domain event on every transition
(MRR-MTH-019: "Profile activation ... MUST each emit a domain event"), plus
``find_accepted_by_capability`` — the read-side matching primitive "which
currently-accepted profiles declare this capability name" the K0-T02
capability dispatch layer and later K1 tasks consume.

This is Layer 1 groundwork only (docs/spec/08_RESEARCH_METHOD_KERNEL.md
section 1/3): it introduces no executor, no claim-ceiling enforcement, and
no protocol lock/amendment machinery (all K1 concerns per this task's
``forbidden_changes``); it adds no new path around Research Score approval,
Task Bundle negotiation, Run Manifests, or Evidence Crate sealing
(MRR-MTH-020) — nothing here touches any of those objects or services at
all.

--- Identity and revisioning (task-packets/K0-T01.yaml derived_decisions) --

``MethodProfile`` is UNSIGNED and revision-based, like ``ResearchScore``/
``Claim`` — not event-only like ``TaskBundle``. Every lifecycle transition
mints a NEW REVISION with ``status`` changed in the body (content hash
recomputed), exactly mirroring
``mrr.services.research_score.service.ResearchScoreService._transition``'s
own pattern. Object identity is the profile's own freshly minted
``BaseObject.id`` (``urn:mrr:method-profile:<ulid>``) — NOT ``profile_key``
(a body-only, human-legible slug with no urn shape; see
``mrr.contracts.method_profile``'s module docstring).

--- ``find_accepted_by_capability``: the ``CapabilityRegistry`` pattern -----

``mrr.domain.repositories.ObjectRepository`` offers no "list, or query by
body field" operation (``get_latest``/``get_revision``/``list_revisions``
all take only an ``id``), and ``profile_key`` is not the repository key
(above) — so this method mirrors
``mrr.services.capability_registry.service.CapabilityRegistry.
find_nodes_with_capability``'s own EXACT pattern: scan the event log for
``method_profile.accepted`` events, collect distinct ``object_id``s, and
re-resolve each candidate's CURRENT state via ``ObjectRepository.get_latest``
— keeping only ids whose latest revision's ``status`` is still
``"accepted"`` (a later ``supersede`` call moves that same id's current
status to ``"superseded"``, and re-resolving the latest revision rather
than trusting the historical accepted event is exactly what excludes it).
This is a **matching primitive, not an authorization decision** — mirroring
``find_nodes_with_capability``'s own "It does not grant permission"
discipline (docs/spec/01_SYSTEM_SPEC.md section 7.3): the return value
answers "which profiles declare capability X and are currently accepted",
nothing more.

--- Wiring shape --------------------------------------------------------

Copied deliberately from ``mrr.services.research_score.service.
ResearchScoreService`` and ``mrr.services.capability_registry.service.
CapabilityRegistry`` — task-packets/K0-T01.yaml names both explicitly as the
patterns to reuse:

- reads go through ``mrr.domain.repositories.ObjectRepository`` (a
  ``Protocol``, structurally satisfied by both
  ``mrr.persistence.repositories.PostgresObjectRepository`` and a
  hand-written unit-test fake);
- the atomic write goes through ``RecordRevisionWithEvent`` — the same
  ``Callable`` shape both sibling services depend on, bound to the real
  E1-T06 ``record_object_revision_with_event`` by ``bind_unit_of_work``
  below (production wiring and integration tests), or backed by a DB-free
  fake unit of work (unit tests);
- a minimal local ``_EventJournal`` Protocol (``read_all`` only) covers both
  this service's causation-chain lookup and ``find_accepted_by_capability``'s
  profile discovery — this service never calls ``append`` directly (that
  stays inside ``bind_unit_of_work``'s closure).

``bind_unit_of_work`` here is a local copy of the module-level
``bind_unit_of_work`` function in ``ResearchScoreService``/
``CapabilityRegistry``'s own few lines, not an import of it or a shared
extraction — matching task-packets/E2-T02.yaml's own explicit precedent
("replicate the small pattern locally rather than refactor merged code").
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import MethodProfile, MethodProfileStatus, Urn
from mrr.domain.exceptions import (
    InvalidTransitionError,
    MethodProfileNotFoundError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import METHOD_PROFILE_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: MRR-MTH-019's event for the draft -> accepted transition specifically
#: (task-packets/K0-T01.yaml derived_decisions (g)): "Profile activation ...
#: MUST emit a domain event" is read as this transition. ``find_accepted_by_capability``
#: also uses this constant to recognize which events name a profile id worth
#: resolving — see that method's docstring.
_ACCEPTED_EVENT_TYPE = "method_profile.accepted"

#: The create/draft-publication event — additive, non-breaking, since
#: docs/spec/03_API_AND_EVENTS.md section 5.2 has no ``method_profile.*``
#: entry at all (task-packets/K0-T01.yaml derived_decisions (g), the same
#: floor-not-ceiling reading task-packets/E6-T01.yaml/E6-T02.yaml already
#: establish for their own additive event types).
_PROPOSED_EVENT_TYPE = "method_profile.proposed"

#: The accepted -> superseded transition's event.
_SUPERSEDED_EVENT_TYPE = "method_profile.superseded"

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``propose()`` with a non-draft initial status. Never a member of
#: ``METHOD_PROFILE_LIFECYCLE.states``, so it can never appear as a legal
#: transition source — mirrors
#: ``mrr.services.research_score.service._NEW_SCORE_SENTINEL_STATE`` exactly.
_NEW_PROFILE_SENTINEL_STATE = "<new>"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments are
#: bound — identical in shape to ``ResearchScoreService``'s/
#: ``CapabilityRegistry``'s own ``RecordRevisionWithEvent``. See the module
#: docstring for why this is a local copy, not a shared import.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    deliberately smaller than the generic ``mrr.provenance.log.EventLog[TTx]``
    Protocol, and not ``@runtime_checkable`` for the same reason
    ``ResearchScoreService``'s/``CapabilityRegistry``'s own identical
    Protocols are not (an ``isinstance`` check on a ``Protocol`` compares
    method names only, never signatures — mypy's static structural check is
    the real conformance guarantee).
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
    ``MethodProfileService`` depends on for atomic writes. Production wiring
    and integration tests call this once; DB-free unit tests pass their own
    trivial callable of the same shape, backed by in-memory fakes, instead.
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


def _profile_to_stored_object(profile: MethodProfile) -> StoredObject:
    """Convert an already-valid ``MethodProfile`` (id, revision, timestamps,
    and content hash already set by the caller — this service mints none of
    those for ``propose``, only for the in-place-preserving lifecycle
    transitions in ``_transition``) into the generic ``StoredObject``
    ``mrr.domain.repositories.ObjectRepository`` persists.

    ``id=profile.id`` — the object's own freshly minted identity, NOT
    ``profile.profile_key`` (task-packets/K0-T01.yaml derived_decisions (b):
    ``profile_key``/``version`` are body-only fields, unlike
    ``NodeManifest``'s urn-shaped ``node_id``).

    ``body`` is the full schema-shaped JSON object (``model_dump_json``
    round-tripped through ``json.loads``, matching
    ``_score_to_stored_object``'s/``_manifest_to_stored_object``'s own
    pattern and ``scripts/check_contracts.py``'s round-trip convention).
    """
    body: dict[str, Any] = json.loads(profile.model_dump_json(exclude_none=True))
    return StoredObject(
        id=profile.id,
        api_version=profile.api_version,
        kind=profile.kind,
        practice_id=profile.practice_id,
        revision=profile.revision,
        created_at=profile.created_at,
        created_by=profile.created_by,
        content_hash=profile.content_hash,
        supersedes=profile.supersedes,
        labels=profile.labels,
        body=body,
    )


class MethodProfileService:
    """docs/spec/08_RESEARCH_METHOD_KERNEL.md section 3's ``MethodProfile``
    registry, implemented per task-packets/K0-T01.yaml.

    Constructed with exactly the dependencies its writes and reads need,
    same as ``ResearchScoreService``/``CapabilityRegistry`` — see the module
    docstring.
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

    # ------------------------------------------------------------------
    # Creation (the draft step, MRR-MTH-019's "profile activation" reading
    # (g): the create step gets its own additive event too).
    # ------------------------------------------------------------------

    def propose(
        self,
        profile: MethodProfile,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``profile`` as revision 1, plus a ``method_profile.proposed``
        event, atomically. Rejects any initial status other than ``draft``.

        ``profile`` must already be a fully valid ``MethodProfile`` — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller (this service does not generate identifiers or compute
        hashes on the caller's behalf); ``profile.revision`` must be ``1``.

        Raises:
            mrr.domain.exceptions.InvalidTransitionError: ``profile.status``
                is not ``METHOD_PROFILE_LIFECYCLE.initial_state`` (``"draft"``)
                — mirrors ``ResearchScoreService.create``'s own reuse of this
                typed error for the same "no real 'from' state for a
                brand-new object" case, rather than inventing a new error
                type for it.
            ValueError: ``profile.revision`` is not ``1``.
        """
        if profile.status != METHOD_PROFILE_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                METHOD_PROFILE_LIFECYCLE.name, _NEW_PROFILE_SENTINEL_STATE, profile.status
            )
        if profile.revision != 1:
            raise ValueError(
                f"MethodProfile.revision must be 1 for propose(), got {profile.revision!r}"
            )

        obj = _profile_to_stored_object(profile)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_PROPOSED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=profile.id,
            object_revision=1,
            payload={"status": profile.status, "profile_key": profile.profile_key},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    # ------------------------------------------------------------------
    # Lifecycle transitions — METHOD_PROFILE_LIFECYCLE edges.
    # ------------------------------------------------------------------

    def accept(
        self, profile_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``draft -> accepted`` (MRR-MTH-019: profile activation MUST emit a
        domain event).
        """
        return self._transition(
            profile_id,
            "accepted",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_ACCEPTED_EVENT_TYPE,
        )

    def supersede(
        self, profile_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """``accepted -> superseded``."""
        return self._transition(
            profile_id,
            "superseded",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type=_SUPERSEDED_EVENT_TYPE,
        )

    # ------------------------------------------------------------------
    # Matching (K0-T02's dispatch layer and later K1 tasks consume this).
    # ------------------------------------------------------------------

    def find_accepted_by_capability(self, capability_name: str) -> list[Urn]:
        """Return the ``MethodProfile`` ids whose CURRENT revision (latest,
        re-resolved from the object repository) is still ``status ==
        "accepted"`` and whose ``executor_task_family`` declares
        ``capability_name``.

        A **matching primitive, not an authorization decision** — mirrors
        ``mrr.services.capability_registry.service.CapabilityRegistry.
        find_nodes_with_capability``'s own discipline exactly (see the
        module docstring). Never a boolean, never a verdict — just the
        list of currently-accepted profile ids declaring the queried
        capability name.

        Profile discovery: ``mrr.domain.repositories.ObjectRepository``
        offers no "list every object id" operation (E1-T05 is an
        append-only revision store, not an index), so this method reads the
        event log (``_EventJournal.read_all()``) and collects the distinct
        ``object_id``s of every ``method_profile.accepted`` event — every
        profile this service has ever accepted, by construction of
        ``accept`` above. Each candidate id is then re-resolved through
        ``ObjectRepository.get_latest`` (the actual current-state source of
        truth, not the event log) before being checked for its current
        status and capability membership — a profile that has since been
        superseded is judged by its true latest state, not by the stale
        historical accepted event, which is exactly what excludes it.
        """
        matching_profile_ids: list[str] = []
        seen_profile_ids: set[str] = set()
        for appended in self._event_log.read_all():
            if appended.event.event_type != _ACCEPTED_EVENT_TYPE:
                continue
            profile_id = appended.event.object_id
            if profile_id in seen_profile_ids:
                continue
            seen_profile_ids.add(profile_id)

            try:
                latest = self._object_repository.get_latest(profile_id)
            except ObjectNotFoundError:  # pragma: no cover - defensive only;
                # every method_profile.accepted event corresponds to a
                # revision `accept` just persisted in the SAME atomic write,
                # so this should be unreachable in practice.
                continue

            if latest.body["status"] != "accepted":
                continue

            capability_names = set(latest.body["executor_task_family"])
            if capability_name in capability_names:
                matching_profile_ids.append(profile_id)
        return matching_profile_ids

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, profile_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(profile_id)
        except ObjectNotFoundError:
            raise MethodProfileNotFoundError(profile_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — the ``causation_id`` for the next
        event in that profile's own causal chain (MRR-NFR-001), distinct
        from ``correlation_id``. ``read_all()`` returns events oldest-first,
        so the last match is the most recent — identical logic to
        ``ResearchScoreService._last_event_id_for``.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _transition(
        self,
        profile_id: Urn,
        to_status: MethodProfileStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
    ) -> StoredObject:
        """Shared implementation for every ``METHOD_PROFILE_LIFECYCLE`` edge
        method: load the latest revision, assert the transition is legal
        (fails closed with ``InvalidTransitionError`` and writes nothing —
        the assertion happens before any ``StoredObject``/``DomainEvent`` is
        even constructed), then persist the next revision (``status``
        changed, nothing else) plus its event atomically — identical
        structure to ``ResearchScoreService._transition``.
        """
        latest = self._get_latest_or_raise(profile_id)
        from_status = latest.body["status"]
        METHOD_PROFILE_LIFECYCLE.assert_transition(from_status, to_status)

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
            causation_id=self._last_event_id_for(profile_id),
            correlation_id=correlation_id,
            object_id=profile_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored
