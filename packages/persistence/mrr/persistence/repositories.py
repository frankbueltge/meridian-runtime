"""SQLAlchemy-backed implementations of the ``mrr.domain.repositories``
protocols (task-packets/E1-T05.yaml), against a supplied
``sqlalchemy.Engine``, plus the ``mrr.provenance.log`` protocols
(task-packets/E1-T06.yaml) — ``PostgresEventLog`` (the append-only,
tamper-evident domain event log) and ``InProcessOutboxDispatcher`` (the
at-least-once reference outbox dispatcher) — ``PostgresProcessedIdStore``
(task-packets/E5-T07.yaml), the durable replay/idempotency store backing
``mrr.domain.envelope_validation.AlreadyProcessed`` and ``mrr.domain.
offline_bundle.BundleAlreadyProcessed`` (see that class's own docstring), and
``PostgresKeyRevocationStore`` (task-packets/E5-T07b.yaml), the durable,
append-only key-revocation-fact store backing ``mrr.domain.trust_revocation.
trust_revoked_after_creation``. See that class's own docstring.

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
``insert_revision``/``add_edge`` do — this is not an ad hoc divergence from
``mrr.provenance.log.EventLog``, it is exactly what that protocol's generic
``append(self, tx: TTx, event: DomainEvent)`` shape requires;
``PostgresEventLog`` is an ``EventLog[Connection]``. The whole point of
E1-T06 is that an object write, its event, and its outbox row commit or roll
back together as ONE transaction (task-packets/E1-T06.yaml invariant: "state
change, event append, and outbox row are one transaction"). An ``Engine``-
based convenience that opened its own transaction would make it easy to call
``append`` outside that shared transaction by accident, silently
reintroducing the very divergence this task exists to close — so it is not
offered, and the protocol itself is shaped so that it cannot be. Composing
an event append with an object write is
``mrr.persistence.unit_of_work.record_object_revision_with_event``.
``read_all``/``verify_chain`` need no such coupling (reads outside a write
transaction are safe) and stay ``Engine``-based like the E1-T05 repositories.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.replay_retention import ProcessedIdKind, processed_id_retention_horizon
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.domain.trust_revocation import RevocationRecord
from mrr.persistence.tables import (
    domain_events_table,
    edges_table,
    key_revocations_table,
    objects_table,
    outbox_table,
    processed_ids_table,
)
from mrr.provenance.events import DomainEvent, compute_event_hash
from mrr.provenance.exceptions import EventAppendError
from mrr.provenance.log import AppendedEvent, EventHandler, verify_appended_events
from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    """``mrr.provenance.log.EventLog[Connection]`` against PostgreSQL
    (task-packets/E1-T06.yaml). See the module docstring for why ``append``
    takes a ``Connection`` rather than being ``Engine``-based like
    ``PostgresObjectRepository``/``PostgresEdgeRepository`` — it is the
    generic protocol's own shape, not a local deviation from it.

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


