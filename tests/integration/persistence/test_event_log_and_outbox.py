"""Integration tests for the append-only domain event log and transactional
outbox (task-packets/E1-T06.yaml), run via the `postgres_engine` fixture in
tests/integration/conftest.py. Skips visibly if MRR_TEST_DATABASE_URL is
unset (fails hard instead if CI=true) — see that module's docstring.

Acceptance-test mapping:

- "alembic upgrade head applies migration 0002 on top of 0001" ->
  ``test_alembic_upgrade_head_creates_all_four_tables`` (every other test in
  this module also exercises this, via the fixture, on every run).
- "appended events read back in order with intact provenance fields" ->
  ``test_append_then_read_all_preserves_order_and_provenance_fields``.
- "chain verification passes on an untampered log and fails after a raw-SQL
  payload alteration" -> ``test_chain_verifies_on_an_untampered_log`` and
  ``test_raw_sql_payload_alteration_breaks_verification_at_that_sequence``.
- "injected failure between state write and event append rolls back both
  (and the outbox row)" ->
  ``test_injected_failure_after_append_leaves_nothing_persisted``.
- "concurrent appends from two connections both succeed serialized and the
  chain remains valid" -> ``test_concurrent_appends_both_succeed_and_chain_
  remains_valid``.
- "pending outbox rows are dispatched exactly once by the reference
  dispatcher; a dispatcher error leaves the row pending" ->
  ``test_dispatcher_dispatches_pending_rows_exactly_once`` and
  ``test_dispatcher_error_leaves_row_pending_with_attempts_incremented``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from mrr.domain.exceptions import ObjectNotFoundError
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import (
    InProcessOutboxDispatcher,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.tables import domain_events_table, outbox_table
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.exceptions import ChainVerificationError
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine, inspect


def _now() -> datetime:
    return datetime.now(UTC)


def _domain_event(
    *,
    correlation_id: str | None = None,
    object_id: str | None = None,
    object_revision: int = 1,
    causation_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> DomainEvent:
    return DomainEvent(
        id=new_urn("domain-event"),
        event_type="claim.status_changed",
        occurred_at=_now(),
        actor=new_urn("agent-role"),
        policy_version="policy-2026-07-01",
        causation_id=causation_id,
        correlation_id=correlation_id or new_urn("research-run"),
        object_id=object_id or new_urn("claim"),
        object_revision=object_revision,
        payload=payload if payload is not None else {"status": "under_review"},
    )


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


def _append(engine: Engine, event_log: PostgresEventLog, event: DomainEvent) -> AppendedEvent:
    with engine.begin() as conn:
        return event_log.append(conn, event)


# ---------------------------------------------------------------------------
# alembic upgrade head
# ---------------------------------------------------------------------------


def test_alembic_upgrade_head_creates_all_four_tables(postgres_engine: Engine) -> None:
    inspector = inspect(postgres_engine)
    table_names = set(inspector.get_table_names())
    assert {"objects", "edges", "domain_events", "outbox"}.issubset(table_names)


# ---------------------------------------------------------------------------
# Append, read, chain verification.
# ---------------------------------------------------------------------------


def test_append_then_read_all_preserves_order_and_provenance_fields(
    postgres_engine: Engine,
) -> None:
    event_log = PostgresEventLog(postgres_engine)
    correlation_id = new_urn("research-run")
    root_event_id = new_urn("domain-event")

    first = _domain_event(
        correlation_id=correlation_id,
        object_revision=1,
        payload={"status": "draft"},
    )
    second = _domain_event(
        correlation_id=correlation_id,
        object_id=first.object_id,
        object_revision=2,
        causation_id=first.id,
        payload={"status": "under_review"},
    )
    # Override the generated id so we can assert on it below.
    first = DomainEvent(
        id=root_event_id,
        event_type=first.event_type,
        occurred_at=first.occurred_at,
        actor=first.actor,
        policy_version=first.policy_version,
        causation_id=None,
        correlation_id=first.correlation_id,
        object_id=first.object_id,
        object_revision=first.object_revision,
        payload=first.payload,
    )
    second = DomainEvent(
        id=second.id,
        event_type=second.event_type,
        occurred_at=second.occurred_at,
        actor=second.actor,
        policy_version=second.policy_version,
        causation_id=root_event_id,
        correlation_id=second.correlation_id,
        object_id=second.object_id,
        object_revision=second.object_revision,
        payload=second.payload,
    )

    appended_first = _append(postgres_engine, event_log, first)
    appended_second = _append(postgres_engine, event_log, second)

    assert appended_first.prev_hash is None
    assert appended_second.prev_hash == appended_first.content_hash

    all_events = event_log.read_all()
    assert [e.sequence for e in all_events] == [appended_first.sequence, appended_second.sequence]
    assert all_events[0].event.id == root_event_id
    assert all_events[0].event.causation_id is None
    assert all_events[1].event.causation_id == root_event_id
    assert all_events[1].event.correlation_id == correlation_id
    assert all_events[1].event.object_id == first.object_id
    assert all_events[1].event.object_revision == 2
    assert all_events[1].event.payload == {"status": "under_review"}


def test_append_creates_a_pending_outbox_row(postgres_engine: Engine) -> None:
    event_log = PostgresEventLog(postgres_engine)
    appended = _append(postgres_engine, event_log, _domain_event())

    with postgres_engine.connect() as conn:
        row = (
            conn.execute(
                sa.select(outbox_table).where(outbox_table.c.event_id == appended.event.id)
            )
            .mappings()
            .one()
        )
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert row["dispatched_at"] is None


def test_chain_verifies_on_an_untampered_log(postgres_engine: Engine) -> None:
    event_log = PostgresEventLog(postgres_engine)
    correlation_id = new_urn("research-run")
    for revision in range(1, 4):
        _append(
            postgres_engine,
            event_log,
            _domain_event(correlation_id=correlation_id, object_revision=revision),
        )

    event_log.verify_chain()  # must not raise


def test_raw_sql_payload_alteration_breaks_verification_at_that_sequence(
    postgres_engine: Engine,
) -> None:
    event_log = PostgresEventLog(postgres_engine)
    correlation_id = new_urn("research-run")
    for revision in range(1, 4):
        _append(
            postgres_engine,
            event_log,
            _domain_event(correlation_id=correlation_id, object_revision=revision),
        )

    event_log.verify_chain()  # untampered — passes

    # Deliberately bypass mrr.persistence.repositories.PostgresEventLog
    # entirely: a raw-SQL UPDATE against the second row's payload.
    with postgres_engine.begin() as conn:
        conn.execute(
            sa.update(domain_events_table)
            .where(domain_events_table.c.sequence == 2)
            .values(payload={"tampered": True})
        )

    with pytest.raises(ChainVerificationError) as excinfo:
        event_log.verify_chain()
    assert excinfo.value.sequence == 2


# ---------------------------------------------------------------------------
# Atomic coupling: object write + event append + outbox row, one transaction.
# ---------------------------------------------------------------------------


def test_injected_failure_after_append_leaves_nothing_persisted(postgres_engine: Engine) -> None:
    object_repo = PostgresObjectRepository(postgres_engine)
    event_log = PostgresEventLog(postgres_engine)
    object_id = new_urn("claim")
    obj = _stored_object(id=object_id, revision=1)
    event = _domain_event(object_id=object_id, object_revision=1)

    def _inject_failure() -> None:
        raise RuntimeError("injected failure after append, before commit")

    with pytest.raises(RuntimeError, match="injected failure"):
        record_object_revision_with_event(
            postgres_engine,
            object_repo,
            event_log,
            obj,
            expected_current_revision=None,
            event=event,
            _after_append=_inject_failure,
        )

    # Neither the object revision...
    with pytest.raises(ObjectNotFoundError):
        object_repo.get_latest(object_id)

    # ...nor the event...
    assert event_log.read_all() == []

    # ...nor its outbox row survive.
    with postgres_engine.connect() as conn:
        outbox_count = conn.execute(
            sa.select(sa.func.count())
            .select_from(outbox_table)
            .where(outbox_table.c.event_id == event.id)
        ).scalar_one()
    assert outbox_count == 0


def test_successful_unit_of_work_persists_object_event_and_outbox_row(
    postgres_engine: Engine,
) -> None:
    object_repo = PostgresObjectRepository(postgres_engine)
    event_log = PostgresEventLog(postgres_engine)
    object_id = new_urn("claim")
    obj = _stored_object(id=object_id, revision=1)
    event = _domain_event(object_id=object_id, object_revision=1)

    stored, appended = record_object_revision_with_event(
        postgres_engine,
        object_repo,
        event_log,
        obj,
        expected_current_revision=None,
        event=event,
    )

    assert stored.id == object_id
    assert object_repo.get_latest(object_id).id == object_id
    assert appended.event.id == event.id
    assert [e.event.id for e in event_log.read_all()] == [event.id]

    with postgres_engine.connect() as conn:
        outbox_status = conn.execute(
            sa.select(outbox_table.c.status).where(outbox_table.c.event_id == event.id)
        ).scalar_one()
    assert outbox_status == "pending"


# ---------------------------------------------------------------------------
# Concurrency: two writers, single-writer chain via the advisory lock.
# ---------------------------------------------------------------------------


def test_concurrent_appends_both_succeed_and_chain_remains_valid(postgres_engine: Engine) -> None:
    event_log = PostgresEventLog(postgres_engine)
    correlation_id = new_urn("research-run")
    event_a = _domain_event(correlation_id=correlation_id, object_revision=1)
    event_b = _domain_event(correlation_id=correlation_id, object_revision=1)

    barrier = threading.Barrier(2, timeout=10)

    def _append_with_barrier(event: DomainEvent) -> AppendedEvent:
        with postgres_engine.begin() as conn:
            barrier.wait()
            return event_log.append(conn, event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_append_with_barrier, event_a)
        future_b = pool.submit(_append_with_barrier, event_b)
        appended_a = future_a.result(timeout=15)
        appended_b = future_b.result(timeout=15)

    assert {appended_a.sequence, appended_b.sequence} == {1, 2}

    event_log.verify_chain()  # must not raise
    all_events = event_log.read_all()
    assert [e.sequence for e in all_events] == [1, 2]


# ---------------------------------------------------------------------------
# InProcessOutboxDispatcher: at-least-once, idempotent-marking.
# ---------------------------------------------------------------------------


def test_dispatcher_dispatches_pending_rows_exactly_once(postgres_engine: Engine) -> None:
    event_log = PostgresEventLog(postgres_engine)
    correlation_id = new_urn("research-run")
    appended = [
        _append(
            postgres_engine,
            event_log,
            _domain_event(correlation_id=correlation_id, object_revision=revision),
        )
        for revision in (1, 2)
    ]

    dispatched: list[str] = []

    def _handler(appended_event: AppendedEvent) -> None:
        dispatched.append(appended_event.event.id)

    dispatcher = InProcessOutboxDispatcher(postgres_engine, _handler)
    dispatched_count = dispatcher.dispatch_pending()

    assert dispatched_count == 2
    assert set(dispatched) == {a.event.id for a in appended}

    with postgres_engine.connect() as conn:
        rows = conn.execute(sa.select(outbox_table)).mappings().all()
    assert all(row["status"] == "dispatched" for row in rows)
    assert all(row["dispatched_at"] is not None for row in rows)
    assert all(row["attempts"] == 1 for row in rows)

    # A second call must find nothing pending left — a dispatched row is
    # never re-created or re-selected.
    dispatched.clear()
    second_round_count = dispatcher.dispatch_pending()
    assert second_round_count == 0
    assert dispatched == []


def test_dispatcher_error_leaves_row_pending_with_attempts_incremented(
    postgres_engine: Engine,
) -> None:
    event_log = PostgresEventLog(postgres_engine)
    appended = _append(
        postgres_engine, event_log, _domain_event(correlation_id=new_urn("research-run"))
    )

    def _always_fails(appended_event: AppendedEvent) -> None:
        raise RuntimeError("handler failure")

    failing_dispatcher = InProcessOutboxDispatcher(postgres_engine, _always_fails)
    assert failing_dispatcher.dispatch_pending() == 0

    with postgres_engine.connect() as conn:
        row = (
            conn.execute(
                sa.select(outbox_table).where(outbox_table.c.event_id == appended.event.id)
            )
            .mappings()
            .one()
        )
    assert row["status"] == "pending"
    assert row["attempts"] == 1
    assert row["dispatched_at"] is None

    # A later call with a working handler succeeds and the row transitions —
    # the earlier failure did not corrupt or drop it.
    succeeded: list[str] = []
    working_dispatcher = InProcessOutboxDispatcher(
        postgres_engine, lambda a: succeeded.append(a.event.id)
    )
    assert working_dispatcher.dispatch_pending() == 1
    assert succeeded == [appended.event.id]

    with postgres_engine.connect() as conn:
        row_after = (
            conn.execute(
                sa.select(outbox_table).where(outbox_table.c.event_id == appended.event.id)
            )
            .mappings()
            .one()
        )
    assert row_after["status"] == "dispatched"
    assert row_after["attempts"] == 2
