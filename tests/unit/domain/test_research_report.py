"""Unit tests for ``mrr.domain.research_report`` (task-packets/E8-T03.yaml),
run entirely DB-free and I/O-free — plain mapping fixtures (the exact shape
``mrr.services.export.service.ExportService.resolve_closure`` hands to
``build_report``), no repository, no filesystem.

Acceptance-test mapping (task-packets/E8-T03.yaml, unit tier):

- R6 "model building from plain bodies" -> every ``test_*`` below that calls
  :func:`build_report` directly on a hand-built ``object_bodies`` mapping.
- R6 "both renderers' determinism (byte-identity across calls and dict
  orders)" -> ``test_render_markdown_is_byte_identical_across_calls``,
  ``test_render_html_is_byte_identical_across_calls``,
  ``test_build_report_does_not_depend_on_object_bodies_dict_order`` (AT4).
- R6 "HTML escaping (a stored string containing markup renders inert)" ->
  ``test_html_escapes_a_stored_script_tag_to_inert_text``.
- R6 "empty-section explicit lines" ->
  ``test_every_section_renders_none_recorded_when_empty``.
- R6 "disagreement marking with two opposing verifications" ->
  ``test_two_disagreeing_verifications_are_both_marked_disagreement_on_record``,
  ``test_a_single_verification_is_never_marked_disagreement``.
- R6 "fail-closed redaction incl. missing-attestation and non-PUBLIC levels"
  -> ``test_public_disclosure_with_empty_attestation_redacts_every_free_text_field``
  (AT2), ``test_each_non_public_classification_level_redacts_identically``.
- R5's two structural tests -> ``test_corrections_section_is_never_omitted``
  (parametrized over format x disclosure x empty/non-empty),
  ``test_rendered_urns_are_a_subset_of_source_urns`` plus
  ``test_the_subset_check_can_fail_control_assertion`` (AT3).
- AT2 (fail-closed granularity per object id) ->
  ``test_public_disclosure_partial_attestation_is_granular_per_object``.
- AT4 (byte-determinism; only date is the crate's own created_at) ->
  ``test_the_only_timestamp_like_token_in_either_render_is_the_crates_created_at``.
- derived_decisions (e) (reviewer confidence label) ->
  ``test_reviewer_confidence_column_is_labeled_self_declared``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from mrr.crypto.canonical import JSONValue
from mrr.domain.projection import ProvenanceEdge
from mrr.domain.public_correction_view import PublicCorrectionRow
from mrr.domain.research_report import build_report, render_html, render_markdown

_CRATE_ID = "urn:mrr:evidence-crate:01AAAAAAAAAAAAAAAAAAAAAAAA"
_RUN_ID = "urn:mrr:run:01AAAAAAAAAAAAAAAAAAAAAAAB"
_PRACTICE_ID = "urn:mrr:practice:01AAAAAAAAAAAAAAAAAAAAAAAC"
_CLAIM_ID = "urn:mrr:claim:01AAAAAAAAAAAAAAAAAAAAAAAD"
_VERIFICATION_ID_1 = "urn:mrr:verification:01AAAAAAAAAAAAAAAAAAAAAAAE"
_VERIFICATION_ID_2 = "urn:mrr:verification:01AAAAAAAAAAAAAAAAAAAAAAAF"
_SOURCE_RECORD_ID = "urn:mrr:source-record:01AAAAAAAAAAAAAAAAAAAAAAAG"
_EVIDENCE_ANCHOR_ID = "urn:mrr:evidence-anchor:01AAAAAAAAAAAAAAAAAAAAAAAH"
_CORRECTION_ID = "urn:mrr:correction:01AAAAAAAAAAAAAAAAAAAAAAAJ"
_CREATED_AT = "2026-07-22T12:00:00Z"


def _crate_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _CRATE_ID,
        "kind": "EvidenceCrate",
        "run_id": _RUN_ID,
        "run_state": "completed",
        "practice_id": _PRACTICE_ID,
        "created_at": _CREATED_AT,
        "content_hash": "sha256:" + "a" * 64,
        "artifacts": [],
        "proposed_claims": [_CLAIM_ID],
        "source_records": [_SOURCE_RECORD_ID],
        "evidence_anchors": [_EVIDENCE_ANCHOR_ID],
        "known_unknowns": [],
        "failures": [],
        "environment": {
            "image_digest": "sha256:" + "b" * 64,
            "code_revision": "git:abc123",
            "input_hashes": [],
        },
    }
    body.update(overrides)
    return body


def _claim_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _CLAIM_ID,
        "kind": "Claim",
        "claim_type": "statistical",
        "scope": {},
        "status": "supported",
        "assertion": "The fixture assertion under test.",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "uncertainty": [],
        "known_unknowns": [],
    }
    body.update(overrides)
    return body


def _verification_body(
    *,
    verification_id: str = _VERIFICATION_ID_1,
    target_id: str = _CLAIM_ID,
    recommendation: str = "pass",
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": verification_id,
        "kind": "VerificationResult",
        "target_id": target_id,
        "reviewer_role": "independent reviewer",
        "recommendation": recommendation,
        "confidence": 0.8,
        "findings": [],
    }
    body.update(overrides)
    return body


def _source_record_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _SOURCE_RECORD_ID,
        "kind": "SourceRecord",
        "title": "Fixture source record",
        "source_type": "journal-article",
        "primary_secondary_derived": "primary",
    }
    body.update(overrides)
    return body


def _evidence_anchor_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _EVIDENCE_ANCHOR_ID,
        "kind": "EvidenceAnchor",
        "relation": "supports",
        "anchor_kind": "text",
        "anchor_validation_status": "validated",
        "anchor_unavailable_reason": None,
    }
    body.update(overrides)
    return body


def _correction_row(**overrides: Any) -> PublicCorrectionRow:
    defaults: dict[str, Any] = {
        "correction_id": _CORRECTION_ID,
        "correction_type": "numeric_error",
        "severity": "critical",
        "status": "OPEN",
        "affected_object_ids": (_CLAIM_ID,),
        "impact_object_ids": (),
        "unresolved": True,
        "reason": "The reported percentage was miscalculated.",
        "requested_action": "Recompute and re-verify.",
        "redacted": False,
    }
    defaults.update(overrides)
    return PublicCorrectionRow(**defaults)


def _minimal_object_bodies(**crate_overrides: Any) -> dict[str, Mapping[str, JSONValue]]:
    return {
        _CRATE_ID: _crate_body(**crate_overrides),
        _CLAIM_ID: _claim_body(),
        _SOURCE_RECORD_ID: _source_record_body(),
        _EVIDENCE_ANCHOR_ID: _evidence_anchor_body(),
    }


def _build(
    *,
    object_bodies: Mapping[str, Mapping[str, JSONValue]] | None = None,
    corrections: list[PublicCorrectionRow] | None = None,
    provenance_by_claim: dict[str, tuple[ProvenanceEdge, ...]] | None = None,
    disclosure: str = "internal",
    classification_by_object_id: Mapping[str, Any] | None = None,
) -> Any:
    return build_report(
        object_bodies=object_bodies if object_bodies is not None else _minimal_object_bodies(),
        crate_id=_CRATE_ID,
        corrections=corrections if corrections is not None else [],
        provenance_by_claim=provenance_by_claim if provenance_by_claim is not None else {},
        disclosure=disclosure,  # type: ignore[arg-type]
        classification_by_object_id=classification_by_object_id or {},
    )


# ---------------------------------------------------------------------------
# Model building from plain bodies.
# ---------------------------------------------------------------------------


def test_header_fields_come_from_crate_body_verbatim() -> None:
    model = _build()
    assert model.header.crate_urn == _CRATE_ID
    assert model.header.run_urn == _RUN_ID
    assert model.header.run_state == "completed"
    assert model.header.practice_id == _PRACTICE_ID
    assert model.header.created_at == _CREATED_AT
    assert model.header.content_hash == "sha256:" + "a" * 64
    assert model.header.object_count == len(_minimal_object_bodies())
    assert model.header.artifact_count == 0


def test_methods_section_reports_run_manifest_not_in_closure_honestly() -> None:
    model = _build()
    assert model.methods.run_manifest_included is False
    assert model.methods.declared_parameters is None


def test_methods_section_reads_declared_parameters_when_run_manifest_resolved() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_RUN_ID] = {
        "id": _RUN_ID,
        "kind": "RunManifest",
        "parameters": {"operation": "percentage", "numerator": 1, "denominator": 2},
    }
    model = _build(object_bodies=object_bodies)
    assert model.methods.run_manifest_included is True
    assert model.methods.declared_parameters == {
        "operation": "percentage",
        "numerator": 1,
        "denominator": 2,
    }


def test_methods_environment_block_is_read_verbatim() -> None:
    object_bodies = _minimal_object_bodies(
        environment={
            "image_digest": "sha256:" + "c" * 64,
            "code_revision": "git:deadbeef",
            "input_hashes": ["sha256:" + "d" * 64],
            "model_profiles": ["profile-one"],
        }
    )
    model = _build(object_bodies=object_bodies)
    assert model.methods.environment_image_digest == "sha256:" + "c" * 64
    assert model.methods.environment_code_revision == "git:deadbeef"
    assert model.methods.environment_input_hashes == ("sha256:" + "d" * 64,)
    assert model.methods.environment_model_profiles == ("profile-one",)


def test_methods_artifact_refs_come_from_crate_artifacts_array() -> None:
    object_bodies = _minimal_object_bodies(
        artifacts=[
            {"artifact_id": "urn:mrr:artifact:b", "content_hash": "sha256:" + "2" * 64},
            {"artifact_id": "urn:mrr:artifact:a", "content_hash": "sha256:" + "1" * 64},
        ]
    )
    model = _build(object_bodies=object_bodies)
    assert [row.artifact_id for row in model.methods.artifact_refs] == [
        "urn:mrr:artifact:a",
        "urn:mrr:artifact:b",
    ]


def test_claim_row_carries_type_scope_uncertainty_relations_and_dependencies() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_CLAIM_ID] = _claim_body(
        claim_type="causal",
        scope={"population": "adults", "conditions": ["x"]},
        uncertainty=[{"kind": "sampling", "statement": "small n", "method": "bootstrap"}],
        evidence_relations=["urn:mrr:evidence-anchor:e1"],
        counterevidence_relations=["urn:mrr:evidence-anchor:e2"],
        dependencies=["urn:mrr:claim:dep1"],
    )
    model = _build(object_bodies=object_bodies)
    (claim,) = model.claims
    assert claim.claim_type == "causal"
    assert claim.scope == {"population": "adults", "conditions": ["x"]}
    assert claim.uncertainty[0].kind == "sampling"
    assert claim.uncertainty[0].statement == "small n"
    assert claim.uncertainty[0].method == "bootstrap"
    assert claim.evidence_relations == ("urn:mrr:evidence-anchor:e1",)
    assert claim.counterevidence_relations == ("urn:mrr:evidence-anchor:e2",)
    assert claim.dependencies == ("urn:mrr:claim:dep1",)


def test_evidence_map_lists_source_records_and_anchors_sorted() -> None:
    object_bodies = _minimal_object_bodies()
    model = _build(object_bodies=object_bodies)
    assert [row.source_record_id for row in model.evidence_map.source_records] == [
        _SOURCE_RECORD_ID
    ]
    assert [row.evidence_anchor_id for row in model.evidence_map.evidence_anchors] == [
        _EVIDENCE_ANCHOR_ID
    ]
    assert model.evidence_map.evidence_anchors[0].anchor_validation_status == "validated"


def test_provenance_summary_reflects_the_supplied_provenance_map_per_claim() -> None:
    edge = ProvenanceEdge(
        source_id=_CLAIM_ID,
        target_id=_EVIDENCE_ANCHOR_ID,
        target_kind="EvidenceAnchor",
        relation="supports",
        via="edge",
        edge_id="edge-1",
    )
    model = _build(provenance_by_claim={_CLAIM_ID: (edge,)})
    (row,) = model.provenance_summary
    assert row.claim_id == _CLAIM_ID
    assert row.edge_count == 1
    assert row.edges[0].target_id == _EVIDENCE_ANCHOR_ID
    assert row.edges[0].relation == "supports"
    assert row.edges[0].via == "edge"


def test_a_claim_with_no_provenance_renders_zero_never_an_error() -> None:
    model = _build(provenance_by_claim={})
    (row,) = model.provenance_summary
    assert row.edge_count == 0
    assert row.edges == ()


# ---------------------------------------------------------------------------
# Disagreement marking.
# ---------------------------------------------------------------------------


def test_two_disagreeing_verifications_are_both_marked_disagreement_on_record() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(recommendation="pass")
    object_bodies[_VERIFICATION_ID_2] = _verification_body(
        verification_id=_VERIFICATION_ID_2, recommendation="fail"
    )
    model = _build(object_bodies=object_bodies)
    (claim,) = model.claims
    assert len(claim.verifications) == 2
    assert all(v.disagreement_on_record for v in claim.verifications)
    recommendations = {v.recommendation for v in claim.verifications}
    assert recommendations == {"pass", "fail"}


def test_a_single_verification_is_never_marked_disagreement() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(recommendation="pass")
    model = _build(object_bodies=object_bodies)
    (claim,) = model.claims
    (verification,) = claim.verifications
    assert verification.disagreement_on_record is False


def test_agreeing_verifications_are_not_marked_disagreement() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(recommendation="pass")
    object_bodies[_VERIFICATION_ID_2] = _verification_body(
        verification_id=_VERIFICATION_ID_2, recommendation="pass"
    )
    model = _build(object_bodies=object_bodies)
    (claim,) = model.claims
    assert all(not v.disagreement_on_record for v in claim.verifications)


def test_verifications_targeting_a_different_claim_are_excluded() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(target_id="urn:mrr:claim:other")
    model = _build(object_bodies=object_bodies)
    (claim,) = model.claims
    assert claim.verifications == ()


# ---------------------------------------------------------------------------
# Fail-closed redaction (public disclosure).
# ---------------------------------------------------------------------------


def test_internal_disclosure_never_redacts_anything() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(
        findings=[{"severity": "critical", "statement": "a real finding"}]
    )
    model = _build(
        object_bodies=object_bodies,
        corrections=[_correction_row(reason="real reason", requested_action="real action")],
        disclosure="internal",
        classification_by_object_id={},
    )
    (claim,) = model.claims
    assert claim.assertion == "The fixture assertion under test."
    assert claim.assertion_redacted is False
    (verification,) = claim.verifications
    assert verification.findings[0].statement == "a real finding"
    assert verification.findings[0].redacted is False
    assert model.corrections[0].reason == "real reason"
    assert model.corrections[0].redacted is False


def test_public_disclosure_with_empty_attestation_redacts_every_free_text_field() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(
        findings=[{"severity": "critical", "statement": "SECRET finding"}]
    )
    object_bodies[_CRATE_ID] = _crate_body(
        known_unknowns=["SECRET crate unknown"],
        failures=[{"code": "x", "category": "unknown", "message": "SECRET failure"}],
    )
    object_bodies[_CLAIM_ID] = _claim_body(known_unknowns=["SECRET claim unknown"])

    model = _build(
        object_bodies=object_bodies,
        corrections=[_correction_row(reason=None, requested_action=None, redacted=True)],
        disclosure="public",
        classification_by_object_id={},
    )
    (claim,) = model.claims
    assert claim.assertion is None
    assert claim.assertion_redacted is True
    (verification,) = claim.verifications
    assert verification.findings[0].redacted is True
    assert "SECRET" not in verification.findings[0].statement
    assert model.corrections[0].reason is None
    assert model.corrections[0].requested_action is None
    assert model.failures[0].redacted is True
    assert "SECRET" not in model.failures[0].message
    assert model.known_unknowns.crate_known_unknowns[0].redacted is True
    assert "SECRET" not in model.known_unknowns.crate_known_unknowns[0].text
    assert model.known_unknowns.per_claim[0].known_unknowns[0].redacted is True

    # Structural facts are never withheld, even under empty attestation.
    assert claim.claim_id == _CLAIM_ID
    assert claim.status == "supported"
    assert model.corrections[0].correction_id == _CORRECTION_ID
    assert model.corrections[0].status == "OPEN"
    assert model.corrections[0].unresolved is True


def test_public_disclosure_partial_attestation_is_granular_per_object() -> None:
    """AT2: the claim attested PUBLIC shows its assertion while an
    un-attested finding statement on the SAME claim's verification stays
    redacted — fail-closed granularity per object id.
    """
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body(
        findings=[{"severity": "minor", "statement": "un-attested finding"}]
    )
    model = _build(
        object_bodies=object_bodies,
        disclosure="public",
        classification_by_object_id={_CLAIM_ID: "PUBLIC"},
    )
    (claim,) = model.claims
    assert claim.assertion == "The fixture assertion under test."
    assert claim.assertion_redacted is False
    (verification,) = claim.verifications
    assert verification.findings[0].redacted is True


def test_each_non_public_classification_level_redacts_identically() -> None:
    for level in ("INTERNAL", "RESTRICTED", "SENSITIVE", "PARTICIPANT_IDENTIFIABLE"):
        model = _build(
            disclosure="public",
            classification_by_object_id={_CLAIM_ID: level},
        )
        (claim,) = model.claims
        assert claim.assertion_redacted is True, f"classification {level} must still redact"


def test_uncertainty_statements_are_never_gated_even_under_public_empty_attestation() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_CLAIM_ID] = _claim_body(
        uncertainty=[{"kind": "sampling", "statement": "visible regardless", "method": None}]
    )
    model = _build(object_bodies=object_bodies, disclosure="public", classification_by_object_id={})
    (claim,) = model.claims
    assert claim.uncertainty[0].statement == "visible regardless"


def test_unresolved_critical_correction_existence_and_status_always_shown() -> None:
    """MRR-FR-095: existence + status shown in BOTH disclosures regardless
    of attestation."""
    for disclosure in ("internal", "public"):
        model = _build(
            corrections=[_correction_row(reason=None, requested_action=None, redacted=True)],
            disclosure=disclosure,
            classification_by_object_id={},
        )
        assert len(model.corrections) == 1
        assert model.corrections[0].correction_id == _CORRECTION_ID
        assert model.corrections[0].status == "OPEN"
        assert model.corrections[0].unresolved is True


# ---------------------------------------------------------------------------
# R5: structural MRR-FR-104 tests.
# ---------------------------------------------------------------------------


def test_corrections_section_is_never_omitted() -> None:
    """The corrections section header ALWAYS appears, in every format and
    disclosure, whether or not any correction exists — MRR-FR-104's "MUST
    NOT omit" made structural.
    """
    for disclosure in ("internal", "public"):
        for corrections in ([], [_correction_row()]):
            model = _build(
                corrections=corrections,
                disclosure=disclosure,
                classification_by_object_id={_CLAIM_ID: "PUBLIC", _CORRECTION_ID: "PUBLIC"},
            )
            md = render_markdown(model)
            html = render_html(model)
            assert "## 5. Corrections" in md
            assert "<h2>5. Corrections</h2>" in html


def _all_urn_like_tokens(text: str) -> set[str]:
    return set(re.findall(r"urn:mrr:[a-z0-9-]+:[0-9A-HJKMNP-TV-Z]{1,26}", text))


def test_rendered_urns_are_a_subset_of_source_urns() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body()
    model = _build(
        object_bodies=object_bodies,
        corrections=[_correction_row()],
        provenance_by_claim={
            _CLAIM_ID: (
                ProvenanceEdge(
                    source_id=_CLAIM_ID,
                    target_id=_EVIDENCE_ANCHOR_ID,
                    target_kind="EvidenceAnchor",
                    relation="supports",
                    via="edge",
                    edge_id="edge-1",
                ),
            )
        },
    )
    md = render_markdown(model)
    html = render_html(model)
    for rendered in (md, html):
        rendered_urns = _all_urn_like_tokens(rendered)
        assert rendered_urns <= model.source_urns, (
            f"rendered urns not in source_urns: {rendered_urns - model.source_urns}"
        )


def test_the_subset_check_can_fail_control_assertion() -> None:
    """R5/AT3's own control assertion: the mechanical subset check above is
    not vacuous — injecting a foreign urn into a COPY of the rendered text
    makes it fail, proving the check actually discriminates.
    """
    model = _build()
    md = render_markdown(model)
    foreign_urn = "urn:mrr:claim:01ZZZZZZZZZZZZZZZZZZZZZZZZ"
    assert foreign_urn not in model.source_urns
    tampered = md + f"\ninjected citation: {foreign_urn}\n"
    rendered_urns = _all_urn_like_tokens(tampered)
    assert not (rendered_urns <= model.source_urns), (
        "control assertion failed: the subset check did not catch an injected foreign urn"
    )


# ---------------------------------------------------------------------------
# Determinism (AT4) and escaping.
# ---------------------------------------------------------------------------


def test_render_markdown_is_byte_identical_across_calls() -> None:
    model = _build(corrections=[_correction_row()])
    assert render_markdown(model) == render_markdown(model)


def test_render_html_is_byte_identical_across_calls() -> None:
    model = _build(corrections=[_correction_row()])
    assert render_html(model) == render_html(model)


def test_build_report_does_not_depend_on_object_bodies_dict_order() -> None:
    bodies = _minimal_object_bodies()
    reordered = dict(reversed(list(bodies.items())))
    assert list(bodies) != list(reordered)  # sanity: order actually differs

    model_a = _build(object_bodies=bodies)
    model_b = _build(object_bodies=reordered)
    assert render_markdown(model_a) == render_markdown(model_b)
    assert render_html(model_a) == render_html(model_b)


def test_html_escapes_a_stored_script_tag_to_inert_text() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_CLAIM_ID] = _claim_body(assertion='<script>alert("x")</script>')
    model = _build(object_bodies=object_bodies)
    html = render_html(model)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_every_section_renders_none_recorded_when_empty() -> None:
    empty_object_bodies: dict[str, Mapping[str, JSONValue]] = {
        _CRATE_ID: _crate_body(proposed_claims=[], source_records=[], evidence_anchors=[])
    }
    model = _build(object_bodies=empty_object_bodies)
    md = render_markdown(model)
    html = render_html(model)
    assert md.count("(none recorded)") >= 6
    assert html.count("(none recorded)") >= 6


def test_the_only_timestamp_like_token_in_either_render_is_the_crates_created_at() -> None:
    model = _build()
    timestamp_pattern = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
    for rendered in (render_markdown(model), render_html(model)):
        matches = set(timestamp_pattern.findall(rendered))
        assert matches <= {_CREATED_AT[:19]}, f"unexpected timestamp-like token(s): {matches}"


def test_reviewer_confidence_column_is_labeled_self_declared() -> None:
    object_bodies = _minimal_object_bodies()
    object_bodies[_VERIFICATION_ID_1] = _verification_body()
    model = _build(object_bodies=object_bodies)
    md = render_markdown(model)
    html = render_html(model)
    assert "reviewer confidence (self-declared)" in md.lower()
    assert "reviewer confidence (self-declared)" in html.lower()


def test_build_report_raises_if_crate_id_is_not_an_evidence_crate() -> None:
    object_bodies: dict[str, Mapping[str, JSONValue]] = {_CRATE_ID: _claim_body(id=_CRATE_ID)}
    try:
        build_report(
            object_bodies=object_bodies,
            crate_id=_CRATE_ID,
            corrections=[],
            provenance_by_claim={},
            disclosure="internal",
            classification_by_object_id={},
        )
    except ValueError as exc:
        assert _CRATE_ID in str(exc)
    else:
        raise AssertionError("expected ValueError for a non-EvidenceCrate crate_id")


# ---------------------------------------------------------------------------
# Drift guard: the restated fail-closed formula vs. the E6-T05 original.
# ---------------------------------------------------------------------------


def test_restated_fail_closed_formula_never_drifts_from_public_correction_view() -> None:
    """``mrr.domain.research_report._all_attested_public`` deliberately
    RESTATES (docstring: "restated rather than imported since that name is
    module-private") the fail-closed formula of ``mrr.domain
    .public_correction_view._all_ids_attested_public`` for the three
    free-text categories that module never covered. This test pins the two
    to each other over the full attestation matrix — if either is ever
    edited without the other, it fails here first, not in a silently
    diverging public render. Reviewer-added guard (E8-T03 review,
    2026-07-22): private-name imports are the point, not an accident —
    the PUBLIC APIs cannot express the comparison this narrowly.
    """
    from mrr.domain.public_correction_view import _all_ids_attested_public
    from mrr.domain.research_report import _all_attested_public

    claim_a = "urn:mrr:claim:01ARZ3NDEKTSV4RRFFQ69G5FAV"
    claim_b = "urn:mrr:claim:01BX5ZZKBKACTAV9WEVGEMMVRZ"
    attestation_cases: list[dict[str, str]] = [
        {},
        {claim_a: "PUBLIC"},
        {claim_a: "PUBLIC", claim_b: "PUBLIC"},
        {claim_a: "PUBLIC", claim_b: "INTERNAL"},
        {claim_a: "INTERNAL"},
        {claim_a: "RESTRICTED"},
        {claim_a: "SENSITIVE"},
        {claim_a: "PARTICIPANT_IDENTIFIABLE"},
        {claim_a: "public"},  # case-sensitive: not the literal "PUBLIC"
        {claim_a: "UNRECOGNIZED-LEVEL"},
    ]
    id_sets: list[tuple[str, ...]] = [(), (claim_a,), (claim_b,), (claim_a, claim_b)]

    for attestation in attestation_cases:
        for ids in id_sets:
            assert _all_attested_public(ids, attestation) == _all_ids_attested_public(  # type: ignore[arg-type]
                ids,
                attestation,  # type: ignore[arg-type]
            ), f"formula drift for ids={ids} attestation={attestation}"
