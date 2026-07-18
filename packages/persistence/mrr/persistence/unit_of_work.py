"""Atomic composition of an object-repository write with an event-log append
and its outbox row, so all three commit or roll back together as ONE
transaction (task-packets/E1-T06.yaml invariant: "state change, event
append, and outbox row are one transaction - an injected failure after any
of the three leaves none of them persisted").

``PostgresObjectRepository.insert_revision_with_connection`` and
``PostgresEventLog.append`` are both connection-accepting (E1-T06's addition
to the E1-T05 repository); this module's one function is the reason those
variants exist — it opens exactly one ``Engine.begin()`` transaction and
threads the same ``Connection`` through both writes.
"""

from __future__ import annotations

from collections.abc import Callable

from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine


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
