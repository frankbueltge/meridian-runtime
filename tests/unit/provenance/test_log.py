"""Unit tests for mrr.provenance.log (E1-T06): the AppendedEvent/OutboxEntry
dataclass surface, EventLog/OutboxDispatcher Protocol conformance (structural
tests double, matching tests/unit/domain/test_repositories.py's pattern for
ObjectRepository/EdgeRepository), and verify_appended_events — the pure,
database-free chain-verification function also used by
mrr.persistence.repositories.PostgresEventLog.verify_chain, exercised here
directly and more thoroughly in tests/property/test_event_chain_properties.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.domain.identity import new_urn
from mrr.provenance.events import DomainEvent, compute_event_hash
from mrr.provenance.exceptions import ChainVerificationError
from mrr.provenance.log import (
    OUTBOX_STATUSES,
    AppendedEvent,
    EventLog,
    OutboxDispatcher,
    OutboxEntry,
    verify_appended_events,
)


def _domain_event(**overrides: Any) -> DomainEvent:
    defaults: dict[str, Any] = {
        "id": new_urn("domain-event"),
        "event_type": "claim.status_changed",
        "occurred_at": datetime.now(UTC),
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


def _build_chain(payloads: list[dict[str, Any]]) -> list[AppendedEvent]:
    """Build a valid in-memory hash chain, exactly the shape
    PostgresEventLog.append would produce, without any database.
    """
    chain: list[AppendedEvent] = []
    prev_hash: str | None = None
    correlation_id = new_urn("research-run")
    for index, payload in enumerate(payloads, start=1):
        event = _domain_event(
            correlation_id=correlation_id,
            object_revision=index,
            payload=payload,
        )
        content_hash = compute_event_hash(event, prev_hash)
        chain.append(
            AppendedEvent(
                event=event, sequence=index, content_hash=content_hash, prev_hash=prev_hash
            )
        )
        prev_hash = content_hash
    return chain


# ---------------------------------------------------------------------------
# AppendedEvent / OutboxEntry — frozen dataclass surface.
# ---------------------------------------------------------------------------


def test_appended_event_is_frozen() -> None:
    appended = AppendedEvent(event=_domain_event(), sequence=1, content_hash="x", prev_hash=None)
    with pytest.raises(AttributeError):
        appended.sequence = 2  # type: ignore[misc]


def test_outbox_entry_carries_all_fields() -> None:
    entry = OutboxEntry(
        event_id=new_urn("domain-event"),
        status="pending",
        created_at=datetime.now(UTC),
        dispatched_at=None,
        attempts=0,
    )
    assert entry.status == "pending"
    assert entry.dispatched_at is None
    assert entry.attempts == 0


def test_outbox_statuses_are_exactly_pending_and_dispatched() -> None:
    assert {"pending", "dispatched"} == OUTBOX_STATUSES


# ---------------------------------------------------------------------------
# Protocol surface — runtime_checkable structural conformance.
# ---------------------------------------------------------------------------


class _FakeEventLog:
    def append(self, event: DomainEvent) -> AppendedEvent:  # pragma: no cover
        raise NotImplementedError

    def read_all(self) -> list[AppendedEvent]:  # pragma: no cover
        raise NotImplementedError

    def verify_chain(self) -> None:  # pragma: no cover
        raise NotImplementedError


class _IncompleteEventLog:
    """Missing verify_chain — must not satisfy the protocol."""

    def append(self, event: DomainEvent) -> AppendedEvent:  # pragma: no cover
        raise NotImplementedError

    def read_all(self) -> list[AppendedEvent]:  # pragma: no cover
        raise NotImplementedError


class _FakeOutboxDispatcher:
    def dispatch_pending(self) -> int:  # pragma: no cover
        raise NotImplementedError


class _IncompleteOutboxDispatcher:
    """No dispatch_pending at all — must not satisfy the protocol."""


def test_event_log_protocol_accepts_a_conforming_implementation() -> None:
    assert isinstance(_FakeEventLog(), EventLog)


def test_event_log_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteEventLog(), EventLog)


def test_outbox_dispatcher_protocol_accepts_a_conforming_implementation() -> None:
    assert isinstance(_FakeOutboxDispatcher(), OutboxDispatcher)


def test_outbox_dispatcher_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteOutboxDispatcher(), OutboxDispatcher)


# ---------------------------------------------------------------------------
# verify_appended_events — pure, database-free chain verification.
# ---------------------------------------------------------------------------


def test_verify_appended_events_accepts_empty_chain() -> None:
    verify_appended_events([])  # must not raise


def test_verify_appended_events_accepts_a_freshly_built_chain() -> None:
    chain = _build_chain([{"n": i} for i in range(5)])
    verify_appended_events(chain)  # must not raise


def test_verify_appended_events_rejects_wrong_first_prev_hash() -> None:
    chain = _build_chain([{"n": 0}])
    tampered_first = AppendedEvent(
        event=chain[0].event,
        sequence=chain[0].sequence,
        content_hash=chain[0].content_hash,
        prev_hash="sha256:" + "f" * 64,
    )
    with pytest.raises(ChainVerificationError) as excinfo:
        verify_appended_events([tampered_first])
    assert excinfo.value.sequence == 1


def test_verify_appended_events_detects_tampered_payload_at_exact_sequence() -> None:
    chain = _build_chain([{"n": i} for i in range(4)])

    tampered_event = DomainEvent(
        id=chain[2].event.id,
        event_type=chain[2].event.event_type,
        occurred_at=chain[2].event.occurred_at,
        actor=chain[2].event.actor,
        policy_version=chain[2].event.policy_version,
        causation_id=chain[2].event.causation_id,
        correlation_id=chain[2].event.correlation_id,
        object_id=chain[2].event.object_id,
        object_revision=chain[2].event.object_revision,
        payload={"n": "tampered"},
    )
    tampered_chain = list(chain)
    tampered_chain[2] = AppendedEvent(
        event=tampered_event,
        sequence=chain[2].sequence,
        content_hash=chain[2].content_hash,
        prev_hash=chain[2].prev_hash,
    )

    with pytest.raises(ChainVerificationError) as excinfo:
        verify_appended_events(tampered_chain)
    assert excinfo.value.sequence == chain[2].sequence == 3


def test_verify_appended_events_detects_tampered_prev_hash_at_exact_sequence() -> None:
    chain = _build_chain([{"n": i} for i in range(4)])

    tampered_chain = list(chain)
    tampered_chain[1] = AppendedEvent(
        event=chain[1].event,
        sequence=chain[1].sequence,
        content_hash=chain[1].content_hash,
        prev_hash="sha256:" + "e" * 64,
    )

    with pytest.raises(ChainVerificationError) as excinfo:
        verify_appended_events(tampered_chain)
    assert excinfo.value.sequence == chain[1].sequence == 2
