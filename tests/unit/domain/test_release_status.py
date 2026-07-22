"""Unit tests for ``mrr.domain.release_status`` (task-packets/E8-T05.yaml
R2), run entirely DB-free and I/O-free — plain dict fixtures shaped like
already-read ``ReleaseRecord``/``CorrectionEvent`` bodies, mirroring
``tests/unit/domain/test_research_report.py``'s own identical
"hand-built object_bodies mapping, no repository" discipline.

Acceptance-test mapping (task-packets/E8-T05.yaml):

- AT1's own status-side check (superseded verdict naming the superseding
  release) -> ``test_superseded_status_yields_superseded_verdict_naming_
  the_superseding_release``.
- AT2 (corrections_affect_this_release; a correction dated BEFORE the
  release, or affecting only objects outside the set, does not flip the
  verdict) -> ``test_correction_after_release_intersecting_shipped_object_
  flips_verdict``, ``test_correction_before_release_does_not_flip_verdict``,
  ``test_correction_touching_only_unshipped_objects_does_not_flip_verdict``.
- R2's own anomaly flag (returned alongside, never instead) ->
  ``test_duplicate_anomaly_flag_rides_alongside_every_verdict``.
"""

from __future__ import annotations

from typing import Any

from mrr.domain.release_status import (
    AffectingCorrection,
    compute_release_banner,
    exported_object_urns,
)

_RELEASE_ID = "urn:mrr:release-record:01AAAAAAAAAAAAAAAAAAAAAAAA"
_NEW_RELEASE_ID = "urn:mrr:release-record:01AAAAAAAAAAAAAAAAAAAAAAAB"
_CRATE_ID = "urn:mrr:evidence-crate:01AAAAAAAAAAAAAAAAAAAAAAAC"
_CLAIM_ID = "urn:mrr:claim:01AAAAAAAAAAAAAAAAAAAAAAAD"
_OTHER_CLAIM_ID = "urn:mrr:claim:01AAAAAAAAAAAAAAAAAAAAAAAE"
_UNSHIPPED_CLAIM_ID = "urn:mrr:claim:01AAAAAAAAAAAAAAAAAAAAAAAF"
_CORRECTION_ID_1 = "urn:mrr:correction:01AAAAAAAAAAAAAAAAAAAAAAAG"
_CORRECTION_ID_2 = "urn:mrr:correction:01AAAAAAAAAAAAAAAAAAAAAAAH"

_RELEASE_CREATED_AT = "2026-07-22T12:00:00+00:00"
_BEFORE_RELEASE = "2026-07-22T11:00:00+00:00"
_AFTER_RELEASE = "2026-07-22T13:00:00+00:00"


def _bundle_files() -> list[dict[str, str]]:
    return [
        {"path": "report.html", "sha256": "sha256:" + "1" * 64},
        {"path": "report.md", "sha256": "sha256:" + "2" * 64},
        {"path": "ro-crate/ro-crate-metadata.json", "sha256": "sha256:" + "3" * 64},
        {
            "path": f"ro-crate/objects/{_CLAIM_ID.replace(':', '_')}.json",
            "sha256": "sha256:" + "4" * 64,
        },
        {
            "path": f"ro-crate/objects/{_OTHER_CLAIM_ID.replace(':', '_')}.json",
            "sha256": "sha256:" + "5" * 64,
        },
        {
            "path": f"ro-crate/artifacts/{'6' * 64}",
            "sha256": "sha256:" + "6" * 64,
        },
    ]


def _release_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _RELEASE_ID,
        "crate_id": _CRATE_ID,
        "status": "released",
        "created_at": _RELEASE_CREATED_AT,
        "labels": None,
        "bundle": {"files": _bundle_files(), "root_hash": "sha256:" + "0" * 64},
    }
    body.update(overrides)
    return body


