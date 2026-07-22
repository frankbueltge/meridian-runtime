"""DB-free unit/smoke tests for ``mrr release supersede``/``mrr release
status`` (task-packets/E8-T05.yaml) — mirrors ``tests/unit/cli
/test_release_cli_args.py``'s own "no PostgreSQL required" discipline: every
refusal that never needs a database connection at all (missing A4 inputs on
``supersede``, ``--output-dir`` ordering) is exercised without a real
database, by pointing ``--database-url`` at a port nothing is listening on.
A NEW file (not an edit of that E8-T04 file, which must pass unmodified per
task-packets/E8-T05.yaml).

Acceptance-test mapping (task-packets/E8-T05.yaml, unit tier):

- R4's own "the full create flag set of E8-T04 R4 PLUS --supersedes" ->
  ``test_supersede_subcommand_help_documents_every_create_flag_plus_supersedes``.
- AT5's DB-free refusal halves (missing A4 inputs; output-dir ordering) ->
  the tests under "supersede: DB-free refusals" below.
- --help/usage-error smoke tests -> the tests under "--help / usage-error
  smoke tests" below.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.domain.identity import new_urn
from mrr.services.cli.main import main
from mrr.services.cli.release_main import build_parser as build_release_parser
from mrr.services.cli.release_main import main as release_main

_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"

_PLACEHOLDER_STATEMENT_FILE = "/nonexistent/placeholder-statement.txt"


def _supersede_args(
    *,
    database_url: str,
    artifact_root: Path,
    crate_id: str,
    output_dir: Path,
    supersedes: str,
    disclosure: str = "internal",
    classification_file: Path | None = None,
    include_approved_by: bool = True,
    include_approval_statement_file: bool = True,
    approval_statement_file: Path | None = None,
    include_approval_mode: bool = True,
    policy_version: str = "policy-e8-t05-unit-test",
) -> list[str]:
    argv = [
        "release",
        "supersede",
        "--database-url",
        database_url,
        "--artifact-root",
        str(artifact_root),
        "--crate-id",
        crate_id,
        "--disclosure",
        disclosure,
        "--output-dir",
        str(output_dir),
        "--policy-version",
        policy_version,
        "--supersedes",
        supersedes,
    ]
    if classification_file is not None:
        argv.extend(["--classification-file", str(classification_file)])
    if include_approved_by:
        argv.extend(["--approved-by", new_urn("person")])
    if include_approval_statement_file:
        statement_file = (
            str(approval_statement_file)
            if approval_statement_file is not None
            else _PLACEHOLDER_STATEMENT_FILE
        )
        argv.extend(["--approval-statement-file", statement_file])
    if include_approval_mode:
        argv.extend(["--approval-mode", "single_human"])
    return argv


def _status_args(*, database_url: str, release_id: str) -> list[str]:
    return ["release", "status", "--database-url", database_url, "--release-id", release_id]


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_supersede_and_status(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "supersede" in out
    assert "status" in out


def test_supersede_subcommand_help_documents_every_create_flag_plus_supersedes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "supersede", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--database-url",
        "--artifact-root",
        "--crate-id",
        "--disclosure",
        "--classification-file",
        "--output-dir",
        "--approved-by",
        "--approval-statement-file",
        "--approval-mode",
        "--policy-version",
        "--correlation-id",
        "--supersedes",
    ):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_status_subcommand_help_documents_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "status", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--database-url", "--release-id"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_supersede_without_supersedes_flag_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "supersede", "--database-url", _UNREACHABLE_DATABASE_URL])
    assert excinfo.value.code != 0


def test_status_without_release_id_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "status", "--database-url", _UNREACHABLE_DATABASE_URL])
    assert excinfo.value.code != 0


def test_standalone_release_main_help_mentions_supersede_and_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        release_main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "supersede" in out
    assert "status" in out


def test_build_release_parser_registers_supersede_and_status() -> None:
    parser = build_release_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["supersede", "--help"])
    assert excinfo.value.code == 0
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["status", "--help"])
    assert excinfo.value.code == 0


# ---------------------------------------------------------------------------
# supersede: DB-free refusals (mirrors test_release_cli_args.py's own
# create-side structure).
# ---------------------------------------------------------------------------


def test_supersede_output_dir_conflict_is_refused_before_any_other_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "marker.txt").write_text("do not touch")

    exit_code = main(
        _supersede_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            supersedes=new_urn("release-record"),
            include_approved_by=False,
            include_approval_statement_file=False,
            include_approval_mode=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "MRR-FR-102" not in err
    assert (output_dir / "marker.txt").read_text() == "do not touch"


def test_supersede_missing_approved_by_is_refused_naming_mrr_fr_102(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _supersede_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            supersedes=new_urn("release-record"),
            include_approved_by=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approved-by" in err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_supersede_missing_approval_statement_file_is_refused_naming_mrr_fr_102(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _supersede_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            supersedes=new_urn("release-record"),
            include_approval_statement_file=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approval-statement-file" in err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_supersede_missing_approval_mode_is_refused_naming_mrr_fr_102(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _supersede_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            supersedes=new_urn("release-record"),
            include_approval_mode=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approval-mode" in err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_supersede_public_disclosure_without_classification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this supersession.")

    exit_code = main(
        _supersede_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            supersedes=new_urn("release-record"),
            disclosure="public",
            approval_statement_file=statement_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "requires --classification-file" in err
    assert not output_dir.exists()


def test_supersede_unreachable_database_is_a_dependency_failure_after_the_other_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this supersession.")

    exit_code = main(
        _supersede_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            supersedes=new_urn("release-record"),
            approval_statement_file=statement_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
    assert not output_dir.exists()


# ---------------------------------------------------------------------------
# status: DB-free dependency check.
# ---------------------------------------------------------------------------


def test_status_unreachable_database_is_a_dependency_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        _status_args(database_url=_UNREACHABLE_DATABASE_URL, release_id=new_urn("release-record"))
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
