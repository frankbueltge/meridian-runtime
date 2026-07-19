"""Unit tests for mrr.domain.envelope_transport (task-packets/E5-T03.yaml):
the ``EnvelopeDeliveryRequest``/``EnvelopeDeliveryOutcome`` value-object
surface and the ``EnvelopeTransport`` Protocol's structural conformance —
including an in-test fake transport that round-trips a
``NodeMessageEnvelope`` with no network of any kind (task-packets/
E5-T03.yaml acceptance test: "an in-test fake transport implements the
abstract port and round-trips an envelope with no network").

No concrete (mTLS) transport is exercised or imported here (task-packets/
E5-T03.yaml forbidden_changes: "any real network, socket, TLS, mTLS ...");
``_FakeEnvelopeTransport`` below is a private, in-memory, deterministic test
double, matching this codebase's own "private module fake, not shared"
precedent (see tests/unit/domain/test_model_adapter.py's own
``_FakeModelAdapter``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.domain.envelope_transport import (
    EnvelopeDeliveryOutcome,
    EnvelopeDeliveryRequest,
    EnvelopeTransport,
)
from mrr.domain.identity import new_urn

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def _envelope(**overrides: Any) -> NodeMessageEnvelope:
    sender_practice_id = new_urn("practice")
    data: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": new_urn("node"),
        "sent_at": _NOW,
        "expires_at": _NOW + timedelta(minutes=5),
        "payload_kind": "TaskBundle",
        "payload_content_hash": "sha256:" + "c" * 64,
        "payload": {"kind": "TaskBundle", "content_hash": "sha256:" + "c" * 64},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": "key-2026-01",
            "algorithm": "Ed25519",
            "signed_at": _NOW,
            "value": "0" * 44,
        },
    }
    data.update(overrides)
    return NodeMessageEnvelope.model_validate(data)


# ---------------------------------------------------------------------------
# EnvelopeDeliveryRequest / EnvelopeDeliveryOutcome — self-validation.
# ---------------------------------------------------------------------------


def test_delivery_request_accepts_valid_fields() -> None:
    envelope = _envelope()
    request = EnvelopeDeliveryRequest(envelope=envelope, recipient_endpoint="node-b.invalid:9443")
    assert request.envelope is envelope


def test_delivery_request_rejects_empty_recipient_endpoint() -> None:
    with pytest.raises(ValueError, match="recipient_endpoint"):
        EnvelopeDeliveryRequest(envelope=_envelope(), recipient_endpoint="")


def test_delivery_outcome_rejects_empty_message_id() -> None:
    with pytest.raises(ValueError, match="message_id"):
        EnvelopeDeliveryOutcome(status="delivered", message_id="")


# ---------------------------------------------------------------------------
# EnvelopeTransport Protocol — structural conformance.
# ---------------------------------------------------------------------------


class _FakeEnvelopeTransport:
    """An in-memory, deterministic fake — no network of any kind. Records
    every envelope it was asked to deliver so a test can prove round-trip
    fidelity.
    """

    def __init__(self) -> None:
        self._delivered: dict[str, NodeMessageEnvelope] = {}

    def send(self, request: EnvelopeDeliveryRequest) -> EnvelopeDeliveryOutcome:
        self._delivered[request.envelope.message_id] = request.envelope
        return EnvelopeDeliveryOutcome(status="delivered", message_id=request.envelope.message_id)

    def delivered_envelope(self, message_id: str) -> NodeMessageEnvelope:
        return self._delivered[message_id]


class _IncompleteEnvelopeTransport:
    """Missing ``send`` entirely — must not satisfy the Protocol."""


def test_envelope_transport_protocol_accepts_a_conforming_fake() -> None:
    assert isinstance(_FakeEnvelopeTransport(), EnvelopeTransport)


def test_envelope_transport_protocol_rejects_an_incomplete_implementation() -> None:
    assert not isinstance(_IncompleteEnvelopeTransport(), EnvelopeTransport)


def test_fake_transport_round_trips_an_envelope_with_no_network() -> None:
    envelope = _envelope()
    transport: EnvelopeTransport = _FakeEnvelopeTransport()
    request = EnvelopeDeliveryRequest(envelope=envelope, recipient_endpoint="node-b.invalid:9443")

    outcome = transport.send(request)

    assert outcome.status == "delivered"
    assert outcome.message_id == envelope.message_id
    # Round-trip fidelity: the fake stored the exact same envelope, with no
    # network call anywhere in this test.
    assert isinstance(transport, _FakeEnvelopeTransport)
    assert transport.delivered_envelope(envelope.message_id) == envelope
