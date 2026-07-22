"""Unit tests for the E9-T00c timezone normalization of the event hash
chain (task-packets/E9-T00c.yaml R2), closing the defect filed in
docs/design/2026-07-21-research-method-kernel-rework.md section 8 ("the
event-log hash chain is sensitive to the database session timezone").

Acceptance-test mapping (AT1 = R2's three tests):

- byte-identity for tz-aware UTC datetimes (every event this codebase ever
  wrote) -> ``test_utc_datetime_serialization_and_hash_are_byte_identical_
  to_pre_fix_behavior`` — the pre-fix expectation is pinned LITERALLY
  (the exact string ``.isoformat()`` produced before the fix), so this
  test would have passed before the change and proves existing archival
  hashes verify unchanged.
- same instant, two zones -> one hash ->
  ``test_same_instant_in_different_timezones_hashes_identically`` (this is
  the defect: it FAILS on the pre-fix code).
- naive refusal -> ``test_naive_occurred_at_is_refused``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from mrr.domain.identity import new_urn
from mrr.provenance.events import DomainEvent, compute_event_hash, event_to_hashable_dict


def _domain_event(**overrides: Any) -> DomainEvent:
    defaults: dict[str, Any] = {
        "id": new_urn("domain-event"),
        "event_type": "claim.status_changed",
        "occurred_at": datetime(2026, 7, 22, 12, 30, 45, 123456, tzinfo=UTC),
        "actor": new_urn("agent-role"),
        "policy_version": "policy-2026-07-01",
        "causation_id": None,
        "correlation_id": new_urn("research-run"),
        "object_id": new_urn("claim"),
        "object_revision": 1,
        "payload": {"status": "under_review"},
    }
    defaults.update(overrides)
    return DomainEvent(**defaults)


def test_utc_datetime_serialization_and_hash_are_byte_identical_to_pre_fix_behavior() -> None:
    event = _domain_event()

    hashable = event_to_hashable_dict(event, None)

    # The literal string the PRE-fix code (`occurred_at.isoformat()`, no
    # normalization) produced for this UTC datetime — pinned verbatim, not
    # recomputed through the code under test: astimezone(UTC) must be the
    # identity for the archive's own tz-aware-UTC events, or every recorded
    # content_hash would silently stop verifying (packet stop_condition).
    assert hashable["occurred_at"] == "2026-07-22T12:30:45.123456+00:00"


def test_same_instant_in_different_timezones_hashes_identically() -> None:
    # The section-8 defect, reduced to the pure function: one instant,
    # rendered by psycopg as +00:00 on a UTC session and +02:00 on a
    # Europe/Berlin session, must produce ONE hash. Fails on pre-fix code.
    instant_utc = datetime(2026, 7, 22, 12, 30, 45, 123456, tzinfo=UTC)
    instant_berlin = instant_utc.astimezone(timezone(timedelta(hours=2)))
    assert instant_utc == instant_berlin  # same instant by construction

    shared_id = new_urn("domain-event")
    shared_actor = new_urn("agent-role")
    shared_correlation = new_urn("research-run")
    shared_object = new_urn("claim")
    event_utc = _domain_event(
        id=shared_id,
        occurred_at=instant_utc,
        actor=shared_actor,
        correlation_id=shared_correlation,
        object_id=shared_object,
    )
    event_berlin = _domain_event(
        id=shared_id,
        occurred_at=instant_berlin,
        actor=shared_actor,
        correlation_id=shared_correlation,
        object_id=shared_object,
    )

    assert compute_event_hash(event_utc, None) == compute_event_hash(event_berlin, None)


def test_naive_occurred_at_is_refused_at_construction() -> None:
    # First line of defense, pre-existing: DomainEvent's own __post_init__
    # already refuses a naive occurred_at — no naive timestamp can enter
    # the chain through the public type.
    with pytest.raises(ValueError, match="aware datetime"):
        _domain_event(occurred_at=datetime(2026, 7, 22, 12, 30, 45, 123456))


def test_naive_occurred_at_is_refused_by_the_serializer_guard() -> None:
    # Second line of defense, this packet's R1: the serializer's own
    # if/raise guard (the ro_crate "documented unreachable guard"
    # convention — reachable only by bypassing the frozen constructor, as
    # done here deliberately) refuses a naive datetime instead of hashing
    # it ambiguously.
    event = _domain_event()
    bypassed = object.__new__(DomainEvent)
    for field_name in event.__dataclass_fields__:
        object.__setattr__(bypassed, field_name, getattr(event, field_name))
    object.__setattr__(bypassed, "occurred_at", datetime(2026, 7, 22, 12, 30, 45, 123456))

    with pytest.raises(ValueError, match="naive.*E9-T00c"):
        event_to_hashable_dict(bypassed, None)
