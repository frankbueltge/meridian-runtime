"""``VerificationService`` (task-packets/E3-T04.yaml): records
``VerificationResult`` objects (docs/spec/02_DOMAIN_MODEL.md section 2.13)
while enforcing the self-verification prohibition (MRR-FR-070, AGENTS.md
rule 8: "No executor may approve or verify its own result" — this task's
own derivation calls it "the heart of this task") and driving a failed
verification into the claim's status by a documented, deterministic policy
(MRR-FR-075). Fourth task of Epic E3 (claim, evidence, correction kernel);
the closest templates are ``mrr.services.source_family.service.SourceFamilyService``
(E3-T03, a create-only entity with the identical "one revision + one event,
atomically" wiring) and ``mrr.services.claim.service.ClaimService`` (E3-T02,
reused here — not reimplemented — to drive the claim's own lifecycle).

--- The headline gate: self-verification is checked FIRST, before anything is written ---

``record``'s very first act — before the revision sanity check, before any
database write — is the self-verification gate: if the reviewer issuing
this verification decision IS the claim's own ``proposer_id``, or IS the
producing run's ``executor_id`` (when known), nothing is persisted at all;
``mrr.domain.exceptions.SelfVerificationError`` is raised instead. This
mirrors ``mrr.services.claim.service.ClaimService._transition``'s own
"check before any write" discipline for ``InvalidTransitionError`` — a
caught instance always means the database looks exactly as it did before
the call, both for the ``VerificationResult`` object and for the claim's
own status.

--- target_id must match the claim actually being recorded against -----------

``record`` takes a full ``Claim`` (not just its id) precisely because the
self-verification check needs ``claim.proposer_id`` and the status-driving
step needs ``claim.status`` — task-packets/E3-T04.yaml's own method
signature, and ``ClaimService`` exposes no public "read the current claim"
method a caller could otherwise use (its public surface is entirely
lifecycle transitions and edge writes; see that module's own docstring).
The caller is therefore trusted to have already resolved the ``Claim`` this
``verification`` is actually about — but a caller mistake (passing a
mismatched ``claim``) would silently defeat the very gate this task exists
to enforce, checking the WRONG claim's ``proposer_id``. ``record`` guards
against exactly this by asserting ``verification.target_id == claim.id``
before anything else; a mismatch is a caller/programmer error (a plain
``ValueError``, matching every other "caller supplied inconsistent data"
guard in this codebase, e.g. ``ClaimService.create``'s
"revision must be 1" check), never silently ignored.

--- The failed-verification-to-claim-status policy (MRR-FR-075) -------------

This is this module's own documented, deterministic mapping — flagged for
reviewer scrutiny in the PR body, since task-packets/E3-T04.yaml gives only
an illustrative "e.g." and leaves the exact rule for this task to choose and
document. The rule is driven by the claim's CURRENT status (as carried on
the ``claim`` argument) and by nothing else:

- ``claim.status == "supported"`` -> ``ClaimService.require_review`` (->
  ``review_required``). NOT ``to_contested``: ``mrr.domain.lifecycles.
  CLAIM_LIFECYCLE`` (E1-T04) draws no ``supported -> contested`` edge at
  all — the only legal outgoing edges from ``supported`` are the two
  universal rules (-> ``withdrawn``, -> ``superseded``) and
  -> ``review_required``. ``review_required`` is therefore the ONLY legal
  transition that removes ``supported`` in response to a failed
  verification; this satisfies the packet's invariant ("never leaves a
  failed-verification claim in `supported`") without inventing a
  CLAIM_LIFECYCLE edge that was never drawn (AGENTS.md rule 3).
- ``claim.status == "under_review"`` -> ``ClaimService.to_contested`` (->
  ``contested``). Both ``under_review -> contested`` and
  ``under_review -> review_required`` are legal CLAIM_LIFECYCLE edges;
  ``contested`` is chosen as the more specific, honest destination for "a
  review actually ran and found the claim's central assertion fails" while
  the claim was still mid-review — ``review_required`` is reserved here for
  the ``supported`` branch, where a full re-review is exactly what is
  needed after a claim previously cleared the bar.
- Any OTHER current status (``draft``, ``contested``, ``contradicted``,
  ``unresolved``, ``unsupported``, ``review_required``, ``withdrawn``,
  ``superseded``, ``legacy_unverified``) -> no status transition is
  attempted. None of these statuses is ``supported``, so the packet's own
  invariant ("never ... supported") is already satisfied without acting;
  inventing a transition out of, say, ``draft`` or a terminal state that
  domain 2.11/CLAIM_LIFECYCLE does not call for would be exactly the kind
  of invented business rule AGENTS.md rule 3 forbids.

This policy runs as a SEPARATE step after the verification record itself
has already been persisted (matching the packet's own numbered steps 2 and
3) — not one combined transaction with it. A verification is always
recorded, whether or not any claim-status transition follows; if the
``claim`` argument's own snapshot happens to be stale relative to the
claim's true current status by the time ``ClaimService`` re-fetches and
validates the transition (a TOCTOU window inherent to this method's own
signature taking an already-resolved ``Claim`` rather than re-reading it
itself — see the module docstring's "target_id must match" section for why
that shape was chosen anyway), ``ClaimService`` raises
``InvalidTransitionError``/``ClaimNotFoundError`` and this method lets it
propagate rather than silently swallow it — flagged as a known limitation
in the PR body.

--- Preserving disagreement (MRR-FR-077) needs no special handling here ------

Every call to ``record`` mints an entirely new ``VerificationResult``
object identity (``verification.id``, set by the caller before calling
this method, exactly like ``SourceFamilyService.create``'s own convention)
and writes it as a brand-new revision-1 object — never a revision of a
prior ``VerificationResult``. Two conflicting reviews of the same claim are
therefore two independent objects from this method's point of view; nothing
here would overwrite the first when the second is recorded. The
``adjudication_relation`` field (carried on ``verification`` itself, set by
the caller) is what links a later adjudicating review back to the one it
resolves — this service does not interpret or validate that link, matching
``mrr.contracts.verification_result.VerificationResult``'s own "declared,
not calculated" stance on independence.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mrr.contracts import Claim, Urn, VerificationResult
from mrr.domain.exceptions import SelfVerificationError
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.claim.service import ClaimService
from sqlalchemy import Engine

#: task-packets/E3-T04.yaml derived_decisions: "persist the verification as
#: revision 1 + a `verification.recorded` event, atomically" — one event
#: type for this entity, dot-separated, matching
#: "source_family.created"/"source_record.created"'s existing convention
#: (this entity's event is named ".recorded", not ".created", per the
#: packet's own literal wording, mirroring docs/spec/03_API_AND_EVENTS.md's
#: own "human_approval.recorded" precedent for a review-shaped event).
_EVENT_VERIFICATION_RECORDED = "verification.recorded"

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. Identical in shape to every other service's own
#: ``RecordRevisionWithEvent`` — see e.g.
#: ``mrr.services.source_family.service``'s own module docstring for why
#: this is a local copy, not a shared import, across separate service
#: modules.
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
    ``VerificationService`` depends on for its one atomic write. Production
    wiring and integration tests call this once; DB-free unit tests pass
    their own trivial callable of the same shape, backed by an in-memory
    fake, instead.
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


def _verification_result_to_stored_object(verification: VerificationResult) -> StoredObject:
    """Convert an already-valid ``VerificationResult`` into the generic
    ``StoredObject`` ``mrr.domain.repositories.ObjectRepository`` persists.
    ``body`` is a plain ``model_dump_json(exclude_none=True)`` round trip —
    no added keys — matching every other service's own
    ``_*_to_stored_object`` helper.
    """
    body: dict[str, Any] = json.loads(verification.model_dump_json(exclude_none=True))
    return StoredObject(
        id=verification.id,
        api_version=verification.api_version,
        kind=verification.kind,
        practice_id=verification.practice_id,
        revision=verification.revision,
        created_at=verification.created_at,
        created_by=verification.created_by,
        content_hash=verification.content_hash,
        supersedes=verification.supersedes,
        labels=verification.labels,
        body=body,
    )


class VerificationService:
    """docs/spec/01_SYSTEM_SPEC.md section 7.7 ("Review and Verification
    Service"), implemented per task-packets/E3-T04.yaml. See the module
    docstring for the full design rationale, especially the
    self-verification gate and the failed-verification-to-claim-status
    policy.
    """

    def __init__(self, record: RecordRevisionWithEvent, claim_service: ClaimService) -> None:
        self._record = record
        self._claim_service = claim_service

    def record(
        self,
        verification: VerificationResult,
        claim: Claim,
        *,
        run_executor_id: Urn | None = None,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Persist ``verification`` as revision 1, plus a
        ``verification.recorded`` event, atomically — then, if
        ``verification.recommendation == "fail"``, drive ``claim``'s status
        via ``ClaimService`` per the module docstring's documented policy.

        ``verification`` must already be a fully valid
        ``VerificationResult`` — its own
        ``id``/``content_hash``/``created_at``/``created_by`` are minted by
        the caller (this service does not generate identifiers or compute
        hashes on the caller's behalf, matching
        ``SourceFamilyService.create``'s own convention); ``revision`` must
        be ``1``. ``claim`` is the ``Claim`` this verification is about,
        already resolved by the caller (see the module docstring's
        "target_id must match" section for why).

        Raises:
            ValueError: ``verification.target_id`` does not match
                ``claim.id`` (a caller/programmer error — checked before the
                self-verification gate, since the gate is meaningless
                against the wrong claim), or ``verification.revision`` is
                not ``1``.
            SelfVerificationError: ``verification.reviewer_id`` equals
                ``claim.proposer_id``, or (when ``run_executor_id`` is
                given) equals ``run_executor_id`` — the packet's headline
                gate (MRR-FR-070 / AGENTS.md rule 8). Checked FIRST, before
                any database write; nothing is persisted.

        A failed-verification claim-status transition
        (``ClaimService.require_review``/``to_contested``) can itself raise
        ``mrr.domain.exceptions.InvalidTransitionError`` or
        ``ClaimNotFoundError`` if ``claim``'s snapshot is stale relative to
        the claim's true current state — see the module docstring's "The
        failed-verification-to-claim-status policy" section. Such an error
        propagates uncaught; by that point the ``VerificationResult`` itself
        has already been durably recorded (steps 2 and 3 are sequential, not
        one combined transaction).
        """
        if verification.target_id != claim.id:
            raise ValueError(
                f"verification.target_id {verification.target_id!r} does not match the "
                f"claim being recorded against (claim.id {claim.id!r})"
            )

        # The self-verification gate — the packet's headline requirement,
        # checked before anything else is written anywhere.
        if verification.reviewer_id == claim.proposer_id:
            raise SelfVerificationError(
                verification.reviewer_id,
                proposer_id=claim.proposer_id,
                executor_id=run_executor_id,
                violated="proposer",
            )
        if run_executor_id is not None and verification.reviewer_id == run_executor_id:
            raise SelfVerificationError(
                verification.reviewer_id,
                proposer_id=claim.proposer_id,
                executor_id=run_executor_id,
                violated="executor",
            )

        if verification.revision != 1:
            raise ValueError(
                f"VerificationResult.revision must be 1 for record(), got {verification.revision!r}"
            )

        obj = _verification_result_to_stored_object(verification)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_VERIFICATION_RECORDED,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=verification.id,
            object_revision=1,
            payload={
                "target_id": verification.target_id,
                "target_kind": verification.target_kind,
                "verification_type": verification.verification_type,
                "recommendation": verification.recommendation,
            },
        )
        stored, _ = self._record(obj, None, event)

        if verification.recommendation == "fail":
            self._drive_claim_status_on_failure(
                claim,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )

        return stored

    def _drive_claim_status_on_failure(
        self,
        claim: Claim,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject | None:
        """MRR-FR-075's deterministic policy — see the module docstring's
        "The failed-verification-to-claim-status policy" section for the
        full rationale of each branch. Returns the claim's new
        ``StoredObject`` if a transition was made, ``None`` for the
        documented no-op branch.
        """
        if claim.status == "supported":
            return self._claim_service.require_review(
                claim.id,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )
        if claim.status == "under_review":
            return self._claim_service.to_contested(
                claim.id,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )
        return None
