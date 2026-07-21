"""Unit tests for ``mrr.domain.public_correction_view`` (task-packets/
E6-T05.yaml), run entirely DB-free and framework-free — plain mapping/
``ClaimTableRow`` fixtures, no repository, no engine.

Acceptance-test mapping (task-packets/E6-T05.yaml, unit tier):

- "fully attested happy path" ->
  ``test_fully_attested_correction_shows_reason_and_requested_action``.
- "no attestation at all ... the correction is never dropped from the list"
  (structural-fact visibility, exercised directly on the row builder here;
  "never dropped from the list" is exercised at the service level in
  tests/unit/services/projection/test_service.py) ->
  ``test_no_attestation_still_reports_every_structural_field_but_redacts_text``.
- "partial attestation fails closed" ->
  ``test_partial_attestation_of_affected_objects_still_redacts``,
  ``test_partial_attestation_of_impact_objects_still_redacts``.
- "a non-PUBLIC known classification does not unlock text" ->
  ``test_each_non_public_known_classification_redacts_identically``.
- "REJECTED_BY_RECIPIENT stays visible as unresolved" ->
  ``test_rejected_by_recipient_is_still_unresolved``.
- "RESOLVED and PARTIALLY_RESOLVED are not unresolved" ->
  ``test_resolved_and_partially_resolved_are_not_unresolved``.
- "severity below critical is never flagged unresolved" ->
  ``test_non_critical_severity_is_never_unresolved``.
- "a Claim's assertion redaction mirrors the correction rule" ->
  ``test_claim_row_shows_assertion_only_when_own_id_and_flagging_corrections_are_public``,
  ``test_claim_row_redacts_when_own_id_is_public_but_flagging_correction_is_not``,
  ``test_claim_row_redacts_when_flagging_correction_is_public_but_own_id_is_not``,
  ``test_unflagged_claim_row_only_needs_its_own_id_attested``.
- determinism -> ``test_build_public_correction_row_is_deterministic``,
  ``test_build_public_claim_row_is_deterministic``.
"""

from __future__ import annotations

from typing import Any

from mrr.domain.artifacts import Classification
from mrr.domain.projection import ClaimTableRow
from mrr.domain.public_correction_view import (
    build_public_claim_row,
    build_public_correction_row,
)

_CORRECTION_ID = "urn:mrr:correction:one"
_AFFECTED_ID = "urn:mrr:claim:affected"
_IMPACT_ID = "urn:mrr:claim:impacted"


def _correction_body(
    *,
    correction_id: str = _CORRECTION_ID,
    severity: str = "critical",
    status: str = "OPEN",
    affected_object_ids: list[str] | None = None,
    impact_object_ids: list[str] | None = None,
    reason: str = "The cited dataset was later withdrawn by its publisher.",
    requested_action: str = "Mark dependent claims review_required.",
) -> dict[str, Any]:
    return {
        "id": correction_id,
        "correction_type": "source_invalidated",
        "severity": severity,
        "status": status,
        "reason": reason,
        "requested_action": requested_action,
        "affected_objects": [{"id": oid} for oid in (affected_object_ids or [_AFFECTED_ID])],
        "impact_objects": list(impact_object_ids or []),
    }


def _claim_table_row(
    *,
    claim_id: str = "urn:mrr:claim:root",
    assertion: str = "The fixture assertion under test.",
    status: str = "supported",
    unresolved_correction_ids: tuple[str, ...] = (),
    flagged: bool | None = None,
) -> ClaimTableRow:
    return ClaimTableRow(
        claim_id=claim_id,
        assertion=assertion,
        status=status,
        evidence_relations=(),
        verification_ids=(),
        unresolved_correction_ids=unresolved_correction_ids,
        flagged=bool(unresolved_correction_ids) if flagged is None else flagged,
        ceiling_checked=False,
        ceiling_violation=None,
    )


# ---------------------------------------------------------------------------
# build_public_correction_row — redaction.
# ---------------------------------------------------------------------------


