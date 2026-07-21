"""Property tests for ``mrr.domain.public_correction_view`` (task-packets/
E6-T05.yaml).

Acceptance-test mapping: "property - for arbitrary correction/claim bodies
and arbitrary classification maps, redacted is true exactly when at least
one contributing id is not attested literally PUBLIC in the map, and false
otherwise, over a wide generated input space" ->
``test_correction_row_redacted_iff_not_every_contributing_id_is_public``,
``test_claim_row_redacted_iff_not_every_contributing_id_is_public``. Also
covers the "unresolved exactly matches is_unresolved_critical_correction"
invariant (never a second, divergent definition) ->
``test_correction_row_unresolved_matches_the_reused_predicate``.

task-packets/E9-T00.yaml item 6 (PR #50 review follow-up, test-only): the
classification-map strategy is widened beyond the five canonical
``Classification`` literals to also generate ``st.text()`` and explicit
case/whitespace near-misses of ``"PUBLIC"`` (``_NEAR_MISS_PUBLIC_VALUES``),
so the "any non-PUBLIC value never unlocks" property is pinned over a wide
input space, not just the five declared levels. The production predicate
itself (``mrr.domain.public_correction_view._all_ids_attested_public``,
exact ``== "PUBLIC"`` string equality) is unchanged — already correct per
that review.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from mrr.domain.artifacts import Classification
from mrr.domain.projection import ClaimTableRow, is_unresolved_critical_correction
from mrr.domain.public_correction_view import (
    build_public_claim_row,
    build_public_correction_row,
)

#: A small, deliberately overlapping pool of object ids so that
#: hypothesis-generated correction/claim bodies and classification maps
#: often collide (some contributing ids attested, some not), matching
#: tests/property/test_correction_impact_properties.py's own "small,
#: overlapping pool" idiom.
_OBJECT_IDS = tuple(f"urn:mrr:object:{i:026d}" for i in range(6))

_ALL_CLASSIFICATIONS: tuple[Classification, ...] = (
    "PUBLIC",
    "INTERNAL",
    "RESTRICTED",
    "SENSITIVE",
    "PARTICIPANT_IDENTIFIABLE",
)

_SEVERITIES = ("minor", "material", "critical")
_STATUSES = (
    "OPEN",
    "IMPACT_ANALYSIS",
    "NOTIFYING",
    "AWAITING_RESPONSES",
    "DELIVERY_PENDING",
    "RESOLVED",
    "PARTIALLY_RESOLVED",
    "REJECTED_BY_RECIPIENT",
)

_id_strategy = st.sampled_from(_OBJECT_IDS)
_id_list_strategy = st.lists(_id_strategy, max_size=4)

#: task-packets/E9-T00.yaml item 6: explicit case/whitespace near-misses of
#: the literal string "PUBLIC" — each must still redact, since
#: ``_all_ids_attested_public`` uses exact Python ``==`` string equality,
#: never a case-insensitive or whitespace-trimmed comparison.
_NEAR_MISS_PUBLIC_VALUES = ("public", "Public", " PUBLIC", "PUBLIC ", "PUBLIC\n")

#: The widened value strategy for a classification map entry: the five
#: canonical ``Classification`` literals (so the "known-public" case is
#: still exercised), the explicit near-misses above, and arbitrary
#: ``st.text()`` — pinning the "any non-PUBLIC value never unlocks"
#: property over a wide input space, not just the five declared levels.
_classification_value_strategy = st.one_of(
    st.sampled_from(_ALL_CLASSIFICATIONS),
    st.sampled_from(_NEAR_MISS_PUBLIC_VALUES),
    st.text(),
)

#: A classification map covering only a SUBSET of _OBJECT_IDS (never all of
#: them) so that "missing entry" is exercised as often as "known entry" —
#: mirrors the task-packet's own emphasis that a missing key must redact
#: exactly like a known non-public key.
_classification_map_strategy = st.dictionaries(
    keys=_id_strategy,
    values=_classification_value_strategy,
    max_size=len(_OBJECT_IDS),
)


def _correction_body(
    correction_id: str,
    affected_ids: list[str],
    impact_ids: list[str],
    severity: str,
    status: str,
) -> dict[str, Any]:
    return {
        "id": correction_id,
        "correction_type": "other",
        "severity": severity,
        "status": status,
        "reason": "Property-test fixture reason.",
        "requested_action": "Property-test fixture requested action.",
        "affected_objects": [{"id": oid} for oid in affected_ids],
        "impact_objects": list(impact_ids),
    }


_correction_strategy = st.builds(
    _correction_body,
    correction_id=_id_strategy,
    affected_ids=_id_list_strategy,
    impact_ids=_id_list_strategy,
    severity=st.sampled_from(_SEVERITIES),
    status=st.sampled_from(_STATUSES),
)


@given(
    correction=_correction_strategy,
    classification_by_object_id=_classification_map_strategy,
)
def test_correction_row_redacted_iff_not_every_contributing_id_is_public(
    correction: dict[str, Any], classification_by_object_id: dict[str, Classification]
) -> None:
    row = build_public_correction_row(
        correction, classification_by_object_id=classification_by_object_id
    )

    contributing_ids = (
        {correction["id"]}
        | {ref["id"] for ref in correction["affected_objects"]}
        | set(correction["impact_objects"])
    )
    expected_public = all(
        classification_by_object_id.get(object_id) == "PUBLIC" for object_id in contributing_ids
    )

    assert row.redacted == (not expected_public)
    if row.redacted:
        assert row.reason is None
        assert row.requested_action is None
    else:
        assert row.reason == correction["reason"]
        assert row.requested_action == correction["requested_action"]


@given(
    correction=_correction_strategy,
    classification_by_object_id=_classification_map_strategy,
)
def test_correction_row_unresolved_matches_the_reused_predicate(
    correction: dict[str, Any], classification_by_object_id: dict[str, Classification]
) -> None:
    row = build_public_correction_row(
        correction, classification_by_object_id=classification_by_object_id
    )

    assert row.unresolved == is_unresolved_critical_correction(
        severity=correction["severity"], status=correction["status"]
    )


@given(
    correction=_correction_strategy,
    classification_by_object_id=_classification_map_strategy,
)
def test_correction_row_structural_fields_never_depend_on_classification(
    correction: dict[str, Any], classification_by_object_id: dict[str, Classification]
) -> None:
    """The structural fields are always the correction's own stored values,
    regardless of ``redacted`` (MRR-FR-095's own "never omit the structural
    fact" invariant).
    """
    row = build_public_correction_row(
        correction, classification_by_object_id=classification_by_object_id
    )

    assert row.correction_id == correction["id"]
    assert row.severity == correction["severity"]
    assert row.status == correction["status"]
    assert row.correction_type == correction["correction_type"]
    assert row.affected_object_ids == tuple(ref["id"] for ref in correction["affected_objects"])
    assert row.impact_object_ids == tuple(correction["impact_objects"])


def _claim_row(
    claim_id: str, unresolved_correction_ids: tuple[str, ...], status: str
) -> ClaimTableRow:
    return ClaimTableRow(
        claim_id=claim_id,
        assertion="Property-test fixture assertion.",
        status=status,
        evidence_relations=(),
        verification_ids=(),
        unresolved_correction_ids=unresolved_correction_ids,
        flagged=bool(unresolved_correction_ids),
        ceiling_checked=False,
        ceiling_violation=None,
    )


_claim_row_strategy = st.builds(
    _claim_row,
    claim_id=_id_strategy,
    unresolved_correction_ids=st.lists(_id_strategy, max_size=3).map(tuple),
    status=st.sampled_from(("draft", "supported", "review_required")),
)


@given(
    claim_row=_claim_row_strategy,
    classification_by_object_id=_classification_map_strategy,
)
def test_claim_row_redacted_iff_not_every_contributing_id_is_public(
    claim_row: ClaimTableRow, classification_by_object_id: dict[str, Classification]
) -> None:
    row = build_public_claim_row(claim_row, classification_by_object_id=classification_by_object_id)

    contributing_ids = {claim_row.claim_id} | set(claim_row.unresolved_correction_ids)
    expected_public = all(
        classification_by_object_id.get(object_id) == "PUBLIC" for object_id in contributing_ids
    )

    assert row.redacted == (not expected_public)
    if row.redacted:
        assert row.assertion is None
    else:
        assert row.assertion == claim_row.assertion

    # Structural fields never depend on classification.
    assert row.claim_id == claim_row.claim_id
    assert row.status == claim_row.status
    assert row.unresolved_correction_ids == claim_row.unresolved_correction_ids
    assert row.flagged == claim_row.flagged
