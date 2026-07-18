"""Unit tests for mrr.domain.repositories (E1-T05): the dataclass and
Protocol surface, the typed error surface in mrr.domain.exceptions, and an
exact cross-check of EDGE_VOCABULARY against docs/spec/02_DOMAIN_MODEL.md
section 3's vocabulary list.

The expected vocabulary below is hardcoded from the specification prose
itself (not derived from mrr.domain.repositories), so this test actually
checks the module against the spec rather than against its own source -
per task-packets/E1-T05.yaml ("hardcode the expected list in the test; the
doc is prose. Keep it a literal cross-check").

Database behavior (inserts, concurrency, edge round-trips against a live
PostgreSQL) is exercised by the integration tier
(tests/integration/persistence/); this module never imports SQLAlchemy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.domain.exceptions import (
    DomainError,
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.repositories import (
    EDGE_VOCABULARY,
    EdgeRepository,
    ObjectRepository,
    StoredObject,
    TypedEdge,
)

#: Verbatim transcription of docs/spec/02_DOMAIN_MODEL.md section 3's
#: "Minimum vocabulary" list, in the document's own order. Independent of
#: mrr.domain.repositories.EDGE_VOCABULARY's own source.
_EXPECTED_EDGE_VOCABULARY = frozenset(
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


def test_edge_vocabulary_matches_spec_section_3_exactly() -> None:
    assert EDGE_VOCABULARY == _EXPECTED_EDGE_VOCABULARY


def test_edge_vocabulary_has_exactly_nineteen_types() -> None:
    assert len(EDGE_VOCABULARY) == 19


# ---------------------------------------------------------------------------
# StoredObject / TypedEdge — frozen dataclass surface.
# ---------------------------------------------------------------------------


def _sample_stored_object(**overrides: Any) -> StoredObject:
    defaults: dict[str, Any] = {
        "id": "urn:mrr:claim:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": "urn:mrr:practice:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": "urn:mrr:agent-role:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "content_hash": "sha256:" + "a" * 64,
        "supersedes": None,
        "labels": None,
        "body": {"kind": "Claim"},
    }
    defaults.update(overrides)
    return StoredObject(**defaults)


def _sample_typed_edge(**overrides: Any) -> TypedEdge:
    defaults: dict[str, Any] = {
        "id": "urn:mrr:edge:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "source_id": "urn:mrr:claim:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "target_id": "urn:mrr:evidence-crate:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "edge_type": "supports",
        "created_at": datetime.now(UTC),
        "created_by": "urn:mrr:agent-role:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "scope": None,
        "status": "active",
        "practice_id": None,
    }
    defaults.update(overrides)
    return TypedEdge(**defaults)


def test_stored_object_carries_all_base_fields_plus_supersedes_labels_body() -> None:
    obj = _sample_stored_object(
        supersedes="urn:mrr:claim:00000000000000000000000000", labels={"a": "b"}
    )
    assert obj.supersedes == "urn:mrr:claim:00000000000000000000000000"
    assert obj.labels == {"a": "b"}
    assert obj.body == {"kind": "Claim"}


def test_stored_object_is_frozen() -> None:
    obj = _sample_stored_object()
    with pytest.raises(AttributeError):
        obj.revision = 2  # type: ignore[misc]


def test_typed_edge_is_frozen() -> None:
    edge = _sample_typed_edge()
    with pytest.raises(AttributeError):
        edge.status = "resolved"  # type: ignore[misc]


def test_typed_edge_scope_and_practice_id_are_independently_optional() -> None:
    edge = _sample_typed_edge(scope={"population": "adults"}, practice_id="urn:mrr:practice:X")
    assert edge.scope == {"population": "adults"}
    assert edge.practice_id == "urn:mrr:practice:X"


# ---------------------------------------------------------------------------
# Protocol surface — runtime_checkable structural conformance.
# ---------------------------------------------------------------------------


class _FakeObjectRepository:
    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:  # pragma: no cover - structural test double
        raise NotImplementedError

    def get_latest(self, id: str) -> StoredObject:  # pragma: no cover
        raise NotImplementedError

    def get_revision(self, id: str, revision: int) -> StoredObject:  # pragma: no cover
        raise NotImplementedError

    def list_revisions(self, id: str) -> list[StoredObject]:  # pragma: no cover
        raise NotImplementedError


class _IncompleteObjectRepository:
    """Missing get_revision/list_revisions - must not satisfy the protocol."""

    def insert_revision(
        self, obj: StoredObject, expected_current_revision: int | None
    ) -> StoredObject:  # pragma: no cover
        raise NotImplementedError

    def get_latest(self, id: str) -> StoredObject:  # pragma: no cover
        raise NotImplementedError


class _FakeEdgeRepository:
    def add_edge(self, edge: TypedEdge) -> TypedEdge:  # pragma: no cover
        raise NotImplementedError

    def edges_from(
        self, id: str, edge_type: str | None = None
    ) -> list[TypedEdge]:  # pragma: no cover
        raise NotImplementedError

    def edges_to(
        self, id: str, edge_type: str | None = None
    ) -> list[TypedEdge]:  # pragma: no cover
        raise NotImplementedError


class _IncompleteEdgeRepository:
    """Missing edges_to - must not satisfy the protocol."""

    def add_edge(self, edge: TypedEdge) -> TypedEdge:  # pragma: no cover
        raise NotImplementedError

    def edges_from(
        self, id: str, edge_type: str | None = None
    ) -> list[TypedEdge]:  # pragma: no cover
        raise NotImplementedError


def test_object_repository_protocol_accepts_a_conforming_implementation() -> None:
    assert isinstance(_FakeObjectRepository(), ObjectRepository)


def test_object_repository_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteObjectRepository(), ObjectRepository)


def test_edge_repository_protocol_accepts_a_conforming_implementation() -> None:
    assert isinstance(_FakeEdgeRepository(), EdgeRepository)


def test_edge_repository_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteEdgeRepository(), EdgeRepository)


# ---------------------------------------------------------------------------
# Typed error surface.
# ---------------------------------------------------------------------------


def test_revision_conflict_error_carries_id_expected_and_actual() -> None:
    err = RevisionConflictError("urn:mrr:claim:X", 1, 2)
    assert isinstance(err, DomainError)
    assert err.id == "urn:mrr:claim:X"
    assert err.expected == 1
    assert err.actual == 2


def test_revision_conflict_error_allows_none_for_expected_and_actual() -> None:
    err = RevisionConflictError("urn:mrr:claim:X", None, None)
    assert err.expected is None
    assert err.actual is None


def test_unknown_edge_type_error_carries_edge_type() -> None:
    err = UnknownEdgeTypeError("not-a-real-type")
    assert isinstance(err, DomainError)
    assert err.edge_type == "not-a-real-type"


def test_object_not_found_error_carries_id_and_optional_revision() -> None:
    err = ObjectNotFoundError("urn:mrr:claim:X")
    assert isinstance(err, DomainError)
    assert err.id == "urn:mrr:claim:X"
    assert err.revision is None

    err_with_revision = ObjectNotFoundError("urn:mrr:claim:X", 3)
    assert err_with_revision.revision == 3
