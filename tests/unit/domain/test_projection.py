"""Unit tests for ``mrr.domain.projection`` (task-packets/E3-T07.yaml),
run entirely DB-free and framework-free — plain mapping fixtures, no
repository, no engine.

Acceptance-test mapping (task-packets/E3-T07.yaml, unit tier):

- "a projection over a small fixture reproduces each claim's latest status,
  evidence/verification ids, and provenance edges, inventing nothing" ->
  ``test_claim_table_row_reflects_latest_status_evidence_and_verification``,
  ``test_claim_table_row_carries_no_unresolved_ids_when_no_correction_matches``.
- "a claim flagged by an unresolved critical correction is shown as flagged;
  once the correction is resolved it is no longer shown as unresolved" ->
  ``test_row_is_flagged_by_an_unresolved_critical_correction_via_affected_objects``,
  ``test_row_is_flagged_by_an_unresolved_critical_correction_via_impact_objects``,
  ``test_row_is_not_flagged_once_the_correction_is_resolved``,
  ``test_row_is_not_flagged_by_a_partially_resolved_correction``.
- non-critical severity and unrelated claims never flag ->
  ``test_material_and_minor_corrections_never_flag_regardless_of_status``,
  ``test_a_correction_naming_a_different_claim_does_not_flag``.
- REJECTED_BY_RECIPIENT stays unresolved (E2E-003's own "recipient autonomy
  is preserved; unresolved public correction is visible" pass criterion) ->
  ``test_rejected_by_recipient_status_still_counts_as_unresolved``.
- the "resolved" set is exactly the two CORRECTION_LIFECYCLE states that
  spell "resolved" -> ``test_resolved_statuses_are_exactly_the_two_named_states``.
- determinism/order-independence of the matched-ids computation ->
  ``test_unresolved_ids_are_sorted_and_deduplicated_regardless_of_input_order``.
"""

from __future__ import annotations

from typing import Any

from mrr.domain.lifecycles import CORRECTION_LIFECYCLE
from mrr.domain.projection import (
    RESOLVED_CORRECTION_STATUSES,
    build_claim_table_row,
    is_unresolved_critical_correction,
    unresolved_critical_correction_ids_for_claim,
)


def _claim_body(*, claim_id: str = "urn:mrr:claim:root", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": claim_id,
        "assertion": "The fixture assertion under test.",
        "status": "supported",
        "evidence_relations": ["urn:mrr:evidence-anchor:one"],
        "verification_ids": ["urn:mrr:verification:one"],
    }
    data.update(overrides)
    return data


def _correction_body(
    *,
    correction_id: str = "urn:mrr:correction:one",
    severity: str = "critical",
    status: str = "OPEN",
    affected_object_ids: list[str] | None = None,
    impact_object_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": correction_id,
        "severity": severity,
        "status": status,
        "affected_objects": [{"id": oid} for oid in (affected_object_ids or [])],
        "impact_objects": list(impact_object_ids or []),
    }


# ---------------------------------------------------------------------------
# is_unresolved_critical_correction / the "resolved" definition itself.
# ---------------------------------------------------------------------------


def test_resolved_statuses_are_exactly_the_two_named_states() -> None:
    """Guards against drift: hardcoded from the spec-derived
    CORRECTION_LIFECYCLE vocabulary itself (not derived from
    mrr.domain.projection), per this codebase's "hardcode the expected list
    in the test" convention (see tests/unit/domain/test_repositories.py).
    """
    assert {"RESOLVED", "PARTIALLY_RESOLVED"} == RESOLVED_CORRECTION_STATUSES
    assert RESOLVED_CORRECTION_STATUSES.issubset(CORRECTION_LIFECYCLE.states)


def test_rejected_by_recipient_status_still_counts_as_unresolved() -> None:
    assert is_unresolved_critical_correction(severity="critical", status="REJECTED_BY_RECIPIENT")


def test_in_flight_statuses_count_as_unresolved() -> None:
    for status in (
        "OPEN",
        "IMPACT_ANALYSIS",
        "NOTIFYING",
        "AWAITING_RESPONSES",
        "DELIVERY_PENDING",
    ):
        assert is_unresolved_critical_correction(severity="critical", status=status), status


def test_resolved_and_partially_resolved_are_not_unresolved() -> None:
    assert not is_unresolved_critical_correction(severity="critical", status="RESOLVED")
    assert not is_unresolved_critical_correction(severity="critical", status="PARTIALLY_RESOLVED")


def test_non_critical_severity_is_never_unresolved_regardless_of_status() -> None:
    for severity in ("minor", "material"):
        for status in ("OPEN", "AWAITING_RESPONSES", "REJECTED_BY_RECIPIENT"):
            assert not is_unresolved_critical_correction(severity=severity, status=status)


# ---------------------------------------------------------------------------
# unresolved_critical_correction_ids_for_claim.
# ---------------------------------------------------------------------------


