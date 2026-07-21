"""Property tests for mrr.domain.replay_retention (task-packets/E5-T07.yaml).

Acceptance-test mapping: "property (no Postgres): for arbitrary expires_at
and grace >= 0, processed_id_retention_horizon(expires_at, grace) >=
expires_at (retention never reopens a replay window)" ->
``test_horizon_is_always_at_or_after_expires_at``. Two further properties
named in the task's own unit-test bullet ("monotonic in expires_at" and the
boundary rule) are generalized here too, arbitrary-input style:
``test_horizon_is_monotonic_in_expires_at``,
``test_horizon_equals_expires_at_plus_grace_exactly``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.replay_retention import processed_id_retention_horizon

#: A wide but finite range so arithmetic on the drawn datetimes/timedeltas
#: never risks overflowing datetime.max.
_expires_at_strategy = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)

#: Non-negative timedeltas only — this function's own documented and
#: enforced precondition (a negative grace is rejected, see
#: tests/unit/domain/test_replay_retention.py's
#: test_negative_grace_is_rejected).
_grace_strategy = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=3650))


@given(expires_at=_expires_at_strategy, grace=_grace_strategy)
def test_horizon_is_always_at_or_after_expires_at(expires_at: datetime, grace: timedelta) -> None:
    horizon = processed_id_retention_horizon(expires_at, grace=grace)
    assert horizon >= expires_at


@given(expires_at=_expires_at_strategy, grace=_grace_strategy)
def test_horizon_equals_expires_at_plus_grace_exactly(
    expires_at: datetime, grace: timedelta
) -> None:
    horizon = processed_id_retention_horizon(expires_at, grace=grace)
    assert horizon == expires_at + grace


@given(expires_at=_expires_at_strategy, delta=_grace_strategy, grace=_grace_strategy)
def test_horizon_is_monotonic_in_expires_at(
    expires_at: datetime, delta: timedelta, grace: timedelta
) -> None:
    earlier_horizon = processed_id_retention_horizon(expires_at, grace=grace)
    later_horizon = processed_id_retention_horizon(expires_at + delta, grace=grace)
    assert later_horizon >= earlier_horizon


@given(expires_at=_expires_at_strategy, smaller=_grace_strategy, larger_delta=_grace_strategy)
def test_horizon_is_monotonic_in_grace(
    expires_at: datetime, smaller: timedelta, larger_delta: timedelta
) -> None:
    larger = smaller + larger_delta
    smaller_horizon = processed_id_retention_horizon(expires_at, grace=smaller)
    larger_horizon = processed_id_retention_horizon(expires_at, grace=larger)
    assert larger_horizon >= smaller_horizon