def _correction_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": _CORRECTION_ID_1,
        "severity": "critical",
        "status": "OPEN",
        "created_at": _AFTER_RELEASE,
        "affected_objects": [{"id": _CLAIM_ID, "content_hash": "sha256:" + "a" * 64}],
        "impact_objects": [],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# exported_object_urns.
# ---------------------------------------------------------------------------


def test_exported_object_urns_extracts_only_objects_paths() -> None:
    paths = [entry["path"] for entry in _bundle_files()]
    urns = exported_object_urns(paths)
    assert urns == frozenset({_CLAIM_ID, _OTHER_CLAIM_ID})


def test_exported_object_urns_ignores_non_object_paths() -> None:
    urns = exported_object_urns(
        ["report.md", "report.html", "ro-crate/ro-crate-metadata.json", "release-manifest.json"]
    )
    assert urns == frozenset()


def test_exported_object_urns_ignores_a_path_that_does_not_decode_to_a_valid_urn() -> None:
    # "objects/not-a-urn.json" -> "ro-crate/objects/not-a-urn.json" decodes to
    # the literal string "not-a-urn", which does not match URN_PATTERN — must
    # be silently excluded, never trusted as a urn.
    urns = exported_object_urns(["ro-crate/objects/not-a-urn.json"])
    assert urns == frozenset()


def test_exported_object_urns_is_empty_for_an_empty_manifest() -> None:
    assert exported_object_urns([]) == frozenset()


# ---------------------------------------------------------------------------
# compute_release_banner — the "superseded" verdict.
# ---------------------------------------------------------------------------


