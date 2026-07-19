"""Contract tests for ``NodeMessageEnvelope`` (task-packets/E5-T03.yaml)
beyond the generic example-driven checks tests/contract/test_examples.py
already runs.

These cases are Pydantic-only semantic checks that JSON Schema cannot
express (no cross-field constraint language for "this field must be
strictly before that field" or "this nested field must equal that
top-level field") — mirroring tests/contract/test_practice_variants.py's
own precedent for a model_validator-only rule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.domain.identity import new_urn
from pydantic import ValidationError

_SENT_AT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_EXPIRES_AT = _SENT_AT + timedelta(minutes=5)
_PAYLOAD_HASH = "sha256:" + "c" * 64


def _base_document(**overrides: Any) -> dict[str, Any]:
    sender_practice_id = new_urn("practice")
    document: dict[str, Any] = {
        "message_id": new_urn("node-message-envelope"),
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": new_urn("node"),
        "sent_at": _SENT_AT,
        "expires_at": _EXPIRES_AT,
        "payload_kind": "TaskBundle",
        "payload_content_hash": _PAYLOAD_HASH,
        "payload": {"kind": "TaskBundle", "content_hash": _PAYLOAD_HASH},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": "key-2026-01",
            "algorithm": "Ed25519",
            "signed_at": _SENT_AT,
            "value": "F" * 88,
        },
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# sent_at must be strictly before expires_at.
# ---------------------------------------------------------------------------


def test_sent_at_strictly_before_expires_at_is_accepted() -> None:
    envelope = NodeMessageEnvelope.model_validate(_base_document())
    assert envelope.sent_at < envelope.expires_at


def test_sent_at_equal_to_expires_at_is_rejected() -> None:
    document = _base_document(expires_at=_SENT_AT)

    with pytest.raises(ValidationError, match="strictly before"):
        NodeMessageEnvelope.model_validate(document)


def test_sent_at_after_expires_at_is_rejected() -> None:
    document = _base_document(sent_at=_EXPIRES_AT, expires_at=_SENT_AT)

    with pytest.raises(ValidationError, match="strictly before"):
        NodeMessageEnvelope.model_validate(document)


# ---------------------------------------------------------------------------
# signature.signer_practice_id must equal this envelope's own
# sender_practice_id.
# ---------------------------------------------------------------------------


def test_signature_signer_matching_sender_practice_is_accepted() -> None:
    document = _base_document()

    envelope = NodeMessageEnvelope.model_validate(document)

    assert envelope.signature.signer_practice_id == envelope.sender_practice_id


def test_signature_signer_not_equal_to_sender_practice_is_rejected() -> None:
    document = _base_document()
    document["signature"]["signer_practice_id"] = new_urn("practice")  # a DIFFERENT practice

    with pytest.raises(ValidationError, match="must equal this envelope's own sender_practice_id"):
        NodeMessageEnvelope.model_validate(document)
