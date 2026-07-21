"""Property tests for ``mrr.domain.delivery_retry`` (task-packets/
E6-T06.yaml).

Acceptance-test mapping: "property test — for arbitrary non-negative
attempt_count sequences and arbitrary expires_at/max_attempts pairs,
is_retry_exhausted is monotonic (once true for a given state, it stays true
for any later evaluation instant or higher attempt_count) and next_retry_at
is always <= expires_at whenever it is not None" ->
``test_is_retry_exhausted_is_monotonic_in_attempt_count``,
``test_is_retry_exhausted_is_monotonic_in_evaluation_instant``,
``test_next_retry_at_is_always_at_or_before_expires_at``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.delivery_retry import is_retry_exhausted, next_retry_at

#: A wide but finite range so arithmetic on the drawn datetimes/timedeltas
#: never risks overflowing datetime.max — mirrors tests/property/
#: test_replay_retention_properties.py's own identical strategy.
_instant_strategy = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 1, 1),
    timezones=st.just(UTC),
)

_nonnegative_timedelta_strategy = st.timedeltas(
    min_value=timedelta(0), max_value=timedelta(days=3650)
)

_attempt_count_strategy = st.integers(min_value=0, max_value=1000)
_max_attempts_strategy = st.integers(min_value=1, max_value=1000)


@given(
    expires_at=_instant_strategy,
    attempt_count=_attempt_count_strategy,
    max_attempts=_max_attempts_strategy,
    at=_instant_strategy,
)
def test_is_retry_exhausted_is_monotonic_in_attempt_count(
    expires_at: datetime, attempt_count: int, max_attempts: int, at: datetime
) -> None:
    """Once ``is_retry_exhausted`` is ``True`` for a given ``attempt_count``,
    it stays ``True`` for any HIGHER ``attempt_count`` (all else fixed) —
    the ``attempt_count >= max_attempts`` disjunct can only become "more
    true", never less, as ``attempt_count`` grows.
    """
    if is_retry_exhausted(
        attempt_count=attempt_count, max_attempts=max_attempts, expires_at=expires_at, at=at
    ):
        assert is_retry_exhausted(
            attempt_count=attempt_count + 1,
            max_attempts=max_attempts,
            expires_at=expires_at,
            at=at,
        )


@given(
    expires_at=_instant_strategy,
    attempt_count=_attempt_count_strategy,
    max_attempts=_max_attempts_strategy,
    at=_instant_strategy,
    delta=_nonnegative_timedelta_strategy,
)
def test_is_retry_exhausted_is_monotonic_in_evaluation_instant(
    expires_at: datetime,
    attempt_count: int,
    max_attempts: int,
    at: datetime,
    delta: timedelta,
) -> None:
    """Once ``is_retry_exhausted`` is ``True`` at a given evaluation instant,
    it stays ``True`` at any LATER evaluation instant (all else fixed).
    """
    if is_retry_exhausted(
        attempt_count=attempt_count, max_attempts=max_attempts, expires_at=expires_at, at=at
    ):
        assert is_retry_exhausted(
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            expires_at=expires_at,
            at=at + delta,
        )


@given(
    last_attempted_at=_instant_strategy,
    attempt_count=st.integers(min_value=1, max_value=1000),
    delay=_nonnegative_timedelta_strategy,
    expires_at=_instant_strategy,
)
def test_next_retry_at_is_always_at_or_before_expires_at(
    last_attempted_at: datetime, attempt_count: int, delay: timedelta, expires_at: datetime
) -> None:
    """``next_retry_at`` never returns an instant strictly after
    ``expires_at``, for an arbitrary non-negative backoff delay and an
    arbitrary ``expires_at`` (including one already before
    ``last_attempted_at`` — the anchoring must hold even in that degenerate
    case, not just the ordinary one)."""
    scheduled = next_retry_at(
        last_attempted_at=last_attempted_at,
        attempt_count=attempt_count,
        backoff=lambda _n: delay,
        expires_at=expires_at,
    )
    assert scheduled <= expires_at
