"""Unit tests for ``mrr.services.validation.service.ValidationService``
(task-packets/N1-T01.yaml R4/R6, unit tier). DB-free: every fixture is a
small, synthetic three-file bundle written under ``tmp_path`` — the REAL
committed model-collapse corpus is exercised separately by the acceptance
tests in tests/contract/test_agreement_acceptance.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mrr.domain.agreement import DuplicateCategoryError, MismatchedRatersError
from mrr.services.validation.service import (
    AnalysisSetFileError,
    MissingAlignedItemError,
    TitleMismatchError,
    UnmappedLabelError,
    ValidationService,
)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _minimal_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write a minimal, valid three-file bundle (one stratum, two items) and
    return (crosswalk_path, corpus_path, blind_path).
    """
    corpus_path = tmp_path / "corpus-entries.json"
    blind_path = tmp_path / "verification" / "blind-returns.json"
    crosswalk_path = tmp_path / "verification" / "crosswalk.json"

    _write_json(
        corpus_path,
        [
            {"entry_id": "e1", "title": "Alpha", "evidence_relation": "supports"},
            {"entry_id": "e2", "title": "Beta", "evidence_relation": "contradicts"},
        ],
    )
    _write_json(
        blind_path,
        {
            "works": [
                {"item": "A1", "title": "Alpha", "verdict": "instantiates"},
                {"item": "A2", "title": "Beta", "verdict": "references-only"},
            ],
            "papers": [],
        },
    )
    _write_json(
        crosswalk_path,
        {
            "reference_rater": "pipeline",
            "source_files": {
                "corpus_entries": "../corpus-entries.json",
                "blind_returns": "blind-returns.json",
            },
            "strata": {
                "only-stratum": {
                    "n": 2,
                    "ordered_categories": ["instantiates", "references"],
                    "label_map": {
                        "pipeline": {
                            "map_to_common": {
                                "supports": "instantiates",
                                "contradicts": "references",
                            }
                        },
                        "blind": {
                            "map_to_common": {
                                "instantiates": "instantiates",
                                "references-only": "references",
                            }
                        },
                    },
                    "items": [
                        {"blind_item": "A1", "corpus_entry_id": "e1", "title": "Alpha"},
                        {"blind_item": "A2", "corpus_entry_id": "e2", "title": "Beta"},
                    ],
                }
            },
        },
    )
    return crosswalk_path, corpus_path, blind_path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_build_report_happy_path_reproduces_perfect_agreement(tmp_path: Path) -> None:
    crosswalk_path, _, _ = _minimal_bundle(tmp_path)

    report = ValidationService().build_report(crosswalk_path)

    assert len(report.strata) == 1
    stratum = report.strata[0]
    assert stratum.stratum_id == "only-stratum"
    assert stratum.n == 2
    assert stratum.observed_agreement == 1.0
    assert stratum.cohen_kappa.value == 1.0
    assert report.crosswalk_path == str(crosswalk_path)
    assert report.crosswalk_sha256.startswith("sha256:")


def test_crosswalk_sha256_matches_actual_file_bytes(tmp_path: Path) -> None:
    """task-packets/N1-T01.yaml AT3: the crosswalk sha256 in the report
    equals the actual sha256 of the crosswalk file; changing the file
    changes the reported hash.
    """
    import hashlib

    crosswalk_path, _, _ = _minimal_bundle(tmp_path)

    report_before = ValidationService().build_report(crosswalk_path)
    expected = f"sha256:{hashlib.sha256(crosswalk_path.read_bytes()).hexdigest()}"
    assert report_before.crosswalk_sha256 == expected

    # Mutate the crosswalk (add a harmless top-level key preserved by the
    # service's lenient reading) and confirm the hash changes too.
    document = json.loads(crosswalk_path.read_text())
    document["_touched"] = True
    crosswalk_path.write_text(json.dumps(document))

    report_after = ValidationService().build_report(crosswalk_path)
    assert report_after.crosswalk_sha256 != report_before.crosswalk_sha256
    assert report_after.crosswalk_sha256 == (
        f"sha256:{hashlib.sha256(crosswalk_path.read_bytes()).hexdigest()}"
    )


def test_source_files_resolved_relative_to_crosswalk_directory_not_cwd(tmp_path: Path) -> None:
    crosswalk_path, _, _ = _minimal_bundle(tmp_path)
    # Regardless of cwd, resolution must be relative to the crosswalk's own
    # parent directory.
    report = ValidationService().build_report(crosswalk_path.resolve())
    assert report.strata[0].n == 2


# ---------------------------------------------------------------------------
# AnalysisSetFileError — file-level dependency failures
# ---------------------------------------------------------------------------


def test_missing_crosswalk_file_raises_analysis_set_file_error(tmp_path: Path) -> None:
    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(tmp_path / "does-not-exist.json")


def test_crosswalk_with_invalid_json_raises_analysis_set_file_error(tmp_path: Path) -> None:
    crosswalk_path = tmp_path / "bad.json"
    crosswalk_path.write_text("{not valid json")
    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(crosswalk_path)


def test_crosswalk_missing_reference_rater_key_raises_analysis_set_file_error(
    tmp_path: Path,
) -> None:
    crosswalk_path, _, _ = _minimal_bundle(tmp_path)
    document = json.loads(crosswalk_path.read_text())
    del document["reference_rater"]
    crosswalk_path.write_text(json.dumps(document))

    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(crosswalk_path)


