"""Atomic composition of an object-repository write with an event-log append
and its outbox row, so all three commit or roll back together as ONE
transaction (task-packets/E1-T06.yaml invariant: "state change, event
append, and outbox row are one transaction - an injected failure after any
of the three leaves none of them persisted").

``PostgresObjectRepository.insert_revision_with_connection`` and
``PostgresEventLog.append`` are both connection-accepting (E1-T06's addition
to the E1-T05 repository); this module's first function,
``record_object_revision_with_event``, is the reason those variants exist —
it opens exactly one ``Engine.begin()`` transaction and threads the same
``Connection`` through both writes. This is the **content-revision** path:
every call mints a new ``StoredObject`` row.

``record_event`` (ADR-0007, docs/spec/adr/ADR-0007-TASK-BUNDLE-TRANSITIONS-ARE-EVENTS.md)
is the complementary **event-only** path: append a domain event (and its
outbox row) atomically, WITHOUT writing any new object content revision.
It exists because the Task Bundle is the one first-class object that is both
lifecycle-bearing and origin-signed (MRR-FR-031) — modeling its lifecycle
transitions (offer/accept/defer/reject, ...) as new content revisions would
mean re-minting the signed payload's ``revision``/``content_hash`` on every
negotiation step, breaking the origin's one-time signature. Under ADR-0007 a
transition is instead an append-only domain event; the authoritative current
status is derived from the event log (``mrr.services.task_bundle.service.
_current_status``), and the signed content record is written once (at
creation) and never touched again by a pure lifecycle transition — only a
genuine content change (``propose_modification``) still goes through
``record_object_revision_with_event``.
"""

from __future__ import annotations

from collections.abc import Callable

from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent, EventLog
from sqlalchemy import Connection, Engine


def record_object_revision_with_event(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
    obj: StoredObject,
    expected_current_revision: int | None,
    event: DomainEvent,
    *,
    _after_append: Callable[[], None] | None = None,
) -> tuple[StoredObject, AppendedEvent]:
    """Insert ``obj`` as a new revision, append ``event``, and create its
    outbox row, all inside one database transaction: either every write
    lands, or none of them do.

    Args:
        engine: opens the single transaction all three writes share.
        object_repository: an existing ``PostgresObjectRepository`` bound to
            ``engine`` (or an engine pointing at the same database).
        event_log: an existing ``PostgresEventLog`` bound likewise.
        obj: the new revision to insert (see
            ``mrr.domain.repositories.ObjectRepository.insert_revision`` for
            the revision-numbering contract).
        expected_current_revision: as for ``insert_revision``.
        event: the domain event describing this state change. Callers are
            responsible for setting ``event.object_id``/``object_revision``
            to match ``obj`` — this function does not cross-check them,
            since doing so would mean guessing at a correspondence the
            specification does not mandate a specific shape for.
        _after_append: test-only synchronization/fault-injection seam (same
            pattern as ``PostgresObjectRepository``'s
            ``_pause_before_insert``, E1-T05): runs after the event append
            but before the transaction commits, so integration tests can
            inject a failure there and assert that NEITHER the object
            revision NOR the event NOR the outbox row survive — all three
            roll back together. Defaults to a no-op; not part of the public
            contract.

    Returns:
        the inserted ``StoredObject`` (with its resolved revision) and the
        ``AppendedEvent`` (with its assigned sequence and computed hashes).

    Raises:
        mrr.domain.exceptions.RevisionConflictError: if ``obj``'s expected
            revision does not match reality; the event is never appended and
            the whole transaction rolls back.
        mrr.provenance.exceptions.EventAppendError: if the event itself
            fails to append (e.g. a duplicate event id); the object revision
            insert rolls back with it.
    """
    hook = _after_append or (lambda: None)
    with engine.begin() as conn:
        stored = object_repository.insert_revision_with_connection(
            conn, obj, expected_current_revision
        )
        appended = event_log.append(conn, event)
        hook()
        return stored, appended


