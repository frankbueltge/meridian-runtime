"""``CorrectionImpactService`` (task-packets/E3-T06.yaml): the application-
layer service that records a ``CorrectionEvent``, drives the pure
``mrr.domain.correction_impact.compute_impact`` traversal over the real typed
edge graph (E1-T05 ``EdgeRepository``), writes the computed downstream
``impact_objects`` back onto the correction, and marks every impacted
``Claim`` ``review_required`` via the E3-T02 ``ClaimService`` — never
deleting a claim's prior decision, only appending a new revision. Sixth task
of Epic E3 (claim, evidence, correction kernel); the closest template is
``mrr.services.claim.service.ClaimService`` itself, named explicitly in the
task packet as such (record-then-transition shape, ``EdgeRepository`` reads,
``bind_unit_of_work``/local ``_EventJournal`` Protocol conventions).

--- Split record()/propagate_impact() rather than one combined method -------

task-packets/E3-T06.yaml's approved design offers a choice: one
``record_and_propagate`` method, or a split. This module splits: ``record``
persists the ``CorrectionEvent`` at revision 1 (a one-time creation, exactly
like every other service's own ``create``), and ``propagate_impact`` is the
separate, REPEATABLE, idempotent step that computes and (re-)applies impact.
Splitting them makes the packet's own idempotency invariant ("re-running does
not add a second review_required revision... and yields the same impact set")
directly expressible as "call ``propagate_impact`` more than once" rather
than needing a combined method to somehow distinguish "first call" (which
also records) from "re-run" (which must not re-record). A caller that wants
the combined one-shot behavior calls ``record`` then ``propagate_impact``
back to back, exactly as ``tests/integration/services/correction/
test_service.py`` does.

--- What counts as "affected" for the review_required transition ------------

task-packets/E3-T06.yaml's approved design: "for each impacted object that is
a CLAIM, call ClaimService.require_review". "Impacted object" is read
literally as ``mrr.domain.correction_impact.compute_impact``'s own output —
the computed DOWNSTREAM set written onto the correction's ``impact_objects``
field — not the correction's own ``affected_objects`` seeds. A seed object
that happens to be a ``Claim`` (the claim the correction is directly ABOUT)
is therefore NOT auto-transitioned to ``review_required`` by this service;
its own fate runs through the ``CorrectionEvent``'s own separate resolution
workflow (``replacement_object_id``, ``requested_action``, and eventually
``CORRECTION_LIFECYCLE``'s own NOTIFYING/AWAITING_RESPONSES states — E6,
out of this task's scope) rather than this task's downstream-propagation
concern. This mirrors ``mrr.domain.correction_impact``'s own documented
"seeds tracked separately" stance and is flagged here for the same reason:
a defensible, literal reading of the approved design text, not the only
possible one — worth reviewer scrutiny if a broader "also affects the named
seed claims directly" reading was actually intended.

--- Not driving CorrectionEvent's own CORRECTION_LIFECYCLE further -----------

``record`` checks the initial status against ``CORRECTION_LIFECYCLE.
initial_state`` ("OPEN"), exactly like ``ClaimService.create``/
``ResearchScoreService.create`` check their own machines' initial states —
but nothing in this service ever transitions a correction's own ``status``
field onward (e.g. OPEN -> IMPACT_ANALYSIS). task-packets/E3-T06.yaml's own
acceptance_tests and invariants name only the ``impact_objects`` field and
claim ``review_required`` transitions, never ``CorrectionEvent.status``
itself — and driving that lifecycle forward risks pre-empting the E6
notification task's own ownership of when a correction is considered past
impact analysis. Left untouched and flagged as an open item rather than
guessed.

--- Gathering edges: a query-driven BFS feeding the pure closure function ---

``mrr.domain.repositories.EdgeRepository`` has no "list every edge" method
(by design — E1-T05 is reuse-as-is, and this task's ``allowed_paths`` does
not include ``packages/persistence/**``), only ``edges_to``/``edges_from``
for one id at a time. ``_gather_impact_edges`` therefore drives its own
breadth-first expansion — visiting each id's incoming edges via
``edges_to(id)`` (no ``edge_type`` filter, since one round trip per id
covering every type is cheaper than one round trip per impact edge type),
keeping only edges whose type is in ``mrr.domain.correction_impact.
IMPACT_EDGE_TYPES``, and following each qualifying edge's ``source_id`` into
the next frontier — until nothing new is discovered. This necessarily
re-derives, at the query-driving level, the same "which id becomes impacted
next" logic ``compute_impact`` already implements purely; the alternative
(no query-driven expansion at all) is not available given the repository's
per-id query shape. To keep this from silently drifting into a SECOND,
divergent notion of "what impact means", the actual authoritative closure is
still always computed by handing the collected edges to ``compute_impact``
once, at the end — this method's own visited/frontier bookkeeping decides
only *which edges to fetch next*, never what the final impacted set is. This
is the same kind of deliberate, documented duplication
``mrr.services.claim.service.bind_edge_unit_of_work``'s own module docstring
flags for reviewer scrutiny ("Edge writes need their own atomic composition").

--- Idempotency: a claim already satisfying the review obligation ----------

``mrr.domain.lifecycles.CLAIM_LIFECYCLE`` declares no self-transition
(``review_required -> review_required`` is not a legal edge — see that
module's own ``StateMachine.__post_init__``), so calling
``ClaimService.require_review`` a second time on an already-
``review_required`` claim would raise ``InvalidTransitionError`` rather than
silently succeeding. The same is true of the two terminal states,
``withdrawn``/``superseded``, which have no drawn outgoing edge at all.
task-packets/E3-T06.yaml's own invariant ("a second run adds no new revision
to an already-review_required claim - check current status first") is
therefore not merely a nicety but a correctness requirement: ``_require_
review_if_needed`` reads the claim's current status and skips the transition
entirely whenever it is already ``review_required`` or one of the two
terminal states (read as "already at least as strict as review_required" per
MRR-FR-092's own "review_required or a stricter status" wording) — a plain
status check, not exception-driven control flow, so a genuinely unexpected
``InvalidTransitionError`` from some other cause still propagates instead of
being swallowed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import CorrectionEvent, Urn
from mrr.domain.correction_impact import IMPACT_EDGE_TYPES, compute_impact
from mrr.domain.exceptions import (
    CorrectionNotFoundError,
    InvalidTransitionError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import CORRECTION_LIFECYCLE
from mrr.domain.repositories import EdgeRepository, ObjectRepository, StoredObject, TypedEdge
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from sqlalchemy import Engine

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``record()`` with a non-OPEN initial status — mirrors
#: ``ClaimService``'s/``ResearchScoreService``'s identical
#: ``_NEW_*_SENTINEL_STATE`` reasoning: creation is not itself a drawn
#: CORRECTION_LIFECYCLE edge, so this reuses the existing typed error against
#: a sentinel rather than inventing a new one.
_NEW_CORRECTION_SENTINEL_STATE = "<new>"

#: A claim status already at least as strict as ``review_required`` per
#: MRR-FR-092's "review_required or a stricter status" wording — see the
#: module docstring's "Idempotency" section. Skipping these avoids both a
#: redundant revision AND an ``InvalidTransitionError`` (neither
#: ``review_required`` nor the two terminal states have a legal
#: self-transition or, for the terminal ones, any outgoing edge at all).
_CLAIM_REVIEW_ALREADY_SATISFIED_STATUSES = frozenset({"review_required", "withdrawn", "superseded"})

_CLAIM_KIND = "Claim"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. A local copy, not a shared import — see
#: ``mrr.services.claim.service``'s own module docstring for why each
#: service module keeps its own.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log — see
    ``mrr.services.claim.service._EventJournal`` for the identical rationale.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple — identical in shape and purpose to
    ``mrr.services.claim.service.bind_unit_of_work``. Production wiring and
    integration tests call this once; DB-free unit tests pass their own
    trivial callable of the same shape, backed by in-memory fakes.
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


def _correction_to_stored_object(correction: CorrectionEvent) -> StoredObject:
    """Convert an already-valid ``CorrectionEvent`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip,
    matching every other service's own ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(correction.model_dump_json(exclude_none=True))
    return StoredObject(
        id=correction.id,
        api_version=correction.api_version,
        kind=correction.kind,
        practice_id=correction.practice_id,
        revision=correction.revision,
        created_at=correction.created_at,
        created_by=correction.created_by,
        content_hash=correction.content_hash,
        supersedes=correction.supersedes,
        labels=correction.labels,
        body=body,
    )


class CorrectionImpactService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.8 ("Correction Impact Service"),
    implemented per task-packets/E3-T06.yaml. See the module docstring for
    the full design rationale.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        edge_repository: EdgeRepository,
        claim_service: ClaimService,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
    ) -> None:
        self._object_repository = object_repository
        self._edge_repository = edge_repository
        self._claim_service = claim_service
        self._event_log = event_log
        self._record = record

    # ------------------------------------------------------------------
    # Recording (MRR-FR-090): one-time creation, revision 1.
    # ------------------------------------------------------------------

    def record(
        self,
        correction: CorrectionEvent,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``correction`` as revision 1, plus a ``correction.recorded``
        event, atomically. Rejects any initial status other than
        ``CORRECTION_LIFECYCLE.initial_state`` ("OPEN").

        ``correction`` must already be a fully valid ``CorrectionEvent`` —
        its own ``id``/``content_hash``/``created_at``/``created_by`` are
        minted by the caller, matching every other ``create()``/``record()``
        in this codebase; ``correction.revision`` must be ``1``. Per
        MRR-FR-090, the schema already requires ``affected_objects``,
        ``reason``, ``severity``, ``evidence_refs``, and
        ``requested_action`` to be present and non-empty where the schema
        says so — nothing about that is re-checked here.
        """
        if correction.status != CORRECTION_LIFECYCLE.initial_state:
            raise InvalidTransitionError(
                CORRECTION_LIFECYCLE.name, _NEW_CORRECTION_SENTINEL_STATE, correction.status
            )
        if correction.revision != 1:
            raise ValueError(
                f"CorrectionEvent.revision must be 1 for record(), got {correction.revision!r}"
            )

        obj = _correction_to_stored_object(correction)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type="correction.recorded",
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=correction.id,
            object_revision=1,
            payload={
                "correction_type": correction.correction_type,
                "severity": correction.severity,
                "affected_object_ids": [ref.id for ref in correction.affected_objects],
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored

    # ------------------------------------------------------------------
    # Impact propagation (MRR-FR-091/092/093): repeatable, idempotent.
    # ------------------------------------------------------------------

    def propagate_impact(
        self,
        correction_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Compute the current downstream impact set for ``correction_id``
        (``mrr.domain.correction_impact.compute_impact`` over the real typed
        edge graph, seeded from the correction's own ``affected_objects``),
        write it onto the correction's ``impact_objects`` field if it
        changed, and mark every impacted ``Claim`` ``review_required``
        (skipping any claim already at least that strict — see the module
        docstring's "Idempotency" section).

        Safe to call repeatedly: if the computed impact set already matches
        the correction's current ``impact_objects``, no new correction
        revision is written; a claim already ``review_required`` (or
        terminal) is never re-transitioned. Returns the correction's latest
        stored revision (unchanged if this call was a no-op).

        Raises:
            CorrectionNotFoundError: ``correction_id`` resolves to no stored
                object at all.
        """
        latest = self._get_latest_correction_or_raise(correction_id)
        seed_ids: set[str] = {ref["id"] for ref in latest.body["affected_objects"]}
        edges = self._gather_impact_edges(seed_ids)
        impacted = compute_impact(seed_ids, edges)

        current_impact_objects = set(latest.body.get("impact_objects", []))
        if impacted != current_impact_objects:
            latest = self._write_impact_objects(
                latest,
                impacted,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )

        for object_id in sorted(impacted):
            self._require_review_if_needed(
                object_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
            )

        return latest

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_correction_or_raise(self, correction_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(correction_id)
        except ObjectNotFoundError:
            raise CorrectionNotFoundError(correction_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — identical rationale to
        ``ClaimService._last_event_id_for``.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _gather_impact_edges(self, seed_ids: set[str]) -> list[TypedEdge]:
        """Breadth-first-expand the correction's downstream closure via
        ``EdgeRepository.edges_to``, collecting only impact-typed edges. See
        the module docstring's "Gathering edges" section for why this exists
        and why it does not itself decide the final impacted set.
        """
        visited: set[str] = set()
        frontier: set[str] = set(seed_ids)
        collected: list[TypedEdge] = []
        while frontier:
            next_frontier: set[str] = set()
            for node_id in frontier:
                if node_id in visited:
                    continue
                visited.add(node_id)
                for edge in self._edge_repository.edges_to(node_id):
                    if edge.edge_type not in IMPACT_EDGE_TYPES:
                        continue
                    collected.append(edge)
                    if edge.source_id not in visited:
                        next_frontier.add(edge.source_id)
            frontier = next_frontier
        return collected

    def _write_impact_objects(
        self,
        latest: StoredObject,
        impacted: set[str],
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist a new correction revision carrying the freshly computed
        ``impact_objects`` (sorted for a deterministic, reproducible
        ``content_hash``), plus a ``correction.impact_computed`` event,
        atomically. Only called when the impact set actually changed
        relative to the correction's current revision (see
        ``propagate_impact``'s no-op guard).
        """
        new_body = dict(latest.body)
        new_body["impact_objects"] = sorted(impacted)
        new_revision = latest.revision + 1
        now = datetime.now(UTC)
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = actor
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash

        # Re-run the CorrectionEvent contract's own validation against the
        # exact revision body about to be persisted — matches
        # ClaimService._transition's identical "re-check before persisting"
        # stance for its own headline gate.
        CorrectionEvent.model_validate(new_body)

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
            event_type="correction.impact_computed",
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(latest.id),
            correlation_id=correlation_id,
            object_id=latest.id,
            object_revision=new_revision,
            payload={"impact_objects": sorted(impacted)},
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    def _require_review_if_needed(
        self,
        object_id: str,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> None:
        """Transition ``object_id`` to ``review_required`` via
        ``ClaimService.require_review`` iff it currently resolves to a
        ``Claim`` not already at least that strict. Silently does nothing
        for an id that resolves to no stored object at all, or to a
        non-``Claim`` kind — see the module docstring's "What counts as
        affected" section for the claims-only scope, and the ``EdgeRepository``
        having no existence constraint on edge endpoints (there is no
        foreign key from ``edges`` to ``objects`` — an edge may reference an
        id this repository has never seen).
        """
        try:
            obj = self._object_repository.get_latest(object_id)
        except ObjectNotFoundError:
            return
        if obj.kind != _CLAIM_KIND:
            return
        if obj.body.get("status") in _CLAIM_REVIEW_ALREADY_SATISFIED_STATUSES:
            return
        self._claim_service.require_review(
            object_id, actor=actor, policy_version=policy_version, correlation_id=correlation_id
        )
