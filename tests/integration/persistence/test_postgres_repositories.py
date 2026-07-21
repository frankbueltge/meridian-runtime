"""Integration tests for mrr.persistence against a real PostgreSQL
(task-packets/E1-T05.yaml acceptance_tests), run via the `postgres_engine`
fixture in tests/integration/conftest.py. Skips visibly if
MRR_TEST_DATABASE_URL is unset (fails hard instead if CI=true) — see that
module's docstring.

Acceptance-test mapping:

- "alembic upgrade head succeeds on an empty database" ->
  ``test_alembic_upgrade_head_creates_both_tables`` (every other test in
  this module also exercises this, via the fixture, on every run).
- "insert revision 1 then revision 2 ... both readable, latest resolution
  correct" -> ``test_insert_then_update_revision_round_trips``.
- "conflicting concurrent insert with the same expected revision loses with
  a typed error" -> ``test_wrong_expected_revision_raises_conflict`` and
  ``test_true_concurrent_insert_exactly_one_wins``.
- "direct duplicate (id, revision) insert fails with the same typed error"
  -> ``test_duplicate_id_and_revision_insert_raises_conflict``.
- "every section-3 edge type round-trips; an invented edge type is
  rejected" -> ``test_every_vocabulary_edge_type_round_trips``,
  ``test_invented_edge_type_rejected_in_code`` and
  ``test_invented_edge_type_rejected_by_database_check_constraint``.
- [K1-T02, migration/EDGE_VOCABULARY] "the migration's downgrade() reverts
  the CHECK constraint to reject one of the four new types ... proving
  genuine reversibility" ->
  ``test_migration_downgrade_reverts_check_constraint_to_reject_a_new_edge_type``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from mrr.domain.exceptions import (
    ObjectNotFoundError,
    RevisionConflictError,
    UnknownEdgeTypeError,
)
from mrr.domain.identity import new_urn
from mrr.domain.repositories import EDGE_VOCABULARY, StoredObject, TypedEdge
from mrr.persistence.repositories import PostgresEdgeRepository, PostgresObjectRepository
from mrr.persistence.tables import edges_table
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError

#: Local, duplicated copies of tests/integration/conftest.py's own path
#: constants — a conftest module is not meant to be imported by test files
#: (there is no tests/ package __init__.py anywhere in this repository), so
#: this one small self-contained duplication mirrors this codebase's own
#: "a local copy, not a shared import" precedent for small test-support
#: constants (see e.g. mrr.services.claim.service's own
#: RecordRevisionWithEvent docstring for the identical rationale).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_object(*, id: str, revision: int, **overrides: object) -> StoredObject:
    defaults: dict[str, object] = {
        "id": id,
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "revision": revision,
        "created_at": _now(),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "supersedes": None,
        "labels": None,
        "body": {"revision": revision},
    }
    defaults.update(overrides)
    return StoredObject(**defaults)  # type: ignore[arg-type]


def _typed_edge(
    *, edge_type: str, source_id: str, target_id: str, **overrides: object
) -> TypedEdge:
    defaults: dict[str, object] = {
        "id": new_urn("edge"),
        "source_id": source_id,
        "target_id": target_id,
        "edge_type": edge_type,
        "created_at": _now(),
        "created_by": new_urn("agent-role"),
        "scope": None,
        "status": "active",
        "practice_id": None,
    }
    defaults.update(overrides)
    return TypedEdge(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# alembic upgrade head
# ---------------------------------------------------------------------------


def test_alembic_upgrade_head_creates_both_tables(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    table_names = set(inspector.get_table_names())
    assert {"objects", "edges"}.issubset(table_names)


# ---------------------------------------------------------------------------
# Objects: insert, read, revisions, concurrency.
# ---------------------------------------------------------------------------


def test_insert_then_update_revision_round_trips(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    object_id = new_urn("claim")

    rev1 = _stored_object(id=object_id, revision=1, body={"status": "draft"})
    repo.insert_revision(rev1, expected_current_revision=None)

    rev2 = _stored_object(id=object_id, revision=2, body={"status": "under_review"})
    repo.insert_revision(rev2, expected_current_revision=1)

    assert repo.get_revision(object_id, 1).body == {"status": "draft"}
    assert repo.get_revision(object_id, 2).body == {"status": "under_review"}

    latest = repo.get_latest(object_id)
    assert latest.revision == 2
    assert latest.body == {"status": "under_review"}

    all_revisions = repo.list_revisions(object_id)
    assert [rev.revision for rev in all_revisions] == [1, 2]


def test_get_latest_raises_not_found_for_unknown_id(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    with pytest.raises(ObjectNotFoundError):
        repo.get_latest(new_urn("claim"))


def test_get_revision_raises_not_found_for_unknown_revision(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    object_id = new_urn("claim")
    repo.insert_revision(_stored_object(id=object_id, revision=1), expected_current_revision=None)

    with pytest.raises(ObjectNotFoundError):
        repo.get_revision(object_id, 2)


def test_list_revisions_is_empty_for_unknown_id(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    assert repo.list_revisions(new_urn("claim")) == []


def test_wrong_expected_revision_raises_conflict(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    object_id = new_urn("claim")
    repo.insert_revision(_stored_object(id=object_id, revision=1), expected_current_revision=None)

    # Caller believes the current revision is 1, but tries to write as if it
    # were still unwritten (expected=None) — actual is 1.
    with pytest.raises(RevisionConflictError) as excinfo:
        repo.insert_revision(
            _stored_object(id=object_id, revision=1, body={"attempt": "stale"}),
            expected_current_revision=None,
        )
    assert excinfo.value.id == object_id
    assert excinfo.value.expected is None
    assert excinfo.value.actual == 1


def test_duplicate_id_and_revision_insert_raises_conflict(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    object_id = new_urn("claim")
    obj = _stored_object(id=object_id, revision=1)

    repo.insert_revision(obj, expected_current_revision=None)

    # Same (id, revision) again, same expected value — the belt check
    # itself catches this (current actual is now 1, not None).
    with pytest.raises(RevisionConflictError) as excinfo:
        repo.insert_revision(obj, expected_current_revision=None)
    assert excinfo.value.actual == 1


def test_insert_revision_rejects_mismatched_obj_revision(postgres_engine: Engine) -> None:
    repo = PostgresObjectRepository(postgres_engine)
    object_id = new_urn("claim")
    # obj.revision=5 does not match what expected_current_revision=None implies (1).
    with pytest.raises(ValueError):
        repo.insert_revision(
            _stored_object(id=object_id, revision=5), expected_current_revision=None
        )


def test_true_concurrent_insert_exactly_one_wins(postgres_engine: Engine) -> None:
    """Two writers, two separate connections, both pass the belt check for
    the same expected_current_revision before either reaches the physical
    INSERT — forced deterministically via a threading.Barrier and the
    repository's test-only `_pause_before_insert` seam. Exactly one must
    succeed; the other must lose with RevisionConflictError.
    """
    object_id = new_urn("claim")
    barrier = threading.Barrier(2, timeout=10)

    def _wait_at_barrier() -> None:
        barrier.wait()

    repo_a = PostgresObjectRepository(postgres_engine, _pause_before_insert=_wait_at_barrier)
    repo_b = PostgresObjectRepository(postgres_engine, _pause_before_insert=_wait_at_barrier)

    obj_a = _stored_object(id=object_id, revision=1, body={"writer": "a"})
    obj_b = _stored_object(id=object_id, revision=1, body={"writer": "b"})

    def _write(repo: PostgresObjectRepository, obj: StoredObject) -> StoredObject | Exception:
        try:
            return repo.insert_revision(obj, expected_current_revision=None)
        except RevisionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_write, repo_a, obj_a)
        future_b = pool.submit(_write, repo_b, obj_b)
        result_a = future_a.result(timeout=15)
        result_b = future_b.result(timeout=15)

    results = [result_a, result_b]
    successes = [r for r in results if isinstance(r, StoredObject)]
    conflicts = [r for r in results if isinstance(r, RevisionConflictError)]

    assert len(successes) == 1, f"expected exactly one winner, got: {results!r}"
    assert len(conflicts) == 1, f"expected exactly one typed conflict loser, got: {results!r}"
    assert conflicts[0].expected is None
    assert conflicts[0].actual == 1

    plain_repo = PostgresObjectRepository(postgres_engine)
    assert [rev.revision for rev in plain_repo.list_revisions(object_id)] == [1]


# ---------------------------------------------------------------------------
# Edges: vocabulary round-trip, unknown type rejection, filters.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("edge_type", sorted(EDGE_VOCABULARY))
def test_every_vocabulary_edge_type_round_trips(postgres_engine: Engine, edge_type: str) -> None:
    repo = PostgresEdgeRepository(postgres_engine)
    source_id = new_urn("claim")
    target_id = new_urn("evidence-crate")

    edge = _typed_edge(edge_type=edge_type, source_id=source_id, target_id=target_id)
    repo.add_edge(edge)

    from_results = repo.edges_from(source_id)
    assert any(e.id == edge.id and e.edge_type == edge_type for e in from_results)

    to_results = repo.edges_to(target_id)
    assert any(e.id == edge.id and e.edge_type == edge_type for e in to_results)


def test_invented_edge_type_rejected_in_code(postgres_engine: Engine) -> None:
    repo = PostgresEdgeRepository(postgres_engine)
    edge = _typed_edge(
        edge_type="not-a-real-edge-type", source_id=new_urn("claim"), target_id=new_urn("claim")
    )
    with pytest.raises(UnknownEdgeTypeError) as excinfo:
        repo.add_edge(edge)
    assert excinfo.value.edge_type == "not-a-real-edge-type"


def test_invented_edge_type_rejected_by_database_check_constraint(postgres_engine: Engine) -> None:
    # Bypass mrr.persistence.repositories entirely and insert raw SQL
    # directly against the table, to prove the DB CHECK constraint is a
    # real, independent enforcement layer and not just decoration.
    with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
        conn.execute(
            sa.insert(edges_table).values(
                id=new_urn("edge"),
                source_id=new_urn("claim"),
                target_id=new_urn("claim"),
                edge_type="not-a-real-edge-type",
                created_at=_now(),
                created_by=new_urn("agent-role"),
                practice_id=None,
                scope=None,
                status="active",
            )
        )


def test_migration_downgrade_reverts_check_constraint_to_reject_a_new_edge_type(
    postgres_engine: Engine,
) -> None:
    """[K1-T02, migration/EDGE_VOCABULARY] Confirms the new edges-vocabulary
    migration's ``downgrade()`` genuinely narrows
    ``ck_edges_edge_type_vocabulary`` back to the original nineteen-value
    list — proving reversibility, not merely additivity. ``postgres_engine``
    is already migrated to head (including this new migration) by the
    fixture; downgrading by exactly one revision here reverts only THIS
    migration, back to ``b64f87a758f3``.
    """
    database_url = postgres_engine.url.render_as_string(hide_password=False)
    alembic_cfg = Config(str(_ALEMBIC_INI))
    alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    alembic_cfg.attributes["sqlalchemy_url"] = database_url
    command.downgrade(alembic_cfg, "-1")

    with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
        conn.execute(
            sa.insert(edges_table).values(
                id=new_urn("edge"),
                source_id=new_urn("claim"),
                target_id=new_urn("method-ruling"),
                edge_type="ruled_by",
                created_at=_now(),
                created_by=new_urn("agent-role"),
                practice_id=None,
                scope=None,
                status="active",
            )
        )


def test_edges_from_edges_to_and_type_filter(postgres_engine: Engine) -> None:
    repo = PostgresEdgeRepository(postgres_engine)
    hub = new_urn("claim")
    a = new_urn("evidence-crate")
    b = new_urn("evidence-crate")

    supports_edge = _typed_edge(edge_type="supports", source_id=hub, target_id=a)
    contradicts_edge = _typed_edge(edge_type="contradicts", source_id=hub, target_id=b)
    incoming_edge = _typed_edge(edge_type="derived_from", source_id=a, target_id=hub)

    repo.add_edge(supports_edge)
    repo.add_edge(contradicts_edge)
    repo.add_edge(incoming_edge)

    outgoing = repo.edges_from(hub)
    assert {e.id for e in outgoing} == {supports_edge.id, contradicts_edge.id}

    only_supports = repo.edges_from(hub, edge_type="supports")
    assert [e.id for e in only_supports] == [supports_edge.id]

    incoming = repo.edges_to(hub)
    assert [e.id for e in incoming] == [incoming_edge.id]

    none_of_this_type = repo.edges_from(hub, edge_type="verifies")
    assert none_of_this_type == []


def test_objects_table_leaves_edges_untouched_and_vice_versa(postgres_engine: Engine) -> None:
    # Sanity check that the two tables are genuinely independent (no shared
    # identifiers colliding), matching the "one revisioned objects table
    # plus one typed edges table" design.
    object_repo = PostgresObjectRepository(postgres_engine)
    edge_repo = PostgresEdgeRepository(postgres_engine)

    object_id = new_urn("claim")
    object_repo.insert_revision(
        _stored_object(id=object_id, revision=1), expected_current_revision=None
    )

    edge = _typed_edge(edge_type="supports", source_id=object_id, target_id=new_urn("claim"))
    edge_repo.add_edge(edge)

    assert object_repo.get_latest(object_id).id == object_id
    assert edge_repo.edges_from(object_id)[0].id == edge.id
