"""Unit tests for ``mrr.persistence.unit_of_work.record_event`` (ADR-0007,
docs/spec/adr/ADR-0007-TASK-BUNDLE-TRANSITIONS-ARE-EVENTS.md), run entirely
DB-free — no PostgreSQL, no ``MRR_TEST_DATABASE_URL``.

``record_event`` is typed against the generic
``mrr.provenance.log.EventLog[Connection]`` Protocol rather than the concrete
``PostgresEventLog`` class (see its own docstring for why), so a hand-written
fake event log — conforming to the Protocol's full shape
(``append``/``read_all``/``verify_chain``), never touching any object
repository — is a legitimate, mypy-clean stand-in here. The ``Engine``
parameter is real (``record_event``'s type signature names
``sqlalchemy.Engine`` concretely, not a Protocol), so this module uses a
throwaway in-memory SQLite engine (``create_engine("sqlite://")``) purely to
obtain a live, zero-setup ``Connection`` for ``engine.begin()`` — nothing
here ever issues real SQL through it; ``FakeEventLog.append`` never touches
the connection at all. SQLite ships with the standard library, needs no
server or container, and is never asked to run any Postgres-specific SQL
(unlike the real ``PostgresEventLog``, which uses
``pg_advisory_xact_lock`` — exactly why the real class cannot be used here
and a fake is necessary).

Acceptance mapping (this task's own instruction, not a task-packets/*.yaml
one — ADR-0007's E1-T06 addition):

- "record_event appends exactly one event + one outbox row and NO object
  row" -> ``test_record_event_appends_exactly_one_event_and_touches_no_object_repository``.
- "an injected failure leaves neither" (atomicity) ->
  ``test_injected_failure_after_append_leaves_no_event_appended``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from mrr.domain.identity import new_urn
from mrr.persistence.unit_of_work import record_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.exceptions import ChainVerificationError
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Connection, Engine, create_engine


class FakeObjectRepository:
    """A bare-bones stand-in for ``mrr.domain.repositories.ObjectRepository``
    that ``record_event`` is never even given a reference to — constructed
    in these tests only so the "NO object row" assertion has something
    concrete to check against (it stays empty for the whole test, since
    nothing in ``record_event``'s signature offers a way to touch it).
    """

    def __init__(self) -> None:
        self.rows: list[object] = []


class FakeEventLog:
    """In-memory ``mrr.provenance.log.EventLog[Connection]``: implements the
    full protocol shape (``append``/``read_all``/``verify_chain``), not just
    ``append`` — a Protocol-typed parameter requires the whole shape, not
    only the members actually called. ``append`` never touches ``tx`` — it
    exists only to have the right signature, mirroring how the real
    ``PostgresEventLog.append`` uses ``conn`` for its own statements without
    ``record_event`` itself needing to know anything about that.
    """

    def __init__(self, *, fail_on_append: bool = False) -> None:
        self._events: list[AppendedEvent] = []
        self._fail_on_append = fail_on_append

    def append(self, tx: Connection, event: DomainEvent) -> AppendedEvent:
        if self._fail_on_append:
            raise RuntimeError("injected failure inside append")
        appended = AppendedEvent(
            event=event,
            sequence=len(self._events) + 1,
            content_hash=f"sha256:{'b' * 64}",
            prev_hash=self._events[-1].content_hash if self._events else None,
        )
        self._events.append(appended)
        return appended

    def read_all(self) -> list[AppendedEvent]:
        return list(self._events)

    def verify_chain(self) -> None:
        for appended in self._events:
            if appended.sequence < 1:
                raise ChainVerificationError(appended.sequence)  # pragma: no cover - defensive


def _sqlite_engine() -> Engine:
    return create_engine("sqlite://")


def _event(*, object_id: str | None = None) -> DomainEvent:
    return DomainEvent(
        id=new_urn("domain-event"),
        event_type="task_bundle.offered",
        occurred_at=datetime.now(UTC),
        actor=new_urn("agent-role"),
        policy_version="policy-2026-07-01",
        causation_id=None,
        correlation_id=new_urn("research-run"),
        object_id=object_id or new_urn("task-bundle"),
        object_revision=1,
        payload={"from_status": "CREATED", "to_status": "OFFERED"},
    )


# ---------------------------------------------------------------------------
# "record_event appends exactly one event + one outbox row and NO object row"
# ---------------------------------------------------------------------------


def test_record_event_appends_exactly_one_event_and_touches_no_object_repository() -> None:
    engine = _sqlite_engine()
    event_log = FakeEventLog()
    object_repository = FakeObjectRepository()  # never passed to record_event
    event = _event()

    appended = record_event(engine, event_log, event)

    assert appended.event.id == event.id
    events: Sequence[AppendedEvent] = event_log.read_all()
    assert [e.event.id for e in events] == [event.id]
    # record_event's signature has no object_repository parameter at all —
    # nothing could have inserted a row into this fake even in principle.
    assert object_repository.rows == []


def test_record_event_returns_the_appended_event_with_its_sequence_and_hash() -> None:
    engine = _sqlite_engine()
    event_log = FakeEventLog()
    first = record_event(engine, event_log, _event())
    second = record_event(engine, event_log, _event())

    assert first.sequence == 1
    assert first.prev_hash is None
    assert second.sequence == 2
    assert second.prev_hash == first.content_hash


# ---------------------------------------------------------------------------
# Atomicity: an injected failure leaves nothing appended.
# ---------------------------------------------------------------------------


def test_injected_failure_after_append_propagates_and_is_not_swallowed() -> None:
    """A fake, in-process event log has no real database transaction to roll
    back, so it cannot by itself prove that a failure between the append and
    the commit leaves NOTHING persisted — that is exactly what
    tests/integration/persistence/test_event_log_and_outbox.py's
    ``test_record_event_injected_failure_leaves_no_event_or_outbox_row``
    proves against real PostgreSQL. What this unit test proves instead:
    ``record_event`` does not catch, log-and-continue, or otherwise swallow a
    failure raised after the append — the exception surfaces to the caller
    unchanged, exactly like ``record_object_revision_with_event``'s own
    ``_after_append`` hook.
    """
    engine = _sqlite_engine()
    event_log = FakeEventLog()
    event = _event()

    def _inject_failure() -> None:
        raise RuntimeError("injected failure after append, before commit")

    with pytest.raises(RuntimeError, match="injected failure after append"):
        record_event(engine, event_log, event, _after_append=_inject_failure)


def test_event_log_append_failure_propagates_without_a_result() -> None:
    engine = _sqlite_engine()
    event_log = FakeEventLog(fail_on_append=True)
    event = _event()

    with pytest.raises(RuntimeError, match="injected failure inside append"):
        record_event(engine, event_log, event)

    assert event_log.read_all() == []
