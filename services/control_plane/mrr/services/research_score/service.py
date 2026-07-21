"""``ResearchScoreService`` (task-packets/E2-T01.yaml): the application-layer
service that creates and revises versioned ``ResearchScore`` objects, drives
them through ``mrr.domain.lifecycles.RESEARCH_SCORE_LIFECYCLE`` recording
approvals, and exposes ``ensure_can_start_work`` — the authoritative gate
docs/spec/01_SYSTEM_SPEC.md MRR-FR-004 requires ("The system MUST reject
execution when the referenced score is missing, unapproved, expired, or
superseded without explicit continuation permission").

Every write this service performs is a new append-only revision of the
``objects`` table (E1-T05) coupled, in the SAME database transaction, to a
domain event (E1-T06) carrying the MRR-NFR-001 provenance fields — never a
bare status flip and never an event written on its own. That atomic coupling
is ``mrr.persistence.unit_of_work.record_object_revision_with_event``; this
service is its first real caller.

Dependency shape and why it is not simply "take an Engine plus the two
concrete E1-T06 Postgres classes and call record_object_revision_with_event
directly":

``record_object_revision_with_event`` is typed against the *concrete*
``mrr.persistence.repositories.PostgresObjectRepository`` and
``PostgresEventLog`` classes (task-packets/E2-T01.yaml forbids modifying
those persistence internals — "reuse E1-T05/T06 as-is"). If
``ResearchScoreService`` depended on those concrete types directly, a
DB-free unit test could not substitute an in-memory fake without breaking
mypy strict (nominal typing, not structural, for a concrete class
parameter) or resorting to ``# type: ignore`` at every call site. Instead:

- reads go through ``mrr.domain.repositories.ObjectRepository`` — a
  ``Protocol`` E1-T05 already made structurally satisfied by
  ``PostgresObjectRepository`` (and by a hand-written unit-test fake, with
  no inheritance needed);
- the atomic write goes through ``RecordRevisionWithEvent`` — a small
  ``Callable`` shaped exactly like ``record_object_revision_with_event``
  minus its already-bound ``engine``/``object_repository``/``event_log``
  arguments. ``bind_unit_of_work`` below is the one place that closes over
  the real ``Engine`` and the real E1-T06 Postgres classes and calls the
  real function; production wiring and integration tests use it. Unit tests
  inject their own trivial callable backed by in-memory fakes instead — the
  task packet's own suggested "lightweight fake unit-of-work" alternative to
  faking ``ObjectRepository``/``EventLog`` and fighting the concrete
  parameter types.
- causation-chain lookups (see ``_last_event_id_for`` below) go through a
  minimal local ``_EventJournal`` protocol (``read_all`` only) rather than
  the full generic ``mrr.provenance.log.EventLog[TTx]`` — this service never
  calls ``append`` directly (that stays inside ``bind_unit_of_work``'s
  closure, alongside the object-repository write, in one transaction), so
  it does not need the rest of that protocol's shape.

None of this reopens or reimplements E1-T05/T06: every actual write and
every hash-chain computation still happens inside
``record_object_revision_with_event``/``PostgresEventLog.append`` exactly as
E1-T06 built them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

from mrr.contracts import ResearchScore, ResearchScoreStatus, Urn
from mrr.domain.exceptions import (
    ApprovalRequiredError,
    InvalidTransitionError,
    ObjectNotFoundError,
    ScoreNotApprovedError,
    ScoreNotFoundError,
)
from mrr.domain.hashing_policy import compute_content_hash
from mrr.domain.identity import new_urn
from mrr.domain.lifecycles import RESEARCH_SCORE_LIFECYCLE
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.persistence.unit_of_work import (
    bind_unit_of_work as bind_unit_of_work,
)
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent

#: Statuses from which a run may start (MRR-FR-004 / docs/spec/01_SYSTEM_SPEC.md
#: section 6.1: "Only APPROVED and ACTIVE revisions may start work").
_STARTABLE_STATUSES = frozenset({"APPROVED", "ACTIVE"})

#: Sentinel "from" state used only when reporting ``InvalidTransitionError``
#: for ``create()`` with a non-DRAFT initial status. Never a member of
#: ``RESEARCH_SCORE_LIFECYCLE.states``, so it can never appear as a legal
#: transition source — see ``ResearchScoreService.create`` for why this
#: reuses the typed transition error rather than inventing a new one.
_NEW_SCORE_SENTINEL_STATE = "<new>"


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log —
    deliberately smaller than the generic ``mrr.provenance.log.EventLog[TTx]``
    Protocol (which also declares ``append``/``verify_chain``, neither of
    which this service calls: ``append`` only ever happens inside
    ``bind_unit_of_work``'s closure, atomically with the object write). Not
    ``@runtime_checkable``, for the same reason ``EventLog`` itself is not
    (see its docstring): an ``isinstance`` check on a ``Protocol`` compares
    method *names* only, never signatures, so it would be false comfort over
    the real conformance guarantee, which is mypy's static structural check.
    """

    def read_all(self) -> list[AppendedEvent]: ...


