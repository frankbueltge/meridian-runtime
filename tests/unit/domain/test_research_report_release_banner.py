"""Unit tests for ``mrr.domain.research_report``'s OPTIONAL E8-T05 R3
release-status banner — run entirely DB-free/I/O-free, mirroring
``tests/unit/domain/test_research_report.py``'s own "hand-built
object_bodies mapping" discipline (a NEW file, not an edit of that E8-T03
file, which must pass unmodified per task-packets/E8-T05.yaml).

Acceptance-test mapping (task-packets/E8-T05.yaml):

- AT3's own absence-regression half ("rendered WITHOUT release context it
  is byte-identical to the E8-T03 output for the same graph") ->
  ``test_absent_release_banner_is_byte_identical_regression``.
- AT3's own presence half ("carries the banner block first ... correction
  free text gated in public, verdict/urns never") -> the tests under
  "Banner renders first" / "Disclosure gating" below.
"""

from __future__ import annotations

from typing import Any

from mrr.domain.release_status import AffectingCorrection, ReleaseBanner
from mrr.domain.research_report import (
    ReleaseBannerInput,
    build_report,
    render_html,
    render_markdown,
)

_CRATE_ID = "urn:mrr:evidence-crate:01BBBBBBBBBBBBBBBBBBBBBBBB"
_RUN_ID = "urn:mrr:run:01BBBBBBBBBBBBBBBBBBBBBBBC"
_PRACTICE_ID = "urn:mrr:practice:01BBBBBBBBBBBBBBBBBBBBBBBD"
_CLAIM_ID = "urn:mrr:claim:01BBBBBBBBBBBBBBBBBBBBBBBE"
_RELEASE_ID = "urn:mrr:release-record:01BBBBBBBBBBBBBBBBBBBBBBBF"
_NEW_RELEASE_ID = "urn:mrr:release-record:01BBBBBBBBBBBBBBBBBBBBBBBG"
_CORRECTION_ID = "urn:mrr:correction:01BBBBBBBBBBBBBBBBBBBBBBBH"
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
        "source_records": [],
        "evidence_anchors": [],
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


def _object_bodies() -> dict[str, dict[str, Any]]:
    return {_CRATE_ID: _crate_body(), _CLAIM_ID: _claim_body()}


def _build(
    *,
    disclosure: str = "internal",
    classification_by_object_id: dict[str, Any] | None = None,
    release_banner: ReleaseBannerInput | None = None,
    omit_release_banner: bool = False,
) -> Any:
    kwargs: dict[str, Any] = {
        "object_bodies": _object_bodies(),
        "crate_id": _CRATE_ID,
        "corrections": [],
        "provenance_by_claim": {},
        "disclosure": disclosure,
        "classification_by_object_id": classification_by_object_id or {},
    }
    if not omit_release_banner:
        kwargs["release_banner"] = release_banner
    return build_report(**kwargs)


def _correction_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _CORRECTION_ID,
        "correction_type": "numeric_error",
        "severity": "critical",
        "status": "OPEN",
        "reason": "The reported percentage was miscalculated.",
        "requested_action": "Recompute and re-verify.",
        "affected_objects": [{"id": _CLAIM_ID, "content_hash": "sha256:" + "c" * 64}],
        "impact_objects": [],
    }
    body.update(overrides)
    return body


def _current_banner_input(**correction_bodies: dict[str, Any]) -> ReleaseBannerInput:
    banner = ReleaseBanner(
        verdict="current",
        release_id=_RELEASE_ID,
        crate_id=_CRATE_ID,
        superseded_by=None,
        affecting_corrections=(),
        duplicate_unsuperseded_releases=False,
    )
    return ReleaseBannerInput(banner=banner, correction_bodies_by_id=correction_bodies)


# ---------------------------------------------------------------------------
# AT3: absence -> byte-identical regression.
# ---------------------------------------------------------------------------


def test_absent_release_banner_is_byte_identical_regression() -> None:
    model_omitted = _build(omit_release_banner=True)
    model_explicit_none = _build(release_banner=None)

    assert model_omitted.release_banner is None
    assert model_explicit_none.release_banner is None
    assert render_markdown(model_omitted) == render_markdown(model_explicit_none)
    assert render_html(model_omitted) == render_html(model_explicit_none)
    assert "Release status" not in render_markdown(model_omitted)
    assert "Release status" not in render_html(model_omitted)


def test_absent_release_banner_model_field_is_none() -> None:
    model = _build(omit_release_banner=True)
    assert model.release_banner is None


# ---------------------------------------------------------------------------
# Banner renders first, before section 1, in both formats.
# ---------------------------------------------------------------------------


def test_banner_renders_before_section_1_markdown() -> None:
    model = _build(release_banner=_current_banner_input())
    text = render_markdown(model)
    assert text.index("## Release status") < text.index("## 1. Header")


