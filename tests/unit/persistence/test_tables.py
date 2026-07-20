"""Unit tests for mrr.persistence.tables: metadata sanity only, no database
connection (E1-T05, extended by E1-T06 for domain_events/outbox and by
E5-T07 for processed_ids). Table structure against a live PostgreSQL -
including that the CHECK constraint actually rejects invalid rows - is
exercised by the integration tier (tests/integration/persistence/).
"""

from __future__ import annotations

from mrr.domain.replay_retention import PROCESSED_ID_KINDS
from mrr.domain.repositories import EDGE_VOCABULARY
from mrr.persistence.tables import (
    domain_events_table,
    edges_table,
    objects_table,
    outbox_table,
    processed_ids_table,
)
from mrr.provenance.log import OUTBOX_STATUSES
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Identity, UniqueConstraint


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


# ---------------------------------------------------------------------------
# domain_events / outbox (E1-T06).
# ---------------------------------------------------------------------------


def test_domain_events_table_primary_key_is_sequence_only() -> None:
    pk_columns = {column.name for column in domain_events_table.primary_key.columns}
    assert pk_columns == {"sequence"}


def test_domain_events_table_sequence_is_a_generated_identity() -> None:
    sequence_column = domain_events_table.c.sequence
    assert isinstance(sequence_column.identity, Identity)
    assert sequence_column.identity.always is True


def test_domain_events_table_has_expected_columns() -> None:
    expected = {
        "sequence",
        "id",
        "event_type",
        "occurred_at",
        "actor",
        "policy_version",
        "causation_id",
        "correlation_id",
        "object_id",
        "object_revision",
        "payload",
        "content_hash",
        "prev_hash",
    }
    assert {column.name for column in domain_events_table.columns} == expected


def test_domain_events_table_nullable_columns_are_exactly_causation_id_and_prev_hash() -> None:
    nullable = {column.name for column in domain_events_table.columns if column.nullable}
    assert nullable == {"causation_id", "prev_hash"}


def test_domain_events_table_id_is_unique() -> None:
    unique_constraints = [
        c for c in domain_events_table.constraints if isinstance(c, UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert {column.name for column in unique_constraints[0].columns} == {"id"}


def test_domain_events_table_has_object_id_and_correlation_id_indexes() -> None:
    index_columns = {
        tuple(column.name for column in index.columns) for index in domain_events_table.indexes
    }
    assert ("object_id",) in index_columns
    assert ("correlation_id",) in index_columns


def test_outbox_table_primary_key_is_event_id_only() -> None:
    pk_columns = {column.name for column in outbox_table.primary_key.columns}
    assert pk_columns == {"event_id"}


def test_outbox_table_has_expected_columns() -> None:
    expected = {"event_id", "status", "created_at", "dispatched_at", "attempts"}
    assert {column.name for column in outbox_table.columns} == expected


def test_outbox_table_nullable_columns_are_exactly_dispatched_at() -> None:
    nullable = {column.name for column in outbox_table.columns if column.nullable}
    assert nullable == {"dispatched_at"}


def test_outbox_table_event_id_references_domain_events_id() -> None:
    fk_constraints = [c for c in outbox_table.constraints if isinstance(c, ForeignKeyConstraint)]
    assert len(fk_constraints) == 1
    fk = fk_constraints[0]
    assert {column.name for column in fk.columns} == {"event_id"}
    assert [element.target_fullname for element in fk.elements] == ["domain_events.id"]


def test_outbox_table_has_status_check_constraint_over_full_vocabulary() -> None:
    check_constraints = [c for c in outbox_table.constraints if isinstance(c, CheckConstraint)]
    assert len(check_constraints) == 1
    constraint = check_constraints[0]
    assert constraint.name == "ck_outbox_status_vocabulary"

    sqltext = str(constraint.sqltext)
    for status in OUTBOX_STATUSES:
        assert f"'{status}'" in sqltext, f"{status!r} missing from CHECK constraint text"


# ---------------------------------------------------------------------------
# processed_ids (E5-T07).
# ---------------------------------------------------------------------------


def test_processed_ids_table_primary_key_is_recipient_node_id_and_id() -> None:
    pk_columns = {column.name for column in processed_ids_table.primary_key.columns}
    assert pk_columns == {"recipient_node_id", "id"}


def test_processed_ids_table_has_expected_columns() -> None:
    expected = {"id", "id_kind", "recipient_node_id", "processed_at", "expires_at"}
    assert {column.name for column in processed_ids_table.columns} == expected


def test_processed_ids_table_has_no_nullable_columns() -> None:
    # Every column is required — a processed-id row is never partially
    # written (append-then-prune only, no UPDATE path).
    nullable = {column.name for column in processed_ids_table.columns if column.nullable}
    assert nullable == set()


def test_processed_ids_table_has_id_kind_check_constraint_over_full_vocabulary() -> None:
    check_constraints = [
        c for c in processed_ids_table.constraints if isinstance(c, CheckConstraint)
    ]
    assert len(check_constraints) == 1
    constraint = check_constraints[0]
    assert constraint.name == "ck_processed_ids_id_kind_vocabulary"

    sqltext = str(constraint.sqltext)
    for kind in PROCESSED_ID_KINDS:
        assert f"'{kind}'" in sqltext, f"{kind!r} missing from CHECK constraint text"


def test_processed_ids_table_has_expires_at_index() -> None:
    index_columns = {
        tuple(column.name for column in index.columns) for index in processed_ids_table.indexes
    }
    assert ("expires_at",) in index_columns
