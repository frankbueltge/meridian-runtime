"""Unit tests for mrr.persistence.tables: metadata sanity only, no database
connection (E1-T05). Table structure against a live PostgreSQL - including
that the CHECK constraint actually rejects invalid rows - is exercised by
the integration tier (tests/integration/persistence/).
"""

from __future__ import annotations

from mrr.domain.repositories import EDGE_VOCABULARY
from mrr.persistence.tables import edges_table, objects_table
from sqlalchemy import CheckConstraint


def test_objects_table_primary_key_is_id_and_revision() -> None:
    pk_columns = {column.name for column in objects_table.primary_key.columns}
    assert pk_columns == {"id", "revision"}


def test_objects_table_has_expected_columns() -> None:
    expected = {
        "id",
        "revision",
        "api_version",
        "kind",
        "practice_id",
        "created_at",
        "created_by",
        "content_hash",
        "supersedes",
        "labels",
        "body",
    }
    assert {column.name for column in objects_table.columns} == expected


def test_objects_table_nullable_columns_are_exactly_supersedes_and_labels() -> None:
    nullable = {column.name for column in objects_table.columns if column.nullable}
    assert nullable == {"supersedes", "labels"}


def test_objects_table_has_no_check_constraint() -> None:
    # Only edges.edge_type is constrained by vocabulary; objects has none.
    check_constraints = [c for c in objects_table.constraints if isinstance(c, CheckConstraint)]
    assert check_constraints == []


def test_objects_table_has_kind_index() -> None:
    index_columns = {
        tuple(column.name for column in index.columns) for index in objects_table.indexes
    }
    assert ("kind",) in index_columns


def test_edges_table_primary_key_is_id_only() -> None:
    pk_columns = {column.name for column in edges_table.primary_key.columns}
    assert pk_columns == {"id"}


def test_edges_table_has_expected_columns() -> None:
    expected = {
        "id",
        "source_id",
        "target_id",
        "edge_type",
        "created_at",
        "created_by",
        "practice_id",
        "scope",
        "status",
    }
    assert {column.name for column in edges_table.columns} == expected


def test_edges_table_nullable_columns_are_exactly_practice_id_and_scope() -> None:
    nullable = {column.name for column in edges_table.columns if column.nullable}
    assert nullable == {"practice_id", "scope"}


def test_edges_table_has_edge_type_check_constraint_over_full_vocabulary() -> None:
    check_constraints = [c for c in edges_table.constraints if isinstance(c, CheckConstraint)]
    assert len(check_constraints) == 1
    constraint = check_constraints[0]
    assert constraint.name == "ck_edges_edge_type_vocabulary"

    sqltext = str(constraint.sqltext)
    for edge_type in EDGE_VOCABULARY:
        assert f"'{edge_type}'" in sqltext, f"{edge_type!r} missing from CHECK constraint text"


def test_edges_table_has_source_target_and_edge_type_indexes() -> None:
    index_columns = {
        tuple(column.name for column in index.columns) for index in edges_table.indexes
    }
    assert ("source_id",) in index_columns
    assert ("target_id",) in index_columns
    assert ("edge_type",) in index_columns
