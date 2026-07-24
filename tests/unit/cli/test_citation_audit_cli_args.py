"""DB-free, no-network unit/smoke tests for ``mrr audit citations``
(task-packets/N2-T01.yaml R5/R6). Like ``mrr validate agreement``, this
command never opens a database or network connection at all — so every test
here runs against a small, synthetic manifest/snapshot pair written under
``tmp_path``, with no unreachable-database trick needed (there is no
``--database-url`` flag to begin with).

Acceptance-test mapping:

- AT5's "an existing --output is refused, checked before anything else" ->
  ``test_pre_existing_output_is_refused_before_reading_the_manifest``.
- AT5's "a missing/unparseable input file exits 2" ->
  ``test_missing_manifest_file_is_a_dependency_failure``,
  ``test_invalid_json_manifest_is_a_dependency_failure``.
- AT5's "a manifest citation absent from the snapshot exits 3 naming the
  citation_id" -> ``test_missing_resolution_is_a_refusal_naming_the_citation_id``.
- ``--help``/usage-error smoke tests -> the tests under that heading below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mrr.services.cli.citation_audit_main import build_parser as build_audit_parser
from mrr.services.cli.citation_audit_main import main as audit_main
from mrr.services.cli.main import build_parser, main


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _minimal_bundle(tmp_path: Path) -> tuple[Path, Path]:
    manifest_path = tmp_path / "citations.manifest.json"
    snapshot_path = tmp_path / "verification" / "resolution-snapshot.json"

    _write_json(
        manifest_path,
        {
            "audit_target": "a synthetic test target",
            "citations": [
                {
                    "citation_id": "c1",
                    "cited_as": "Some Paper",
                    "cited_url": "https://arxiv.org/abs/2511.02824",
                    "identifiers": {"arxiv": "2511.02824"},
                    "claimed_title": None,
                }
            ],
        },
    )
    _write_json(
        snapshot_path,
        {
            "resolutions": [
                {
                    "citation_id": "c1",
                    "resolved": True,
                    "resolved_title": "Some Resolved Title",
                }
            ]
        },
    )
    return manifest_path, snapshot_path


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_audit_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "audit" in capsys.readouterr().out


def test_citations_subcommand_help_documents_every_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "citations", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--manifest", "--snapshot", "--format", "--output"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_audit_without_citations_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit"])
    assert excinfo.value.code != 0


def test_invalid_format_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "audit",
                "citations",
                "--manifest",
                "m.json",
                "--snapshot",
                "s.json",
                "--format",
                "yaml",
            ]
        )
    assert excinfo.value.code != 0


def test_missing_required_flags_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", "citations"])
    assert excinfo.value.code != 0


def test_main_parser_registers_audit_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["audit", "citations", "--help"])
    assert excinfo.value.code == 0


def test_build_audit_parser_prog_name_is_mrr_audit() -> None:
    parser = build_audit_parser()
    assert parser.prog == "mrr audit"


def test_standalone_audit_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        audit_main(["--help"])
    assert excinfo.value.code == 0
    assert "citations" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# MRR-NFR-012 ordering + refusal tests.
# ---------------------------------------------------------------------------


def test_pre_existing_output_is_refused_before_reading_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    output.write_text("i already exist")

    exit_code = main(
        [
            "audit",
            "citations",
            "--manifest",
            str(tmp_path / "does-not-exist.json"),  # would fail differently if reached
            "--snapshot",
            str(tmp_path / "also-does-not-exist.json"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    assert output.read_text() == "i already exist"


def test_missing_manifest_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, snapshot_path = _minimal_bundle(tmp_path)
    exit_code = main(
        [
            "audit",
            "citations",
            "--manifest",
            str(tmp_path / "does-not-exist.json"),
            "--snapshot",
            str(snapshot_path),
        ]
    )
    assert exit_code == 2
    assert "does-not-exist.json" in capsys.readouterr().err


def test_missing_snapshot_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, _ = _minimal_bundle(tmp_path)
    exit_code = main(
        [
            "audit",
            "citations",
            "--manifest",
            str(manifest_path),
            "--snapshot",
            str(tmp_path / "does-not-exist.json"),
        ]
    )
    assert exit_code == 2
    assert "does-not-exist.json" in capsys.readouterr().err


def test_invalid_json_manifest_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, snapshot_path = _minimal_bundle(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")

    exit_code = main(
        ["audit", "citations", "--manifest", str(bad), "--snapshot", str(snapshot_path)]
    )
    assert exit_code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_missing_resolution_is_a_refusal_naming_the_citation_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, snapshot_path = _minimal_bundle(tmp_path)
    document = json.loads(snapshot_path.read_text())
    document["resolutions"] = []  # remove the only resolution -> a structural gap
    snapshot_path.write_text(json.dumps(document))

    exit_code = main(
        ["audit", "citations", "--manifest", str(manifest_path), "--snapshot", str(snapshot_path)]
    )
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "c1" in err


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_successful_run_without_output_prints_markdown_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, snapshot_path = _minimal_bundle(tmp_path)

    exit_code = main(
        ["audit", "citations", "--manifest", str(manifest_path), "--snapshot", str(snapshot_path)]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Citation audit report" in out
    assert "c1" in out


def test_successful_run_with_output_writes_file_and_prints_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, snapshot_path = _minimal_bundle(tmp_path)
    output = tmp_path / "out" / "report.json"

    exit_code = main(
        [
            "audit",
            "citations",
            "--manifest",
            str(manifest_path),
            "--snapshot",
            str(snapshot_path),
            "--format",
            "json",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.exists()
    written = json.loads(output.read_text())
    assert written["citations"][0]["citation_id"] == "c1"
    assert written["summary"]["resolved"] == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output)
    assert payload["format"] == "json"
    assert payload["summary"]["resolved"] == 1
    assert payload["snapshot_sha256"].startswith("sha256:")
