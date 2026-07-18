"""SQLAlchemy-backed implementations of the ``mrr.domain.repositories``
protocols (task-packets/E1-T05.yaml), against a supplied
``sqlalchemy.Engine``.

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

Neither repository offers an update or delete of an existing revision row —
``insert_revision`` and ``add_edge`` are the only writes, and both always
create a new row.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import sqlalchemy as sa
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.persistence.tables import edges_table, objects_table
from sqlalchemy import Engine
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
        new_revision = 1 if expected_current_revision is None else expected_current_revision + 1
        if obj.revision != new_revision:
            raise ValueError(
                f"obj.revision ({obj.revision!r}) does not match the revision implied by "
                f"expected_current_revision ({expected_current_revision!r}): expected "
                f"{new_revision!r}. The caller must set obj.revision to "
                "expected_current_revision + 1 (or 1 when expected_current_revision is None) "
                "before calling insert_revision."
            )

        try:
            with self._engine.begin() as conn:
                raw_max = conn.execute(
                    sa.select(sa.func.max(objects_table.c.revision)).where(
                        objects_table.c.id == obj.id
                    )
                ).scalar_one()
                current_max: int | None = int(raw_max) if raw_max is not None else None
                if current_max != expected_current_revision:
                    raise RevisionConflictError(obj.id, expected_current_revision, current_max)

                self._pause_before_insert()

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
            # and our own INSERT. Re-read the real current revision in a
            # fresh transaction (the failed one above is already rolled
            # back) and report it as the actual value in the conflict.
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
