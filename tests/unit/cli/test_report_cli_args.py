"""DB-free unit/smoke tests for ``mrr report render`` (task-packets/
E8-T03.yaml) — mirrors ``tests/unit/cli/test_export_cli_args.py``'s own "no
PostgreSQL required" discipline: MRR-NFR-012's explicit-degradation paths
(a missing/forbidden/unreadable/invalid ``--classification-file``, an
unreachable ``--database-url``) and the pre-existing ``--output`` refusal
are all exercised without a real database, by pointing ``--database-url`` at
a port nothing is listening on — the unreachable-database message (which
WOULD appear if the code ever reached that check) is absent from every test
except the one that specifically exercises it, which is itself proof of the
R4 ordering (output, then classification-file, then database — task-packets/
E8-T03.yaml R4's own "cheapest local dependency first").

Acceptance-test mapping (task-packets/E8-T03.yaml, unit tier):

- AT5's DB-free refusal/dependency paths (existing output file, missing
  classification file for public, classification file supplied for
  internal, and every classification-file shape/value problem) -> the tests
  under "MRR-NFR-012 ordering + refusal tests" below.
- --help/usage-error smoke tests -> the tests under "--help / usage-error
  smoke tests" below.

Acceptance-test mapping (task-packets/E8-T06.yaml, unit tier — the one-of
root group, all DB-free):

- --help documents the new flags -> the extended
  ``test_render_subcommand_help_documents_every_flag``.
- the mutually-exclusive, required root group (argparse's own usage error,
  exit 2) -> ``test_no_root_flag_at_all_is_a_usage_error``,
  ``test_crate_id_and_claim_id_together_is_a_usage_error``,
  ``test_crate_id_and_all_claims_together_is_a_usage_error``.
- ``--claim-id`` is repeatable, and reaches the (unreachable-database)
  dependency check like any other valid invocation ->
  ``test_claim_id_is_repeatable_and_reaches_the_database_check``.
- ``--classification-file``'s required-with-public/forbidden-with-internal
  rule applies identically to the claim-rooted path ->
  ``test_all_claims_public_disclosure_without_classification_file_is_a_dependency_failure``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mrr.domain.identity import new_urn
from mrr.services.cli.main import build_parser, main
from mrr.services.cli.report_main import build_parser as build_report_parser
from mrr.services.cli.report_main import main as report_main

#: 127.0.0.1:1 is a reserved, unassigned port that refuses connections
#: immediately on every CI/dev platform this project targets — mirrors
#: tests/unit/cli/test_export_cli_args.py's own identical constant/rationale.
_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"


def _args(
    *,
    database_url: str,
    crate_id: str,
    output: Path,
    fmt: str = "md",
    disclosure: str = "internal",
    classification_file: Path | None = None,
) -> list[str]:
    argv = [
        "report",
        "render",
        "--database-url",
        database_url,
        "--crate-id",
        crate_id,
        "--output",
        str(output),
        "--format",
        fmt,
        "--disclosure",
        disclosure,
    ]
    if classification_file is not None:
        argv.extend(["--classification-file", str(classification_file)])
    return argv


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_report_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "report" in capsys.readouterr().out


def test_render_subcommand_help_documents_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "render", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--database-url",
        "--crate-id",
        "--output",
        "--format",
        "--disclosure",
        "--classification-file",
    ):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_report_without_render_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report"])
    assert excinfo.value.code != 0


def test_invalid_format_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "render", "--format", "pdf"])
    assert excinfo.value.code != 0


def test_invalid_disclosure_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "render", "--disclosure", "secret"])
    assert excinfo.value.code != 0


def test_main_parser_registers_report_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["report", "render", "--help"])
    assert excinfo.value.code == 0


def test_build_report_parser_prog_name_is_mrr_report() -> None:
    parser = build_report_parser()
    assert parser.prog == "mrr report"


def test_standalone_report_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        report_main(["--help"])
    assert excinfo.value.code == 0
    assert "render" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# MRR-NFR-012 ordering + refusal tests, all DB-free.
# ---------------------------------------------------------------------------


def test_pre_existing_output_is_refused_before_any_other_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    output.write_text("i already exist")

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",  # would also need --classification-file — proves it's not reached
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "--classification-file" not in err
    assert "cannot reach the PostgreSQL database" not in err
    assert output.read_text() == "i already exist"


def test_public_disclosure_without_classification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "requires --classification-file" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output.exists()


def test_internal_disclosure_with_classification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    classification_file = tmp_path / "classification.json"
    classification_file.write_text(json.dumps({new_urn("claim"): "PUBLIC"}))

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="internal",
            classification_file=classification_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "forbids --classification-file" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output.exists()


def test_unreadable_classification_file_is_a_dependency_failure_before_db_connect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    missing_file = tmp_path / "does-not-exist.json"

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",
            classification_file=missing_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot read --classification-file" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output.exists()


def test_classification_file_with_invalid_json_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    classification_file = tmp_path / "classification.json"
    classification_file.write_text("{not valid json")

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",
            classification_file=classification_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert not output.exists()


def test_classification_file_that_is_not_a_json_object_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    classification_file = tmp_path / "classification.json"
    classification_file.write_text(json.dumps(["PUBLIC", "INTERNAL"]))

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",
            classification_file=classification_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "must be a JSON object" in err
    assert not output.exists()


def test_classification_file_with_an_unrecognized_value_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    classification_file = tmp_path / "classification.json"
    claim_id = new_urn("claim")
    classification_file.write_text(json.dumps({claim_id: "TOP_SECRET"}))

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",
            classification_file=classification_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert claim_id in err
    assert "TOP_SECRET" in err
    assert not output.exists()


def test_unreachable_database_is_a_dependency_failure_after_the_other_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    classification_file = tmp_path / "classification.json"
    classification_file.write_text(json.dumps({new_urn("claim"): "PUBLIC"}))

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="public",
            classification_file=classification_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
    assert not output.exists()


# ---------------------------------------------------------------------------
# task-packets/E8-T06.yaml: the one-of root group.
# ---------------------------------------------------------------------------


def test_render_subcommand_help_also_documents_the_new_root_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fresh, additive test rather than widening the pre-existing
    ``test_render_subcommand_help_documents_every_flag`` (task-packets/
    E8-T03.yaml) — see ``tests/unit/cli/test_export_cli_args.py``'s own
    identical, sibling test for why leaving that one byte-for-byte
    untouched is both sufficient and the more literal reading of
    "E8-T01..T05 suites must pass UNMODIFIED".
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "render", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--claim-id", "--all-claims"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_no_root_flag_at_all_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "report",
                "render",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--output",
                "/tmp/does-not-matter",
                "--format",
                "md",
                "--disclosure",
                "internal",
            ]
        )
    assert excinfo.value.code != 0


def test_crate_id_and_claim_id_together_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "report",
                "render",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--crate-id",
                new_urn("evidence-crate"),
                "--claim-id",
                new_urn("claim"),
                "--output",
                "/tmp/does-not-matter",
                "--format",
                "md",
                "--disclosure",
                "internal",
            ]
        )
    assert excinfo.value.code != 0


def test_crate_id_and_all_claims_together_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "report",
                "render",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--crate-id",
                new_urn("evidence-crate"),
                "--all-claims",
                "--output",
                "/tmp/does-not-matter",
                "--format",
                "md",
                "--disclosure",
                "internal",
            ]
        )
    assert excinfo.value.code != 0


def test_claim_id_is_repeatable_and_reaches_the_database_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"

    exit_code = main(
        [
            "report",
            "render",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--claim-id",
            new_urn("claim"),
            "--claim-id",
            new_urn("claim"),
            "--output",
            str(output),
            "--format",
            "md",
            "--disclosure",
            "internal",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err


def test_all_claims_public_disclosure_without_classification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pre-existing ``--classification-file`` rule applies identically
    regardless of root — task-packets/E8-T06.yaml: "Disclosure gating
    (E8-T03) ... apply unchanged".
    """
    output = tmp_path / "report.md"

    exit_code = main(
        [
            "report",
            "render",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--all-claims",
            "--output",
            str(output),
            "--format",
            "md",
            "--disclosure",
            "public",
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "requires --classification-file" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output.exists()


def test_unreachable_database_with_internal_disclosure_and_no_classification_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid, minimal internal-disclosure invocation (no
    --classification-file at all) still reaches, and fails at, the database
    check — proving internal disclosure's own happy path is not blocked by
    the classification-file validation step.
    """
    output = tmp_path / "report.md"

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            crate_id=new_urn("evidence-crate"),
            output=output,
            disclosure="internal",
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
    assert not output.exists()
