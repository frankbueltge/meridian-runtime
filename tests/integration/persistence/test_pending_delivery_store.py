"""Integration tests for
``mrr.persistence.repositories.PostgresDeliveryPendingStore`` (task-packets/
E6-T06.yaml), run via the ``postgres_engine`` fixture in
tests/integration/conftest.py. Skips visibly if ``MRR_TEST_DATABASE_URL`` is
unset (fails hard instead if ``CI=true``) — see that module's docstring.

Acceptance-test mapping:

- "alembic upgrade head" (creates the pending_deliveries table) ->
  ``test_alembic_upgrade_head_creates_pending_deliveries_table`` (every
  other test in this module also exercises this, via the fixture, on every
  run).
- "open_pending_delivery creates exactly one row for a (recipient_node_id,
  notification_id) pair with status = 'pending' and attempt_count = 1;
  calling it again for the SAME pair is idempotent (no duplicate row, no
  error)" -> ``test_open_pending_delivery_creates_one_pending_row``,
  ``test_open_pending_delivery_is_idempotent_for_the_same_pair``.
- "record_retry_attempt reporting 'pending' increments attempt_count,
  updates last_attempted_at and next_retry_at, and leaves status =
  'pending'; reporting 'delivered' transitions the row to status =
  'delivered', sets resolved_at, and clears next_retry_at" ->
  ``test_record_retry_attempt_failed_increments_and_reschedules``,
  ``test_record_retry_attempt_delivered_resolves_and_clears_next_retry_at``.
- "a retry attempt evaluated at/after the record's own
  notification_expires_at, or once attempt_count reaches the caller's
  max_attempts, transitions the row to status = 'exhausted' with a non-null
  exhausted_reason and resolved_at, and this holds regardless of ordering
  between the two triggers" ->
  ``test_record_retry_attempt_exhausts_via_max_attempts``,
  ``test_record_retry_attempt_exhausts_via_expiry_before_max_attempts``.
- "adversarial ... calling record_retry_attempt or mark_exhausted against an
  already-delivered or already-exhausted row raises InvalidTransitionError
  and leaves the row completely unchanged" ->
  ``test_record_retry_attempt_against_delivered_row_raises_and_leaves_row_unchanged``,
  ``test_record_retry_attempt_against_exhausted_row_raises_and_leaves_row_unchanged``,
  ``test_mark_exhausted_against_delivered_row_raises_and_leaves_row_unchanged``.
- "the retry-due query returns exactly the rows with status = 'pending' AND
  next_retry_at <= now, excluding delivered/exhausted rows and pending rows
  not yet due" -> ``test_list_due_for_retry_returns_exactly_the_due_pending_rows``.
- missing-record / explicit-exhaustion / validation ->
  ``test_record_retry_attempt_missing_record_raises_pending_delivery_not_found``,
  ``test_mark_exhausted_missing_record_raises_pending_delivery_not_found``,
  ``test_mark_exhausted_requires_a_non_empty_reason``,
  ``test_mark_exhausted_resolves_an_open_record_explicitly``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from mrr.domain.exceptions import InvalidTransitionError, PendingDeliveryNotFoundError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresDeliveryPendingStore
from mrr.persistence.tables import pending_deliveries_table
from sqlalchemy import Engine, inspect

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


def _linear_backoff(attempt_number: int) -> timedelta:
    return timedelta(minutes=attempt_number)


def _store(engine: Engine, *, max_attempts: int = 3) -> PostgresDeliveryPendingStore:
    return PostgresDeliveryPendingStore(engine, max_attempts=max_attempts, backoff=_linear_backoff)


# ---------------------------------------------------------------------------
# alembic upgrade head
# ---------------------------------------------------------------------------


def test_alembic_upgrade_head_creates_pending_deliveries_table(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    assert "pending_deliveries" in set(inspector.get_table_names())


# ---------------------------------------------------------------------------
# open_pending_delivery: idempotency.
# ---------------------------------------------------------------------------


def test_open_pending_delivery_creates_one_pending_row(postgres_engine: Engine) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    correction_id = new_urn("correction")
    expires_at = _NOW + timedelta(minutes=30)

    newly_opened = store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=correction_id,
        notification_expires_at=expires_at,
        at=_NOW,
    )

    assert newly_opened is True
    record = store.get_pending_delivery(recipient_node_id, notification_id)
    assert record is not None
    assert record.status == "pending"
    assert record.attempt_count == 1
    assert record.correction_id == correction_id
    assert record.notification_expires_at == expires_at
    assert record.next_retry_at is not None
    assert record.next_retry_at <= expires_at
    assert record.resolved_at is None
    assert record.exhausted_reason is None

    with postgres_engine.connect() as conn:
        row_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(pending_deliveries_table)
            .where(
                pending_deliveries_table.c.recipient_node_id == recipient_node_id,
                pending_deliveries_table.c.notification_id == notification_id,
            )
        ).scalar_one()
    assert row_count == 1


def test_open_pending_delivery_is_idempotent_for_the_same_pair(postgres_engine: Engine) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(minutes=30)

    first = store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )
    second = store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),  # a different value, ignored
        notification_expires_at=expires_at,
        at=_NOW + timedelta(seconds=1),
    )

    assert first is True
    assert second is False  # idempotent no-op, not an error
    record = store.get_pending_delivery(recipient_node_id, notification_id)
    assert record is not None and record.attempt_count == 1

    with postgres_engine.connect() as conn:
        row_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(pending_deliveries_table)
            .where(
                pending_deliveries_table.c.recipient_node_id == recipient_node_id,
                pending_deliveries_table.c.notification_id == notification_id,
            )
        ).scalar_one()
    assert row_count == 1


# ---------------------------------------------------------------------------
# record_retry_attempt: pending/delivered outcomes.
# ---------------------------------------------------------------------------


def test_record_retry_attempt_failed_increments_and_reschedules(postgres_engine: Engine) -> None:
    store = _store(postgres_engine, max_attempts=10)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(days=1)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )

    record = store.record_retry_attempt(
        recipient_node_id, notification_id, outcome="failed", at=_NOW + timedelta(minutes=1)
    )

    assert record.status == "pending"
    assert record.attempt_count == 2
    assert record.last_attempted_at == _NOW + timedelta(minutes=1)
    assert record.next_retry_at is not None
    assert record.next_retry_at <= expires_at
    assert record.resolved_at is None


def test_record_retry_attempt_delivered_resolves_and_clears_next_retry_at(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(days=1)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )

    record = store.record_retry_attempt(
        recipient_node_id, notification_id, outcome="delivered", at=_NOW + timedelta(minutes=1)
    )

    assert record.status == "delivered"
    assert record.attempt_count == 2
    assert record.resolved_at == _NOW + timedelta(minutes=1)
    assert record.next_retry_at is None


# ---------------------------------------------------------------------------
# Exhaustion: either trigger, in either order.
# ---------------------------------------------------------------------------


def test_record_retry_attempt_exhausts_via_max_attempts(postgres_engine: Engine) -> None:
    store = _store(postgres_engine, max_attempts=2)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(days=365)  # far from expiry
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )  # attempt_count == 1

    record = store.record_retry_attempt(
        recipient_node_id, notification_id, outcome="failed", at=_NOW + timedelta(minutes=1)
    )  # attempt_count == 2 == max_attempts

    assert record.status == "exhausted"
    assert record.attempt_count == 2
    assert record.exhausted_reason
    assert "max_attempts" in record.exhausted_reason
    assert record.resolved_at == _NOW + timedelta(minutes=1)
    assert record.next_retry_at is None


def test_record_retry_attempt_exhausts_via_expiry_before_max_attempts(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine, max_attempts=1000)  # effectively unreachable
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(minutes=5)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )

    record = store.record_retry_attempt(
        recipient_node_id,
        notification_id,
        outcome="failed",
        at=expires_at,  # at/after expiry
    )

    assert record.status == "exhausted"
    assert record.exhausted_reason
    assert "validity window" in record.exhausted_reason
    assert record.next_retry_at is None


def test_record_retry_attempt_exhausts_when_both_triggers_hold_at_once(
    postgres_engine: Engine,
) -> None:
    """Both the max_attempts and expiry triggers hold simultaneously — the
    outcome (exhausted) is the same regardless of which one "fired first",
    and the recorded reason names both.
    """
    store = _store(postgres_engine, max_attempts=2)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(minutes=1)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )

    record = store.record_retry_attempt(
        recipient_node_id,
        notification_id,
        outcome="failed",
        at=expires_at + timedelta(minutes=1),  # past expiry AND hits max_attempts
    )

    assert record.status == "exhausted"
    assert record.exhausted_reason
    assert "max_attempts" in record.exhausted_reason
    assert "validity window" in record.exhausted_reason


# ---------------------------------------------------------------------------
# Adversarial: an already-terminal row fails closed, unchanged.
# ---------------------------------------------------------------------------


def test_record_retry_attempt_against_delivered_row_raises_and_leaves_row_unchanged(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(days=1)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )
    delivered = store.record_retry_attempt(
        recipient_node_id, notification_id, outcome="delivered", at=_NOW + timedelta(minutes=1)
    )

    with pytest.raises(InvalidTransitionError):
        store.record_retry_attempt(
            recipient_node_id, notification_id, outcome="failed", at=_NOW + timedelta(minutes=2)
        )

    assert store.get_pending_delivery(recipient_node_id, notification_id) == delivered


def test_record_retry_attempt_against_exhausted_row_raises_and_leaves_row_unchanged(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine, max_attempts=1)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(days=1)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )
    exhausted = store.record_retry_attempt(
        recipient_node_id, notification_id, outcome="failed", at=_NOW + timedelta(minutes=1)
    )
    assert exhausted.status == "exhausted"

    with pytest.raises(InvalidTransitionError):
        store.record_retry_attempt(
            recipient_node_id, notification_id, outcome="delivered", at=_NOW + timedelta(minutes=2)
        )

    assert store.get_pending_delivery(recipient_node_id, notification_id) == exhausted


def test_mark_exhausted_against_delivered_row_raises_and_leaves_row_unchanged(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    expires_at = _NOW + timedelta(days=1)
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW,
    )
    delivered = store.record_retry_attempt(
        recipient_node_id, notification_id, outcome="delivered", at=_NOW + timedelta(minutes=1)
    )

    with pytest.raises(InvalidTransitionError):
        store.mark_exhausted(
            recipient_node_id,
            notification_id,
            reason="attempted early exhaustion",
            at=_NOW + timedelta(minutes=2),
        )

    assert store.get_pending_delivery(recipient_node_id, notification_id) == delivered


# ---------------------------------------------------------------------------
# Missing-record / explicit-exhaustion / validation.
# ---------------------------------------------------------------------------


def test_record_retry_attempt_missing_record_raises_pending_delivery_not_found(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")

    with pytest.raises(PendingDeliveryNotFoundError) as excinfo:
        store.record_retry_attempt(recipient_node_id, notification_id, outcome="failed", at=_NOW)
    assert excinfo.value.recipient_node_id == recipient_node_id
    assert excinfo.value.notification_id == notification_id


def test_mark_exhausted_missing_record_raises_pending_delivery_not_found(
    postgres_engine: Engine,
) -> None:
    store = _store(postgres_engine)
    with pytest.raises(PendingDeliveryNotFoundError):
        store.mark_exhausted(
            new_urn("node"), new_urn("correction-notification"), reason="fixture", at=_NOW
        )


def test_mark_exhausted_requires_a_non_empty_reason(postgres_engine: Engine) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=_NOW + timedelta(days=1),
        at=_NOW,
    )

    with pytest.raises(ValueError, match="reason"):
        store.mark_exhausted(recipient_node_id, notification_id, reason="", at=_NOW)

    record = store.get_pending_delivery(recipient_node_id, notification_id)
    assert record is not None and record.status == "pending"


def test_mark_exhausted_resolves_an_open_record_explicitly(postgres_engine: Engine) -> None:
    store = _store(postgres_engine)
    recipient_node_id = new_urn("node")
    notification_id = new_urn("correction-notification")
    store.open_pending_delivery(
        recipient_node_id,
        notification_id,
        correction_id=new_urn("correction"),
        notification_expires_at=_NOW + timedelta(days=1),
        at=_NOW,
    )

    record = store.mark_exhausted(
        recipient_node_id,
        notification_id,
        reason="recipient endpoint permanently decommissioned",
        at=_NOW + timedelta(hours=1),
    )

    assert record.status == "exhausted"
    assert record.exhausted_reason == "recipient endpoint permanently decommissioned"
    assert record.resolved_at == _NOW + timedelta(hours=1)
    assert record.next_retry_at is None


# ---------------------------------------------------------------------------
# list_due_for_retry.
# ---------------------------------------------------------------------------


def test_list_due_for_retry_returns_exactly_the_due_pending_rows(postgres_engine: Engine) -> None:
    store = _store(postgres_engine, max_attempts=100)
    expires_at = _NOW + timedelta(days=1)

    due_recipient = new_urn("node")
    due_notification = new_urn("correction-notification")
    store.open_pending_delivery(
        due_recipient,
        due_notification,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW - timedelta(minutes=10),
    )  # next_retry_at == _NOW - 10min + 1min == _NOW - 9min, already due at _NOW

    not_yet_due_recipient = new_urn("node")
    not_yet_due_notification = new_urn("correction-notification")
    store.open_pending_delivery(
        not_yet_due_recipient,
        not_yet_due_notification,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW + timedelta(days=300),
    )  # next_retry_at far in the future, not due at _NOW

    delivered_recipient = new_urn("node")
    delivered_notification = new_urn("correction-notification")
    store.open_pending_delivery(
        delivered_recipient,
        delivered_notification,
        correction_id=new_urn("correction"),
        notification_expires_at=expires_at,
        at=_NOW - timedelta(minutes=10),
    )
    store.record_retry_attempt(
        delivered_recipient,
        delivered_notification,
        outcome="delivered",
        at=_NOW - timedelta(minutes=5),
    )

    exhausted_recipient = new_urn("node")
    exhausted_notification = new_urn("correction-notification")
    store.open_pending_delivery(
        exhausted_recipient,
        exhausted_notification,
        correction_id=new_urn("correction"),
        notification_expires_at=_NOW - timedelta(minutes=1),  # already expired at open time
        at=_NOW - timedelta(minutes=10),
    )
    store.record_retry_attempt(
        exhausted_recipient,
        exhausted_notification,
        outcome="failed",
        at=_NOW,  # at/after its own expires_at (_NOW - 1min) -> exhausted
    )

    due = store.list_due_for_retry(_NOW)
    due_keys = {(r.recipient_node_id, r.notification_id) for r in due}

    assert (due_recipient, due_notification) in due_keys
    assert (not_yet_due_recipient, not_yet_due_notification) not in due_keys
    assert (delivered_recipient, delivered_notification) not in due_keys
    assert (exhausted_recipient, exhausted_notification) not in due_keys
    assert all(r.status == "pending" for r in due)
    assert all(r.next_retry_at is not None and r.next_retry_at <= _NOW for r in due)
