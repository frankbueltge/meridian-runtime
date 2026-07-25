"""DB-free, no-network unit/smoke tests for ``mrr audit anchoring``
(task-packets/N2-T02b.yaml R6/R7). Like ``mrr audit citations``, this
command never opens a database or network connection at all — every test
here runs against a small, synthetic archive dump + anchoring-batch
descriptor written under ``tmp_path``. The REAL committed
``corpora/archive-integrity`` batch is exercised separately at the contract
tier (tests/contract/test_anchoring_integrity_acceptance.py).

Acceptance-test mapping:

- AT4's "an existing --output is refused, checked before anything else" ->
  ``test_pre_existing_output_is_refused_before_reading_the_batch``.
- AT4's "a missing/unparseable descriptor or missing dump file exits 2" ->
  ``test_missing_batch_file_is_a_dependency_failure``,
  ``test_invalid_json_batch_is_a_dependency_failure``,
  ``test_missing_declared_dump_file_is_a_dependency_failure``.
- AT4's "a dump-anchor mismatch exits 3, naming the dump" ->
  ``test_dump_anchor_mismatch_is_a_refusal_naming_the_schema``.
- AT6's "a structurally malformed dump exits 3" ->
  ``test_malformed_dump_after_a_clean_gate_is_a_refusal``.
- The "citations" subcommand is untouched by this packet ->
  ``test_citations_subcommand_still_registered_and_unaffected``.
- ``--help``/usage-error smoke tests -> the tests under that heading below.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mrr.services.cli.anchoring_integrity_main import build_parser as build_anchoring_parser
from mrr.services.cli.anchoring_integrity_main import main as anchoring_main
from mrr.services.cli.main import build_parser, main


def _sha256_of(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _dump_text() -> str:
    return (
        "\n".join(
            (
                "COPY mrr_test.objects (id, kind, body) FROM stdin;",
                'urn:mrr:sr:1\tSourceRecord\t{"title": "Work One"}',
                'urn:mrr:ea:1\tEvidenceAnchor\t{"source_record_id": "urn:mrr:sr:1"}',
                'urn:mrr:claim:1\tClaim\t{"evidence_relations": ["urn:mrr:ea:1"], '
                '"counterevidence_relations": []}',
                r"\.",
            )
        )
        + "\n"
    )


def _minimal_batch(tmp_path: Path) -> Path:
    dump_path = tmp_path / "mrr_test.sql"
    dump_path.write_text(_dump_text(), encoding="utf-8")

    batch_path = tmp_path / "anchoring-batch.v1.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "archive-anchoring-batch.v1",
                "batch_id": "synthetic-batch",
                "observation_kind": "archive-anchoring-integrity",
                "audit_target": "a synthetic test target",
                "dumps": [
                    {
                        "schema_name": "mrr_test",
                        "path": "mrr_test.sql",
                        "sha256": _sha256_of(dump_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return batch_path


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_audit_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "audit" in capsys.readouterr().out


def test_audit_help_mentions_both_citations_and_anchoring(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "citations" in out
    assert "anchoring" in out


def test_anchoring_subcommand_help_documents_every_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "anchoring", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--batch", "--format", "--output"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_audit_without_a_sub_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit"])
    assert excinfo.value.code != 0


def test_invalid_format_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "anchoring", "--batch", "b.json", "--format", "yaml"])
    assert excinfo.value.code != 0


def test_missing_required_flags_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "anchoring"])
    assert excinfo.value.code != 0


def test_main_parser_registers_anchoring_under_audit() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["audit", "anchoring", "--help"])
    assert excinfo.value.code == 0


def test_build_anchoring_parser_prog_name_is_mrr_audit() -> None:
    parser = build_anchoring_parser()
    assert parser.prog == "mrr audit"


def test_standalone_anchoring_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        anchoring_main(["--help"])
    assert excinfo.value.code == 0
    assert "anchoring" in capsys.readouterr().out


def test_citations_subcommand_still_registered_and_unaffected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """task-packets/N2-T02b.yaml R6: "without changing citations' parser,
    dispatch, or behaviour in any way" — 'mrr audit citations --help' still
    works, listing exactly the same flags it always did."""
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "citations", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--manifest", "--snapshot", "--format", "--output"):
        assert flag in out


# ---------------------------------------------------------------------------
# MRR-NFR-012 ordering + refusal tests.
# ---------------------------------------------------------------------------


def test_pre_existing_output_is_refused_before_reading_the_batch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    output.write_text("i already exist")

    exit_code = main(
        [
            "audit",
            "anchoring",
            "--batch",
            str(tmp_path / "does-not-exist.json"),  # would fail differently if reached
            "--output",
            str(output),
        ]
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    assert output.read_text() == "i already exist"


def test_missing_batch_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["audit", "anchoring", "--batch", str(tmp_path / "does-not-exist.json")])
    assert exit_code == 2
    assert "does-not-exist.json" in capsys.readouterr().err


def test_invalid_json_batch_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")

    exit_code = main(["audit", "anchoring", "--batch", str(bad)])
    assert exit_code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_missing_declared_dump_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    (tmp_path / "mrr_test.sql").unlink()

    exit_code = main(["audit", "anchoring", "--batch", str(batch_path)])
    assert exit_code == 2
    assert "mrr_test.sql" in capsys.readouterr().err


def test_dump_anchor_mismatch_is_a_refusal_naming_the_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["dumps"][0]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    exit_code = main(["audit", "anchoring", "--batch", str(batch_path)])
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "mrr_test" in err
    assert "integrity gate failed" in err


def test_malformed_dump_after_a_clean_gate_is_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dump_path = tmp_path / "mrr_test.sql"
    dump_path.write_text("not a COPY block at all\n", encoding="utf-8")
    batch_path = tmp_path / "anchoring-batch.v1.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "archive-anchoring-batch.v1",
                "batch_id": "synthetic-batch",
                "observation_kind": "archive-anchoring-integrity",
                "audit_target": "a synthetic test target",
                "dumps": [
                    {
                        "schema_name": "mrr_test",
                        "path": "mrr_test.sql",
                        "sha256": _sha256_of(dump_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["audit", "anchoring", "--batch", str(batch_path)])
    assert exit_code == 3
    assert "objects" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_successful_run_without_output_prints_markdown_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)

    exit_code = main(["audit", "anchoring", "--batch", str(batch_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Anchoring integrity report" in out
    assert "anchoring_is_not_support" in out
    assert "mrr_test" in out


def test_successful_run_with_output_writes_file_and_prints_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    output = tmp_path / "out" / "report.json"

    exit_code = main(
        [
            "audit",
            "anchoring",
            "--batch",
            str(batch_path),
            "--format",
            "json",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    written = json.loads(output.read_text())
    assert written["batch_id"] == "synthetic-batch"
    assert written["dumps"][0]["violations"]["anchor_dangling"] == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output)
    assert payload["format"] == "json"
    assert payload["batch_id"] == "synthetic-batch"
    assert payload["dump_anchors_matched"] is True


def test_batch_dump_paths_resolve_relative_to_the_descriptor_directory_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_path = _minimal_batch(tmp_path)
    other_cwd = tmp_path.parent
    monkeypatch.chdir(other_cwd)

    exit_code = main(["audit", "anchoring", "--batch", str(batch_path.resolve())])
    assert exit_code == 0
