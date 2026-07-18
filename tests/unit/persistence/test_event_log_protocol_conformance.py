"""Machine-checked conformance: ``PostgresEventLog`` structurally satisfies
``mrr.provenance.log.EventLog[Connection]`` (task-packets/E1-T06.yaml,
correction from independent review of PR #6).

``EventLog`` is generic and deliberately not ``@runtime_checkable`` — a
runtime ``isinstance`` check only compares method *names*, never signatures,
so it would report ``True`` even for a class whose ``append`` takes
incompatible arguments (exactly what PR #6 originally shipped:
``PostgresEventLog.append(self, conn, event)`` against a non-generic
``EventLog.append(self, event)`` — no implementation could ever have
satisfied that shape, and a runtime check would never have caught it).

The typed assignment below is the real conformance guarantee instead:
``make typecheck`` (mypy strict) only accepts it if ``PostgresEventLog``'s
``append``/``read_all``/``verify_chain`` actually match
``EventLog[Connection]``'s shape. The test function exists only so this
module stays collected by ``make test`` and a future accidental deletion of
the assignment is noticed; the assignment itself is the check.
"""

from __future__ import annotations

from mrr.persistence.repositories import PostgresEventLog
from mrr.provenance.log import EventLog
from sqlalchemy import Connection, create_engine


def test_postgres_event_log_satisfies_event_log_protocol() -> None:
    # Never connected — sqlalchemy.create_engine is lazy, and nothing here
    # calls a method that would open a connection.
    engine = create_engine("postgresql+psycopg://user:pass@localhost/db")

    log: EventLog[Connection] = PostgresEventLog(engine)

    assert isinstance(log, PostgresEventLog)
