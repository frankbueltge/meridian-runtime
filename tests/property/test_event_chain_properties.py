"""Property tests for the domain event hash chain (E1-T06).

Covers the packet's named properties (task-packets/E1-T06.yaml
acceptance_tests): "for arbitrary event payload sequences, a freshly
computed chain always verifies; flipping any single event's payload (or
prev_hash) makes verification fail at exactly that sequence."

Builds chains purely in memory via
``mrr.provenance.events.compute_event_hash`` and verifies them via
``mrr.provenance.log.verify_appended_events`` — the same pure functions
``mrr.persistence.repositories.PostgresEventLog`` uses against a live
database — so no PostgreSQL is required here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from _json_strategies import json_objects
from hypothesis import assume, given
from hypothesis import strategies as st
from mrr.domain.identity import new_urn
from mrr.provenance.events import DomainEvent, compute_event_hash
from mrr.provenance.exceptions import ChainVerificationError
from mrr.provenance.log import AppendedEvent, verify_appended_events

_BOGUS_HASH = "sha256:" + "f" * 64

#: Keep generated chains short enough that each hypothesis example still
#: hashes/canonicalizes quickly, matching the sizing already used by
#: tests/property/test_canonical_hash_properties.py.
_payload_lists = st.lists(json_objects(min_size=0, max_size=6), min_size=1, max_size=12)


def _domain_event(*, correlation_id: str, revision: int, payload: dict[str, Any]) -> DomainEvent:
    return DomainEvent(
        id=new_urn("domain-event"),
        event_type="claim.status_changed",
        occurred_at=datetime.now(UTC),
        actor=new_urn("agent-role"),
        policy_version="policy-2026-07-01",
        causation_id=None,
        correlation_id=correlation_id,
        object_id=new_urn("claim"),
        object_revision=revision,
        payload=payload,
    )


def _build_chain(payloads: list[dict[str, Any]]) -> list[AppendedEvent]:
    """Build a valid in-memory hash chain — the same shape
    ``PostgresEventLog.append`` produces one row at a time against a real
    database — entirely in memory.
    """
    correlation_id = new_urn("research-run")
    chain: list[AppendedEvent] = []
    prev_hash: str | None = None
    for index, payload in enumerate(payloads, start=1):
        event = _domain_event(correlation_id=correlation_id, revision=index, payload=payload)
        content_hash = compute_event_hash(event, prev_hash)
        chain.append(
            AppendedEvent(
                event=event, sequence=index, content_hash=content_hash, prev_hash=prev_hash
            )
        )
        prev_hash = content_hash
    return chain


@given(_payload_lists)
def test_freshly_built_chain_always_verifies(payloads: list[dict[str, Any]]) -> None:
    chain = _build_chain(payloads)
    verify_appended_events(chain)  # must not raise


@given(st.data())
def test_flipping_a_single_payload_fails_verification_at_exactly_that_sequence(
    data: st.DataObject,
) -> None:
    payloads = data.draw(_payload_lists)
    chain = _build_chain(payloads)

    tamper_index = data.draw(st.integers(min_value=0, max_value=len(chain) - 1))
    new_payload = data.draw(
        json_objects(min_size=0, max_size=6).filter(lambda value: value != payloads[tamper_index])
    )

    original = chain[tamper_index]
    tampered_event = _domain_event(
        correlation_id=original.event.correlation_id,
        revision=original.event.object_revision,
        payload=new_payload,
    )
    tampered_chain = list(chain)
    # Simulates a raw-SQL `UPDATE domain_events SET payload = ...` at this
    # sequence: the stored content_hash/prev_hash are untouched, only the
    # event's own field changed underneath them.
    tampered_chain[tamper_index] = AppendedEvent(
        event=tampered_event,
        sequence=original.sequence,
        content_hash=original.content_hash,
        prev_hash=original.prev_hash,
    )

    with pytest.raises(ChainVerificationError) as excinfo:
        verify_appended_events(tampered_chain)
    assert excinfo.value.sequence == original.sequence


@given(st.data())
def test_flipping_prev_hash_fails_verification_at_exactly_that_sequence(
    data: st.DataObject,
) -> None:
    payloads = data.draw(_payload_lists)
    chain = _build_chain(payloads)

    tamper_index = data.draw(st.integers(min_value=0, max_value=len(chain) - 1))
    original = chain[tamper_index]
    assume(original.prev_hash != _BOGUS_HASH)

    tampered_chain = list(chain)
    tampered_chain[tamper_index] = AppendedEvent(
        event=original.event,
        sequence=original.sequence,
        content_hash=original.content_hash,
        prev_hash=_BOGUS_HASH,
    )

    with pytest.raises(ChainVerificationError) as excinfo:
        verify_appended_events(tampered_chain)
    assert excinfo.value.sequence == original.sequence
