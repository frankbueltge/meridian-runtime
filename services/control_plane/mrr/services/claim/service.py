"""``ClaimService`` (task-packets/E3-T02.yaml): the application-layer service
that creates atomic ``Claim`` objects, drives them through
``mrr.domain.lifecycles.CLAIM_LIFECYCLE`` (E1-T04), and connects them to
evidence, counterevidence, dependencies, and other claims as typed graph
edges over the E1-T05 ``EdgeRepository`` — the claim/evidence graph
docs/spec/01_SYSTEM_SPEC.md section 7.6 describes ("Stores typed nodes and
edges in PostgreSQL. A graph database is not required for v1."). Second task
of Epic E3 (claim, evidence, correction kernel); the closest template is
``mrr.services.research_score.service.ResearchScoreService`` (E2-T01), named
explicitly in the packet as such.

--- Claim is UNSIGNED: transitions are new revisions, not ADR-0007 events ---

Unlike ``TaskBundle`` (ADR-0007: lifecycle transitions are append-only
domain events, WITHOUT a new content revision, because a Task Bundle is
origin-signed and re-minting its ``revision``/``content_hash`` on every
negotiation step would break that one-time signature), ``Claim`` carries no
``signature`` field (schemas/claim.schema.json has none) — there is nothing
a lifecycle transition could break by writing a new revision. So, exactly
like ``ResearchScore``, every ``ClaimService`` lifecycle method persists a
NEW ``StoredObject`` revision (status changed in the body) plus its event,
atomically, via ``mrr.persistence.unit_of_work.record_object_revision_with_event``
— never a bare event with no revision. ADR-0007 does not apply here.

--- The headline gate: supported requires evidence AND verification -------

docs/spec/01_SYSTEM_SPEC.md MRR-FR-062 ("A claim with status `supported`
MUST have at least one valid support relation and no unresolved hard
verification failure") is already enforced at the contract level:
``mrr.contracts.claim.Claim._supported_requires_evidence_and_verification``
(a ``model_validator``, E1-T03) rejects a ``Claim`` whose ``status ==
"supported"`` carries an empty ``evidence_relations`` or ``verification_ids``
list. ``ClaimService.to_supported`` re-runs that validator against the exact
revision body about to be persisted (see ``_transition`` below) — the
contract enforces "not empty"; this service enforces something the contract
CANNOT check on its own: that every URN actually listed in
``evidence_relations`` is backed by a real typed ``supports`` edge FROM this
claim (``add_evidence_edge(..., edge_type="supports")`` must run first, or
``to_supported`` raises ``mrr.domain.exceptions.MissingSupportEdgeError`` and
persists nothing). Verification records themselves, and the
self-verification/independence gates over them, are E3-T04/T05 — here,
``verification_ids`` are structural URN references only (the packet's own
derived_decisions: "actual verification RECORDS ... are E3-T04/T05 — here,
verification_ids are structural references the contract requires for
`supported`"), so no matching edge is required for them.

--- Field-vs-edge consistency (design decision, flagged for review) -------

task-packets/E3-T02.yaml asks this task to "decide and document" how a
Claim's own ``evidence_relations``/``counterevidence_relations``/
``dependencies`` URN-list fields relate to the typed edges in the graph.
The decision made here: the OBJECT FIELDS and the GRAPH EDGES are two
independent writes with one required consistency point, not a single
auto-derived projection of each other in either direction:

- ``add_evidence_edge``/``add_counterevidence_edge``/``add_dependency_edge``/
  ``link_related_claim`` write ONLY a typed edge (plus its own event) — they
  never touch the claim's own object revision. A claim can accumulate
  candidate evidence/counterevidence/dependency edges over many separate
  calls, at any nonterminal status, without forcing a new claim revision
  for each one.
- ``to_supported`` is the one place ``evidence_relations``/
  ``verification_ids`` are written onto the claim's own object body, and it
  is given the FULL explicit list to write (never a silent append to
  whatever the latest revision already held — the same "no implicit merge"
  stance ``ResearchScoreService.revise()`` already takes for material
  changes). At that moment, and only at that moment, the service checks
  that every ``evidence_relations`` URN already has a matching typed
  ``supports`` edge.

This means a caller MUST call ``add_evidence_edge(..., edge_type="supports")``
for each evidence relation before (or in the same orchestration step
immediately before) calling ``to_supported`` with that URN in
``evidence_relations`` — the two are deliberately two separate calls/writes
(edge first, object field second), not one combined operation, so that
"which URNs count as evidence" is always visible as both a field on the
object AND a real, independently queryable graph edge, never only one of
the two. ``counterevidence_relations``/``dependencies`` are NOT
cross-checked against their own edges by this service (the packet's
"headline gate" and its point 2 both name ``evidence_relations`` specifically
) — flagged in the PR body as an intentionally narrower scope than a fully
symmetric field/edge consistency check across every relation kind would be.

--- Edge writes need their own atomic composition ---------------------------

``mrr.persistence.unit_of_work`` provides
``record_object_revision_with_event`` (an object revision + its event, one
transaction) and ``record_event`` (an event alone, no object write) — but
nothing that composes an ``edges`` table insert with an event append into
one transaction, because no prior task ever wrote an edge from a service.
task-packets/E3-T02.yaml's ``allowed_paths`` for this task does not include
``packages/persistence/**`` (E1-T05/T06 are "reuse as-is"), so this module
cannot add an ``insert_edge_with_connection`` counterpart to
``PostgresEdgeRepository`` the way E1-T06 added
``insert_revision_with_connection`` to ``PostgresObjectRepository`` for the
exact same reason. Instead, ``bind_edge_unit_of_work`` below performs the
identical insert ``PostgresEdgeRepository.add_edge`` does (same columns,
same values, same ``EDGE_VOCABULARY``/``UnknownEdgeTypeError`` fail-closed
check — nothing about "how an edge is inserted" is reimplemented or
diverges), but shares ``event_log.append``'s connection instead of opening
its own transaction the way ``add_edge`` does — so the edge row and its
domain event either both commit or both roll back. This is a deliberate,
documented deviation worth reviewer scrutiny (flagged again in the PR body):
the alternative would have been calling the Engine-based
``PostgresEdgeRepository.add_edge`` and ``mrr.persistence.unit_of_work.
record_event`` back to back as two separate transactions, which cannot
satisfy the packet's own invariant ("every ... edge write records a domain
event ... atomically with the persisted ... edge") or its acceptance test
("transitions and edge writes persist atomically with their events").
``mrr.persistence.tables.edges_table`` is already imported directly by
``tests/integration/persistence/test_postgres_repositories.py`` to bypass
the repository layer for a CHECK-constraint test, so treating that table
definition as reusable from outside ``mrr.persistence.repositories`` is
consistent with existing precedent in this codebase, not a new liberty
taken here.

--- Dependency shape (mirrors ResearchScoreService) -------------------------

See ``mrr.services.research_score.service``'s own module docstring for the
full rationale (Protocol-typed reads, a bound-callable write dependency so a
DB-free unit test can substitute in-memory fakes without fighting concrete
Postgres-typed parameters or mypy strict). The same shape is used twice here
— once for object revisions (``RecordRevisionWithEvent``, identical to
``ResearchScoreService``'s own) and once for edges
(``RecordEdgeWithEvent``, new to this module).

--- K1-T02: the claim-ceiling gate (MRR-MTH-004/005/006) --------------------

``attach_ruling`` is the "at submission" gate and the ONLY place a
``ruled_by`` edge is ever written. It resolves ``method_ruling_id`` via the
already-generic ``self._object_repository.get_latest`` (works for ANY object
kind — no new constructor dependency), then walks
``ruling.body["protocol_id"]`` -> ``MethodProtocol`` ->
``protocol.body["profile_id"]`` -> ``MethodProfile``, reading
``max_claim_ceiling`` from that profile's CURRENT latest revision regardless
of its own lifecycle status (flagged in the PR body's specification_gaps:
this does NOT require ``status == "accepted"``). It calls
``mrr.domain.claim_ceiling.ceiling_violation_reason`` against the resolved
claim's own ``claim_type``; on a non-``None`` result it raises
``ClaimCeilingExceededError`` BEFORE writing anything — mirrors
``to_supported``'s "checked first, nothing persisted on failure" discipline.
On success it writes the ``ruled_by`` edge via the existing ``_write_edge``
helper (event_type ``"claim.ruling_attached"``), reusing its already-atomic
edge-plus-event composition unchanged.

``_transition`` gains a second, independent ceiling re-check: whenever
``to_status`` is one of ``{"supported", "contested", "contradicted",
"unsupported"}`` (the four ``CLAIM_LIFECYCLE`` targets that assert some claim
language), every ``ruled_by`` edge already attached to the claim is
re-resolved and re-checked. Empty -> skipped entirely (zero behavior change
for every claim created before this task and for every existing
``to_supported``/``to_contested``/etc. test that seeds no ``ruled_by`` edge).
One or more -> ALL are re-checked; ANY violation raises
``ClaimCeilingExceededError`` and persists nothing (fail-closed: a claim with
multiple, possibly-conflicting rulings is rejected if ANY attached ruling
would license a violation).

``apply_kill_condition`` (MRR-MTH-010) reuses the EXISTING ``withdrawn``
``CLAIM_LIFECYCLE`` state — no new ``Claim`` status is invented; "killed
branches remain addressable" is exactly ``withdraw()``'s own already-
documented behavior. It (1) resolves ``research_decision_id`` and calls the
pure ``mrr.domain.kill_condition.assert_licenses_kill``, raising
``InvalidKillDecisionError`` BEFORE anything is persisted if the resolved
object is not a ``ResearchDecision`` with ``decision_type == "kill_branch"``;
(2) transitions the claim via the EXISTING ``_transition`` helper to
``"withdrawn"``, but with ``event_type="claim.kill_condition_triggered"`` (a
NEW, distinctly-named event type, kept separate from the plain
``"claim.withdrawn"`` a voluntary researcher withdrawal produces) whose
payload carries ``{"code": "KILL_CONDITION_TRIGGERED", "research_decision_id":
..., "from_status": ..., "to_status": "withdrawn"}`` — this is how spec 08's
literal, canonical ``KILL_CONDITION_TRIGGERED`` string becomes a real,
greppable, directly-assertable fact on the persisted event, without breaking
this module's own uniform lowercase-dotted ``event_type`` naming convention;
(3) writes a NEW ``decided_by`` edge, ``claim_id -> research_decision_id``,
via the EXISTING ``_write_edge`` helper (event_type
``"claim.kill_decision_recorded"``). These are TWO SEQUENTIAL, each
independently atomic writes — not one combined multi-table transaction,
since no persistence primitive in this module's ``allowed_paths`` composes
an object-revision write AND an edge write atomically together, only each
with its OWN event (mirrors this module's own "object fields and graph edges
are two independent writes with one required consistency point" precedent).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from mrr.contracts import Claim, ClaimStatus, Urn
from mrr.domain.claim_ceiling import ceiling_violation_reason
from mrr.domain.exceptions import (
    ClaimCeilingExceededError,
    ClaimNotFoundError,
    InvalidTransitionError,
    MissingSupportEdgeError,
    ObjectNotFoundError,
    UnknownEdgeTypeError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.kill_condition import assert_licenses_kill
from mrr.domain.lifecycles import CLAIM_LIFECYCLE
from mrr.domain.repositories import (
    EDGE_VOCABULARY,
    EdgeRepository,
    ObjectRepository,
    StoredObject,
    TypedEdge,
)
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.tables import edges_table
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``create()`` with a non-draft initial status — see
#: ``ResearchScoreService.create``'s identical ``_NEW_SCORE_SENTINEL_STATE``
#: for the full rationale. Never a member of ``CLAIM_LIFECYCLE.states``.
_NEW_CLAIM_SENTINEL_STATE = "<new>"

#: The four CLAIM_LIFECYCLE targets that assert some claim language —
#: `_transition`'s K1-T02 ceiling re-check runs only for these (MRR-MTH-004).
#: `unresolved`/`review_required`/`withdrawn`/`superseded` do not, per their
#: own existing names, and are excluded.
_CEILING_ASSERTING_STATUSES = frozenset({"supported", "contested", "contradicted", "unsupported"})

#: The one typed-edge kind `attach_ruling` ever writes, and the one
#: `_transition`'s ceiling re-check reads back (K1-T02).
_RULED_BY_EDGE_TYPE = "ruled_by"

#: The one typed-edge kind `apply_kill_condition` ever writes (K1-T02,
#: MRR-MTH-010).
_DECIDED_BY_EDGE_TYPE = "decided_by"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments are
#: bound. Identical in shape to every other service's own
#: ``RecordRevisionWithEvent`` (a local copy, not a shared import — see
#: ``mrr.services.evidence.service``'s module docstring for why each service
#: module keeps its own).
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]

#: The callable shape ``bind_edge_unit_of_work`` produces: insert one typed
#: edge and append one domain event, atomically. See the module docstring's
#: "Edge writes need their own atomic composition" section.
RecordEdgeWithEvent = Callable[[TypedEdge, DomainEvent], tuple[TypedEdge, AppendedEvent]]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log — see
    ``mrr.services.research_score.service._EventJournal`` for the full
    rationale (deliberately smaller than the generic
    ``mrr.provenance.log.EventLog[TTx]`` Protocol; not ``@runtime_checkable``
    for the same reason that Protocol is not).
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
    ``mrr.services.research_score.service.bind_unit_of_work``. Production
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


def bind_edge_unit_of_work(
    engine: Engine,
    event_log: PostgresEventLog,
) -> RecordEdgeWithEvent:
    """Compose an ``edges`` table insert with a domain-event append into ONE
    database transaction. See the module docstring's "Edge writes need
    their own atomic composition" section for why this is defined here
    (rather than as an addition to ``mrr.persistence``, which this task's
    ``allowed_paths`` does not include) and why it is not a second,
    divergent implementation of "how an edge is inserted": same columns,
    same values, same ``EDGE_VOCABULARY``/``UnknownEdgeTypeError`` check as
    ``mrr.persistence.repositories.PostgresEdgeRepository.add_edge``, just
    sharing ``event_log.append``'s connection instead of opening its own.

    Production wiring and integration tests call this once; DB-free unit
    tests pass their own trivial callable of the same
    ``RecordEdgeWithEvent`` shape, backed by an in-memory fake, instead.
    """

    def _record_edge(edge: TypedEdge, event: DomainEvent) -> tuple[TypedEdge, AppendedEvent]:
        if edge.edge_type not in EDGE_VOCABULARY:
            raise UnknownEdgeTypeError(edge.edge_type)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(edges_table).values(
                    id=edge.id,
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    edge_type=edge.edge_type,
                    created_at=edge.created_at,
                    created_by=edge.created_by,
                    practice_id=edge.practice_id,
                    scope=edge.scope,
                    status=edge.status,
                )
            )
            appended = event_log.append(conn, event)
        return edge, appended

    return _record_edge


def _claim_to_stored_object(claim: Claim) -> StoredObject:
    """Convert an already-valid ``Claim`` (id, revision, timestamps, and
    content hash already set by the caller for ``create()``; minted fresh by
    ``ClaimService._transition`` for every lifecycle transition) into the
    generic ``StoredObject`` ``mrr.domain.repositories.ObjectRepository``
    persists. ``body`` is a plain ``model_dump_json(exclude_none=True)``
    round trip, matching every other service's own ``_*_to_stored_object``
    helper.
    """
    body: dict[str, Any] = json.loads(claim.model_dump_json(exclude_none=True))
    return StoredObject(
        id=claim.id,
        api_version=claim.api_version,
        kind=claim.kind,
        practice_id=claim.practice_id,
        revision=claim.revision,
        created_at=claim.created_at,
        created_by=claim.created_by,
        content_hash=claim.content_hash,
        supersedes=claim.supersedes,
        labels=claim.labels,
        body=body,
    )


class ClaimService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.6 ("Claim and Evidence Graph"),
    implemented per task-packets/E3-T02.yaml. See the module docstring for
    the full design rationale.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        event_log: _EventJournal,
        edge_repository: EdgeRepository,
        record: RecordRevisionWithEvent,
        record_edge: RecordEdgeWithEvent,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._edge_repository = edge_repository
        self._record = record
        self._record_edge = record_edge

    # ------------------------------------------------------------------
    # Creation (MRR-FR-060/061).
    # ------------------------------------------------------------------

    def create(
        self,
        claim: Claim,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``claim`` as revision 1, plus a ``claim.created`` event,
        atomically. Rejects any initial status other than
        ``CLAIM_LIFECYCLE.initial_state`` ("draft").

        ``claim`` must already be a fully valid ``Claim`` — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller (this service does not generate identifiers or compute
        hashes on the caller's behalf, matching every other ``create()`` in
        this codebase); ``claim.revision`` must be ``1``.
        """
        if claim.status != CLAIM_LIFECYCLE.initial_state:
            # Creation is not itself a drawn CLAIM_LIFECYCLE edge — see
            # ResearchScoreService.create's identical reasoning for reusing
            # InvalidTransitionError against a sentinel "from" state rather
            # than inventing a new error type.
            raise InvalidTransitionError(
                CLAIM_LIFECYCLE.name, _NEW_CLAIM_SENTINEL_STATE, claim.status
            )
        if claim.revision != 1:
            raise ValueError(f"Claim.revision must be 1 for create(), got {claim.revision!r}")

        obj = _claim_to_stored_object(claim)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type="claim.created",
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=claim.id,
            object_revision=1,
            payload={"claim_type": claim.claim_type, "status": claim.status},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    # ------------------------------------------------------------------
    # Lifecycle transitions — CLAIM_LIFECYCLE edges (E1-T04).
    # ------------------------------------------------------------------

    def submit_for_review(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """draft -> under_review."""
        return self._transition(
            claim_id,
            "under_review",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.submitted_for_review",
        )

    def to_supported(
        self,
        claim_id: Urn,
        *,
        evidence_relations: list[Urn],
        verification_ids: list[Urn],
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """under_review -> supported (MRR-FR-062, the packet's headline
        gate). See the module docstring's "The headline gate" and
        "Field-vs-edge consistency" sections.

        ``evidence_relations``/``verification_ids`` are the FULL, explicit
        list of URNs the resulting revision carries (no implicit merge with
        whatever the latest revision already held). Two independent checks
        must both pass before anything is persisted:

        1. every URN in ``evidence_relations`` already has a matching typed
           ``supports`` edge from this claim (this service's own check,
           first — cheaper and gives the more specific error); then
        2. the Claim contract's own ``model_validator`` (E1-T03): both lists
           non-empty.

        Raises:
            MissingSupportEdgeError: an ``evidence_relations`` URN has no
                matching ``supports`` edge.
            pydantic.ValidationError: either list is empty, or the resulting
                revision is otherwise not a valid ``Claim``.
            InvalidTransitionError: ``under_review -> supported`` is not
                legal from the claim's current status.

        None of these leave anything persisted.
        """
        return self._transition(
            claim_id,
            "supported",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.supported",
            evidence_relations=evidence_relations,
            verification_ids=verification_ids,
        )

    def to_contested(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """under_review -> contested."""
        return self._transition(
            claim_id,
            "contested",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.contested",
        )

    def to_contradicted(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """under_review -> contradicted."""
        return self._transition(
            claim_id,
            "contradicted",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.contradicted",
        )

    def to_unresolved(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """under_review -> unresolved."""
        return self._transition(
            claim_id,
            "unresolved",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.unresolved",
        )

    def to_unsupported(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """under_review -> unsupported."""
        return self._transition(
            claim_id,
            "unsupported",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.unsupported",
        )

    def require_review(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """Any nonterminal status -> review_required."""
        return self._transition(
            claim_id,
            "review_required",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.review_required",
        )

    def withdraw(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """Any nonterminal status -> withdrawn (a withdrawn claim remains
        addressable — docs/spec/01_SYSTEM_SPEC.md section 6.3)."""
        return self._transition(
            claim_id,
            "withdrawn",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.withdrawn",
        )

    def supersede(
        self, claim_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """Any nonterminal status -> superseded (a superseded claim remains
        addressable — docs/spec/01_SYSTEM_SPEC.md section 6.3)."""
        return self._transition(
            claim_id,
            "superseded",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.superseded",
        )

    # ------------------------------------------------------------------
    # Typed graph edges (E1-T05) — evidence, counterevidence, dependencies,
    # and claim-to-claim relations (MRR-FR-066).
    # ------------------------------------------------------------------

    def add_evidence_edge(
        self,
        claim_id: Urn,
        target_id: Urn,
        edge_type: str,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        scope: dict[str, Any] | None = None,
    ) -> TypedEdge:
        """Create a typed edge ``claim_id -> target_id`` using any
        section-3 vocabulary type (``supports``/``qualifies``/
        ``contextualizes``/``derived_from``/``uses_source``/...),
        atomically with a ``claim.evidence_edge_added`` event.

        Raises:
            UnknownEdgeTypeError: ``edge_type`` is not in
                ``mrr.domain.repositories.EDGE_VOCABULARY`` (E1-T05's own
                fail-closed check, reused as-is) — checked before
                ``claim_id`` is even resolved, so nothing is persisted.
            ClaimNotFoundError: ``claim_id`` resolves to no stored object.
        """
        return self._write_edge(
            claim_id,
            target_id,
            edge_type,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=scope,
            event_type="claim.evidence_edge_added",
        )

    def add_counterevidence_edge(
        self,
        claim_id: Urn,
        target_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        scope: dict[str, Any] | None = None,
    ) -> TypedEdge:
        """Thin wrapper over ``add_evidence_edge`` fixing
        ``edge_type="contradicts"``."""
        return self._write_edge(
            claim_id,
            target_id,
            "contradicts",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=scope,
            event_type="claim.counterevidence_edge_added",
        )

    def add_dependency_edge(
        self,
        claim_id: Urn,
        target_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        scope: dict[str, Any] | None = None,
    ) -> TypedEdge:
        """Thin wrapper over ``add_evidence_edge`` fixing
        ``edge_type="depends_on"``."""
        return self._write_edge(
            claim_id,
            target_id,
            "depends_on",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=scope,
            event_type="claim.dependency_edge_added",
        )

    def link_related_claim(
        self,
        claim_id: Urn,
        other_claim_id: Urn,
        edge_type: str,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        scope: dict[str, Any] | None = None,
    ) -> TypedEdge:
        """MRR-FR-066: link two materially different claims by a typed edge
        (typically ``qualifies``/``contextualizes``, but any vocabulary type
        is accepted — the graph does not distinguish a claim-to-evidence
        edge from a claim-to-claim edge structurally) instead of merging
        them into one object. Neither claim's own object revision is
        touched — the two remain fully separate, independently addressable
        objects; only a new edge (plus its event) is written.
        """
        return self._write_edge(
            claim_id,
            other_claim_id,
            edge_type,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=scope,
            event_type="claim.related_claim_linked",
        )

    # ------------------------------------------------------------------
    # K1-T02: the claim-ceiling gate (MRR-MTH-004/005/006).
    # ------------------------------------------------------------------

    def attach_ruling(
        self,
        claim_id: Urn,
        method_ruling_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> TypedEdge:
        """The "at submission" ceiling gate (MRR-MTH-004) and the ONLY place
        a ``ruled_by`` edge is ever written. See the module docstring's
        "K1-T02: the claim-ceiling gate" section for the full rationale.

        Raises:
            ClaimCeilingExceededError: the resolved ``MethodRuling`` ->
                ``MethodProtocol`` -> ``MethodProfile`` chain, checked against
                this claim's own ``claim_type``, reports a violation — either
                the universal profile-max check or the causal-specific check
                (``mrr.domain.claim_ceiling.ceiling_violation_reason``).
                Nothing is persisted.
            ClaimNotFoundError: ``claim_id`` resolves to no stored object.
            ObjectNotFoundError: ``method_ruling_id`` (or the
                ``MethodProtocol``/``MethodProfile`` it references) resolves
                to no stored object at all.
        """
        claim = self._get_latest_or_raise(claim_id)
        ruled_ceiling, profile_max_ceiling = self._resolve_ruling_ceiling_chain(method_ruling_id)

        reason = ceiling_violation_reason(
            claim_type=claim.body["claim_type"],
            ruled_ceiling=ruled_ceiling,
            profile_max_ceiling=profile_max_ceiling,
        )
        if reason is not None:
            raise ClaimCeilingExceededError(claim_id=claim_id, reason=reason)

        return self._write_edge(
            claim_id,
            method_ruling_id,
            _RULED_BY_EDGE_TYPE,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=None,
            event_type="claim.ruling_attached",
        )

    # ------------------------------------------------------------------
    # K1-T02: kill-condition transition plumbing (MRR-MTH-010).
    # ------------------------------------------------------------------

    def apply_kill_condition(
        self,
        claim_id: Urn,
        research_decision_id: Urn,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> tuple[StoredObject, TypedEdge]:
        """Kill-condition transition plumbing (MRR-MTH-010): reuses the
        EXISTING ``withdrawn`` ``CLAIM_LIFECYCLE`` state (no new ``Claim``
        status is invented) plus a distinctly-typed
        ``claim.kill_condition_triggered`` event carrying the canonical
        ``KILL_CONDITION_TRIGGERED`` string in its own payload, plus a new
        ``decided_by`` edge to the licensing ``ResearchDecision``. See the
        module docstring's "K1-T02: kill-condition transition plumbing"
        section for the full rationale, including why this is TWO
        sequential, independently atomic writes rather than one combined
        transaction.

        This method implements PLUMBING only — it does not parse or evaluate
        a ``MethodProtocol.kill_conditions`` free-text entry against real
        evidence to decide WHETHER a condition is currently satisfied
        (task-packets/K1-T03.yaml's/K1-T04.yaml's job); it acts on a
        caller-supplied, already-decided ``research_decision_id``.

        Raises:
            InvalidKillDecisionError: the object resolved for
                ``research_decision_id`` is not a ``ResearchDecision`` with
                ``decision_type == "kill_branch"``. Checked FIRST; nothing is
                persisted.
            InvalidTransitionError: the claim's current status has no
                ``CLAIM_LIFECYCLE`` edge into ``withdrawn`` (e.g. it is
                already ``withdrawn``/``superseded`` — both terminal).
                Nothing is persisted.
            ClaimNotFoundError: ``claim_id`` resolves to no stored object.
        """
        decision = self._object_repository.get_latest(research_decision_id)
        assert_licenses_kill(
            research_decision_id=research_decision_id,
            decision_kind=decision.kind,
            decision_type=decision.body.get("decision_type"),
        )

        stored = self._transition(
            claim_id,
            "withdrawn",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="claim.kill_condition_triggered",
            extra_payload={
                "code": "KILL_CONDITION_TRIGGERED",
                "research_decision_id": research_decision_id,
            },
        )
        edge = self._write_edge(
            claim_id,
            research_decision_id,
            _DECIDED_BY_EDGE_TYPE,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            scope=None,
            event_type="claim.kill_decision_recorded",
        )
        return stored, edge

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, claim_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(claim_id)
        except ObjectNotFoundError:
            raise ClaimNotFoundError(claim_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — see
        ``ResearchScoreService._last_event_id_for`` for the identical
        rationale (``causation_id`` vs. the caller-supplied
        ``correlation_id``).
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _transition(
        self,
        claim_id: Urn,
        to_status: ClaimStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
        evidence_relations: list[Urn] | None = None,
        verification_ids: list[Urn] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> StoredObject:
        """Shared implementation for every CLAIM_LIFECYCLE edge method: load
        the latest revision, assert the transition is legal (fails closed
        with ``InvalidTransitionError`` and writes nothing), re-check the
        K1-T02 ceiling gate for every attached ``ruled_by`` ruling when
        transitioning into a language-asserting status, enforce the
        supported-requires-support-edges invariant when transitioning INTO
        ``supported``, then persist the next revision (status changed, plus
        ``evidence_relations``/``verification_ids`` when transitioning to
        ``supported``) plus its event atomically.

        ``evidence_relations``/``verification_ids`` are ``None`` for every
        transition except ``to_supported`` — asserted non-``None`` in that
        one branch below purely so this shared helper's own typing stays
        honest about the other transitions never needing them.

        ``extra_payload`` (K1-T02) is merged into the event's ``payload``
        dict on top of the always-present ``from_status``/``to_status``
        pair — ``None`` (the default) for every pre-K1-T02 caller, so every
        existing transition method's event payload is byte-identical to
        before. Only ``apply_kill_condition`` supplies it today, to carry
        the canonical ``KILL_CONDITION_TRIGGERED`` code and the licensing
        ``research_decision_id`` on the transition event itself.
        """
        latest = self._get_latest_or_raise(claim_id)
        from_status = latest.body["status"]
        CLAIM_LIFECYCLE.assert_transition(from_status, to_status)

        if to_status in _CEILING_ASSERTING_STATUSES:
            # K1-T02, MRR-MTH-004 defense-in-depth: re-verify the ceiling
            # gate for EVERY ruled_by edge already attached to this claim.
            # Empty -> skipped entirely (zero behavior change for every
            # claim with no ruled_by edge, i.e. every claim that existed
            # before this task). One or more -> ALL are re-checked; ANY
            # violation raises and persists nothing, before the transition
            # ever touches the claim's own body.
            claim_type = latest.body["claim_type"]
            for edge in self._edge_repository.edges_from(claim_id, _RULED_BY_EDGE_TYPE):
                ruled_ceiling, profile_max_ceiling = self._resolve_ruling_ceiling_chain(
                    edge.target_id
                )
                reason = ceiling_violation_reason(
                    claim_type=claim_type,
                    ruled_ceiling=ruled_ceiling,
                    profile_max_ceiling=profile_max_ceiling,
                )
                if reason is not None:
                    raise ClaimCeilingExceededError(claim_id=claim_id, reason=reason)

        new_body = dict(latest.body)
        new_body["status"] = to_status

        if to_status == "supported":
            if evidence_relations is None or verification_ids is None:
                raise ValueError(
                    "transition to 'supported' requires evidence_relations and verification_ids"
                )
            existing_support_targets = {
                edge.target_id for edge in self._edge_repository.edges_from(claim_id, "supports")
            }
            missing_targets = [
                urn for urn in evidence_relations if urn not in existing_support_targets
            ]
            if missing_targets:
                raise MissingSupportEdgeError(claim_id, missing_targets)
            new_body["evidence_relations"] = list(evidence_relations)
            new_body["verification_ids"] = list(verification_ids)

        new_revision = latest.revision + 1
        now = datetime.now(UTC)
        new_body["revision"] = new_revision
        new_body["created_at"] = now.isoformat()
        new_body["created_by"] = actor
        new_content_hash = compute_content_hash(new_body)
        new_body["content_hash"] = new_content_hash

        if to_status == "supported":
            # Re-run the Claim contract's own model_validator against the
            # EXACT revision body about to be persisted — the packet's
            # headline gate (E1-T03's "supported requires non-empty
            # evidence_relations AND verification_ids"). Deliberately only
            # for this one transition: every other transition changes only
            # `status`, carrying an already-valid body forward unchanged, so
            # there is nothing new here for the contract to re-check.
            Claim.model_validate(new_body)

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
        payload: dict[str, Any] = {"from_status": from_status, "to_status": to_status}
        if extra_payload:
            payload.update(extra_payload)

        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=event_type,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(claim_id),
            correlation_id=correlation_id,
            object_id=claim_id,
            object_revision=new_revision,
            payload=payload,
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored

    def _resolve_ruling_ceiling_chain(self, method_ruling_id: str) -> tuple[str, str]:
        """Resolve a ``MethodRuling`` -> ``MethodProtocol`` -> ``MethodProfile``
        chain to ``(ruled_ceiling, profile_max_ceiling)`` (K1-T02). Reused by
        both ``attach_ruling`` and ``_transition``'s ceiling re-check so the
        resolution logic is written exactly once.

        Raises:
            ObjectNotFoundError: ``method_ruling_id``, or the
                ``MethodProtocol``/``MethodProfile`` id it references, does
                not resolve to any stored object.
        """
        ruling = self._object_repository.get_latest(method_ruling_id)
        protocol = self._object_repository.get_latest(ruling.body["protocol_id"])
        profile = self._object_repository.get_latest(protocol.body["profile_id"])
        return ruling.body["ruled_ceiling"], profile.body["max_claim_ceiling"]

    def _write_edge(
        self,
        source_id: Urn,
        target_id: Urn,
        edge_type: str,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        scope: dict[str, Any] | None,
        event_type: str,
    ) -> TypedEdge:
        """Shared implementation for every typed-edge-writing method. Checks
        ``edge_type`` against ``EDGE_VOCABULARY`` directly (in addition to
        the identical check inside ``bind_edge_unit_of_work``'s closure —
        belt and braces, matching E1-T05's own "checked in code in addition
        to the database CHECK constraint" precedent) BEFORE resolving
        ``source_id``, so a DB-free unit test can exercise "invalid edge
        type rejected" without seeding a claim into its fake repository at
        all, and so a bad edge_type never depends on `source_id` resolving
        first.
        """
        if edge_type not in EDGE_VOCABULARY:
            raise UnknownEdgeTypeError(edge_type)

        latest = self._get_latest_or_raise(source_id)
        now = datetime.now(UTC)
        edge = TypedEdge(
            id=new_urn("edge"),
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            created_at=now,
            created_by=actor,
            scope=scope,
            status="active",
            practice_id=latest.practice_id,
        )
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=event_type,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(source_id),
            correlation_id=correlation_id,
            object_id=source_id,
            object_revision=latest.revision,
            payload={"edge_id": edge.id, "edge_type": edge_type, "target_id": target_id},
        )
        stored_edge, _ = self._record_edge(edge, event)
        return stored_edge