#: The callable shape ``record_object_revision_with_event`` takes once its
#: ``engine``/``object_repository``/``event_log`` arguments are bound — see
#: ``bind_unit_of_work`` below. Added here (task-packets/E9-T00b.yaml,
#: behavior-preserving DRY consolidation) as the single canonical definition;
#: every one of the 19 service modules that used to define this identical
#: alias locally now re-exports it from here instead
#: (``from mrr.persistence.unit_of_work import RecordRevisionWithEvent as
#: RecordRevisionWithEvent``) — see this module's own history for why it
#: previously lived only at each call site (task-packets/E2-T01.yaml's own
#: ``allowed_paths`` did not include this file).
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
    triple, producing the ``RecordRevisionWithEvent`` callable each service
    depends on for atomic writes. Production wiring and integration tests
    call it once to build the ``record`` argument a service's own
    ``__init__`` takes; DB-free unit tests skip it entirely and pass their
    own trivial callable of the same ``RecordRevisionWithEvent`` shape,
    backed by in-memory fakes, instead.
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


def record_event(
    engine: Engine,
    event_log: EventLog[Connection],
    event: DomainEvent,
    *,
    _after_append: Callable[[], None] | None = None,
) -> AppendedEvent:
    """Append ``event`` (and its outbox row) atomically, WITHOUT writing any
    object content revision — the event-only counterpart to
    ``record_object_revision_with_event`` (ADR-0007: a Task Bundle lifecycle
    transition is a domain event, not a new signed content revision).

    Body is deliberately exactly ``with engine.begin() as conn: return
    event_log.append(conn, event)`` — one transaction, one write. There is no
    ``object_repository`` parameter at all: this function cannot touch the
    ``objects`` table even by accident, which is the whole point (a caller
    that wants a content revision uses ``record_object_revision_with_event``
    instead; the two are not interchangeable).

    Typed against the generic ``mrr.provenance.log.EventLog[Connection]``
    Protocol rather than the concrete ``PostgresEventLog`` class (unlike
    ``record_object_revision_with_event``, which needs the concrete
    ``PostgresObjectRepository``/``PostgresEventLog`` types because
    ``insert_revision_with_connection`` is a Postgres-only addition not on
    the framework-free ``ObjectRepository`` protocol). ``EventLog[TTx]``'s
    own connection-accepting ``append`` shape already covers everything this
    function needs, so nothing is lost by depending on the Protocol here —
    and it is what lets a DB-free unit test substitute a fake event log
    (``tests/unit/persistence/test_unit_of_work.py``) without any
    ``# type: ignore``. ``PostgresEventLog`` itself still satisfies this
    Protocol exactly as before (it *is* an ``EventLog[Connection]``, per its
    own docstring), so production wiring and the integration tier are
    unaffected.

    Args:
        engine: opens the single transaction the append shares (with its
            outbox row — both are written inside ``PostgresEventLog.append``
            itself, which this function does not duplicate).
        event_log: an event log bound to ``engine`` (or an engine pointing at
            the same database).
        event: the domain event describing the lifecycle transition.
        _after_append: test-only fault-injection seam, identical in spirit to
            ``record_object_revision_with_event``'s own ``_after_append``: it
            runs after the event (and its outbox row) is written but before
            the transaction commits, so a test can inject a failure there and
            assert that NEITHER the event NOR its outbox row survive. Not
            part of the public contract; defaults to a no-op.

    Returns:
        the ``AppendedEvent`` (with its assigned sequence and computed
        hashes).

    Raises:
        mrr.provenance.exceptions.EventAppendError: if the event itself
            fails to append (e.g. a duplicate event id); the transaction
            rolls back with it.
    """
    hook = _after_append or (lambda: None)
    with engine.begin() as conn:
        appended = event_log.append(conn, event)
        hook()
        return appended
