"""Contract tests for ``OfflineBundle`` (task-packets/E5-T06.yaml) beyond
the generic example-driven checks tests/contract/test_examples.py already
runs.

These cases are Pydantic-only semantic checks that JSON Schema cannot
express (no cross-field constraint language for "this field must be
strictly before that field", "this nested field must equal that top-level
field", or "these two lists must correspond 1:1, in order") — mirroring
tests/contract/test_node_message_envelope_variants.py's own precedent for a
model_validator-only rule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts.offline_bundle import OfflineBundle
from mrr.domain.identity import new_urn
from pydantic import ValidationError

_CREATED_AT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_EXPIRES_AT = _CREATED_AT + timedelta(days=7)
_PAYLOAD_HASH = "sha256:" + "c" * 64
_ENVELOPE_HASH = "sha256:" + "d" * 64


def _envelope_document(
    *, message_id: str, sender_practice_id: str, recipient_node_id: str
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "sent_at": _CREATED_AT,
        "expires_at": _CREATED_AT + timedelta(minutes=5),
        "payload_kind": "TaskBundle",
        "payload_content_hash": _PAYLOAD_HASH,
        "payload": {"kind": "TaskBundle", "content_hash": _PAYLOAD_HASH},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": "key-2026-01",
            "algorithm": "Ed25519",
            "signed_at": _CREATED_AT,
            "value": "F" * 88,
        },
    }


def _base_document(**overrides: Any) -> dict[str, Any]:
    sender_practice_id = new_urn("practice")
    recipient_node_id = new_urn("node")
    message_id = new_urn("node-message-envelope")
    document: dict[str, Any] = {
        "bundle_id": new_urn("offline-bundle"),
        "bundle_nonce": "example-bundle-nonce",
        "sender_node_id": new_urn("node"),
        "sender_practice_id": sender_practice_id,
        "recipient_node_id": recipient_node_id,
        "created_at": _CREATED_AT,
        "expires_at": _EXPIRES_AT,
        "entries": [
            {
                "message_id": message_id,
                "payload_kind": "TaskBundle",
                "envelope_content_hash": _ENVELOPE_HASH,
            }
        ],
        "envelopes": [
            _envelope_document(
                message_id=message_id,
                sender_practice_id=sender_practice_id,
                recipient_node_id=recipient_node_id,
            )
        ],
        "encryption": {"scheme": "none"},
        "signature": {
            "signer_practice_id": sender_practice_id,
            "key_id": "key-2026-01",
            "algorithm": "Ed25519",
            "signed_at": _CREATED_AT,
            "value": "E" * 88,
        },
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# created_at must be strictly before expires_at.
# ---------------------------------------------------------------------------


def test_created_at_strictly_before_expires_at_is_accepted() -> None:
    bundle = OfflineBundle.model_validate(_base_document())
    assert bundle.created_at < bundle.expires_at


def test_created_at_equal_to_expires_at_is_rejected() -> None:
    document = _base_document(expires_at=_CREATED_AT)

    with pytest.raises(ValidationError, match="strictly before"):
        OfflineBundle.model_validate(document)


def test_created_at_after_expires_at_is_rejected() -> None:
    document = _base_document(created_at=_EXPIRES_AT, expires_at=_CREATED_AT)

    with pytest.raises(ValidationError, match="strictly before"):
        OfflineBundle.model_validate(document)


# ---------------------------------------------------------------------------
# signature.signer_practice_id must equal this bundle's own
# sender_practice_id.
# ---------------------------------------------------------------------------


def test_signature_signer_matching_sender_practice_is_accepted() -> None:
    document = _base_document()

    bundle = OfflineBundle.model_validate(document)

    assert bundle.signature.signer_practice_id == bundle.sender_practice_id


def test_signature_signer_not_equal_to_sender_practice_is_rejected() -> None:
    document = _base_document()
    document["signature"]["signer_practice_id"] = new_urn("practice")  # a DIFFERENT practice

    with pytest.raises(ValidationError, match="must equal this bundle's own sender_practice_id"):
        OfflineBundle.model_validate(document)


# ---------------------------------------------------------------------------
# entries and envelopes must correspond 1:1, in order.
# ---------------------------------------------------------------------------


def test_entries_and_envelopes_of_equal_length_with_matching_ids_is_accepted() -> None:
    bundle = OfflineBundle.model_validate(_base_document())
    assert len(bundle.entries) == len(bundle.envelopes) == 1


def test_more_entries_than_envelopes_is_rejected() -> None:
    document = _base_document()
    document["entries"] = document["entries"] * 2

    with pytest.raises(ValidationError, match="must correspond 1:1"):
        OfflineBundle.model_validate(document)


def test_more_envelopes_than_entries_is_rejected() -> None:
    document = _base_document()
    document["envelopes"] = document["envelopes"] * 2

    with pytest.raises(ValidationError, match="must correspond 1:1"):
        OfflineBundle.model_validate(document)


def test_entry_message_id_not_matching_envelope_at_same_position_is_rejected() -> None:
    document = _base_document()
    document["entries"][0]["message_id"] = new_urn("node-message-envelope")

    with pytest.raises(ValidationError, match="does not equal"):
        OfflineBundle.model_validate(document)
