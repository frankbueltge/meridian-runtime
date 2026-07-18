"""Framework-free interfaces for the append-only domain event log and its
transactional outbox (task-packets/E1-T06.yaml, MRR-NFR-001/002). Concrete
PostgreSQL implementations - ``PostgresEventLog``, ``InProcessOutboxDispatcher``
- live in ``mrr.persistence.repositories``; the atomic coupling with an
object-repository write lives in ``mrr.persistence.unit_of_work``.

This module carries no SQLAlchemy, driver, or framework import -
mrr.provenance stays framework-independent (MRR-NFR-010; enforced by the
import-linter contract in pyproject.toml and by
tests/unit/architecture/test_provenance_boundary.py). ``EventLog`` is generic
over the transaction type ``append`` requires (``TTx``) precisely so that
this stays true even though a real ``append`` needs a live database
transaction to satisfy the atomicity invariant below - ``TTx`` is an
abstract type parameter here, never bound to a concrete SQLAlchemy/psycopg
type in this module. ``mrr.persistence.repositories.PostgresEventLog``
instantiates it as ``EventLog[sqlalchemy.Connection]``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable

from mrr.provenance.events import DomainEvent, compute_event_hash
from mrr.provenance.exceptions import ChainVerificationError

#: The transaction/connection type ``EventLog.append`` requires. Declared
#: contravariant because ``TTx`` only ever appears in an *argument* position
#: (``append(self, tx: TTx, ...)``) - never returned - which is exactly the
#: position contravariance describes: an ``EventLog[Base]`` implementation
#: (whose ``append`` accepts any ``Base``) can stand in wherever an
#: ``EventLog[Derived]`` is expected, for any ``Derived`` that is a subtype
#: of ``Base``.
TTx = TypeVar("TTx", contravariant=True)


@dataclass(frozen=True, slots=True)
class AppendedEvent:
    """One event as it exists in the persisted log: the event itself, its
    assigned ``sequence`` (append order, 1-based), the ``content_hash``
    computed for it, and the ``prev_hash`` it was chained onto (``None`` only
    for the very first physical row in the whole log).
    """

    event: DomainEvent
    sequence: int
    content_hash: str
    prev_hash: str | None


class EventLog(Protocol[TTx]):
    """Append-only, tamper-evident domain event log (MRR-NFR-002: "Domain
    events MUST be append-only and tamper-evident"). No public interface
    anywhere on this protocol updates or deletes an appended event - the
    only write is ``append``, and it always creates a new entry.

    ``append`` takes an explicit ``tx: TTx`` - a caller-supplied transaction/
    connection - rather than managing its own transaction internally. This
    is the atomicity invariant's home (task-packets/E1-T06.yaml: "state
    change, event append, and outbox row are one transaction"): requiring
    the transaction as a parameter is what makes it possible to compose an
    append with another write (an object-repository insert, its outbox row)
    into one caller-owned unit of work, and structurally impossible to call
    ``append`` in a way that opens its own transaction and lets an event
    escape that unit of work. See
    ``mrr.persistence.unit_of_work.record_object_revision_with_event`` for
    the concrete composition.

    Deliberately not ``@runtime_checkable``: Python's runtime protocol
    ``isinstance`` check only compares method *names*, never signatures - it
    would happily report ``True`` for a class whose ``append`` has an
    entirely different, incompatible parameter list. The static, generic
    shape here (checked by mypy, e.g.
    ``tests/unit/persistence/test_event_log_protocol_conformance.py``'s
    typed assignment) is the real conformance guarantee; a passing
    ``isinstance`` check would be false comfort on top of it, not additional
    safety.
    """

    def append(self, tx: TTx, event: DomainEvent) -> AppendedEvent:
        """Append ``event`` as the new head of the chain, using ``tx`` for
        every statement, and return the persisted entry (with its assigned
        sequence and computed hashes). The caller owns ``tx``'s lifecycle
        (commit/rollback).
        """
        ...

    def read_all(self) -> list[AppendedEvent]:
        """Return every appended event, oldest (sequence 1) first.

        Unlike ``append``, reading needs no caller-supplied transaction -
        implementations may manage their own read-only connection.
        """
        ...

    def verify_chain(self) -> None:
        """Recompute every event's hash in sequence order and check the
        ``prev_hash`` links.

        Raises:
            mrr.provenance.exceptions.ChainVerificationError: at the first
                sequence whose stored ``content_hash``/``prev_hash`` cannot
                be reproduced from what is actually persisted.
        """
        ...


def verify_appended_events(appended_events: Sequence[AppendedEvent]) -> None:
    """Pure, database-free chain verification over an in-order sequence of
    already-appended events: recompute every hash in sequence order and
    check the ``prev_hash`` links, raising
    ``mrr.provenance.exceptions.ChainVerificationError`` at the first break.

    This is exactly the logic
    ``mrr.persistence.repositories.PostgresEventLog.verify_chain`` runs
    against a live database (it calls this function over
    ``self.read_all()``); exposing it here lets the property-test tier
    exercise real chain-verification logic - not a reimplementation that
    could silently drift from it - without a database
    (task-packets/E1-T06.yaml acceptance: "a freshly computed chain always
    verifies; flipping any single event's payload (or prev_hash) makes
    verification fail at exactly that sequence").

    ``appended_events`` MUST already be in ascending sequence order (as
    ``read_all()`` returns them); this function does not re-sort.
    """
    prev_hash: str | None = None
    for appended in appended_events:
        if appended.prev_hash != prev_hash:
            raise ChainVerificationError(appended.sequence)
        if compute_event_hash(appended.event, prev_hash) != appended.content_hash:
            raise ChainVerificationError(appended.sequence)
        prev_hash = appended.content_hash


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """One row of the transactional outbox: at-least-once delivery
    bookkeeping for one appended event. ``status`` is one of
    ``"pending"``/``"dispatched"`` - never re-created once dispatched, and a
    failed dispatch attempt leaves it ``"pending"`` with ``attempts``
    incremented (task-packets/E1-T06.yaml invariant: "outbox dispatch is
    at-least-once and idempotent-marking - a dispatched row is never
    re-created, a failed dispatch stays pending").
    """

    event_id: str
    status: str
    created_at: datetime
    dispatched_at: datetime | None
    attempts: int


#: The exact outbox status vocabulary. Single source of truth for both the
#: database CHECK constraint built in mrr.persistence.tables (mirroring how
#: mrr.domain.repositories.EDGE_VOCABULARY feeds
#: mrr.persistence.tables' edge_type CHECK, E1-T05) and any code that needs
#: to reason about valid status values.
OUTBOX_STATUSES: frozenset[str] = frozenset({"pending", "dispatched"})

#: A dispatch handler receives one appended event and either returns
#: normally (dispatch succeeded) or raises (dispatch failed - the row stays
#: "pending" with "attempts" incremented; see ``OutboxDispatcher``).
EventHandler = Callable[[AppendedEvent], None]


@runtime_checkable
class OutboxDispatcher(Protocol):
    """At-least-once dispatcher over pending outbox rows."""

    def dispatch_pending(self) -> int:
        """Attempt to dispatch every currently pending row once, in creation
        order.

        Returns:
            the number of rows successfully dispatched (transitioned
            ``"pending"`` -> ``"dispatched"``) in this call. A handler
            exception for a given row leaves that row ``"pending"`` with
            ``attempts`` incremented and does not stop the rest of the
            batch from being attempted; it is not counted in the return
            value.
        """
        ...
