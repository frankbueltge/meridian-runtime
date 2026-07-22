"""DB-free unit/smoke tests for ``mrr release create``/``mrr release verify``
(task-packets/E8-T04.yaml) — mirrors ``tests/unit/cli/test_report_cli_args.py``'s/
``test_export_cli_args.py``'s own "no PostgreSQL required" discipline:
MRR-NFR-012's explicit-degradation paths and every REFUSAL that never needs
a database connection at all (the pre-existing ``--output-dir``, the A4
act's own no-default absence checks) are exercised without a real database,
by pointing ``--database-url`` at a port nothing is listening on.

Acceptance-test mapping (task-packets/E8-T04.yaml, unit tier):

- AT2's DB-free halves (missing --approved-by/--approval-statement-file/
  --approval-mode; the pre-existing --output-dir) -> the tests under
  "create: MRR-FR-102 no-default refusals" and "create: ordering" below.
  AT2's DB-needing halves (agent-role approver, empty statement FILE
  content) live at the integration tier instead, mirroring
  ``test_export_cli_ro_crate.py``'s own tier split for its identical
  DB-needing refusal cases.
- --help/usage-error smoke tests -> the tests under "--help / usage-error
  smoke tests" below.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.domain.identity import new_urn
from mrr.services.cli.main import build_parser, main
from mrr.services.cli.release_main import build_parser as build_release_parser
from mrr.services.cli.release_main import main as release_main

#: 127.0.0.1:1 is a reserved, unassigned port that refuses connections
#: immediately on every CI/dev platform this project targets — mirrors
#: every sibling CLI unit-test module's own identical constant/rationale.
_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"

_PLACEHOLDER_STATEMENT_FILE = "/nonexistent/placeholder-statement.txt"


def _create_args(
    *,
    database_url: str,
    artifact_root: Path,
    crate_id: str,
    output_dir: Path,
    disclosure: str = "internal",
    classification_file: Path | None = None,
    include_approved_by: bool = True,
    include_approval_statement_file: bool = True,
    approval_statement_file: Path | None = None,
    include_approval_mode: bool = True,
    policy_version: str = "policy-e8-t04-unit-test",
) -> list[str]:
    argv = [
        "release",
        "create",
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


def _verify_args(
    *,
    database_url: str,
    release_id: str,
    artifact_root: Path | None = None,
    bundle_dir: Path | None = None,
    classification_file: Path | None = None,
) -> list[str]:
    argv = ["release", "verify", "--database-url", database_url, "--release-id", release_id]
    if artifact_root is not None:
        argv.extend(["--artifact-root", str(artifact_root)])
    if bundle_dir is not None:
        argv.extend(["--bundle-dir", str(bundle_dir)])
    if classification_file is not None:
        argv.extend(["--classification-file", str(classification_file)])
    return argv


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_release_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "release" in capsys.readouterr().out


def test_create_subcommand_help_documents_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "create", "--help"])
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
    ):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_verify_subcommand_help_documents_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "verify", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--database-url",
        "--release-id",
        "--artifact-root",
        "--bundle-dir",
        "--classification-file",
    ):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_release_without_a_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release"])
    assert excinfo.value.code != 0


def test_invalid_disclosure_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "create", "--disclosure", "secret"])
    assert excinfo.value.code != 0


def test_invalid_approval_mode_choice_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["release", "create", "--approval-mode", "automatic"])
    assert excinfo.value.code != 0


def test_main_parser_registers_release_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["release", "create", "--help"])
    assert excinfo.value.code == 0


def test_build_release_parser_prog_name_is_mrr_release() -> None:
    parser = build_release_parser()
    assert parser.prog == "mrr release"


def test_standalone_release_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        release_main(["--help"])
    assert excinfo.value.code == 0
    assert "create" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# create: ordering — --output-dir conflict is checked before anything else.
# ---------------------------------------------------------------------------


def test_pre_existing_output_dir_is_refused_before_any_other_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "marker.txt").write_text("do not touch")

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            # Proves the output-dir check runs even before the A4-act
            # presence checks: none of the three are supplied, yet the
            # ERROR MESSAGE is about --output-dir, not MRR-FR-102.
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


def test_an_existing_empty_output_dir_is_not_a_conflict_by_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            include_approved_by=False,
            include_approval_statement_file=False,
            include_approval_mode=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" not in err
    assert "MRR-FR-102" in err


# ---------------------------------------------------------------------------
# create: MRR-FR-102 no-default refusals.
# ---------------------------------------------------------------------------


def test_missing_approved_by_is_refused_naming_mrr_fr_102(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            include_approved_by=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approved-by" in err
    assert "MRR-FR-102" in err
    assert "no default exists by design" in err
    assert not output_dir.exists()


def test_missing_approval_statement_file_is_refused_naming_mrr_fr_102(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            include_approval_statement_file=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approval-statement-file" in err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_missing_approval_mode_is_refused_naming_mrr_fr_102(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            include_approval_mode=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approval-mode" in err
    assert "MRR-FR-102" in err
    assert not output_dir.exists()


def test_all_three_missing_names_all_three_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            include_approved_by=False,
            include_approval_statement_file=False,
            include_approval_mode=False,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "--approved-by" in err
    assert "--approval-statement-file" in err
    assert "--approval-mode" in err


# ---------------------------------------------------------------------------
# create: --classification-file / --artifact-root / database ordering
# (mirrors test_report_cli_args.py's own identical structure).
# ---------------------------------------------------------------------------


def test_public_disclosure_without_classification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this release.")

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            disclosure="public",
            approval_statement_file=statement_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "requires --classification-file" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output_dir.exists()


def test_internal_disclosure_with_classification_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this release.")
    classification_file = tmp_path / "classification.json"
    classification_file.write_text("{}")

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            disclosure="internal",
            classification_file=classification_file,
            approval_statement_file=statement_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "forbids --classification-file" in err
    assert not output_dir.exists()


def test_unreadable_artifact_root_is_a_dependency_failure_before_db_connect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this release.")

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "does-not-exist",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            approval_statement_file=statement_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--artifact-root" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output_dir.exists()


def test_unreachable_database_is_a_dependency_failure_after_the_other_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    statement_file = tmp_path / "statement.txt"
    statement_file.write_text("Approving this release.")

    exit_code = main(
        _create_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
            approval_statement_file=statement_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
    assert not output_dir.exists()


# ---------------------------------------------------------------------------
# verify: DB-free dependency checks.
# ---------------------------------------------------------------------------


def test_verify_rebuild_mode_requires_artifact_root(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        _verify_args(database_url=_UNREACHABLE_DATABASE_URL, release_id=new_urn("release-record"))
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--artifact-root" in err
    assert "cannot reach the PostgreSQL database" not in err


def test_verify_bundle_dir_mode_requires_an_existing_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_bundle_dir = tmp_path / "does-not-exist"

    exit_code = main(
        _verify_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            release_id=new_urn("release-record"),
            bundle_dir=missing_bundle_dir,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--bundle-dir" in err
    assert "cannot reach the PostgreSQL database" not in err


def test_verify_bundle_dir_mode_forbids_classification_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    classification_file = tmp_path / "classification.json"
    classification_file.write_text("{}")

    exit_code = main(
        _verify_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            release_id=new_urn("release-record"),
            bundle_dir=bundle_dir,
            classification_file=classification_file,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "forbidden with --bundle-dir" in err
    assert "cannot reach the PostgreSQL database" not in err


def test_verify_unreachable_database_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    exit_code = main(
        _verify_args(
            database_url=_UNREACHABLE_DATABASE_URL,
            release_id=new_urn("release-record"),
            artifact_root=artifact_root,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
