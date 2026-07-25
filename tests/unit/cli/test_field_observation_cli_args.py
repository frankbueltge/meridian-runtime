"""DB-free, no-network unit/smoke tests for ``mrr observe field``
(task-packets/R2-T01.yaml R5/R6). Like ``mrr audit citations``, this command
never opens a database or network connection at all — so every test here
runs against a small, synthetic descriptor + manifest/snapshot bundle
written under ``tmp_path``, with no unreachable-database trick needed (there
is no ``--database-url`` flag to begin with). The REAL committed
``corpora/e2e-survey`` batch is exercised separately at the contract tier
(tests/contract/test_field_observation_acceptance.py).

Acceptance-test mapping:

- AT4's "an existing --output is refused, checked before anything else" ->
  ``test_pre_existing_output_is_refused_before_reading_the_batch``.
- AT4's "a missing/unparseable descriptor or missing declared input exits 2"
  -> ``test_missing_batch_file_is_a_dependency_failure``,
  ``test_invalid_json_batch_is_a_dependency_failure``,
  ``test_missing_declared_input_file_is_a_dependency_failure``.
- AT3/AT4's "an anchor mismatch exits 3, naming the role" ->
  ``test_anchor_mismatch_is_a_refusal_naming_the_role``.
- AT4's "a manifest citation absent from the snapshot exits 3" ->
  ``test_missing_resolution_is_a_refusal_naming_the_citation_id``.
- AT5's "descriptor-relative paths, CWD-independent" ->
  ``test_batch_input_paths_resolve_relative_to_the_descriptor_directory_not_cwd``.
- ``--help``/usage-error smoke tests -> the tests under that heading below.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mrr.services.cli.field_observation_main import build_parser as build_observe_parser
from mrr.services.cli.field_observation_main import main as observe_main
from mrr.services.cli.main import build_parser, main


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _sha256_of(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _minimal_batch(tmp_path: Path) -> Path:
    """Write a minimal, valid batch descriptor + manifest/snapshot bundle
    under ``tmp_path`` and return the descriptor's own path. Mirrors
    ``tests/unit/cli/test_citation_audit_cli_args.py._minimal_bundle``'s
    identical one-citation shape.
    """
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

    batch_path = tmp_path / "observation-batch.v1.json"
    _write_json(
        batch_path,
        {
            "schema_version": "observation-batch.v1",
            "batch_id": "synthetic-batch",
            "observation_kind": "citation_audit",
            "audit_target": "a synthetic test target",
            "inputs": {
                "manifest": {
                    "path": "citations.manifest.json",
                    "sha256": _sha256_of(manifest_path),
                },
                "snapshot": {
                    "path": "verification/resolution-snapshot.json",
                    "sha256": _sha256_of(snapshot_path),
                },
            },
        },
    )
    return batch_path


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_observe_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "observe" in capsys.readouterr().out


def test_field_subcommand_help_documents_every_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["observe", "field", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--batch", "--format", "--output"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_observe_without_field_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["observe"])
    assert excinfo.value.code != 0


def test_invalid_format_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["observe", "field", "--batch", "b.json", "--format", "yaml"])
    assert excinfo.value.code != 0


def test_missing_required_flags_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["observe", "field"])
    assert excinfo.value.code != 0


def test_main_parser_registers_observe_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["observe", "field", "--help"])
    assert excinfo.value.code == 0


def test_build_observe_parser_prog_name_is_mrr_observe() -> None:
    parser = build_observe_parser()
    assert parser.prog == "mrr observe"


def test_standalone_observe_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        observe_main(["--help"])
    assert excinfo.value.code == 0
    assert "field" in capsys.readouterr().out


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
            "observe",
            "field",
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
    exit_code = main(["observe", "field", "--batch", str(tmp_path / "does-not-exist.json")])
    assert exit_code == 2
    assert "does-not-exist.json" in capsys.readouterr().err


def test_invalid_json_batch_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")

    exit_code = main(["observe", "field", "--batch", str(bad)])
    assert exit_code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_missing_declared_input_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    (tmp_path / "citations.manifest.json").unlink()

    exit_code = main(["observe", "field", "--batch", str(batch_path)])
    assert exit_code == 2
    assert "citations.manifest.json" in capsys.readouterr().err


def test_anchor_mismatch_is_a_refusal_naming_the_role(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    document = json.loads(batch_path.read_text())
    document["inputs"]["manifest"]["sha256"] = "sha256:" + "0" * 64  # deliberately wrong
    batch_path.write_text(json.dumps(document))

    exit_code = main(["observe", "field", "--batch", str(batch_path)])
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "manifest" in err
    assert "integrity gate failed" in err


def test_missing_resolution_is_a_refusal_naming_the_citation_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    snapshot_path = tmp_path / "verification" / "resolution-snapshot.json"
    snapshot_document = json.loads(snapshot_path.read_text())
    snapshot_document["resolutions"] = []  # remove the only resolution -> a structural gap
    snapshot_path.write_text(json.dumps(snapshot_document))
    # Re-pin the snapshot anchor to the mutated file's own new hash so this
    # test exercises ONLY the missing-resolution refusal, not a coincidental
    # anchor mismatch.
    batch_document = json.loads(batch_path.read_text())
    batch_document["inputs"]["snapshot"]["sha256"] = _sha256_of(snapshot_path)
    batch_path.write_text(json.dumps(batch_document))

    exit_code = main(["observe", "field", "--batch", str(batch_path)])
    assert exit_code == 3
    err = capsys.readouterr().err
    assert "c1" in err


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_successful_run_without_output_prints_markdown_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)

    exit_code = main(["observe", "field", "--batch", str(batch_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "# Field observation report" in out
    assert "observation_is_not_optimization" in out
    assert "c1" in out


def test_successful_run_with_output_writes_file_and_prints_json_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch_path = _minimal_batch(tmp_path)
    output = tmp_path / "out" / "report.json"

    exit_code = main(
        [
            "observe",
            "field",
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
    assert written["citation_audit"]["citations"][0]["citation_id"] == "c1"
    assert all(row["matched"] for row in written["anchors"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output)
    assert payload["format"] == "json"
    assert payload["batch_id"] == "synthetic-batch"
    assert payload["anchors_matched"] is True
    assert payload["citation_summary"]["resolved"] == 1


def test_batch_input_paths_resolve_relative_to_the_descriptor_directory_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """task-packets/R2-T01.yaml AT5: pointing --batch at the descriptor via
    an ABSOLUTE path, from a different process CWD, still resolves its
    declared inputs relative to the descriptor's own directory and succeeds
    identically.
    """
    batch_path = _minimal_batch(tmp_path)
    other_cwd = tmp_path.parent
    monkeypatch.chdir(other_cwd)

    exit_code = main(["observe", "field", "--batch", str(batch_path.resolve())])
    assert exit_code == 0
