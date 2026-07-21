"""Contract tests for ``CorrectionNotification`` (task-packets/E6-T03.yaml)
beyond the generic example-driven checks tests/contract/test_examples.py
already runs.

These cases are Pydantic-only semantic checks that JSON Schema cannot
express (no cross-field constraint language for "this field must be
strictly before that field" or "this nested field must equal that
top-level field") — mirroring
tests/contract/test_node_message_envelope_variants.py's own precedent for a
model_validator-only rule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mrr.contracts.correction_notification import CorrectionNotification
from mrr.domain.identity import new_urn
from pydantic import ValidationError

_SENT_AT = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_EXPIRES_AT = _SENT_AT + timedelta(minutes=5)


def _base_document(**overrides: Any) -> dict[str, Any]:
    notifying_practice_id = new_urn("practice")
    document: dict[str, Any] = {
        "notification_id": new_urn("correction-notification"),
        "correction_id": new_urn("correction"),
        "correction_revision": 1,
        "notifying_practice_id": notifying_practice_id,
        "recipient_practice_id": new_urn("practice"),
        "notified_object_ids": [new_urn("claim")],
        "correction_type": "numeric_error",
        "severity": "material",
        "reason": "Fixture reason for a contract-level variant test.",
        "requested_action": "Mark dependent claims review_required and recompute.",
        "replacement_object_id": None,
        "content_hash": "sha256:" + "3" * 64,
        "nonce": "n" * 16,
        "sent_at": _SENT_AT,
        "expires_at": _EXPIRES_AT,
        "signature": {
            "signer_practice_id": notifying_practice_id,
            "key_id": "key-2026-01",
            "algorithm": "Ed25519",
            "signed_at": _SENT_AT,
            "value": "G" * 88,
        },
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# sent_at must be strictly before expires_at.
# ---------------------------------------------------------------------------


def test_sent_at_strictly_before_expires_at_is_accepted() -> None:
    notification = CorrectionNotification.model_validate(_base_document())
    assert notification.sent_at < notification.expires_at


def test_sent_at_equal_to_expires_at_is_rejected() -> None:
    document = _base_document(expires_at=_SENT_AT)

    with pytest.raises(ValidationError, match="strictly before"):
        CorrectionNotification.model_validate(document)


def test_sent_at_after_expires_at_is_rejected() -> None:
    document = _base_document(sent_at=_EXPIRES_AT, expires_at=_SENT_AT)

    with pytest.raises(ValidationError, match="strictly before"):
        CorrectionNotification.model_validate(document)


# ---------------------------------------------------------------------------
# signature.signer_practice_id must equal this notification's own
# notifying_practice_id.
# ---------------------------------------------------------------------------


def test_signature_signer_matching_notifying_practice_is_accepted() -> None:
    document = _base_document()

    notification = CorrectionNotification.model_validate(document)

    assert notification.signature.signer_practice_id == notification.notifying_practice_id


def test_signature_signer_not_equal_to_notifying_practice_is_rejected() -> None:
    document = _base_document()
    document["signature"]["signer_practice_id"] = new_urn("practice")  # a DIFFERENT practice

    with pytest.raises(
        ValidationError, match="must equal this notification's own notifying_practice_id"
    ):
        CorrectionNotification.model_validate(document)


# ---------------------------------------------------------------------------
# replacement_object_id is optional and nullable; notified_object_ids is
# non-empty.
# ---------------------------------------------------------------------------


def test_replacement_object_id_may_be_omitted() -> None:
    document = _base_document()
    del document["replacement_object_id"]

    notification = CorrectionNotification.model_validate(document)

    assert notification.replacement_object_id is None


def test_empty_notified_object_ids_is_rejected() -> None:
    document = _base_document(notified_object_ids=[])

    with pytest.raises(ValidationError):
        CorrectionNotification.model_validate(document)
