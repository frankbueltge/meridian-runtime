"""SQLAlchemy-backed implementations of the ``mrr.domain.repositories``
protocols (task-packets/E1-T05.yaml), against a supplied
``sqlalchemy.Engine``, plus the ``mrr.provenance.log`` protocols
(task-packets/E1-T06.yaml) — ``PostgresEventLog`` (the append-only,
tamper-evident domain event log) and ``InProcessOutboxDispatcher`` (the
at-least-once reference outbox dispatcher).

Optimistic concurrency (``PostgresObjectRepository.insert_revision``) is
belt-and-braces:

1. **Belt** — inside the write transaction, ``SELECT max(revision)`` for the
   object id and compare it against ``expected_current_revision`` before
   attempting the insert. This rejects the common case cheaply and with a
   clear error.
2. **Braces** — the ``(id, revision)`` primary key is the actual race-safe
   arbiter. If two writers both pass the belt check for the same expected
   revision (a genuine race), only one physical ``INSERT`` can succeed;
   the loser's ``IntegrityError`` is caught, the object's real current
   revision is re-read in a fresh transaction, and
   ``mrr.domain.exceptions.RevisionConflictError`` is raised with that
   freshly observed value.

Neither ``PostgresObjectRepository``/``PostgresEdgeRepository`` nor
``PostgresEventLog`` offers an update or delete of an existing row —
``insert_revision``, ``add_edge``, and ``append`` are the only writes, and
all three always create a new row.

``PostgresEventLog.append`` deliberately takes a live ``Connection`` rather
than opening its own transaction from an ``Engine`` the way
``insert_revision``/``add_edge`` do. The whole point of E1-T06 is that an
object write, its event, and its outbox row commit or roll back together as
ONE transaction (task-packets/E1-T06.yaml invariant: "state change, event
append, and outbox row are one transaction"). An ``Engine``-based
convenience that opened its own transaction would make it easy to call
``append`` outside that shared transaction by accident, silently
reintroducing the very divergence this task exists to close — so it is not
offered. Composing an event append with an object write is
``mrr.persistence.unit_of_work.record_object_revision_with_event``.
``read_all``/``verify_chain`` need no such coupling (reads outside a write
transaction are safe) and stay ``Engine``-based like the E1-T05 repositories.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.persistence.tables import domain_events_table, edges_table, objects_table, outbox_table
from mrr.provenance.events import DomainEvent, compute_event_hash
from mrr.provenance.exceptions import EventAppendError
from mrr.provenance.log import AppendedEvent, EventHandler, verify_appended_events
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import IntegrityError


def _row_to_stored_object(row: Any) -> StoredObject:
    return StoredObject(
        id=row.id,
        api_version=row.api_version,
        kind=row.kind,
        practice_id=row.practice_id,
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        content_hash=row.content_hash,
        supersedes=row.supersedes,
        labels=row.labels,
        body=row.body,
    )


def _expected_new_revision(obj: StoredObject, expected_current_revision: int | None) -> int:
    new_revision = 1 if expected_current_revision is None else expected_current_revision + 1
    if obj.revision != new_revision:
        raise ValueError(
            f"obj.revision ({obj.revision!r}) does not match the revision implied by "
            f"expected_current_revision ({expected_current_revision!r}): expected "
            f"{new_revision!r}. The caller must set obj.revision to "
            "expected_current_revision + 1 (or 1 when expected_current_revision is None) "
            "before calling insert_revision."
        )
    return new_revision


def _row_to_typed_edge(row: Any) -> TypedEdge:
    return TypedEdge(
        id=row.id,
        source_id=row.source_id,
        target_id=row.target_id,
        edge_type=row.edge_type,
        created_at=row.created_at,
        created_by=row.created_by,
        scope=row.scope,
        status=row.status,
        practice_id=row.practice_id,
    )


class PostgresObjectRepository:
    """``mrr.domain.repositories.ObjectRepository`` against PostgreSQL."""

    def __init__(
        self,
        engine: Engine,
        *,
        _pause_before_insert: Callable[[], None] | None = None,
    ) -> None:
        self._engine = engine
        # Test-only synchronization seam, not part of the public repository
        # contract: it lets tests/integration force a deterministic thread
        # interleaving for the true-concurrency acceptance test (two writers
        # both passing the belt check before either reaches the physical
        # INSERT). Defaults to a no-op, so ordinary callers are unaffected.
        self._pause_before_insert = _pause_before_insert or (lambda: None)

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        new_revision = _expected_new_revision(obj, expected_current_revision)
        with self._engine.begin() as conn:
            return self._insert_revision_core(conn, obj, expected_current_revision, new_revision)

    def insert_revision_with_connection(
        self,
        conn: Connection,
        obj: StoredObject,
        expected_current_revision: int | None,
    ) -> StoredObject:
        """Same as ``insert_revision``, but writes through a caller-supplied
        ``Connection`` instead of opening its own transaction — the
        connection-accepting variant task-packets/E1-T06.yaml asks for so an
        object write can share ONE transaction with an event append and its
        outbox row. See
        ``mrr.persistence.unit_of_work.record_object_revision_with_event``.

        The caller owns the transaction: it is responsible for beginning it
        (or using an already-begun ``Connection``) and for commit/rollback.
        """
        new_revision = _expected_new_revision(obj, expected_current_revision)
        return self._insert_revision_core(conn, obj, expected_current_revision, new_revision)

    def _insert_revision_core(
        self,
        conn: Connection,
        obj: StoredObject,
        expected_current_revision: int | None,
        new_revision: int,
    ) -> StoredObject:
        raw_max = conn.execute(
            sa.select(sa.func.max(objects_table.c.revision)).where(objects_table.c.id == obj.id)
        ).scalar_one()
        current_max: int | None = int(raw_max) if raw_max is not None else None
        if current_max != expected_current_revision:
            raise RevisionConflictError(obj.id, expected_current_revision, current_max)

        self._pause_before_insert()

        try:
            conn.execute(
                sa.insert(objects_table).values(
                    id=obj.id,
                    revision=new_revision,
                    api_version=obj.api_version,
                    kind=obj.kind,
                    practice_id=obj.practice_id,
                    created_at=obj.created_at,
                    created_by=obj.created_by,
                    content_hash=obj.content_hash,
                    supersedes=obj.supersedes,
                    labels=obj.labels,
                    body=obj.body,
                )
            )
        except IntegrityError:
            # A concurrent writer won the race between our belt check above
            # and our own INSERT. Re-read the real current revision through a
            # brand-new connection (this one may now be in an aborted-
            # transaction state, e.g. when called from inside a shared unit
            # of work) and report it as the actual value in the conflict.
            actual = self._current_max_revision(obj.id)
            raise RevisionConflictError(obj.id, expected_current_revision, actual) from None

        return replace(obj, revision=new_revision)

    def _current_max_revision(self, id: str) -> int | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                sa.select(sa.func.max(objects_table.c.revision)).where(objects_table.c.id == id)
            ).scalar_one()
        return int(value) if value is not None else None

    def get_latest(self, id: str) -> StoredObject:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(objects_table)
                    .where(objects_table.c.id == id)
                    .order_by(objects_table.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ObjectNotFoundError(id)
        return _row_to_stored_object(row)

    def get_revision(self, id: str, revision: int) -> StoredObject:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(objects_table).where(
                        objects_table.c.id == id, objects_table.c.revision == revision
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ObjectNotFoundError(id, revision)
        return _row_to_stored_object(row)

    def list_revisions(self, id: str) -> list[StoredObject]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.select(objects_table)
                    .where(objects_table.c.id == id)
                    .order_by(objects_table.c.revision.asc())
                )
                .mappings()
                .all()
            )
        return [_row_to_stored_object(row) for row in rows]


class PostgresEdgeRepository:
    """``mrr.domain.repositories.EdgeRepository`` against PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_edge(self, edge: TypedEdge) -> TypedEdge:
        if edge.edge_type not in EDGE_VOCABULARY:
            raise UnknownEdgeTypeError(edge.edge_type)

        with self._engine.begin() as conn:
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
        return edge

    def edges_from(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return self._edges_by(edges_table.c.source_id, id, edge_type)

    def edges_to(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        return self._edges_by(edges_table.c.target_id, id, edge_type)

    def _edges_by(
        self, column: sa.ColumnElement[Any], id: str, edge_type: str | None
    ) -> list[TypedEdge]:
        stmt = sa.select(edges_table).where(column == id).order_by(edges_table.c.created_at.asc())
        if edge_type is not None:
            stmt = stmt.where(edges_table.c.edge_type == edge_type)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_typed_edge(row) for row in rows]


def _row_to_domain_event(row: Any) -> DomainEvent:
    return DomainEvent(
        id=row.id,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        actor=row.actor,
        policy_version=row.policy_version,
        causation_id=row.causation_id,
        correlation_id=row.correlation_id,
        object_id=row.object_id,
        object_revision=row.object_revision,
        payload=row.payload,
    )


def _row_to_appended_event(row: Any) -> AppendedEvent:
    return AppendedEvent(
        event=_row_to_domain_event(row),
        sequence=row.sequence,
        content_hash=row.content_hash,
        prev_hash=row.prev_hash,
    )


class PostgresEventLog:
    """``mrr.provenance.log``-shaped append-only domain event log against
    PostgreSQL (task-packets/E1-T06.yaml). See the module docstring for why
    ``append`` takes a ``Connection`` rather than being ``Engine``-based like
    ``PostgresObjectRepository``/``PostgresEdgeRepository``.

    Tamper evidence is a hash chain with a single writer: ``append`` takes
    ``pg_advisory_xact_lock(_ADVISORY_LOCK_KEY)`` first, so two concurrent
    appends (even from different connections/processes) serialize instead of
    racing to read the same "current head" and computing two hashes chained
    onto the same predecessor. The lock is a transaction-scoped advisory
    lock — PostgreSQL releases it automatically at commit or rollback, so it
    never needs an explicit unlock call.
    """

    #: Arbitrary but fixed advisory-lock key naming this log's single-writer
    #: serialization point. Computed once as
    #: ``zlib.crc32(b"mrr.persistence.domain_events")`` and then frozen as a
    #: literal — recomputing it from the source string at import time would
    #: let an accidental future rename of that string silently change the
    #: key and split writers across two different locks. Any fixed 63-bit
    #: integer would do; this one is simply reproducible and documented.
    _ADVISORY_LOCK_KEY = 495_698_251

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, conn: Connection, event: DomainEvent) -> AppendedEvent:
        """Append ``event`` as the new head of the chain, using ``conn`` for
        every statement — including the advisory lock, which is scoped to
        ``conn``'s current transaction. The caller (typically
        ``mrr.persistence.unit_of_work.record_object_revision_with_event``)
        owns that transaction's lifecycle.

        Raises:
            mrr.provenance.exceptions.EventAppendError: if the physical
                insert fails (e.g. ``event.id`` collides with an existing
                row's ``id``).
        """
        # Explicitly cast to bigint: pg_advisory_xact_lock has both a
        # single-arg bigint overload and a two-arg (int, int) overload, and a
        # driver-bound plain Python int can otherwise be sent with an int4
        # OID, leaving the server to guess which overload it must match. The
        # cast removes that ambiguity outright rather than relying on it
        # being resolved favorably.
        conn.execute(
            sa.select(
                sa.func.pg_advisory_xact_lock(sa.cast(self._ADVISORY_LOCK_KEY, sa.BigInteger))
            )
        )

        prev_hash = conn.execute(
            sa.select(domain_events_table.c.content_hash)
            .order_by(domain_events_table.c.sequence.desc())
            .limit(1)
        ).scalar_one_or_none()

        new_hash = compute_event_hash(event, prev_hash)

        try:
            sequence = conn.execute(
                sa.insert(domain_events_table)
                .values(
                    id=event.id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at,
                    actor=event.actor,
                    policy_version=event.policy_version,
                    causation_id=event.causation_id,
                    correlation_id=event.correlation_id,
                    object_id=event.object_id,
                    object_revision=event.object_revision,
                    payload=event.payload,
                    content_hash=new_hash,
                    prev_hash=prev_hash,
                )
                .returning(domain_events_table.c.sequence)
            ).scalar_one()

            conn.execute(
                sa.insert(outbox_table).values(
                    event_id=event.id,
                    status="pending",
                    created_at=datetime.now(UTC),
                    dispatched_at=None,
                    attempts=0,
                )
            )
        except IntegrityError as exc:
            raise EventAppendError(event.id, str(exc)) from None

        return AppendedEvent(
            event=event, sequence=sequence, content_hash=new_hash, prev_hash=prev_hash
        )

    def read_all(self) -> list[AppendedEvent]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    sa.select(domain_events_table).order_by(domain_events_table.c.sequence.asc())
                )
                .mappings()
                .all()
            )
        return [_row_to_appended_event(row) for row in rows]

    def verify_chain(self) -> None:
        """Recompute every event's hash in sequence order and check the
        ``prev_hash`` links; delegates to
        ``mrr.provenance.log.verify_appended_events`` so the same logic is
        exercised, DB-free, by the property-test tier.

        Raises:
            mrr.provenance.exceptions.ChainVerificationError: at the first
                sequence where the chain breaks.
        """
        verify_appended_events(self.read_all())