class PostgresProcessedIdStore:
    """Durable replay/idempotency store (task-packets/E5-T07.yaml) against
    ``mrr.persistence.tables.processed_ids_table`` — the persistence layer
    behind ``mrr.domain.envelope_validation.AlreadyProcessed`` and
    ``mrr.domain.offline_bundle.BundleAlreadyProcessed``, both of which are
    reused completely unchanged: this class only fills the predicate seam
    those two modules were built with, it does not touch either.

    Keyed by ``(recipient_node_id, id)``, not ``id`` alone — see
    ``processed_ids_table``'s own docstring. Every method that needs a
    recipient node takes it explicitly rather than binding one at
    construction: ``already_processed`` returns a predicate CURRIED over a
    specific ``recipient_node_id`` (so the returned closure has the exact
    ``Callable[[str], bool]`` shape both validators want, with no wrapping
    needed at the call site), while ``record_processed``/
    ``record_processed_with_connection`` take ``recipient_node_id``
    explicitly per call — a single store instance (bound only to an
    ``Engine``, exactly like ``PostgresObjectRepository``/
    ``PostgresEdgeRepository``/``PostgresEventLog`` above) can therefore
    serve as many recipient nodes as a process needs, which is also what
    lets ``tests/integration/persistence/test_processed_id_store.py``
    demonstrate that two different recipient nodes recording the same id do
    not shadow each other without constructing two separate store objects.

    ``grace`` (task-packets/E5-T07.yaml: "the concrete retention grace as a
    policy value" is left to the caller/deployment, not decided here) is
    bound once at construction and applied by every ``prune_expired`` call
    via ``mrr.domain.replay_retention.processed_id_retention_horizon`` — see
    that function's own docstring for why it must be non-negative. Defaults
    to ``timedelta(0)`` (no extra margin beyond the recorded object's own
    ``expires_at``), an honest "no grace configured" starting point rather
    than an invented policy number.
    """

    def __init__(self, engine: Engine, *, grace: timedelta = timedelta(0)) -> None:
        if grace < timedelta(0):
            raise ValueError(f"grace must be >= timedelta(0), got {grace!r}")
        self._engine = engine
        self._grace = grace

    def already_processed(self, recipient_node_id: str) -> Callable[[str], bool]:
        """Return a predicate bound to ``recipient_node_id`` — the exact
        ``Callable[[str], bool]`` shape of both ``mrr.domain.
        envelope_validation.AlreadyProcessed`` and ``mrr.domain.
        offline_bundle.BundleAlreadyProcessed`` — usable VERBATIM as the
        ``already_processed=`` argument of ``validate_inbound_envelope``
        (over ``message_id``) or ``validate_inbound_bundle`` (over
        ``bundle_id``); no adaptation needed at either call site:

            validate_inbound_envelope(
                envelope, ..., already_processed=store.already_processed(this_node_id),
            )

        Each call of the returned predicate opens its own short-lived read
        connection — the predicate is meant to be handed straight to a
        validator, not held open across a caller's own write transaction.
        """

        def _predicate(id: str) -> bool:
            with self._engine.connect() as conn:
                return self._is_processed(conn, recipient_node_id, id)

        return _predicate

    def _is_processed(self, conn: Connection, recipient_node_id: str, id: str) -> bool:
        row = conn.execute(
            sa.select(processed_ids_table.c.id).where(
                processed_ids_table.c.recipient_node_id == recipient_node_id,
                processed_ids_table.c.id == id,
            )
        ).first()
        return row is not None

    def record_processed(
        self,
        id: str,
        *,
        id_kind: ProcessedIdKind,
        recipient_node_id: str,
        expires_at: datetime,
        at: datetime,
    ) -> bool:
        """Idempotently record ``id`` as processed for ``recipient_node_id``,
        opening its own transaction.

        ``INSERT ... ON CONFLICT (recipient_node_id, id) DO NOTHING`` is the
        entire idempotency mechanism: recording the SAME ``(recipient_node_id,
        id)`` twice is a no-op at the database itself — never an
        application-level "check then insert" that could race — leaving
        exactly one row and never raising.

        Args:
            id: the envelope's ``message_id`` or the bundle's ``bundle_id``.
            id_kind: which id namespace ``id`` belongs to.
            recipient_node_id: the receiving node this id was processed for.
            expires_at: the processed object's own ``expires_at``, stored so
                ``prune_expired`` can later evaluate this row's retention
                horizon without re-fetching the original object.
            at: the instant this id was recorded (stored as
                ``processed_at``) — caller-supplied, exactly like
                ``mrr.provenance.events.DomainEvent.occurred_at``, never
                generated internally.

        Returns:
            ``True`` if this call newly inserted the row, ``False`` if
            ``(recipient_node_id, id)`` already existed (the idempotent
            no-op case).
        """
        with self._engine.begin() as conn:
            return self._record_processed_core(
                conn,
                id,
                id_kind=id_kind,
                recipient_node_id=recipient_node_id,
                expires_at=expires_at,
                at=at,
            )

    def record_processed_with_connection(
        self,
        conn: Connection,
        id: str,
        *,
        id_kind: ProcessedIdKind,
        recipient_node_id: str,
        expires_at: datetime,
        at: datetime,
    ) -> bool:
        """Same as ``record_processed``, but writes through a caller-supplied
        ``Connection`` instead of opening its own transaction — the
        connection-accepting variant so recording a processed id can share
        ONE transaction with whatever else a caller's unit of work does
        (mirrors ``PostgresObjectRepository.insert_revision_with_connection``'s
        identical split from ``insert_revision``; a full validate-then-record
        intake transaction composing this with an accept decision is
        E2E-002, not this task). The caller owns the transaction's
        lifecycle: a rollback after this call leaves no row, whether or not
        this call itself newly inserted one.
        """
        return self._record_processed_core(
            conn,
            id,
            id_kind=id_kind,
            recipient_node_id=recipient_node_id,
            expires_at=expires_at,
            at=at,
        )

    def _record_processed_core(
        self,
        conn: Connection,
        id: str,
        *,
        id_kind: ProcessedIdKind,
        recipient_node_id: str,
        expires_at: datetime,
        at: datetime,
    ) -> bool:
        """``RETURNING`` — not ``CursorResult.rowcount`` — carries the
        "newly inserted?" answer: SQLAlchemy memoizes ``rowcount`` only for
        UPDATE/DELETE (the ORM versioning paths), and for a plain INSERT the
        psycopg 3 cursor is already closed by the time ``rowcount`` is read,
        which yields ``-1`` — i.e. ``rowcount > 0`` is ``False`` even for a
        genuinely new row. ``ON CONFLICT DO NOTHING RETURNING`` instead
        reports the fact directly from the server: exactly one row comes
        back iff this statement inserted, no row iff the key already
        existed. (``prune_expired`` below may keep using ``rowcount``
        because DELETE is one of the memoized statement kinds.)
        """
        stmt = (
            pg_insert(processed_ids_table)
            .values(
                id=id,
                id_kind=id_kind,
                recipient_node_id=recipient_node_id,
                processed_at=at,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["recipient_node_id", "id"])
            .returning(processed_ids_table.c.id)
        )
        return conn.execute(stmt).first() is not None

    def prune_expired(self, now: datetime) -> int:
        """Delete every row whose retention horizon has passed at ``now``,
        and return the count deleted.

        The horizon rule itself (``mrr.domain.replay_retention.
        processed_id_retention_horizon``) is called per candidate row below
        rather than reimplemented as an independent SQL expression, so
        there is only ever one place a future change to the rule needs to
        happen. This is a two-phase delete, not a single blind ``DELETE ...
        WHERE expires_at <= now``:

        1. Select every row that is at least a CANDIDATE — ``expires_at <=
           now``. Since ``grace >= 0`` makes ``horizon = expires_at + grace
           >= expires_at`` always, a row with ``expires_at > now`` can never
           be prunable yet regardless of ``grace``, so this prefilter never
           excludes a genuinely prunable row while typically excluding most
           still-valid ones from the more precise check below.
        2. Keep only the candidates whose actual horizon
           (``processed_id_retention_horizon(row.expires_at, grace=self.
           _grace)``) is at or before ``now``, and delete exactly those, in
           one statement.

        A row whose object is still within its validity window is never
        touched by this method — pruning cannot reopen a replay window.
        """
        with self._engine.begin() as conn:
            candidates = (
                conn.execute(
                    sa.select(
                        processed_ids_table.c.recipient_node_id,
                        processed_ids_table.c.id,
                        processed_ids_table.c.expires_at,
                    ).where(processed_ids_table.c.expires_at <= now)
                )
                .mappings()
                .all()
            )

            prunable_keys = [
                (row["recipient_node_id"], row["id"])
                for row in candidates
                if processed_id_retention_horizon(row["expires_at"], grace=self._grace) <= now
            ]
            if not prunable_keys:
                return 0

            result = conn.execute(
                sa.delete(processed_ids_table).where(
                    sa.tuple_(
                        processed_ids_table.c.recipient_node_id, processed_ids_table.c.id
                    ).in_(prunable_keys)
                )
            )
            return result.rowcount


