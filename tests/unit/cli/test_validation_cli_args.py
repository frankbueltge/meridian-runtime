"""DB-free unit/smoke tests for ``mrr validate agreement`` (task-packets/
N1-T01.yaml R5/R6). Unlike every other CLI module in ``mrr.services.cli``,
this command never opens a database connection at all — so every test here
runs against a small, synthetic three-file bundle written under
``tmp_path``, with no unreachable-database trick needed (there is no
``--database-url`` flag to begin with).

Acceptance-test mapping:

- AT5's "an existing --output is refused, checked before anything else" ->
  ``test_pre_existing_output_is_refused_before_reading_the_analysis_set``.
- AT5's "a missing/unparseable input file exits 2" ->
  ``test_missing_analysis_set_file_is_a_dependency_failure``,
  ``test_invalid_json_analysis_set_is_a_dependency_failure``.
- AT5's "an incomplete alignment ... exits 3 naming the item" ->
  ``test_incomplete_alignment_is_a_refusal_naming_the_item``.
- ``--help``/usage-error smoke tests -> the tests under that heading below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mrr.services.cli.main import build_parser, main
from mrr.services.cli.validation_main import build_parser as build_validation_parser
from mrr.services.cli.validation_main import main as validation_main


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _minimal_bundle(tmp_path: Path) -> Path:
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
    return crosswalk_path


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_validate_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "validate" in capsys.readouterr().out


def test_agreement_subcommand_help_documents_every_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "agreement", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--analysis-set", "--format", "--output"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_validate_without_agreement_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["validate"])
    assert excinfo.value.code != 0


def test_invalid_format_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "agreement", "--analysis-set", "x.json", "--format", "yaml"])
    assert excinfo.value.code != 0


def test_missing_required_analysis_set_flag_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["validate", "agreement"])
    assert excinfo.value.code != 0


def test_main_parser_registers_validate_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["validate", "agreement", "--help"])
    assert excinfo.value.code == 0


def test_build_validation_parser_prog_name_is_mrr_validate() -> None:
    parser = build_validation_parser()
    assert parser.prog == "mrr validate"


def test_standalone_validation_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        validation_main(["--help"])
    assert excinfo.value.code == 0
    assert "agreement" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# MRR-NFR-012 ordering + refusal tests.
# ---------------------------------------------------------------------------


def test_pre_existing_output_is_refused_before_reading_the_analysis_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    output.write_text("i already exist")

    exit_code = main(
        [
            "validate",
            "agreement",
            "--analysis-set",
            str(tmp_path / "does-not-exist.json"),  # would fail differently if reached
            "--output",
            str(output),
        ]
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    assert output.read_text() == "i already exist"


def test_missing_analysis_set_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "validate",
            "agreement",
            "--analysis-set",
            str(tmp_path / "does-not-exist.json"),
        ]
    )
    assert exit_code == 2
    assert "does-not-exist.json" in capsys.readouterr().err


def test_invalid_json_analysis_set_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")

    exit_code = main(["validate", "agreement", "--analysis-set", str(bad)])
    assert exit_code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_incomplete_alignment_is_a_refusal_naming_the_item(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    crosswalk_path = _minimal_bundle(tmp_path)
    corpus_path = tmp_path / "corpus-entries.json"
    document = json.loads(corpus_path.read_text())
    document.pop(1)  # remove entry "e2" -> incomplete alignment
    corpus_path.write_text(json.dumps(document))

    exit_code = main(["validate", "agreement", "--analysis-set", str(crosswalk_path)])
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "e2" in err
    assert "incomplete alignment" in err


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_successful_run_without_output_prints_markdown_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    crosswalk_path = _minimal_bundle(tmp_path)

    exit_code = main(["validate", "agreement", "--analysis-set", str(crosswalk_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Agreement report" in out
    assert "only-stratum" in out


def test_successful_run_with_output_writes_file_and_prints_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    crosswalk_path = _minimal_bundle(tmp_path)
    output = tmp_path / "out" / "report.json"

    exit_code = main(
        [
            "validate",
            "agreement",
            "--analysis-set",
            str(crosswalk_path),
            "--format",
            "json",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    written = json.loads(output.read_text())
    assert written["strata"][0]["stratum_id"] == "only-stratum"

    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output)
    assert payload["format"] == "json"
    assert payload["strata"] == ["only-stratum"]
    assert payload["crosswalk_sha256"].startswith("sha256:")