def test_banner_renders_before_section_1_html() -> None:
    model = _build(release_banner=_current_banner_input())
    text = render_html(model)
    assert text.index("<h2>Release status</h2>") < text.index("<h2>1. Header</h2>")


def test_banner_structural_fields_rendered_verbatim() -> None:
    model = _build(release_banner=_current_banner_input())
    md = render_markdown(model)
    html = render_html(model)
    for text in (md, html):
        assert _RELEASE_ID in text
        assert _CRATE_ID in text
        assert "current" in text


def test_superseded_by_rendered_when_applicable() -> None:
    banner = ReleaseBanner(
        verdict="superseded",
        release_id=_RELEASE_ID,
        crate_id=_CRATE_ID,
        superseded_by=_NEW_RELEASE_ID,
        affecting_corrections=(),
        duplicate_unsuperseded_releases=False,
    )
    model = _build(release_banner=ReleaseBannerInput(banner=banner, correction_bodies_by_id={}))
    md = render_markdown(model)
    html = render_html(model)
    assert _NEW_RELEASE_ID in md
    assert _NEW_RELEASE_ID in html
    assert "superseded" in md


def test_anomaly_flag_rendered_when_true() -> None:
    banner = ReleaseBanner(
        verdict="current",
        release_id=_RELEASE_ID,
        crate_id=_CRATE_ID,
        superseded_by=None,
        affecting_corrections=(),
        duplicate_unsuperseded_releases=True,
    )
    model = _build(release_banner=ReleaseBannerInput(banner=banner, correction_bodies_by_id={}))
    md = render_markdown(model)
    assert "True" in md.split("## Release status")[1].split("## 1. Header")[0]


# ---------------------------------------------------------------------------
# Disclosure gating: verdict/urns never gate; free text does.
# ---------------------------------------------------------------------------


def _affecting_banner_input(*, redacted_correction_body: bool = True) -> ReleaseBannerInput:
    banner = ReleaseBanner(
        verdict="corrections_affect_this_release",
        release_id=_RELEASE_ID,
        crate_id=_CRATE_ID,
        superseded_by=None,
        affecting_corrections=(
            AffectingCorrection(correction_id=_CORRECTION_ID, intersecting_object_ids=(_CLAIM_ID,)),
        ),
        duplicate_unsuperseded_releases=False,
    )
    bodies = {_CORRECTION_ID: _correction_body()} if redacted_correction_body else {}
    return ReleaseBannerInput(banner=banner, correction_bodies_by_id=bodies)


def test_internal_disclosure_shows_correction_free_text() -> None:
    model = _build(disclosure="internal", release_banner=_affecting_banner_input())
    md = render_markdown(model)
    assert "The reported percentage was miscalculated." in md
    assert "Recompute and re-verify." in md


def test_public_disclosure_with_empty_attestation_redacts_free_text() -> None:
    model = _build(
        disclosure="public",
        classification_by_object_id={},
        release_banner=_affecting_banner_input(),
    )
    md = render_markdown(model)
    assert "The reported percentage was miscalculated." not in md
    assert "[redacted: not attested PUBLIC]" in md
    # Structural facts still shown, unredacted, even under public/empty attestation.
    assert _CORRECTION_ID in md
    assert _CLAIM_ID in md
    assert "corrections_affect_this_release" in md


def test_public_disclosure_with_full_attestation_shows_free_text() -> None:
    attestation = {_CORRECTION_ID: "PUBLIC", _CLAIM_ID: "PUBLIC"}
    model = _build(
        disclosure="public",
        classification_by_object_id=attestation,
        release_banner=_affecting_banner_input(),
    )
    md = render_markdown(model)
    assert "The reported percentage was miscalculated." in md


def test_verdict_and_urns_never_gate_even_under_public_empty_attestation() -> None:
    model = _build(
        disclosure="public",
        classification_by_object_id={},
        release_banner=_affecting_banner_input(),
    )
    for text in (render_markdown(model), render_html(model)):
        assert _RELEASE_ID in text
        assert _CRATE_ID in text
        assert _CORRECTION_ID in text
        assert _CLAIM_ID in text
        assert "corrections_affect_this_release" in text


def test_missing_correction_body_renders_fail_closed_redacted() -> None:
    model = _build(
        disclosure="internal",
        release_banner=_affecting_banner_input(redacted_correction_body=False),
    )
    md = render_markdown(model)
    assert "[redacted: not attested PUBLIC]" in md
    assert _CORRECTION_ID in md


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_banner_rendering_is_byte_identical_across_calls() -> None:
    model_a = _build(release_banner=_affecting_banner_input())
    model_b = _build(release_banner=_affecting_banner_input())
    assert render_markdown(model_a) == render_markdown(model_b)
    assert render_html(model_a) == render_html(model_b)
