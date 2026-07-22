"""Integration test for E9-T00c (task-packets/E9-T00c.yaml R3): the event
hash chain verifies independently of the database SESSION timezone.

The defect (docs/design/2026-07-21-research-method-kernel-rework.md
section 8, filed on PR #40, verbatim): "The event-log hash chain is
sensitive to the database session timezone (fails against a non-UTC server
because the rendered ``timestamptz`` differs on read-back). CI is UTC, so
it never surfaces there." This exact shape — append over a Europe/Berlin
session, verify over an Asia/Tokyo and a UTC session — FAILED with
``ChainVerificationError`` before the fix (``occurred_at`` rendered
``+02:00``/``+09:00``/``+00:00`` respectively, three different hash
inputs for one instant) and is green after it.

The test also asserts its own premise (non-vacuity guard): the Tokyo
session really does hand back a NON-UTC utcoffset for the stored
``timestamptz`` — if a future driver change made every session return UTC
datetimes, the assertion flags that this test no longer exercises the
defect surface, instead of passing silently for the wrong reason.

Wiring follows tests/integration/persistence/test_event_log_and_outbox.py
(the ``postgres_engine`` fixture; sibling engines are derived from its own
URL with the session timezone appended to the SAME libpq ``options``
parameter that already carries the schema-scoped ``search_path``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import PostgresEventLog
from mrr.provenance.events import DomainEvent
from sqlalchemy import Engine


def _engine_with_session_timezone(base_engine: Engine, tz_name: str) -> Engine:
    url = base_engine.url
    existing_options = url.query.get("options", "")
    options = f"{existing_options} -c timezone={tz_name}".strip()
    return sa.create_engine(url.set(query={**url.query, "options": options}))


def _domain_event(**overrides: Any) -> DomainEvent:
    defaults: dict[str, Any] = {
        "id": new_urn("domain-event"),
        "event_type": "claim.status_changed",
        "occurred_at": datetime.now(UTC),
        "actor": new_urn("agent-role"),
        "policy_version": "policy-e9-t00c-test",
        "causation_id": None,
        "correlation_id": new_urn("research-run"),
        "object_id": new_urn("claim"),
        "object_revision": 1,
        "payload": {"status": "under_review"},
    }
    defaults.update(overrides)
    return DomainEvent(**defaults)


def test_verify_chain_is_session_timezone_independent(postgres_engine: Engine) -> None:
    berlin_engine = _engine_with_session_timezone(postgres_engine, "Europe/Berlin")
    tokyo_engine = _engine_with_session_timezone(postgres_engine, "Asia/Tokyo")
    try:
        # Append a three-event chain over the Berlin session.
        berlin_log = PostgresEventLog(berlin_engine)
        correlation = new_urn("research-run")
        object_id = new_urn("claim")
        prev_id: str | None = None
        for revision in (1, 2, 3):
            event = _domain_event(
                correlation_id=correlation,
                object_id=object_id,
                object_revision=revision,
                causation_id=prev_id,
            )
            with berlin_engine.begin() as conn:
                berlin_log.append(conn, event)
            prev_id = event.id

        # Non-vacuity guard: the Tokyo session must actually render the
        # stored timestamptz with a non-UTC offset (see module docstring).
        tokyo_log = PostgresEventLog(tokyo_engine)
        tokyo_events = tokyo_log.read_all()
        assert len(tokyo_events) == 3
        offsets = {appended.event.occurred_at.utcoffset() for appended in tokyo_events}
        assert offsets == {timedelta(hours=9)}, (
            "premise lost: the Tokyo session no longer returns non-UTC datetimes — "
            "this test would stop exercising the section-8 defect surface"
        )

        # The defect: before the E9-T00c fix, BOTH of these raised
        # ChainVerificationError (occurred_at rendered +09:00 / +00:00,
        # never the +02:00 the hashes were computed from).
        tokyo_log.verify_chain()
        PostgresEventLog(postgres_engine).verify_chain()

        # And the writing session's own view stays green too.
        berlin_log.verify_chain()
    finally:
        berlin_engine.dispose()
        tokyo_engine.dispose()
