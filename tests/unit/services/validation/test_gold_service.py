"""task-packets/N1-T02.yaml AT2 (freeze is real), AT3 (order gate) and AT4's
absence half — the three refusals that make "frozen gold standard" mean
something checkable rather than something asserted in a document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mrr.domain.agreement import MismatchedRatersError
from mrr.services.validation.gold_service import (
    GoldSetFileError,
    GoldSetFrozenHashMismatchError,
    GoldSetLabelError,
    GoldSetOrderGateError,
    GoldSetSyntheticProvenanceError,
    GoldValidityService,
    compute_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "mb-cls-v2.synthetic.json"
PREDICTIONS = (
    REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "mb-cls-v2.synthetic.predictions.json"
)

CATEGORIES = ["supports", "contradicts", "qualifies", "contextualizes"]


def _minimal_set(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "set_id": "test-set",
        "categories": CATEGORIES,
        "criteria_version": "v1",
        "criteria_locked_at": "2026-08-01T00:00:00Z",
        "criteria_lock_content_hash": "sha256:" + "1" * 64,
        "labelled_at": "2026-08-01T12:00:00Z",
        "label_provenance": {
            "producing_practice": "test-practice",
            "account": "hand-written for this test",
            "encounter_id": None,
            "blind_to_measured_labels": True,
        },
        "cases": [
            {
                "case_id": "a",
                "excerpt": "An excerpt.",
                "excerpt_sha256": compute_sha256(b"An excerpt."),
                "claim_text": "A claim.",
                "expected_relation": "supports",
                "expected_rationale": "Because.",
                "decided_by": "definition:supports",
            }
        ],
    }
    document.update(overrides)
    return document


def _write(tmp_path: Path, document: dict[str, Any], name: str = "gold.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path


# --- AT2: the freeze is real -------------------------------------------------


def test_at2_loader_accepts_the_fixture_at_its_pinned_hash() -> None:
    pinned = compute_sha256(FIXTURE.read_bytes())
    gold_set = GoldValidityService().load_gold_set(
        FIXTURE, expected_sha256=pinned, allow_synthetic=True
    )
    assert gold_set.sha256 == pinned
    assert gold_set.fixture_set_id == f"mb-cls-v2-synthetic@{pinned}"
    assert len(gold_set.cases) == 23  # 20 decidable + 3 undecidable


def test_at2_one_flipped_byte_makes_the_loader_refuse(tmp_path: Path) -> None:
    pinned = compute_sha256(FIXTURE.read_bytes())
    moved = tmp_path / "moved.json"
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # A change that alters not one label, not one number — only prose. The
    # standard still MOVED, and that is the whole point: "frozen" is about
    # bytes, because anything less is a judgement call about what counts as a
    # material change.
    document["notes"] = document["notes"] + " "
    moved.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(GoldSetFrozenHashMismatchError) as excinfo:
        GoldValidityService().load_gold_set(moved, expected_sha256=pinned, allow_synthetic=True)

    assert excinfo.value.expected == pinned
    assert excinfo.value.actual != pinned
    assert "moved standard" in str(excinfo.value)


def test_at2_freeze_registry_matches_the_committed_fixture() -> None:
    registry = json.loads(
        (REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "FROZEN.json").read_text(
            encoding="utf-8"
        )
    )
    entry = registry["frozen"]["mb-cls-v2-synthetic"]
    assert entry["sha256"] == compute_sha256((REPO_ROOT / entry["path"]).read_bytes())


# --- AT3: the order gate -----------------------------------------------------


def test_at3_labels_strictly_after_the_lock_are_accepted(tmp_path: Path) -> None:
    path = _write(tmp_path, _minimal_set())
    assert GoldValidityService().load_gold_set(path).labelled_at == "2026-08-01T12:00:00Z"


def test_at3_labels_at_the_same_instant_as_the_lock_are_refused(tmp_path: Path) -> None:
    # Equality is refused, not accepted: labels stamped at the same instant as
    # the criteria cannot be shown to have followed them.
    path = _write(tmp_path, _minimal_set(labelled_at="2026-08-01T00:00:00Z"))
    with pytest.raises(GoldSetOrderGateError):
        GoldValidityService().load_gold_set(path)


def test_at3_labels_before_the_lock_are_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, _minimal_set(labelled_at="2026-07-31T23:59:59Z"))
    with pytest.raises(GoldSetOrderGateError) as excinfo:
        GoldValidityService().load_gold_set(path)
    # The refusal names the violated ordering, not a generic parse failure.
    assert "order gate violated" in str(excinfo.value)
    assert "still moving" in str(excinfo.value)


# --- The synthetic quarantine ------------------------------------------------


def test_synthetic_provenance_is_refused_by_default() -> None:
    with pytest.raises(GoldSetSyntheticProvenanceError) as excinfo:
        GoldValidityService().load_gold_set(FIXTURE)
    assert "--allow-synthetic" in str(excinfo.value)


def test_synthetic_provenance_loads_only_on_an_explicit_opt_in() -> None:
    assert GoldValidityService().load_gold_set(FIXTURE, allow_synthetic=True).set_id


# --- AT4: absence, and the shapes that cannot be read at all -----------------


def test_at4_a_missing_gold_set_is_a_dependency_failure(tmp_path: Path) -> None:
    with pytest.raises(GoldSetFileError):
        GoldValidityService().load_gold_set(tmp_path / "nope.json")


def test_unparseable_gold_set_is_a_dependency_failure(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GoldSetFileError):
        GoldValidityService().load_gold_set(path)


def test_duplicate_case_ids_are_refused(tmp_path: Path) -> None:
    document = _minimal_set()
    document["cases"] = document["cases"] + document["cases"]
    with pytest.raises(GoldSetFileError, match="duplicate case_id"):
        GoldValidityService().load_gold_set(_write(tmp_path, document))


def test_a_gold_label_outside_the_declared_categories_is_refused(tmp_path: Path) -> None:
    document = _minimal_set()
    document["cases"][0]["expected_relation"] = "endorses"
    with pytest.raises(GoldSetLabelError):
        GoldValidityService().load_gold_set(_write(tmp_path, document))


# --- Measurement refusals ----------------------------------------------------


def test_a_prediction_label_outside_the_declared_categories_is_refused() -> None:
    service = GoldValidityService()
    gold_set = service.load_gold_set(FIXTURE, allow_synthetic=True)
    predictions = dict.fromkeys(gold_set.gold_labels(), "endorses")
    with pytest.raises(GoldSetLabelError) as excinfo:
        service.build_report(gold_set, system_id="s", predictions=predictions)
    assert excinfo.value.side == "system"


def test_predictions_covering_only_some_cases_are_refused_not_measured() -> None:
    # A partial measurement over an unstated subset is the quiet result this
    # apparatus exists to prevent — align_ratings raises rather than pairing
    # whatever happens to overlap.
    service = GoldValidityService()
    gold_set = service.load_gold_set(FIXTURE, allow_synthetic=True)
    partial = dict(list(gold_set.gold_labels().items())[:5])
    with pytest.raises(MismatchedRatersError):
        service.build_report(gold_set, system_id="s", predictions=partial)


def test_the_committed_fixture_and_predictions_reproduce_the_at1_oracle() -> None:
    service = GoldValidityService()
    gold_set = service.load_gold_set(FIXTURE, allow_synthetic=True)
    system_id, predictions = service.load_predictions(PREDICTIONS)
    report = service.build_report(gold_set, system_id=system_id, predictions=predictions)

    assert report.confusion_matrix == ((8, 1, 1, 0), (1, 3, 1, 0), (1, 0, 2, 0), (1, 0, 0, 1))
    assert report.n == 20
    assert report.observed_agreement == 0.7
    assert report.majority_baseline == 0.5
    assert report.cohen_kappa.value == 17 / 32
    assert report.false_support.value == 0.3
    assert report.below_power is True
    # The fixture is honest about not being a standard, and the report says so.
    assert report.producing_practice == "synthetic-fixture"
    assert report.not_blind_warning is not None


# --- Criteria v2: what Ulysses' two objections bought ------------------------


def test_undecidable_cases_are_excluded_from_the_matrix_and_counted_instead() -> None:
    # R-undecidable-is-a-finding. The oracle matrix is over the 20 decidable
    # cases; the 3 undecidable ones must not swell it, must not be scored as
    # errors, and must not disappear.
    service = GoldValidityService()
    gold_set = service.load_gold_set(FIXTURE, allow_synthetic=True)
    system_id, predictions = service.load_predictions(PREDICTIONS)
    report = service.build_report(gold_set, system_id=system_id, predictions=predictions)

    assert len(gold_set.cases) == 23
    assert report.n == 20
    assert len(report.undecidable_case_ids) == 3
    assert set(report.undecidable_case_ids) == {
        "syn-undecidable-01",
        "syn-undecidable-02",
        "syn-undecidable-03",
    }
    # A prediction WAS supplied for each of them and is simply not scored.
    assert all(case_id in predictions for case_id in report.undecidable_case_ids)
    # The oracle survives untouched — that is the test that the exclusion is an
    # exclusion and not a quiet reweighting.
    assert report.observed_agreement == 0.7
    assert report.cohen_kappa.value == 17 / 32


def test_tie_broken_cases_are_named_so_the_ceiling_has_a_width() -> None:
    # R-conservative-supports as amended. Ulysses' whole objection in one
    # assertion: a fired tie-break must be recoverable from the record.
    service = GoldValidityService()
    gold_set = service.load_gold_set(FIXTURE, allow_synthetic=True)
    system_id, predictions = service.load_predictions(PREDICTIONS)
    report = service.build_report(gold_set, system_id=system_id, predictions=predictions)

    assert set(report.tie_broken_case_ids) == {
        "syn-contradicts-05",
        "syn-qualifies-01",
        "syn-qualifies-02",
    }
    rendered = json.loads(
        __import__("mrr.domain.gold_validity_report", fromlist=["render_json"]).render_json(report)
    )
    assert rendered["tie_broken_case_ids"] == list(report.tie_broken_case_ids)


def test_a_tie_with_the_label_itself_is_refused(tmp_path: Path) -> None:
    document = _minimal_set()
    document["cases"][0]["tie_with"] = "supports"  # same as its own label
    with pytest.raises(GoldSetFileError, match="runner-up"):
        GoldValidityService().load_gold_set(_write(tmp_path, document))


def test_an_undecidable_case_carrying_a_label_is_refused(tmp_path: Path) -> None:
    # One or the other, never both — otherwise "undecidable" becomes a note
    # attached to a decision, which is not what it means.
    document = _minimal_set()
    document["cases"][0]["undecidable"] = True
    document["cases"][0]["undecidable_reason"] = "the criteria do not settle it"
    with pytest.raises(GoldSetFileError, match="one or the other"):
        GoldValidityService().load_gold_set(_write(tmp_path, document))


def test_a_set_with_nothing_decidable_is_refused(tmp_path: Path) -> None:
    document = _minimal_set()
    document["cases"][0].update(
        {
            "expected_relation": None,
            "expected_rationale": None,
            "undecidable": True,
            "undecidable_reason": "the criteria do not settle it",
        }
    )
    with pytest.raises(GoldSetFileError, match="nothing to measure against"):
        GoldValidityService().load_gold_set(_write(tmp_path, document))
