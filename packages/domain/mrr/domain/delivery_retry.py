"""Offline recipient delivery tracking — task-packets/E6-T06.yaml, the
"durable pending-delivery record" half of MRR-FR-094 ("Every affected
practice MUST receive a signed notification or a durable pending-delivery
record"), the half task-packets/E6-T03.yaml's own ``notify_affected_
practices`` explicitly deferred: it performs one synchronous online delivery
ATTEMPT per recipient and records the outcome, but never retries and never
builds durable tracking over a recipient left ``DELIVERY_PENDING``. This
module is that tracking layer's PURE, framework-free core: a narrow
three-state ``pending``/``delivered``/``exhausted`` machine, a scheduling
function, an exhaustion predicate, the record's own value object, and the
``Protocol`` a durable store implements — mirroring ``mrr.domain.
replay_retention``'s own "one pure decision function, unit-testable without
Postgres" shape, and ``mrr.domain.repositories.ObjectRepository``/
``EdgeRepository``'s own "Protocol in mrr.domain, concrete implementation in
mrr.persistence" split.

--- Why a NEW narrow machine, not a fifth member of mrr.domain.lifecycles ---

A per-recipient delivery-tracking record is internal bookkeeping this task
invents, not one of "the four MRR lifecycles defined in docs/spec/
01_SYSTEM_SPEC.md section 6" that module's own docstring scopes itself to
(``ResearchScore``/``TaskBundle``/``Claim``/``CorrectionEvent``) plus the
later E6/K0/K1 additions grounded in their own FR text or spec-08 table rows
— this record has no section-6 diagram and no spec-08 table row of its own
at all; it is this task's own, invented, internal concept. So
``DELIVERY_RETRY_LIFECYCLE`` below is declared HERE, built by IMPORTING
``mrr.domain.lifecycles.StateMachine`` (the existing, reused-verbatim
dataclass) and ``mrr.domain.exceptions.InvalidTransitionError`` (reused
verbatim from the SAME module every other machine's fail-closed check
already raises) — ``mrr.domain.lifecycles`` itself gains no fifth machine
and is not imported anywhere except for these two reused names. Its own
``CORRECTION_LIFECYCLE`` stays byte-for-byte unchanged, including its own,
already-documented "``DELIVERY_PENDING`` has no drawn outgoing edge at all"
dead end (that module's docstring, lines 72-75) — this task tracks and
resolves every per-recipient record WITHOUT ever driving the owning
``CorrectionEvent``'s own status out of ``DELIVERY_PENDING`` (see
``mrr.services.correction.service``'s own module docstring for the E6-T06
section once wired). Whether a correction whose every recipient eventually
resolves should ever leave ``DELIVERY_PENDING`` is left to a future ADR or
spec amendment — flagged, not decided, here.

The record's own ``status`` transitions only ``pending -> delivered`` and
``pending -> exhausted``; both are terminal (no edge leads anywhere from
either, and neither leads to the other). Unlike ``mrr.domain.
replay_retention``'s ``processed_ids`` rows (append-then-prune-only), this
record is never pruned: the record itself IS the durable audit fact
MRR-FR-094 requires, not a transient replay cache — deleting a resolved
record would defeat the requirement it exists to satisfy. Long-term storage
growth of resolved records is an open operational question, not solved here.

--- Scheduling: anchored to the notification's OWN expires_at ---------------

:func:`next_retry_at` mirrors ``mrr.domain.replay_retention.
processed_id_retention_horizon``'s own "anchored on the object's own expiry,
not an independent policy number" reasoning, applied in the OPPOSITE
temporal direction: that function computes the EARLIEST instant a replay
record becomes safe to prune (never BEFORE the object's own expiry); this
function computes the LATEST instant a retry may still be scheduled (never
AFTER the wrapped notification's own ``expires_at``) — retrying an envelope
whose own validity window has already closed is provably futile, because the
recipient's OWN, unchanged ``mrr.domain.envelope_validation.
validate_inbound_envelope`` would reject it as expired
(``EnvelopeNotWithinValidityWindowError``) regardless of how many further
attempts are made or which channel carries them. The concrete retry backoff
CURVE between attempts is a caller-supplied policy value this module does
not fix (mirrors task-packets/E5-T07.yaml's own identical deferral of "the
concrete retention grace as a policy value") — passed in as a plain
``Callable[[int], timedelta]`` (:data:`Backoff`) mapping an attempt number to
a delay, rather than this module presupposing linear, exponential, or any
other specific shape.

:func:`is_retry_exhausted` is ``True`` once EITHER a caller-supplied
``max_attempts`` is reached OR the evaluation instant is at/after that same
``expires_at`` — whichever comes first. Both conditions are independent and
either alone is sufficient; this is a plain ``or``, so the predicate is
monotonic in both ``attempt_count`` and the evaluation instant by
construction (once ``True`` for a given state, it stays ``True`` for any
later evaluation instant or any higher ``attempt_count`` — see
``tests/property/test_delivery_retry_properties.py``).

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network, no scheduler, cron, or message queue —
pure functions/value objects over already-in-memory values, CI-testable with
no database. :class:`DeliveryPendingStore` is a ``Protocol`` only; the real,
durable implementation (``PostgresDeliveryPendingStore``) lives in
``mrr.persistence.repositories``, exactly as ``mrr.domain.repositories.
ObjectRepository``/``EdgeRepository`` are Protocols satisfied by
``PostgresObjectRepository``/``PostgresEdgeRepository`` there. This module
also does not decide who calls the retry-due query on any cadence — that
remains a plain callable, mirroring task-packets/E5-T07.yaml's own identical
``prune_expired`` stance ("who schedules it is out of scope").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from mrr.domain.lifecycles import StateMachine

__all__ = [
    "DELIVERY_RECORD_STATUSES",
    "DELIVERY_RETRY_LIFECYCLE",
    "Backoff",
    "DeliveryChannel",
    "DeliveryOutcome",
    "DeliveryPendingRecord",
    "DeliveryPendingStore",
    "is_retry_exhausted",
    "next_retry_at",
]

#: A caller-supplied retry-backoff CURVE: attempt number (the attempt about
#: to be scheduled, >= 1) -> delay added on top of the instant of the most
#: recent attempt. Left entirely to the caller/deployment (see the module
#: docstring's "Scheduling" section) — this module fixes only how the result
#: is anchored, never the curve's shape.
Backoff = Callable[[int], timedelta]

#: The terminal outcome of one delivery retry ATTEMPT, as reported back to
#: :class:`DeliveryPendingStore`.record_retry_attempt` — mirrors ``mrr.
#: domain.envelope_transport.DeliveryStatus``'s identical two-value
#: vocabulary (this module declares its own rather than importing that one,
#: since the two describe different things: a TRANSPORT-level outcome there,
#: a delivery-TRACKING outcome here).
DeliveryOutcome = Literal["delivered", "failed"]

#: Which retry channel produced a given attempt/outcome — carried in the
#: ``correction.notification_sent`` event payload (task-packets/E6-T06.yaml
#: derived_decisions (g)) so the durable audit trail distinguishes a further
#: online attempt from a fresh offline-bundle export.
DeliveryChannel = Literal["online", "offline_bundle"]

#: Verbatim vocabulary for the ``pending_deliveries.status`` CHECK
#: constraint, alphabetically sorted — the single source of truth
#: ``mrr.persistence.tables`` imports from, mirroring ``mrr.domain.
#: replay_retention.PROCESSED_ID_KINDS``'s identical drift-prevention
#: pattern.
DELIVERY_RECORD_STATUSES: tuple[str, ...] = ("delivered", "exhausted", "pending")

_DELIVERY_RETRY_STATES = frozenset({"pending", "delivered", "exhausted"})

_DELIVERY_RETRY_TRANSITIONS = frozenset(
    {
        ("pending", "delivered"),
        ("pending", "exhausted"),
    }
)

#: The narrow three-state machine this task's own per-recipient
#: pending-delivery record is tracked by — built by IMPORTING the existing,
#: generic ``mrr.domain.lifecycles.StateMachine`` dataclass, never added as a
#: fifth member alongside that module's own four (plus later E6/K0/K1) MRR
#: lifecycles. See the module docstring's "Why a NEW narrow machine" section.
DELIVERY_RETRY_LIFECYCLE = StateMachine(
    name="DeliveryRetry",
    states=_DELIVERY_RETRY_STATES,
    transitions=_DELIVERY_RETRY_TRANSITIONS,
    initial_state="pending",
)


def next_retry_at(
    *,
    last_attempted_at: datetime,
    attempt_count: int,
    backoff: Backoff,
    expires_at: datetime,
) -> datetime:
    """Return the instant the NEXT retry attempt (attempt number
    ``attempt_count``) may be scheduled for, anchored so it can never fall
    strictly after ``expires_at`` — the wrapped notification's own expiry.
    See the module docstring's "Scheduling" section for the full anchoring
    rationale.

    ``result = min(last_attempted_at + backoff(attempt_count), expires_at)``:
    always at/before ``expires_at``, regardless of how large a delay
    ``backoff`` returns. Monotonically non-decreasing across successive
    calls with an increasing ``attempt_count`` for a fixed, non-decreasing
    ``backoff`` policy and non-decreasing ``last_attempted_at`` (the ordinary
    case — each real attempt happens no earlier than the previous one).

    Args:
        last_attempted_at: the instant the attempt ``attempt_count`` is being
            scheduled AFTER (the most recent attempt's own instant, or the
            first failed synchronous attempt's instant when scheduling the
            second attempt).
        attempt_count: the attempt number about to be scheduled. MUST be
            ``>= 1`` — enforced, not merely documented.
        backoff: the caller-supplied delay curve (:data:`Backoff`). MUST
            return a non-negative ``timedelta`` for ``attempt_count`` —
            enforced, not merely documented: a negative delay would let a
            later-scheduled retry precede an earlier one, which is exactly
            the failure mode this function exists to make impossible by
            construction.
        expires_at: the wrapped notification's own ``expires_at`` — the
            absolute ceiling no retry may ever be scheduled past.

    Returns:
        The anchored next-retry instant, always ``<= expires_at``.

    Raises:
        ValueError: ``attempt_count < 1``, or ``backoff(attempt_count)`` is
            negative.
    """
    if attempt_count < 1:
        raise ValueError(f"attempt_count must be >= 1, got {attempt_count!r}")
    delay = backoff(attempt_count)
    if delay < timedelta(0):
        raise ValueError(f"backoff({attempt_count!r}) must be >= timedelta(0), got {delay!r}")
    candidate = last_attempted_at + delay
    return min(candidate, expires_at)


def is_retry_exhausted(
    *,
    attempt_count: int,
    max_attempts: int,
    expires_at: datetime,
    at: datetime,
) -> bool:
    """``True`` iff EITHER ``attempt_count >= max_attempts`` OR ``at >=
    expires_at`` — whichever comes first, since retrying past ``expires_at``
    is provably futile regardless of how many attempts remain (see the
    module docstring's "Scheduling" section).

    A plain ``or`` of two independently-monotonic conditions: unconditionally
    ``True`` at/after ``expires_at`` regardless of ``attempt_count`` (even
    ``attempt_count == 0``), and unconditionally ``True`` once
    ``attempt_count >= max_attempts`` regardless of how much of the validity
    window remains. Monotonic in both ``attempt_count`` and ``at`` by
    construction: once ``True`` for a given state, it stays ``True`` for any
    later evaluation instant or any higher ``attempt_count``.

    Args:
        attempt_count: the number of attempts made so far (>= 0).
        max_attempts: the caller-supplied policy ceiling on attempts. MUST be
            ``>= 1`` — enforced, not merely documented.
        expires_at: the wrapped notification's own ``expires_at``.
        at: the evaluation instant.

    Returns:
        Whether retrying is exhausted.

    Raises:
        ValueError: ``attempt_count < 0`` or ``max_attempts < 1``.
    """
    if attempt_count < 0:
        raise ValueError(f"attempt_count must be >= 0, got {attempt_count!r}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts!r}")
    return attempt_count >= max_attempts or at >= expires_at


@dataclass(frozen=True, slots=True, kw_only=True)
class DeliveryPendingRecord:
    """One durably tracked per-recipient pending-delivery record — the value
    object :class:`DeliveryPendingStore`'s read/write methods exchange.

    Carries no raw restricted or participant-identifiable data — only ids
    (``correction_id``, ``notification_id``, ``recipient_node_id``),
    timestamps, an attempt count, and a coarse ``status``/
    ``exhausted_reason`` string (task-packets/E6-T06.yaml invariant,
    MRR-NFR-006), mirroring ``mrr.contracts.correction_notification.
    CorrectionNotification``'s own no-raw-data shape one layer further in.

    ``next_retry_at``/``resolved_at``/``exhausted_reason`` are ``None``
    exactly when the record's own lifecycle stage does not yet (or no
    longer) carry that field: ``next_retry_at`` is set only while
    ``status == "pending"``; ``resolved_at`` is set only once ``status`` is
    ``"delivered"`` or ``"exhausted"``; ``exhausted_reason`` is set only once
    ``status == "exhausted"``.
    """

    recipient_node_id: str
    notification_id: str
    correction_id: str
    status: Literal["pending", "delivered", "exhausted"]
    attempt_count: int
    opened_at: datetime
    last_attempted_at: datetime
    next_retry_at: datetime | None
    notification_expires_at: datetime
    resolved_at: datetime | None
    exhausted_reason: str | None


@runtime_checkable
class DeliveryPendingStore(Protocol):
    """The durable per-recipient pending-delivery store's own interface —
    mirrors ``mrr.domain.repositories.ObjectRepository``/``EdgeRepository``'s
    identical "Protocol in mrr.domain, concrete Postgres implementation in
    mrr.persistence" split. The real implementation is ``mrr.persistence.
    repositories.PostgresDeliveryPendingStore``; tests that do not need a
    real PostgreSQL may satisfy this Protocol with an in-memory fake.

    Keyed throughout by ``(recipient_node_id, notification_id)`` — mirrors
    ``mrr.persistence.tables.processed_ids_table``'s own ``(recipient_node_id,
    id)`` primary-key shape, substituting the correction notification's own
    stable ``notification_id`` (independent of whichever envelope/bundle most
    recently wrapped it — the stable key multiple retry attempts across
    different envelopes/bundles must share).
    """

    def open_pending_delivery(
        self,
        recipient_node_id: str,
        notification_id: str,
        *,
        correction_id: str,
        notification_expires_at: datetime,
        at: datetime,
    ) -> bool:
        """Idempotently open a NEW pending-delivery record at
        ``status="pending"``, ``attempt_count=1``, the FIRST time a
        recipient's synchronous delivery attempt reports ``"failed"``.

        Returns:
            ``True`` if this call newly created the record, ``False`` if
            ``(recipient_node_id, notification_id)`` already existed (the
            idempotent no-op case) — mirrors ``PostgresProcessedIdStore.
            record_processed``'s own idempotent-boolean shape.
        """
        ...

    def record_retry_attempt(
        self,
        recipient_node_id: str,
        notification_id: str,
        *,
        outcome: DeliveryOutcome,
        at: datetime,
    ) -> DeliveryPendingRecord:
        """Record one further retry attempt's outcome against an existing
        ``status="pending"`` record.

        ``outcome="delivered"``: transitions the record to
        ``status="delivered"``, sets ``resolved_at=at``, clears
        ``next_retry_at``.

        ``outcome="failed"``: increments ``attempt_count``; if
        :func:`is_retry_exhausted` now holds (evaluated against the
        incremented ``attempt_count``, the record's own
        ``notification_expires_at``, and ``at``), transitions to
        ``status="exhausted"`` with a non-empty, auto-generated
        ``exhausted_reason``, sets ``resolved_at=at``, clears
        ``next_retry_at``; otherwise recomputes ``next_retry_at`` (via
        :func:`next_retry_at`) and leaves ``status="pending"``.

        Raises:
            mrr.domain.exceptions.PendingDeliveryNotFoundError: no record
                exists for ``(recipient_node_id, notification_id)``.
            mrr.domain.exceptions.InvalidTransitionError: the record is
                already ``status="delivered"`` or ``status="exhausted"`` —
                the row is left completely unchanged (no partial update, no
                silent reopening of a terminal record).
        """
        ...

    def mark_exhausted(
        self,
        recipient_node_id: str,
        notification_id: str,
        *,
        reason: str,
        at: datetime,
    ) -> DeliveryPendingRecord:
        """Explicitly transition an existing ``status="pending"`` record to
        ``status="exhausted"`` — for a caller-decided early exhaustion (e.g.
        the recipient endpoint is known permanently gone) rather than one
        discovered as a side effect of :meth:`record_retry_attempt`.

        Raises:
            ValueError: ``reason`` is empty.
            mrr.domain.exceptions.PendingDeliveryNotFoundError: no record
                exists for ``(recipient_node_id, notification_id)``.
            mrr.domain.exceptions.InvalidTransitionError: the record is
                already ``status="delivered"`` or ``status="exhausted"`` —
                the row is left completely unchanged.
        """
        ...

    def get_pending_delivery(
        self, recipient_node_id: str, notification_id: str
    ) -> DeliveryPendingRecord | None:
        """Return the record for ``(recipient_node_id, notification_id)``, or
        ``None`` if it has never been opened."""
        ...

    def list_due_for_retry(self, now: datetime) -> list[DeliveryPendingRecord]:
        """Return every record with ``status == "pending"`` AND
        ``next_retry_at <= now`` — a plain callable query; no scheduler,
        cron, or queue is built to invoke it on any cadence (mirrors
        task-packets/E5-T07.yaml's own identical ``prune_expired`` stance).
        """
        ...