def test_fully_attested_correction_shows_reason_and_requested_action() -> None:
    correction = _correction_body(
        affected_object_ids=[_AFFECTED_ID], impact_object_ids=[_IMPACT_ID]
    )
    attestation: dict[str, Classification] = {
        _CORRECTION_ID: "PUBLIC",
        _AFFECTED_ID: "PUBLIC",
        _IMPACT_ID: "PUBLIC",
    }

    row = build_public_correction_row(correction, classification_by_object_id=attestation)

    assert row.redacted is False
    assert row.reason == correction["reason"]
    assert row.requested_action == correction["requested_action"]


def test_no_attestation_still_reports_every_structural_field_but_redacts_text() -> None:
    correction = _correction_body(
        affected_object_ids=[_AFFECTED_ID], impact_object_ids=[_IMPACT_ID]
    )

    row = build_public_correction_row(correction, classification_by_object_id={})

    assert row.correction_id == correction["id"]
    assert row.severity == correction["severity"]
    assert row.status == correction["status"]
    assert row.correction_type == correction["correction_type"]
    assert row.affected_object_ids == (_AFFECTED_ID,)
    assert row.impact_object_ids == (_IMPACT_ID,)
    assert row.unresolved is True
    assert row.redacted is True
    assert row.reason is None
    assert row.requested_action is None


def test_partial_attestation_of_affected_objects_still_redacts() -> None:
    correction = _correction_body(affected_object_ids=[_AFFECTED_ID])
    attestation: dict[str, Classification] = {
        _CORRECTION_ID: "PUBLIC",
        _AFFECTED_ID: "INTERNAL",
    }

    row = build_public_correction_row(correction, classification_by_object_id=attestation)

    assert row.redacted is True
    assert row.reason is None
    assert row.requested_action is None


def test_partial_attestation_of_impact_objects_still_redacts() -> None:
    correction = _correction_body(
        affected_object_ids=[_AFFECTED_ID], impact_object_ids=[_IMPACT_ID]
    )
    attestation: dict[str, Classification] = {
        _CORRECTION_ID: "PUBLIC",
        _AFFECTED_ID: "PUBLIC",
        # _IMPACT_ID deliberately missing.
    }

    row = build_public_correction_row(correction, classification_by_object_id=attestation)

    assert row.redacted is True
    assert row.reason is None


def test_each_non_public_known_classification_redacts_identically() -> None:
    non_public_levels: tuple[Classification, ...] = (
        "INTERNAL",
        "RESTRICTED",
        "SENSITIVE",
        "PARTICIPANT_IDENTIFIABLE",
    )
    correction = _correction_body(affected_object_ids=[_AFFECTED_ID])

    for level in non_public_levels:
        attestation: dict[str, Classification] = {_CORRECTION_ID: level, _AFFECTED_ID: level}
        row = build_public_correction_row(correction, classification_by_object_id=attestation)
        assert row.redacted is True, level
        assert row.reason is None, level
        assert row.requested_action is None, level


def test_an_unrecognized_classification_string_redacts_identically() -> None:
    """A value that is not even one of the five declared Classification
    levels (a typo, or arbitrary caller input) redacts exactly the same as
    a missing entry or a known non-public level — the fail-closed check is
    plain equality against the literal "PUBLIC", so this needs no special
    case (see the module docstring).
    """
    correction = _correction_body(affected_object_ids=[_AFFECTED_ID])
    attestation = {
        _CORRECTION_ID: "not-a-real-classification-level",
        _AFFECTED_ID: "not-a-real-classification-level",
    }

    row = build_public_correction_row(
        correction,
        classification_by_object_id=attestation,  # type: ignore[arg-type]
    )

    assert row.redacted is True
    assert row.reason is None
    assert row.requested_action is None


# ---------------------------------------------------------------------------
# build_public_correction_row — unresolved-ness (mirrors
# mrr.domain.projection's own is_unresolved_critical_correction tests).
# ---------------------------------------------------------------------------


