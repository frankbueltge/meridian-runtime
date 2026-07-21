"""Unit tests for ``mrr.domain.delivery_retry`` (task-packets/E6-T06.yaml),
run entirely DB-free — no PostgreSQL. The property-level generalization of
the ``next_retry_at``/``is_retry_exhausted`` bullets lives in
tests/property/test_delivery_retry_properties.py.

Acceptance-test mapping:

- "next_retry_at never returns an instant strictly after the wrapped
  notification's own expires_at, and is monotonically non-decreasing across
  successive calls with an increasing attempt_count for a fixed backoff
  policy" -> ``test_next_retry_at_never_exceeds_expires_at``,
  ``test_next_retry_at_is_monotonic_in_attempt_count_for_a_fixed_backoff``.
- "is_retry_exhausted is True once attempt_count >= max_attempts,
  independently True once the evaluation instant is at/after expires_at even
  with attempt_count still below max_attempts, and False when neither
  condition holds" -> ``test_is_retry_exhausted_true_at_max_attempts``,
  ``test_is_retry_exhausted_true_at_expiry_regardless_of_attempt_count``,
  ``test_is_retry_exhausted_false_when_neither_condition_holds``.
- "the new narrow delivery-record state machine accepts exactly
  pending -> delivered and pending -> exhausted; every other pair ... raises
  InvalidTransitionError via the reused, unmodified
  StateMachine.assert_transition" ->
  ``test_delivery_retry_lifecycle_accepts_exactly_the_two_drawn_edges``,
  ``test_delivery_retry_lifecycle_rejects_every_other_pair``.
- "regression (no Postgres) — CORRECTION_LIFECYCLE.transitions and
  CORRECTION_LIFECYCLE.states are asserted byte-identical to their value
  before this task ... guarding against an accidental new edge out of
  DELIVERY_PENDING ever being introduced by this task or a future merge
  conflict" -> ``test_correction_lifecycle_is_byte_identical_to_its_pre_e6_t06_declaration``.
- input validation on the two pure functions (negative attempt_count/
  max_attempts/backoff) -> the ``test_*_rejects_*`` group.
- ``DELIVERY_RECORD_STATUSES``/``DeliveryPendingRecord`` shape ->
  ``test_delivery_record_statuses_is_exactly_the_three_values``,
  ``test_delivery_pending_record_carries_no_more_than_the_documented_fields``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from mrr.domain.delivery_retry import (
    DELIVERY_RECORD_STATUSES,
    DELIVERY_RETRY_LIFECYCLE,
    Backoff,
    DeliveryPendingRecord,
    is_retry_exhausted,
    next_retry_at,
)
from mrr.domain.exceptions import InvalidTransitionError
from mrr.domain.lifecycles import CORRECTION_LIFECYCLE

_EXPIRES_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_SENT_AT = _EXPIRES_AT - timedelta(minutes=5)


def _fixed_backoff(minutes: int) -> Backoff:
    def _backoff(attempt_count: int) -> timedelta:
        return timedelta(minutes=minutes * attempt_count)

    return _backoff


# ---------------------------------------------------------------------------
# next_retry_at
# ---------------------------------------------------------------------------


def test_next_retry_at_never_exceeds_expires_at() -> None:
    # A huge backoff would, uncapped, land far past expires_at.
    scheduled = next_retry_at(
        last_attempted_at=_SENT_AT,
        attempt_count=1,
        backoff=lambda _n: timedelta(days=365),
        expires_at=_EXPIRES_AT,
    )
    assert scheduled == _EXPIRES_AT


def test_next_retry_at_returns_the_uncapped_candidate_when_it_is_within_the_window() -> None:
    scheduled = next_retry_at(
        last_attempted_at=_SENT_AT,
        attempt_count=1,
        backoff=lambda _n: timedelta(minutes=1),
        expires_at=_EXPIRES_AT,
    )
    assert scheduled == _SENT_AT + timedelta(minutes=1)


def test_next_retry_at_is_monotonic_in_attempt_count_for_a_fixed_backoff() -> None:
    backoff = _fixed_backoff(1)
    at_1 = next_retry_at(
        last_attempted_at=_SENT_AT, attempt_count=1, backoff=backoff, expires_at=_EXPIRES_AT
    )
    at_2 = next_retry_at(
        last_attempted_at=_SENT_AT, attempt_count=2, backoff=backoff, expires_at=_EXPIRES_AT
    )
    at_3 = next_retry_at(
        last_attempted_at=_SENT_AT, attempt_count=3, backoff=backoff, expires_at=_EXPIRES_AT
    )
    assert at_1 <= at_2 <= at_3


def test_next_retry_at_rejects_attempt_count_below_one() -> None:
    with pytest.raises(ValueError, match="attempt_count must be >= 1"):
        next_retry_at(
            last_attempted_at=_SENT_AT,
            attempt_count=0,
            backoff=lambda _n: timedelta(minutes=1),
            expires_at=_EXPIRES_AT,
        )


def test_next_retry_at_rejects_a_negative_backoff() -> None:
    with pytest.raises(ValueError, match="must be >= timedelta"):
        next_retry_at(
            last_attempted_at=_SENT_AT,
            attempt_count=1,
            backoff=lambda _n: timedelta(seconds=-1),
            expires_at=_EXPIRES_AT,
        )


# ---------------------------------------------------------------------------
# is_retry_exhausted
# ---------------------------------------------------------------------------


def test_is_retry_exhausted_true_at_max_attempts() -> None:
    assert (
        is_retry_exhausted(attempt_count=3, max_attempts=3, expires_at=_EXPIRES_AT, at=_SENT_AT)
        is True
    )


def test_is_retry_exhausted_true_above_max_attempts() -> None:
    assert (
        is_retry_exhausted(attempt_count=5, max_attempts=3, expires_at=_EXPIRES_AT, at=_SENT_AT)
        is True
    )


def test_is_retry_exhausted_true_at_expiry_regardless_of_attempt_count() -> None:
    assert (
        is_retry_exhausted(
            attempt_count=0, max_attempts=100, expires_at=_EXPIRES_AT, at=_EXPIRES_AT
        )
        is True
    )


def test_is_retry_exhausted_true_after_expiry_regardless_of_attempt_count() -> None:
    assert (
        is_retry_exhausted(
            attempt_count=0,
            max_attempts=100,
            expires_at=_EXPIRES_AT,
            at=_EXPIRES_AT + timedelta(seconds=1),
        )
        is True
    )


def test_is_retry_exhausted_false_when_neither_condition_holds() -> None:
    assert (
        is_retry_exhausted(attempt_count=1, max_attempts=3, expires_at=_EXPIRES_AT, at=_SENT_AT)
        is False
    )


def test_is_retry_exhausted_rejects_negative_attempt_count() -> None:
    with pytest.raises(ValueError, match="attempt_count must be >= 0"):
        is_retry_exhausted(attempt_count=-1, max_attempts=3, expires_at=_EXPIRES_AT, at=_SENT_AT)


def test_is_retry_exhausted_rejects_max_attempts_below_one() -> None:
    with pytest.raises(ValueError, match="max_attempts must be >= 1"):
        is_retry_exhausted(attempt_count=0, max_attempts=0, expires_at=_EXPIRES_AT, at=_SENT_AT)


# ---------------------------------------------------------------------------
# DELIVERY_RETRY_LIFECYCLE — the new narrow pending/delivered/exhausted
# machine, built by IMPORTING mrr.domain.lifecycles.StateMachine.
# ---------------------------------------------------------------------------


def test_delivery_retry_lifecycle_accepts_exactly_the_two_drawn_edges() -> None:
    assert DELIVERY_RETRY_LIFECYCLE.states == {"pending", "delivered", "exhausted"}
    assert DELIVERY_RETRY_LIFECYCLE.initial_state == "pending"
    assert DELIVERY_RETRY_LIFECYCLE.transitions == {
        ("pending", "delivered"),
        ("pending", "exhausted"),
    }
    DELIVERY_RETRY_LIFECYCLE.assert_transition("pending", "delivered")  # must not raise
    DELIVERY_RETRY_LIFECYCLE.assert_transition("pending", "exhausted")  # must not raise


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("delivered", "pending"),
        ("exhausted", "pending"),
        ("delivered", "exhausted"),
        ("exhausted", "delivered"),
        ("pending", "pending"),
        ("delivered", "delivered"),
        ("exhausted", "exhausted"),
    ],
)
def test_delivery_retry_lifecycle_rejects_every_other_pair(from_state: str, to_state: str) -> None:
    assert DELIVERY_RETRY_LIFECYCLE.can_transition(from_state, to_state) is False
    with pytest.raises(InvalidTransitionError) as excinfo:
        DELIVERY_RETRY_LIFECYCLE.assert_transition(from_state, to_state)
    assert excinfo.value.machine == "DeliveryRetry"
    assert excinfo.value.from_state == from_state
    assert excinfo.value.to_state == to_state


# ---------------------------------------------------------------------------
# CORRECTION_LIFECYCLE regression — mrr.domain.lifecycles stays byte-for-byte
# unchanged by this task (no edge added out of DELIVERY_PENDING).
# ---------------------------------------------------------------------------

#: Verbatim snapshot of CORRECTION_LIFECYCLE's own declaration in
#: mrr.domain.lifecycles as it stood BEFORE task-packets/E6-T06.yaml (and
#: unchanged by it — that module is a forbidden-to-modify path for this
#: task). A literal tuple/frozenset, not a re-derived expression, so this
#: test actually catches an accidental edit rather than trivially agreeing
#: with whatever the module currently says.
_CORRECTION_STATES_BEFORE_E6_T06 = frozenset(
    {
        "OPEN",
        "IMPACT_ANALYSIS",
        "NOTIFYING",
        "AWAITING_RESPONSES",
        "DELIVERY_PENDING",
        "RESOLVED",
        "PARTIALLY_RESOLVED",
        "REJECTED_BY_RECIPIENT",
    }
)

_CORRECTION_TRANSITIONS_BEFORE_E6_T06 = frozenset(
    {
        ("OPEN", "IMPACT_ANALYSIS"),
        ("IMPACT_ANALYSIS", "NOTIFYING"),
        ("NOTIFYING", "AWAITING_RESPONSES"),
        ("AWAITING_RESPONSES", "RESOLVED"),
        ("AWAITING_RESPONSES", "PARTIALLY_RESOLVED"),
        ("AWAITING_RESPONSES", "REJECTED_BY_RECIPIENT"),
        ("AWAITING_RESPONSES", "DELIVERY_PENDING"),
    }
)


def test_correction_lifecycle_is_byte_identical_to_its_pre_e6_t06_declaration() -> None:
    assert CORRECTION_LIFECYCLE.states == _CORRECTION_STATES_BEFORE_E6_T06
    assert CORRECTION_LIFECYCLE.transitions == _CORRECTION_TRANSITIONS_BEFORE_E6_T06
    assert CORRECTION_LIFECYCLE.initial_state == "OPEN"
    # In particular, still no drawn edge out of DELIVERY_PENDING at all.
    assert not any(
        from_state == "DELIVERY_PENDING" for from_state, _ in CORRECTION_LIFECYCLE.transitions
    )


# ---------------------------------------------------------------------------
# DELIVERY_RECORD_STATUSES / DeliveryPendingRecord shape.
# ---------------------------------------------------------------------------


def test_delivery_record_statuses_is_exactly_the_three_values() -> None:
    assert set(DELIVERY_RECORD_STATUSES) == {"pending", "delivered", "exhausted"}


def test_delivery_pending_record_carries_no_more_than_the_documented_fields() -> None:
    record = DeliveryPendingRecord(
        recipient_node_id="urn:mrr:node:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        notification_id="urn:mrr:correction-notification:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        correction_id="urn:mrr:correction:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        status="pending",
        attempt_count=1,
        opened_at=_SENT_AT,
        last_attempted_at=_SENT_AT,
        next_retry_at=_SENT_AT + timedelta(minutes=1),
        notification_expires_at=_EXPIRES_AT,
        resolved_at=None,
        exhausted_reason=None,
    )
    field_names = {f.name for f in dataclasses.fields(record)}
    assert field_names == {
        "recipient_node_id",
        "notification_id",
        "correction_id",
        "status",
        "attempt_count",
        "opened_at",
        "last_attempted_at",
        "next_retry_at",
        "notification_expires_at",
        "resolved_at",
        "exhausted_reason",
    }