def _score_to_stored_object(score: ResearchScore) -> StoredObject:
    """Convert an already-valid ``ResearchScore`` (id, revision, timestamps,
    and content hash already set by the caller — this service mints none of
    those for ``create``/``revise``, only for the in-place-preserving
    lifecycle transitions in ``_transition``) into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.

    ``body`` is the full schema-shaped JSON object (``model_dump_json`` round
    -tripped through ``json.loads``, matching ``scripts/check_contracts.py``'s
    own round-trip pattern) rather than a partial projection, since
    ``StoredObject.body`` is documented as "the authoritative full payload".
    """
    body: dict[str, Any] = json.loads(score.model_dump_json(exclude_none=True))
    return StoredObject(
        id=score.id,
        api_version=score.api_version,
        kind=score.kind,
        practice_id=score.practice_id,
        revision=score.revision,
        created_at=score.created_at,
        created_by=score.created_by,
        content_hash=score.content_hash,
        supersedes=score.supersedes,
        labels=score.labels,
        body=body,
    )


class ResearchScoreService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.1 ("Research Score Service:
    Validates score contracts, approvals, revisions, and policy
    references."), implemented per task-packets/E2-T01.yaml.

    Constructed with exactly the dependencies its writes and reads need —
    nothing is constructed internally (no engine built from a connection
    string, no repository instantiated behind the caller's back), so a
    caller (production wiring, an integration test, or a DB-free unit test)
    fully controls what backs those dependencies. See the module docstring
    for why the write dependency is ``RecordRevisionWithEvent`` rather than
    the raw E1-T06 Postgres classes.
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
    # Creation and material revision (MRR-FR-001/002/003).
    # ------------------------------------------------------------------

    def create(
        self,
        score: ResearchScore,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``score`` as revision 1, plus a ``research_score.created``
        event, atomically. Rejects any initial status other than ``DRAFT``.

        ``score`` must already be a fully valid ``ResearchScore`` — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller (this service does not generate identifiers or compute
        hashes on the caller's behalf); ``score.revision`` must be ``1``.
        """
        if score.status != RESEARCH_SCORE_LIFECYCLE.initial_state:
            # Creation is not itself a drawn RESEARCH_SCORE_LIFECYCLE edge —
            # there is no real "from" state for a brand-new object. Modeled
            # here as an attempted transition from a sentinel non-state into
            # the caller's requested initial status, reusing
            # InvalidTransitionError (task-packets/E2-T01.yaml names exactly
            # three new typed errors plus this reused one; it does not name
            # a fifth for this case) rather than inventing a new error type.
            raise InvalidTransitionError(
                RESEARCH_SCORE_LIFECYCLE.name, _NEW_SCORE_SENTINEL_STATE, score.status
            )
        if score.revision != 1:
            raise ValueError(
                f"ResearchScore.revision must be 1 for create(), got {score.revision!r}"
            )

        obj = _score_to_stored_object(score)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type="research_score.created",
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=score.id,
            object_revision=1,
            payload={"status": score.status},
        )
        stored, _ = self._record(obj, None, event)
        return stored

    def revise(
        self,
        new_score: ResearchScore,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``new_score`` as the next revision of an existing score,
        plus a ``research_score.revised`` event, atomically (MRR-FR-002: "A
        material change to question, scope, data class, autonomy, budget, or
        publication policy MUST create a new score revision").

        This is the *only* route for a material change — every prior
        revision is already immutable by construction (``insert_revision``
        never updates a row, E1-T05), so MRR-FR-003 ("MUST NOT retroactively
        alter ... completed runs") holds automatically once a revision is
        written; nothing here can reach backward and edit revision 1.

        Status MUST be unchanged from the latest revision — a status change
        is a lifecycle transition (``submit_for_review``/``approve``/...),
        never a side effect of ``revise``, so the two concerns cannot be
        silently conflated.
        """
        latest = self._get_latest_or_raise(new_score.id)
        latest_status = latest.body["status"]
        if new_score.status != latest_status:
            raise ValueError(
                "revise() must not change status — use a lifecycle transition method "
                f"for that (latest status {latest_status!r}, new_score.status "
                f"{new_score.status!r})"
            )
        expected_revision = latest.revision + 1
        if new_score.revision != expected_revision:
            raise ValueError(
                f"new_score.revision must be {expected_revision!r} (latest.revision + 1), "
                f"got {new_score.revision!r}"
            )

        obj = _score_to_stored_object(new_score)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type="research_score.revised",
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(new_score.id),
            correlation_id=correlation_id,
            object_id=new_score.id,
            object_revision=new_score.revision,
            payload={"from_revision": latest.revision, "to_revision": new_score.revision},
        )
        stored, _ = self._record(obj, latest.revision, event)
        return stored

    # ------------------------------------------------------------------
    # Lifecycle transitions — RESEARCH_SCORE_LIFECYCLE edges (E1-T04).
    # ------------------------------------------------------------------

    def submit_for_review(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """DRAFT -> IN_REVIEW."""
        return self._transition(
            score_id,
            "IN_REVIEW",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.submitted_for_review",
        )

    def approve(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """IN_REVIEW -> APPROVED. Requires at least one recorded approval
        reference on the latest revision (``ApprovalRequiredError``
        otherwise) — task-packets/E2-T01.yaml invariant "APPROVED requires
        at least one recorded approval reference".
        """
        return self._transition(
            score_id,
            "APPROVED",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.approved",
        )

    def reject(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """IN_REVIEW -> REJECTED."""
        return self._transition(
            score_id,
            "REJECTED",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.rejected",
        )

    def activate(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """APPROVED -> ACTIVE."""
        return self._transition(
            score_id,
            "ACTIVE",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.activated",
        )

    def suspend(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """APPROVED -> SUSPENDED."""
        return self._transition(
            score_id,
            "SUSPENDED",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.suspended",
        )

    def supersede(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """ACTIVE -> SUPERSEDED."""
        return self._transition(
            score_id,
            "SUPERSEDED",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.superseded",
        )

    def archive(
        self, score_id: Urn, *, actor: Urn, policy_version: str, correlation_id: Urn
    ) -> StoredObject:
        """SUPERSEDED -> ARCHIVED."""
        return self._transition(
            score_id,
            "ARCHIVED",
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
            event_type="research_score.archived",
        )

    # ------------------------------------------------------------------
    # The MRR-FR-004 gate.
    # ------------------------------------------------------------------

    def ensure_can_start_work(self, score_id: Urn) -> StoredObject:
        """The authoritative "may a run start from this score" gate
        (MRR-FR-004 / docs/spec/01_SYSTEM_SPEC.md section 6.1: "Only APPROVED
        and ACTIVE revisions may start work").

        Raises:
            ScoreNotFoundError: ``score_id`` resolves to no stored object at
                all.
            ScoreNotApprovedError: the latest revision exists but its status
                is not ``APPROVED``/``ACTIVE`` — carries the actual status
                (DRAFT, IN_REVIEW, REJECTED, SUSPENDED, SUPERSEDED, or
                ARCHIVED all land here).

        Never returns a boolean. See the module/PR notes for two MRR-FR-004
        cases this slice cannot implement: the schema has no expiry field
        (so "expired" cannot be evaluated), and there is no modeled
        "explicit continuation permission" escape hatch for a superseded
        score — both are flagged as open specification questions rather
        than invented.
        """
        latest = self._get_latest_or_raise(score_id)
        status = latest.body["status"]
        if status not in _STARTABLE_STATUSES:
            raise ScoreNotApprovedError(score_id, status)
        return latest

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, score_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(score_id)
        except ObjectNotFoundError:
            raise ScoreNotFoundError(score_id) from None

    def _last_event_id_for(self, object_id: str) -> str | None:
        """The id of the most recently appended event for ``object_id``, or
        ``None`` if there is none yet — the ``causation_id`` for the next
        event in that score's own causal chain (MRR-NFR-001), distinct from
        ``correlation_id`` (caller-supplied, stable across the whole score's
        lifecycle). ``read_all()`` returns events oldest-first (both the
        ``EventLog`` protocol's contract and ``PostgresEventLog``'s
        implementation), so the last match is the most recent.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == object_id
        ]
        return matching_ids[-1] if matching_ids else None

    def _transition(
        self,
        score_id: Urn,
        to_status: ResearchScoreStatus,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        event_type: str,
    ) -> StoredObject:
        """Shared implementation for every RESEARCH_SCORE_LIFECYCLE edge
        method: load the latest revision, assert the transition is legal
        (fails closed with ``InvalidTransitionError`` and writes nothing —
        both the assertion and the approval check below happen before any
        ``StoredObject``/``DomainEvent`` is even constructed), enforce the
        APPROVED-requires-an-approval invariant, then persist the next
        revision (status changed, nothing else) plus its event atomically.

        Only ``status`` changes here — every other field is carried over
        unchanged from the latest revision's body, keeping the material-
        change surface (``revise``) and the lifecycle surface (this method)
        strictly separate, per task-packets/E2-T01.yaml's explicit
        instruction not to let ``revise`` silently change status.
        """
        latest = self._get_latest_or_raise(score_id)
        from_status = latest.body["status"]
        RESEARCH_SCORE_LIFECYCLE.assert_transition(from_status, to_status)

        if to_status == "APPROVED" and not latest.body.get("approvals"):
            raise ApprovalRequiredError(score_id)

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
            causation_id=self._last_event_id_for(score_id),
            correlation_id=correlation_id,
            object_id=score_id,
            object_revision=new_revision,
            payload={"from_status": from_status, "to_status": to_status},
        )

        stored, _ = self._record(obj, latest.revision, event)
        return stored