def test_matches_via_affected_objects() -> None:
    claim_id = "urn:mrr:claim:root"
    correction = _correction_body(affected_object_ids=[claim_id])

    assert unresolved_critical_correction_ids_for_claim(claim_id, [correction]) == (
        correction["id"],
    )


def test_matches_via_impact_objects() -> None:
    claim_id = "urn:mrr:claim:dependent"
    correction = _correction_body(impact_object_ids=[claim_id])

    assert unresolved_critical_correction_ids_for_claim(claim_id, [correction]) == (
        correction["id"],
    )


def test_a_correction_naming_a_different_claim_does_not_flag() -> None:
    correction = _correction_body(affected_object_ids=["urn:mrr:claim:someone-else"])

    assert unresolved_critical_correction_ids_for_claim("urn:mrr:claim:root", [correction]) == ()


def test_material_and_minor_corrections_never_flag_regardless_of_status() -> None:
    claim_id = "urn:mrr:claim:root"
    corrections = [
        _correction_body(
            correction_id="urn:mrr:correction:material",
            severity="material",
            affected_object_ids=[claim_id],
        ),
        _correction_body(
            correction_id="urn:mrr:correction:minor",
            severity="minor",
            affected_object_ids=[claim_id],
        ),
    ]

    assert unresolved_critical_correction_ids_for_claim(claim_id, corrections) == ()


def test_resolved_correction_does_not_flag() -> None:
    claim_id = "urn:mrr:claim:root"
    correction = _correction_body(status="RESOLVED", affected_object_ids=[claim_id])

    assert unresolved_critical_correction_ids_for_claim(claim_id, [correction]) == ()


def test_unresolved_ids_are_sorted_and_deduplicated_regardless_of_input_order() -> None:
    claim_id = "urn:mrr:claim:root"
    correction_a = _correction_body(
        correction_id="urn:mrr:correction:b", affected_object_ids=[claim_id]
    )
    correction_b = _correction_body(
        correction_id="urn:mrr:correction:a", affected_object_ids=[claim_id]
    )

    forward = unresolved_critical_correction_ids_for_claim(claim_id, [correction_a, correction_b])
    backward = unresolved_critical_correction_ids_for_claim(claim_id, [correction_b, correction_a])

    assert forward == backward == ("urn:mrr:correction:a", "urn:mrr:correction:b")


# ---------------------------------------------------------------------------
# build_claim_table_row.
# ---------------------------------------------------------------------------


def test_claim_table_row_reflects_latest_status_evidence_and_verification() -> None:
    claim = _claim_body(status="supported")

    row = build_claim_table_row(claim, [])

    assert row.claim_id == claim["id"]
    assert row.assertion == claim["assertion"]
    assert row.status == "supported"
    assert row.evidence_relations == tuple(claim["evidence_relations"])
    assert row.verification_ids == tuple(claim["verification_ids"])


def test_claim_table_row_carries_no_unresolved_ids_when_no_correction_matches() -> None:
    claim = _claim_body()

    row = build_claim_table_row(
        claim, [_correction_body(affected_object_ids=["urn:mrr:claim:other"])]
    )

    assert row.unresolved_correction_ids == ()
    assert row.flagged is False


def test_row_is_flagged_by_an_unresolved_critical_correction_via_affected_objects() -> None:
    claim = _claim_body()
    correction = _correction_body(affected_object_ids=[claim["id"]])

    row = build_claim_table_row(claim, [correction])

    assert row.flagged is True
    assert row.unresolved_correction_ids == (correction["id"],)


def test_row_is_flagged_by_an_unresolved_critical_correction_via_impact_objects() -> None:
    claim = _claim_body(claim_id="urn:mrr:claim:dependent", status="review_required")
    correction = _correction_body(impact_object_ids=[claim["id"]])

    row = build_claim_table_row(claim, [correction])

    assert row.flagged is True
    assert row.unresolved_correction_ids == (correction["id"],)


def test_row_is_not_flagged_once_the_correction_is_resolved() -> None:
    claim = _claim_body()
    unresolved = _correction_body(
        correction_id="urn:mrr:correction:open", status="OPEN", affected_object_ids=[claim["id"]]
    )
    resolved = _correction_body(
        correction_id="urn:mrr:correction:resolved",
        status="RESOLVED",
        affected_object_ids=[claim["id"]],
    )

    flagged_row = build_claim_table_row(claim, [unresolved])
    unflagged_row = build_claim_table_row(claim, [resolved])

    assert flagged_row.flagged is True
    assert unflagged_row.flagged is False
    assert unflagged_row.unresolved_correction_ids == ()


def test_row_is_not_flagged_by_a_partially_resolved_correction() -> None:
    claim = _claim_body()
    correction = _correction_body(status="PARTIALLY_RESOLVED", affected_object_ids=[claim["id"]])

    row = build_claim_table_row(claim, [correction])

    assert row.flagged is False