class PostgresKeyRevocationStore:
    """Durable, append-only key-revocation-fact store (task-packets/
    E5-T07b.yaml) against ``mrr.persistence.tables.key_revocations_table`` —
    the persistence layer a durably recorded revocation fact for
    ``mrr.domain.trust_revocation.trust_revoked_after_creation`` is read
    from.

    Keyed by ``kid`` ALONE — see ``key_revocations_table``'s own docstring
    for why a kid needs no additional scoping the way ``processed_ids_table``
    needs ``recipient_node_id``.

    NO update and NO delete method exists anywhere on this class: "a
    revocation, once recorded, can never be un-recorded by any public
    interface" is enforced by the absence of any such method, not by a
    runtime check. This is a deliberately more rigid guarantee than
    ``mrr.domain.key_management.revoke``'s in-memory, freely-repeatable
    ``replace(descriptor, state="revoked")`` — appropriate because THIS
    store is the durable, single source of truth for "when", where the
    in-memory ring is a transient, caller-assembled view. This class is also
    deliberately NOT wired to feed a ``mrr.domain.key_management.KeyRing``:
    doing so would re-invite exactly the at-instant re-verification of an
    already-accepted object that docs/spec/04_SECURITY_AND_POLICY.md section
    8.4's "existing objects remain historically attributable" forbids — see
    ``mrr.domain.trust_revocation``'s own module docstring.

    This store performs NO authorization check that ``practice_id`` actually
    owns ``kid`` — that check needs a persisted practice/key registry that
    does not exist anywhere in this codebase yet (task-packets/E5-T02.yaml's
    own forbidden_changes: "a NEW persisted practice registry ... is a later
    concern"), so it is the CALLER's responsibility, exactly like
    ``PostgresProcessedIdStore.record_processed`` leaving the decision of
    what to record to its caller.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_revocation(
        self,
        kid: str,
        *,
        practice_id: str,
        revoked_at: datetime,
        reason: str | None = None,
    ) -> bool:
        """Idempotently record ``kid`` as revoked, opening its own
        transaction.

        ``INSERT ... ON CONFLICT (kid) DO NOTHING`` is the entire
        idempotency AND immutability mechanism: recording the SAME ``kid``
        twice — even with a different ``practice_id``/``revoked_at``/
        ``reason`` — is a no-op at the database itself, never an
        application-level "check then insert" that could race, and it
        PRESERVES the first-ever recorded fact: the original row is never
        touched.

        Args:
            kid: the revoked Ed25519 public key's id
                (``mrr.crypto.keys.derive_key_id``).
            practice_id: the id of the practice revoking ``kid``.
            revoked_at: the instant the revocation takes effect —
                caller-supplied, exactly like ``mrr.provenance.events.
                DomainEvent.occurred_at`` and ``PostgresProcessedIdStore.
                record_processed``'s own ``at``, never generated internally.
            reason: an optional free-text reason.

        Returns:
            ``True`` if this call newly inserted the row, ``False`` if
            ``kid`` already existed (the idempotent no-op case) — in which
            case the row's original ``practice_id``/``revoked_at``/
            ``reason`` are left completely unchanged.
        """
        with self._engine.begin() as conn:
            return self._record_revocation_core(
                conn, kid, practice_id=practice_id, revoked_at=revoked_at, reason=reason
            )

    def record_revocation_with_connection(
        self,
        conn: Connection,
        kid: str,
        *,
        practice_id: str,
        revoked_at: datetime,
        reason: str | None = None,
    ) -> bool:
        """Same as ``record_revocation``, but writes through a
        caller-supplied ``Connection`` instead of opening its own
        transaction — the connection-accepting variant so recording a
        revocation can share ONE transaction with whatever else a caller's
        unit of work does (mirrors ``PostgresProcessedIdStore.
        record_processed_with_connection``'s identical split from
        ``record_processed``). The caller owns the transaction's lifecycle:
        a rollback after this call leaves no row, whether or not this call
        itself newly inserted one.
        """
        return self._record_revocation_core(
            conn, kid, practice_id=practice_id, revoked_at=revoked_at, reason=reason
        )

    def _record_revocation_core(
        self,
        conn: Connection,
        kid: str,
        *,
        practice_id: str,
        revoked_at: datetime,
        reason: str | None,
    ) -> bool:
        """``RETURNING`` — not ``CursorResult.rowcount`` — carries the
        "newly inserted?" answer. See ``PostgresProcessedIdStore.
        _record_processed_core``'s own docstring for why: SQLAlchemy
        memoizes ``rowcount`` only for UPDATE/DELETE, and for a plain INSERT
        the psycopg 3 cursor is already closed by the time ``rowcount`` is
        read, which yields ``-1`` — i.e. ``rowcount > 0`` is ``False`` even
        for a genuinely new row. ``ON CONFLICT DO NOTHING RETURNING``
        instead reports the fact directly from the server: exactly one row
        comes back iff this statement inserted, no row iff ``kid`` already
        existed.
        """
        stmt = (
            pg_insert(key_revocations_table)
            .values(
                kid=kid,
                practice_id=practice_id,
                revoked_at=revoked_at,
                reason=reason,
            )
            .on_conflict_do_nothing(index_elements=["kid"])
            .returning(key_revocations_table.c.kid)
        )
        return conn.execute(stmt).first() is not None

    def get_revocation(self, kid: str) -> RevocationRecord | None:
        """Return the ``RevocationRecord`` for ``kid`` if and only if
        ``record_revocation``/``record_revocation_with_connection`` has ever
        been called for it; ``None`` if ``kid`` has never been recorded —
        there is no other source of "is this kid revoked" in this table.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                sa.select(key_revocations_table).where(key_revocations_table.c.kid == kid)
            ).first()
        if row is None:
            return None
        return RevocationRecord(
            kid=row.kid,
            practice_id=row.practice_id,
            revoked_at=row.revoked_at,
            reason=row.reason,
        )
