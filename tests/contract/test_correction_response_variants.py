"""Contract tests for ``CorrectionResponse`` (task-packets/E6-T04.yaml)
beyond the generic example-driven checks tests/contract/test_examples.py and
the dual-layer (JSON Schema + Pydantic) checks
tests/contract/test_negative_fixtures.py already run.

These cases are Pydantic-only semantic checks the JSON Schema deliberately
does NOT express (task-packets/E6-T04.yaml derived_decisions (c)/(d) flag
each as the schema-design axis with the most implementer latitude) —
mirroring tests/contract/test_correction_notification_variants.py's own
precedent for a model_validator-only rule:

- ``reason`` must be null/absent for decision NOT in (reject, defer) — the
  schema only requires ``reason`` for reject/defer, it never forbids it for
  accept/adapt.
- ``adaptations`` must be empty for decision != adapt — the schema only
  requires ``adaptations`` to be non-empty for adapt, it never forbids
  non-empty adaptations for accept/reject/defer.
- every ``adaptations[].notified_object_id`` must be a member of this
  response's own ``notified_object_ids`` — no JSON Schema in this repository
  expresses cross-array membership constraints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mrr.contracts.correction_response import CorrectionResponse
from mrr.domain.identity import new_urn
from pydantic import ValidationError

_CREATED_AT = datetime(2026, 7, 19, 12, 4, 0, tzinfo=UTC)


def _base_document(**overrides: Any) -> dict[str, Any]:
    notified_object_id = new_urn("claim")
    document: dict[str, Any] = {
        "id": new_urn("correction-response"),
        "api_version": "mrr/v1alpha1",
        "kind": "CorrectionResponse",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": _CREATED_AT,
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "4" * 64,
        "correction_notification_id": new_urn("correction-notification"),
        "notifying_practice_id": new_urn("practice"),
        "origin_correction_event_id": new_urn("correction"),
        "origin_correction_event_revision": 1,
        "notified_object_ids": [notified_object_id],
        "decision": "accept",
        "adaptations": [],
    }
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# reason: required (non-empty) iff reject/defer; forbidden otherwise.
# ---------------------------------------------------------------------------


def test_accept_with_no_reason_is_accepted() -> None:
    response = CorrectionResponse.model_validate(_base_document(decision="accept"))
    assert response.reason is None


@pytest.mark.parametrize("decision", ["reject", "defer"])
def test_reject_or_defer_with_non_empty_reason_is_accepted(decision: str) -> None:
    document = _base_document(decision=decision, reason="Fixture reason for rejecting/deferring.")
    response = CorrectionResponse.model_validate(document)
    assert response.decision == decision
    assert response.reason == "Fixture reason for rejecting/deferring."


@pytest.mark.parametrize("decision", ["accept", "adapt"])
def test_reason_present_for_accept_or_adapt_is_rejected(decision: str) -> None:
    document = _base_document(decision=decision, reason="This reason must not be here.")
    if decision == "adapt":
        adapted_object_id = new_urn("claim")
        document["notified_object_ids"] = [document["notified_object_ids"][0]]
        document["adaptations"] = [
            {
                "adapted_object_id": adapted_object_id,
                "notified_object_id": document["notified_object_ids"][0],
            }
        ]

    with pytest.raises(ValidationError, match="must not carry a reason"):
        CorrectionResponse.model_validate(document)


@pytest.mark.parametrize("decision", ["reject", "defer"])
def test_reject_or_defer_with_empty_reason_is_rejected(decision: str) -> None:
    document = _base_document(decision=decision, reason="")

    with pytest.raises(ValidationError, match="non-empty reason"):
        CorrectionResponse.model_validate(document)


# ---------------------------------------------------------------------------
# adaptations: required (non-empty) iff adapt; forbidden otherwise.
# ---------------------------------------------------------------------------


def test_adapt_with_one_adaptation_is_accepted() -> None:
    notified_object_id = new_urn("claim")
    adapted_object_id = new_urn("claim")
    document = _base_document(
        decision="adapt",
        notified_object_ids=[notified_object_id],
        adaptations=[
            {"adapted_object_id": adapted_object_id, "notified_object_id": notified_object_id}
        ],
    )
    response = CorrectionResponse.model_validate(document)
    assert len(response.adaptations) == 1


@pytest.mark.parametrize("decision", ["accept", "reject", "defer"])
def test_non_adapt_decision_with_non_empty_adaptations_is_rejected(decision: str) -> None:
    notified_object_id = new_urn("claim")
    adapted_object_id = new_urn("claim")
    document = _base_document(
        decision=decision,
        notified_object_ids=[notified_object_id],
        adaptations=[
            {"adapted_object_id": adapted_object_id, "notified_object_id": notified_object_id}
        ],
    )
    if decision in ("reject", "defer"):
        document["reason"] = "Fixture reason."

    with pytest.raises(ValidationError, match="must not carry any adaptations entries"):
        CorrectionResponse.model_validate(document)


def test_adapt_with_empty_adaptations_is_rejected() -> None:
    document = _base_document(decision="adapt", adaptations=[])

    with pytest.raises(ValidationError, match="at least one adaptations entry"):
        CorrectionResponse.model_validate(document)


# ---------------------------------------------------------------------------
# adaptations[].notified_object_id must be a member of notified_object_ids.
# ---------------------------------------------------------------------------


def test_adaptation_notified_object_id_not_in_notified_object_ids_is_rejected() -> None:
    notified_object_id = new_urn("claim")
    other_object_id = new_urn("claim")  # NOT in notified_object_ids
    adapted_object_id = new_urn("claim")
    document = _base_document(
        decision="adapt",
        notified_object_ids=[notified_object_id],
        adaptations=[
            {"adapted_object_id": adapted_object_id, "notified_object_id": other_object_id}
        ],
    )

    with pytest.raises(ValidationError, match="not a member of this response's own"):
        CorrectionResponse.model_validate(document)
