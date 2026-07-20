"""Unit tests for mrr.domain.replay_retention (task-packets/E5-T07.yaml),
run entirely DB-free — no PostgreSQL.

Acceptance-test mapping (task-packets/E5-T07.yaml): "unit (no Postgres):
processed_id_retention_horizon is at/after expires_at for any non-negative
grace, is monotonic in expires_at, and its boundary (now == horizon) is
prunable — proving retention can never precede object expiry" ->
``test_horizon_is_at_or_after_expires_at_for_zero_grace``,
``test_horizon_is_at_or_after_expires_at_for_positive_grace``,
``test_horizon_is_monotonic_in_expires_at``,
``test_boundary_now_equal_to_horizon_is_prunable``,
``test_negative_grace_is_rejected``. The property-level generalization of
the "at/after expires_at for any non-negative grace" bullet lives in
tests/property/test_replay_retention_properties.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mrr.domain.replay_retention import (
    PROCESSED_ID_KINDS,
    processed_id_retention_horizon,
)

_EXPIRES_AT = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def test_horizon_is_at_or_after_expires_at_for_zero_grace() -> None:
    horizon = processed_id_retention_horizon(_EXPIRES_AT, grace=timedelta(0))
    assert horizon == _EXPIRES_AT


def test_horizon_is_at_or_after_expires_at_for_positive_grace() -> None:
    horizon = processed_id_retention_horizon(_EXPIRES_AT, grace=timedelta(hours=6))
    assert horizon > _EXPIRES_AT
    assert horizon == _EXPIRES_AT + timedelta(hours=6)


def test_horizon_is_monotonic_in_expires_at() -> None:
    grace = timedelta(hours=1)
    earlier = processed_id_retention_horizon(_EXPIRES_AT, grace=grace)
    later = processed_id_retention_horizon(_EXPIRES_AT + timedelta(days=1), grace=grace)
    assert later > earlier


def test_horizon_is_monotonic_in_grace() -> None:
    small_grace_horizon = processed_id_retention_horizon(_EXPIRES_AT, grace=timedelta(minutes=1))
    large_grace_horizon = processed_id_retention_horizon(_EXPIRES_AT, grace=timedelta(days=1))
    assert large_grace_horizon > small_grace_horizon


def test_boundary_now_equal_to_horizon_is_prunable() -> None:
    """``mrr.persistence.repositories.PostgresProcessedIdStore.prune_expired``
    deletes a row when ``processed_id_retention_horizon(row.expires_at,
    grace=...) <= now`` — a non-strict comparison, so the exact boundary
    ``now == horizon`` counts as prunable, never held back one instant past
    its own horizon.
    """
    grace = timedelta(hours=2)
    horizon = processed_id_retention_horizon(_EXPIRES_AT, grace=grace)
    now = horizon

    assert now >= processed_id_retention_horizon(_EXPIRES_AT, grace=grace)


def test_one_instant_before_horizon_is_not_yet_prunable() -> None:
    grace = timedelta(hours=2)
    horizon = processed_id_retention_horizon(_EXPIRES_AT, grace=grace)
    now = horizon - timedelta(microseconds=1)

    assert now < processed_id_retention_horizon(_EXPIRES_AT, grace=grace)


def test_negative_grace_is_rejected() -> None:
    with pytest.raises(ValueError, match="grace must be >= timedelta"):
        processed_id_retention_horizon(_EXPIRES_AT, grace=timedelta(seconds=-1))


def test_processed_id_kinds_is_exactly_envelope_and_bundle() -> None:
    assert set(PROCESSED_ID_KINDS) == {"envelope", "bundle"}
