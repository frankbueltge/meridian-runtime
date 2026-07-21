"""Property test for ``CorrectionResponse`` (task-packets/E6-T04.yaml).

Acceptance-test mapping: "property test — for arbitrary decision/reason/
adaptations triples, contract construction succeeds if and only if reason is
present and non-empty exactly when decision is reject or defer, and
adaptations is non-empty exactly when decision is adapt, and every
adaptation's notified_object_id is a declared notified_object_ids member;
construction never partially succeeds" -> ``test_construction_succeeds_iff_
decision_reason_and_adaptations_are_consistent``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from mrr.contracts.correction_response import CorrectionResponse
from pydantic import ValidationError

#: A small, fixed pool of notified-object URNs so hypothesis-generated
#: adaptations sometimes reference a genuine member of notified_object_ids
#: and sometimes do not — exercising both sides of the referential
#: consistency check.
_NOTIFIED_OBJECT_IDS = tuple(f"urn:mrr:claim:{i:026d}" for i in range(3))
_ADAPTED_OBJECT_IDS = tuple(f"urn:mrr:claim:{100 + i:026d}" for i in range(3))

_DECISIONS = ("accept", "adapt", "reject", "defer")

_BASE_DOCUMENT: dict[str, Any] = {
    "id": "urn:mrr:correction-response:00000000000000000000000001",
    "api_version": "mrr/v1alpha1",
    "kind": "CorrectionResponse",
    "practice_id": "urn:mrr:practice:00000000000000000000000002",
    "revision": 1,
    "created_at": datetime(2026, 7, 19, 12, 4, 0, tzinfo=UTC),
    "created_by": "urn:mrr:agent-role:00000000000000000000000003",
    "content_hash": "sha256:" + "4" * 64,
    "correction_notification_id": "urn:mrr:correction-notification:00000000000000000000000004",
    "notifying_practice_id": "urn:mrr:practice:00000000000000000000000005",
    "origin_correction_event_id": "urn:mrr:correction:00000000000000000000000006",
    "origin_correction_event_revision": 1,
    "notified_object_ids": list(_NOTIFIED_OBJECT_IDS),
}

_reason_strategy = st.one_of(st.none(), st.just(""), st.text(min_size=1, max_size=20))

_adaptation_strategy = st.builds(
    lambda adapted_object_id, notified_object_id: {
        "adapted_object_id": adapted_object_id,
        "notified_object_id": notified_object_id,
    },
    adapted_object_id=st.sampled_from(_ADAPTED_OBJECT_IDS),
    # Sometimes a genuine member of notified_object_ids, sometimes a
    # completely unrelated id (exercising the "not a member" rejection).
    notified_object_id=st.one_of(
        st.sampled_from(_NOTIFIED_OBJECT_IDS),
        st.just("urn:mrr:claim:09999999999999999999999999"),
    ),
)

_adaptations_strategy = st.lists(_adaptation_strategy, max_size=3)


def _expected_to_succeed(
    decision: str, reason: str | None, adaptations: list[dict[str, str]]
) -> bool:
    if decision in ("reject", "defer"):
        if not reason:
            return False
    elif reason is not None:
        return False

    if decision == "adapt":
        if not adaptations:
            return False
    elif adaptations:
        return False

    notified = set(_NOTIFIED_OBJECT_IDS)
    return all(entry["notified_object_id"] in notified for entry in adaptations)


@given(
    decision=st.sampled_from(_DECISIONS),
    reason=_reason_strategy,
    adaptations=_adaptations_strategy,
)
def test_construction_succeeds_iff_decision_reason_and_adaptations_are_consistent(
    decision: str, reason: str | None, adaptations: list[dict[str, str]]
) -> None:
    document = dict(_BASE_DOCUMENT)
    document["decision"] = decision
    document["adaptations"] = adaptations
    if reason is not None:
        document["reason"] = reason

    expected_success = _expected_to_succeed(decision, reason, adaptations)

    if expected_success:
        response = CorrectionResponse.model_validate(document)
        assert response.decision == decision
        assert len(response.adaptations) == len(adaptations)
    else:
        try:
            CorrectionResponse.model_validate(document)
        except ValidationError:
            pass
        else:
            raise AssertionError(
                f"expected construction to fail for decision={decision!r} "
                f"reason={reason!r} adaptations={adaptations!r}, but it succeeded"
            )
