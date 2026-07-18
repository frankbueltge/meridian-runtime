"""Framework-free repository interfaces for first-class MRR objects and typed
graph edges, per docs/spec/01_SYSTEM_SPEC.md section 7.6 ("Claim and Evidence
Graph ... Stores typed nodes and edges in PostgreSQL") and
docs/spec/02_DOMAIN_MODEL.md sections 1 (identity, revision, and hashing) and
3 (edge vocabulary).

These Protocols carry no SQLAlchemy, driver, or framework import — mrr.domain
stays framework-independent (MRR-NFR-010; enforced by the import-linter
contract in pyproject.toml, and by an explicit unit test,
tests/unit/domain/test_repositories.py). Concrete implementations live in
mrr.persistence (packages/persistence/mrr/persistence/repositories.py),
which depends on mrr.domain, never the other way around.

Revisions are append-only: ``insert_revision`` is the *only* write operation
either protocol offers, and it always creates a new row — there is no update
or delete of an existing revision anywhere on this interface (AGENTS.md rule
"no silent overwriting of prior object revisions";
docs/spec/02_DOMAIN_MODEL.md section 7 invariant 5, "No silent overwrite of
sealed objects").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

#: The exact edge vocabulary from docs/spec/02_DOMAIN_MODEL.md section 3
#: ("The claim/evidence graph MUST use typed edges. Minimum vocabulary:"),
#: transcribed verbatim. Nineteen types — nothing added, nothing dropped.
#: Both this constant and the database CHECK constraint in
#: packages/persistence/mrr/persistence/tables.py enforce it (fail closed
#: in code and in the database).
EDGE_VOCABULARY: frozenset[str] = frozenset(
    {
        "supports",
        "contradicts",
        "qualifies",
        "contextualizes",
        "derived_from",
        "depends_on",
        "replicates",
        "fails_to_replicate",
        "supersedes",
        "corrects",
        "transferred_from",
        "adapted_from",
        "reviews",
        "verifies",
        "invalidates",
        "uses_source",
        "member_of_source_family",
        "subject_to_obligation",
        "projected_into",
    }
)


@dataclass(frozen=True, slots=True)
class StoredObject:
    """One persisted revision row of a first-class MRR object.

    Carries the eight base fields every first-class object MUST contain
    (docs/spec/02_DOMAIN_MODEL.md section 1: id, api_version, kind,
    practice_id, revision, created_at, created_by, content_hash) plus the
    two optional ones (``supersedes``, ``labels``), and ``body`` — the full
    contract JSON as a plain dict. The base fields are duplicated into
    dedicated columns (see ``mrr.persistence.tables.objects_table``) so they
    stay queryable and indexable without unpacking JSONB; ``body`` is the
    authoritative full payload.
    """

    id: str
    api_version: str
    kind: str
    practice_id: str
    revision: int
    created_at: datetime
    created_by: str
    content_hash: str
    supersedes: str | None
    labels: dict[str, str] | None
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TypedEdge:
    """One typed edge in the claim/evidence graph
    (docs/spec/02_DOMAIN_MODEL.md section 3: "Each edge has identity,
    provenance, creator, timestamp, optional scope, and lifecycle status.").

    Edges are not revisioned like objects — each is an independent,
    append-only row identified by its own urn. ``scope`` is optional per
    section 3; ``status`` (lifecycle status) is always present.
    """

    id: str
    source_id: str
    target_id: str
    edge_type: str
    created_at: datetime
    created_by: str
    scope: dict[str, Any] | None
    status: str
    practice_id: str | None


@runtime_checkable
class ObjectRepository(Protocol):
    """Append-only storage for revisions of first-class MRR objects.

    No update or delete of an existing revision row is offered anywhere on
    this interface — the only write operation is ``insert_revision``, and it
    always creates a new row, never modifies one.
    """

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:
        """Insert ``obj`` as a new revision, enforcing optimistic concurrency.

        ``expected_current_revision`` is the revision number the caller last
        observed for ``obj.id`` (``None`` means "no revision exists yet;
        ``obj`` must be the object's first revision, at revision 1").
        ``obj.revision`` MUST already equal ``expected_current_revision + 1``
        (or ``1`` when ``expected_current_revision`` is ``None``).

        Raises:
            mrr.domain.exceptions.RevisionConflictError: if the object's
                actual current revision does not match
                ``expected_current_revision`` — whether detected before the
                write or by a same-instant concurrent writer. Exactly one
                concurrent writer with the same expectation wins; every
                other loses with this error. There is no boolean-returning
                failure form.
        """
        ...

    def get_latest(self, id: str) -> StoredObject:
        """Return the highest-revision row for ``id``.

        Raises:
            mrr.domain.exceptions.ObjectNotFoundError: if ``id`` does not
                exist. Never returns ``None`` for a missing object.
        """
        ...

    def get_revision(self, id: str, revision: int) -> StoredObject:
        """Return exactly the row ``(id, revision)``.

        Raises:
            mrr.domain.exceptions.ObjectNotFoundError: if that exact
                revision does not exist, whether or not other revisions of
                ``id`` do.
        """
        ...

    def list_revisions(self, id: str) -> list[StoredObject]:
        """Return every revision of ``id``, ordered oldest first.

        Returns an empty list if ``id`` has never been written — a listing
        query, unlike ``get_latest``/``get_revision``, has no single object
        to report as missing.
        """
        ...


@runtime_checkable
class EdgeRepository(Protocol):
    """Append-only storage for typed graph edges. No update or delete is
    offered anywhere on this interface.
    """

    def add_edge(self, edge: TypedEdge) -> TypedEdge:
        """Insert ``edge``.

        Raises:
            mrr.domain.exceptions.UnknownEdgeTypeError: if
                ``edge.edge_type`` is not in ``EDGE_VOCABULARY`` — checked in
                code (fail closed) in addition to the database CHECK
                constraint on the same vocabulary.
        """
        ...

    def edges_from(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        """Return every edge with ``source_id == id``, oldest first,
        optionally filtered to a single ``edge_type``.
        """
        ...

    def edges_to(self, id: str, edge_type: str | None = None) -> list[TypedEdge]:
        """Return every edge with ``target_id == id``, oldest first,
        optionally filtered to a single ``edge_type``.
        """
        ...