def test_rejected_by_recipient_is_still_unresolved() -> None:
    correction = _correction_body(status="REJECTED_BY_RECIPIENT")

    row = build_public_correction_row(correction, classification_by_object_id={})

    assert row.unresolved is True


def test_resolved_and_partially_resolved_are_not_unresolved() -> None:
    for status in ("RESOLVED", "PARTIALLY_RESOLVED"):
        correction = _correction_body(status=status)
        row = build_public_correction_row(correction, classification_by_object_id={})
        assert row.unresolved is False, status


def test_non_critical_severity_is_never_unresolved() -> None:
    for severity in ("minor", "material"):
        correction = _correction_body(severity=severity, status="OPEN")
        row = build_public_correction_row(correction, classification_by_object_id={})
        assert row.unresolved is False, severity


# ---------------------------------------------------------------------------
# build_public_claim_row — redaction mirrors the correction rule.
# ---------------------------------------------------------------------------


def test_claim_row_shows_assertion_only_when_own_id_and_flagging_corrections_are_public() -> None:
    claim_row = _claim_table_row(unresolved_correction_ids=(_CORRECTION_ID,))
    attestation: dict[str, Classification] = {
        claim_row.claim_id: "PUBLIC",
        _CORRECTION_ID: "PUBLIC",
    }

    row = build_public_claim_row(claim_row, classification_by_object_id=attestation)

    assert row.redacted is False
    assert row.assertion == claim_row.assertion
    assert row.flagged is True
    assert row.unresolved_correction_ids == (_CORRECTION_ID,)


def test_claim_row_redacts_when_own_id_is_public_but_flagging_correction_is_not() -> None:
    claim_row = _claim_table_row(unresolved_correction_ids=(_CORRECTION_ID,))
    attestation: dict[str, Classification] = {claim_row.claim_id: "PUBLIC"}

    row = build_public_claim_row(claim_row, classification_by_object_id=attestation)

    assert row.redacted is True
    assert row.assertion is None


def test_claim_row_redacts_when_flagging_correction_is_public_but_own_id_is_not() -> None:
    claim_row = _claim_table_row(unresolved_correction_ids=(_CORRECTION_ID,))
    attestation: dict[str, Classification] = {_CORRECTION_ID: "PUBLIC"}

    row = build_public_claim_row(claim_row, classification_by_object_id=attestation)

    assert row.redacted is True
    assert row.assertion is None


def test_unflagged_claim_row_only_needs_its_own_id_attested() -> None:
    claim_row = _claim_table_row(unresolved_correction_ids=())
    attestation: dict[str, Classification] = {claim_row.claim_id: "PUBLIC"}

    row = build_public_claim_row(claim_row, classification_by_object_id=attestation)

    assert row.redacted is False
    assert row.assertion == claim_row.assertion
    assert row.flagged is False


def test_unflagged_claim_row_still_redacts_with_no_attestation() -> None:
    claim_row = _claim_table_row(unresolved_correction_ids=())

    row = build_public_claim_row(claim_row, classification_by_object_id={})

    assert row.redacted is True
    assert row.assertion is None
    assert row.claim_id == claim_row.claim_id
    assert row.status == claim_row.status


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_build_public_correction_row_is_deterministic() -> None:
    correction = _correction_body(affected_object_ids=[_AFFECTED_ID])
    attestation: dict[str, Classification] = {_CORRECTION_ID: "PUBLIC", _AFFECTED_ID: "PUBLIC"}

    first = build_public_correction_row(correction, classification_by_object_id=attestation)
    second = build_public_correction_row(correction, classification_by_object_id=attestation)

    assert first == second


def test_build_public_claim_row_is_deterministic() -> None:
    claim_row = _claim_table_row(unresolved_correction_ids=(_CORRECTION_ID,))
    attestation: dict[str, Classification] = {
        claim_row.claim_id: "PUBLIC",
        _CORRECTION_ID: "PUBLIC",
    }

    first = build_public_claim_row(claim_row, classification_by_object_id=attestation)
    second = build_public_claim_row(claim_row, classification_by_object_id=attestation)

    assert first == second