class InProcessOutboxDispatcher:
    """Reference ``mrr.provenance.log.OutboxDispatcher`` implementation:
    polls pending ``outbox`` rows and calls a handler callable for each.

    At-least-once semantics: a handler that raises leaves its row
    ``"pending"`` with ``attempts`` incremented rather than propagating —
    one broken or transiently failing delivery must not block every other
    pending row in the same batch, and the row remains available for a
    later ``dispatch_pending`` call to retry. A handler that returns
    normally has its row marked ``"dispatched"`` (with ``dispatched_at`` set
    and ``attempts`` incremented) — dispatched rows are never re-created or
    re-selected by a later call, so a handler is invoked at most once per
    *successful* delivery, but the overall log is at-least-once (a crash
    between a successful handler call and the row's own status update would
    leave it "pending" and eligible for a repeat delivery on the next
    ``dispatch_pending`` — dispatch consumers MUST be idempotent, per
    docs/spec/03_API_AND_EVENTS.md section 5.3).
    """

    def __init__(self, engine: Engine, handler: EventHandler) -> None:
        self._engine = engine
        self._handler = handler

    def dispatch_pending(self) -> int:
        dispatched_count = 0
        for event_id in self._pending_event_ids():
            appended = self._load_appended_event(event_id)
            try:
                self._handler(appended)
            except Exception:
                # Intentionally broad: see class docstring — one bad row
                # must not block the rest of the batch. The failure is
                # recorded (not silenced) via the incremented attempts count.
                self._record_failed_attempt(event_id)
                continue
            self._mark_dispatched(event_id)
            dispatched_count += 1
        return dispatched_count

    def _pending_event_ids(self) -> list[str]:
        with self._engine.connect() as conn:
            return list(
                conn.execute(
                    sa.select(outbox_table.c.event_id)
                    .where(outbox_table.c.status == "pending")
                    .order_by(outbox_table.c.created_at.asc())
                ).scalars()
            )

    def _load_appended_event(self, event_id: str) -> AppendedEvent:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    sa.select(domain_events_table).where(domain_events_table.c.id == event_id)
                )
                .mappings()
                .one()
            )
        return _row_to_appended_event(row)

    def _mark_dispatched(self, event_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(outbox_table)
                .where(outbox_table.c.event_id == event_id)
                .values(
                    status="dispatched",
                    dispatched_at=datetime.now(UTC),
                    attempts=outbox_table.c.attempts + 1,
                )
            )

    def _record_failed_attempt(self, event_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.update(outbox_table)
                .where(outbox_table.c.event_id == event_id)
                .values(attempts=outbox_table.c.attempts + 1)
            )