def test_superseded_status_yields_superseded_verdict_naming_the_superseding_release() -> None:
    banner = compute_release_banner(
        release_body=_release_body(status="superseded", labels={"superseded_by": _NEW_RELEASE_ID}),
        correction_bodies=[],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "superseded"
    assert banner.release_id == _RELEASE_ID
    assert banner.crate_id == _CRATE_ID
    assert banner.superseded_by == _NEW_RELEASE_ID
    assert banner.affecting_corrections == ()


def test_superseded_status_never_checks_corrections_at_all() -> None:
    # A correction that WOULD otherwise flip the verdict is simply ignored
    # once the release is already superseded (AT4's own "history is frozen").
    banner = compute_release_banner(
        release_body=_release_body(status="superseded", labels={"superseded_by": _NEW_RELEASE_ID}),
        correction_bodies=[_correction_body()],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "superseded"
    assert banner.affecting_corrections == ()


def test_superseded_status_with_no_labels_reports_superseded_by_none() -> None:
    banner = compute_release_banner(
        release_body=_release_body(status="superseded", labels=None),
        correction_bodies=[],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "superseded"
    assert banner.superseded_by is None


# ---------------------------------------------------------------------------
# compute_release_banner — "corrections_affect_this_release" (AT2).
# ---------------------------------------------------------------------------


def test_correction_after_release_intersecting_shipped_object_flips_verdict() -> None:
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(
                id=_CORRECTION_ID_1,
                created_at=_AFTER_RELEASE,
                affected_objects=[{"id": _CLAIM_ID, "content_hash": "sha256:" + "a" * 64}],
                impact_objects=[],
            )
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "corrections_affect_this_release"
    assert banner.affecting_corrections == (
        AffectingCorrection(correction_id=_CORRECTION_ID_1, intersecting_object_ids=(_CLAIM_ID,)),
    )


def test_correction_before_release_does_not_flip_verdict() -> None:
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(created_at=_BEFORE_RELEASE, affected_objects=[{"id": _CLAIM_ID}])
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "current"
    assert banner.affecting_corrections == ()


def test_correction_at_exactly_release_created_at_does_not_flip_verdict() -> None:
    # "strictly LATER" — an equal timestamp is not "after".
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(created_at=_RELEASE_CREATED_AT, affected_objects=[{"id": _CLAIM_ID}])
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "current"


def test_correction_touching_only_unshipped_objects_does_not_flip_verdict() -> None:
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(
                created_at=_AFTER_RELEASE,
                affected_objects=[{"id": _UNSHIPPED_CLAIM_ID}],
                impact_objects=[],
            )
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "current"
    assert banner.affecting_corrections == ()


def test_correction_flips_via_impact_objects_too() -> None:
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(
                created_at=_AFTER_RELEASE, affected_objects=[], impact_objects=[_OTHER_CLAIM_ID]
            )
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "corrections_affect_this_release"
    assert banner.affecting_corrections[0].intersecting_object_ids == (_OTHER_CLAIM_ID,)


def test_multiple_qualifying_corrections_are_all_reported_sorted_by_id() -> None:
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(
                id=_CORRECTION_ID_2,
                created_at=_AFTER_RELEASE,
                affected_objects=[{"id": _OTHER_CLAIM_ID}],
            ),
            _correction_body(
                id=_CORRECTION_ID_1,
                created_at=_AFTER_RELEASE,
                affected_objects=[{"id": _CLAIM_ID}],
            ),
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.verdict == "corrections_affect_this_release"
    assert [row.correction_id for row in banner.affecting_corrections] == [
        _CORRECTION_ID_1,
        _CORRECTION_ID_2,
    ]


def test_intersection_is_sorted_and_deduplicated_per_correction() -> None:
    banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(
                created_at=_AFTER_RELEASE,
                affected_objects=[{"id": _OTHER_CLAIM_ID}, {"id": _CLAIM_ID}],
                impact_objects=[_CLAIM_ID],
            )
        ],
        duplicate_unsuperseded_releases=False,
    )
    assert banner.affecting_corrections[0].intersecting_object_ids == (
        _CLAIM_ID,
        _OTHER_CLAIM_ID,
    )


def test_no_qualifying_correction_yields_current() -> None:
    banner = compute_release_banner(
        release_body=_release_body(), correction_bodies=[], duplicate_unsuperseded_releases=False
    )
    assert banner.verdict == "current"
    assert banner.superseded_by is None
    assert banner.affecting_corrections == ()


# ---------------------------------------------------------------------------
# The anomaly flag rides alongside every verdict, never instead.
# ---------------------------------------------------------------------------


def test_duplicate_anomaly_flag_rides_alongside_every_verdict() -> None:
    current_banner = compute_release_banner(
        release_body=_release_body(), correction_bodies=[], duplicate_unsuperseded_releases=True
    )
    assert current_banner.verdict == "current"
    assert current_banner.duplicate_unsuperseded_releases is True

    affecting_banner = compute_release_banner(
        release_body=_release_body(),
        correction_bodies=[
            _correction_body(created_at=_AFTER_RELEASE, affected_objects=[{"id": _CLAIM_ID}])
        ],
        duplicate_unsuperseded_releases=True,
    )
    assert affecting_banner.verdict == "corrections_affect_this_release"
    assert affecting_banner.duplicate_unsuperseded_releases is True

    superseded_banner = compute_release_banner(
        release_body=_release_body(status="superseded", labels={"superseded_by": _NEW_RELEASE_ID}),
        correction_bodies=[],
        duplicate_unsuperseded_releases=True,
    )
    assert superseded_banner.verdict == "superseded"
    assert superseded_banner.duplicate_unsuperseded_releases is True


def test_duplicate_anomaly_flag_defaults_false_through_when_caller_says_so() -> None:
    banner = compute_release_banner(
        release_body=_release_body(), correction_bodies=[], duplicate_unsuperseded_releases=False
    )
    assert banner.duplicate_unsuperseded_releases is False


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------


def test_compute_release_banner_is_byte_identical_across_calls() -> None:
    kwargs: dict[str, Any] = {
        "release_body": _release_body(),
        "correction_bodies": [
            _correction_body(created_at=_AFTER_RELEASE, affected_objects=[{"id": _CLAIM_ID}])
        ],
        "duplicate_unsuperseded_releases": True,
    }
    assert compute_release_banner(**kwargs) == compute_release_banner(**kwargs)