def test_crosswalk_with_unknown_reference_rater_raises_analysis_set_file_error(
    tmp_path: Path,
) -> None:
    crosswalk_path, _, _ = _minimal_bundle(tmp_path)
    document = json.loads(crosswalk_path.read_text())
    document["reference_rater"] = "neither"
    crosswalk_path.write_text(json.dumps(document))

    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(crosswalk_path)


def test_missing_corpus_source_file_raises_analysis_set_file_error(tmp_path: Path) -> None:
    crosswalk_path, corpus_path, _ = _minimal_bundle(tmp_path)
    corpus_path.unlink()

    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(crosswalk_path)


def test_missing_blind_source_file_raises_analysis_set_file_error(tmp_path: Path) -> None:
    crosswalk_path, _, blind_path = _minimal_bundle(tmp_path)
    blind_path.unlink()

    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(crosswalk_path)


def test_corpus_file_that_is_not_a_json_array_raises_analysis_set_file_error(
    tmp_path: Path,
) -> None:
    crosswalk_path, corpus_path, _ = _minimal_bundle(tmp_path)
    corpus_path.write_text(json.dumps({"not": "a list"}))

    with pytest.raises(AnalysisSetFileError):
        ValidationService().build_report(crosswalk_path)


# ---------------------------------------------------------------------------
# Refusals — never a silent partial (R4)
# ---------------------------------------------------------------------------


def test_item_missing_from_corpus_raises_missing_aligned_item_error(tmp_path: Path) -> None:
    crosswalk_path, corpus_path, _ = _minimal_bundle(tmp_path)
    document = json.loads(corpus_path.read_text())
    document.pop(1)  # remove entry "e2"
    corpus_path.write_text(json.dumps(document))

    with pytest.raises(MissingAlignedItemError) as excinfo:
        ValidationService().build_report(crosswalk_path)
    assert excinfo.value.corpus_entry_id == "e2"
    assert excinfo.value.side == "corpus"


def test_item_missing_from_blind_raises_missing_aligned_item_error(tmp_path: Path) -> None:
    crosswalk_path, _, blind_path = _minimal_bundle(tmp_path)
    document = json.loads(blind_path.read_text())
    document["works"].pop(1)  # remove "A2"
    blind_path.write_text(json.dumps(document))

    with pytest.raises(MissingAlignedItemError) as excinfo:
        ValidationService().build_report(crosswalk_path)
    assert excinfo.value.blind_item == "A2"
    assert excinfo.value.side == "blind"


def test_title_mismatch_against_corpus_raises_title_mismatch_error(tmp_path: Path) -> None:
    crosswalk_path, corpus_path, _ = _minimal_bundle(tmp_path)
    document = json.loads(corpus_path.read_text())
    document[0]["title"] = "Totally Different Title"
    corpus_path.write_text(json.dumps(document))

    with pytest.raises(TitleMismatchError) as excinfo:
        ValidationService().build_report(crosswalk_path)
    assert excinfo.value.source == "corpus-entries.json"


def test_title_mismatch_against_blind_raises_title_mismatch_error(tmp_path: Path) -> None:
    crosswalk_path, _, blind_path = _minimal_bundle(tmp_path)
    document = json.loads(blind_path.read_text())
    document["works"][0]["title"] = "Totally Different Title"
    blind_path.write_text(json.dumps(document))

    with pytest.raises(TitleMismatchError) as excinfo:
        ValidationService().build_report(crosswalk_path)
    assert excinfo.value.source == "blind-returns.json"


def test_unmapped_pipeline_label_raises_unmapped_label_error(tmp_path: Path) -> None:
    crosswalk_path, corpus_path, _ = _minimal_bundle(tmp_path)
    document = json.loads(corpus_path.read_text())
    document[0]["evidence_relation"] = "some-new-unmapped-relation"
    corpus_path.write_text(json.dumps(document))

    with pytest.raises(UnmappedLabelError) as excinfo:
        ValidationService().build_report(crosswalk_path)
    assert excinfo.value.rater == "pipeline"
    assert excinfo.value.raw_label == "some-new-unmapped-relation"


def test_unmapped_blind_label_raises_unmapped_label_error(tmp_path: Path) -> None:
    crosswalk_path, _, blind_path = _minimal_bundle(tmp_path)
    document = json.loads(blind_path.read_text())
    document["works"][0]["verdict"] = "some-new-unmapped-verdict"
    blind_path.write_text(json.dumps(document))

    with pytest.raises(UnmappedLabelError) as excinfo:
        ValidationService().build_report(crosswalk_path)
    assert excinfo.value.rater == "blind"


def test_duplicate_ordered_categories_propagates_domain_error(tmp_path: Path) -> None:
    crosswalk_path, _, _ = _minimal_bundle(tmp_path)
    document = json.loads(crosswalk_path.read_text())
    document["strata"]["only-stratum"]["ordered_categories"] = ["instantiates", "instantiates"]
    crosswalk_path.write_text(json.dumps(document))

    with pytest.raises(DuplicateCategoryError):
        ValidationService().build_report(crosswalk_path)


def test_mismatched_raters_is_unreachable_via_the_service_by_construction(
    tmp_path: Path,
) -> None:
    """Both raters are always built over the identical crosswalk items list
    in :meth:`ValidationService._build_one_stratum`, so
    :class:`mrr.domain.agreement.MismatchedRatersError` cannot actually be
    raised through this service — documented here rather than exercised,
    since there is no honest way to construct the mismatch through the
    service's own public surface without first tripping
    :class:`MissingAlignedItemError` instead (see
    test_item_missing_from_corpus_raises_missing_aligned_item_error).
    """
    assert issubclass(MismatchedRatersError, Exception)
