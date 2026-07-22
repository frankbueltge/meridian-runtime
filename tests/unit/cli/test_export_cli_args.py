"""DB-free unit/smoke tests for ``mrr export ro-crate`` (task-packets/
E8-T01.yaml) — mirrors ``tests/unit/cli/test_verification_cli_args.py``'s
own "no PostgreSQL required" discipline: MRR-NFR-012's explicit-degradation
paths (an unreadable ``--artifact-root``, an unreachable ``--database-url``)
and the pre-existing ``--output-dir`` refusal are all exercised without a
real database, by pointing ``--database-url`` at a port nothing is
listening on — the unreachable-database message (which WOULD appear if the
code ever reached that check) is absent from every test except the one that
specifically exercises it, which is itself proof of the R5 ordering
(output-dir, then artifact-root, then database — task-packets/E8-T01.yaml
R5's own "cheapest local dependency first").

Acceptance-test mapping (task-packets/E8-T01.yaml, unit tier):

- AT3's DB-free refusal/dependency paths ->
  ``test_pre_existing_output_file_is_refused_before_any_other_check``,
  ``test_pre_existing_non_empty_output_dir_is_refused_and_left_untouched``,
  ``test_unreadable_artifact_root_is_a_dependency_failure_before_db_connect``,
  ``test_artifact_root_that_is_a_file_is_a_dependency_failure``,
  ``test_unreachable_database_is_a_dependency_failure_after_the_other_two_checks``.
- --help/usage-error smoke tests (mirroring the K1-T05 precedent's own
  ``--help`` coverage) -> the tests under "--help / usage-error smoke
  tests" below.

Acceptance-test mapping (task-packets/E8-T06.yaml, unit tier — the one-of
root group and the ``--artifact-root`` shape refusal, all DB-free):

- --help documents the new flags -> the extended
  ``test_ro_crate_subcommand_help_documents_every_flag``.
- the mutually-exclusive, required root group (argparse's own usage error,
  exit 2) -> ``test_no_root_flag_at_all_is_a_usage_error``,
  ``test_crate_id_and_claim_id_together_is_a_usage_error``,
  ``test_crate_id_and_all_claims_together_is_a_usage_error``.
- ``--artifact-root`` REQUIRED with ``--crate-id``, FORBIDDEN with
  ``--claim-id``/``--all-claims`` (the new, disclosed-exit-2 usage-refusal
  shape check) -> ``test_crate_id_without_artifact_root_is_a_dependency_failure``,
  ``test_claim_id_with_artifact_root_is_a_dependency_failure``,
  ``test_all_claims_with_artifact_root_is_a_dependency_failure``,
  ``test_artifact_root_shape_check_runs_before_the_existence_check``.
- ``--claim-id`` is repeatable -> ``test_claim_id_is_repeatable``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.domain.identity import new_urn
from mrr.services.cli.export_main import build_parser as build_export_parser
from mrr.services.cli.export_main import main as export_main
from mrr.services.cli.main import build_parser, main

#: 127.0.0.1:1 is a reserved, unassigned port that refuses connections
#: immediately on every CI/dev platform this project targets — mirrors
#: tests/unit/cli/test_verification_cli_args.py's own identical
#: constant/rationale.
_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"


def _args(*, database_url: str, artifact_root: Path, crate_id: str, output_dir: Path) -> list[str]:
    return [
        "export",
        "ro-crate",
        "--database-url",
        database_url,
        "--artifact-root",
        str(artifact_root),
        "--crate-id",
        crate_id,
        "--output-dir",
        str(output_dir),
    ]


# ---------------------------------------------------------------------------
# --help / usage-error smoke tests.
# ---------------------------------------------------------------------------


def test_top_level_help_mentions_the_export_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert "export" in capsys.readouterr().out


def test_ro_crate_subcommand_help_documents_every_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["export", "ro-crate", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--database-url", "--artifact-root", "--crate-id", "--output-dir"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_export_without_ro_crate_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["export"])
    assert excinfo.value.code != 0


def test_main_parser_registers_export_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["export", "ro-crate", "--help"])
    assert excinfo.value.code == 0


def test_build_parser_prog_name_is_mrr_export() -> None:
    parser = build_export_parser()
    assert parser.prog == "mrr export"


def test_standalone_export_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        export_main(["--help"])
    assert excinfo.value.code == 0
    assert "ro-crate" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# MRR-NFR-012 ordering + refusal tests, all DB-free.
# ---------------------------------------------------------------------------


def test_pre_existing_output_file_is_refused_before_any_other_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.write_text("i am a file, not a directory")
    artifact_root = tmp_path / "artifacts"  # deliberately does not exist either

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    # Proof no later check ran at all.
    assert "--artifact-root" not in err
    assert "cannot reach the PostgreSQL database" not in err
    assert output_dir.read_text() == "i am a file, not a directory"


def test_pre_existing_non_empty_output_dir_is_refused_and_left_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    marker = output_dir / "pre-existing.txt"
    marker.write_text("do not touch me")

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=tmp_path / "artifacts",
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
        )
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert list(output_dir.iterdir()) == [marker]
    assert marker.read_text() == "do not touch me"


def test_an_existing_empty_output_dir_is_not_a_conflict_by_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An existing EMPTY ``--output-dir`` is explicitly NOT a refusal
    condition (``mrr.services.export.service.output_path_conflict``'s own
    documented invariant) — this test proves the CLI's OWN pre-check agrees:
    it passes the output-dir check and proceeds to the NEXT one
    (``--artifact-root``), never reporting "already exists".
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()  # exists, but empty
    artifact_root = tmp_path / "does-not-exist"

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "already exists" not in err
    assert "--artifact-root" in err


def test_unreadable_artifact_root_is_a_dependency_failure_before_db_connect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"  # does not exist -> no conflict
    artifact_root = tmp_path / "does-not-exist"

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--artifact-root" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output_dir.exists()


def test_artifact_root_that_is_a_file_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    artifact_root_file = tmp_path / "artifact-root-is-a-file"
    artifact_root_file.write_text("not a directory")

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root_file,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--artifact-root" in err
    assert "cannot reach the PostgreSQL database" not in err


def test_unreachable_database_is_a_dependency_failure_after_the_other_two_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    exit_code = main(
        _args(
            database_url=_UNREACHABLE_DATABASE_URL,
            artifact_root=artifact_root,
            crate_id=new_urn("evidence-crate"),
            output_dir=output_dir,
        )
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
    assert not output_dir.exists()


# ---------------------------------------------------------------------------
# task-packets/E8-T06.yaml: the one-of root group + --artifact-root shape.
# ---------------------------------------------------------------------------


def test_ro_crate_subcommand_help_also_documents_the_new_root_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A fresh, additive test rather than widening the pre-existing
    ``test_ro_crate_subcommand_help_documents_every_flag`` (task-packets/
    E8-T01.yaml) — that test's own original assertion already passes
    unmodified (it checks a SUBSET of --help's output is present, and
    --help output only grew), so leaving it byte-for-byte untouched is both
    sufficient and the more literal reading of "E8-T01..T05 suites must
    pass UNMODIFIED".
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["export", "ro-crate", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--claim-id", "--all-claims"):
        assert flag in out, f"expected {flag!r} to be documented in --help output"


def test_no_root_flag_at_all_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "export",
                "ro-crate",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--output-dir",
                "/tmp/does-not-matter",
            ]
        )
    assert excinfo.value.code != 0


def test_crate_id_and_claim_id_together_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "export",
                "ro-crate",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--crate-id",
                new_urn("evidence-crate"),
                "--claim-id",
                new_urn("claim"),
                "--output-dir",
                "/tmp/does-not-matter",
            ]
        )
    assert excinfo.value.code != 0


def test_crate_id_and_all_claims_together_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "export",
                "ro-crate",
                "--database-url",
                _UNREACHABLE_DATABASE_URL,
                "--crate-id",
                new_urn("evidence-crate"),
                "--all-claims",
                "--output-dir",
                "/tmp/does-not-matter",
            ]
        )
    assert excinfo.value.code != 0


def test_claim_id_is_repeatable(tmp_path: Path) -> None:
    """Argparse itself accepts a repeated ``--claim-id`` (the mutually-
    exclusive group's own "one distinct flag" check does not count a
    repeated occurrence of the SAME flag as two) — proven by reaching PAST
    argument parsing into the actual dependency check (an unreachable DB),
    not by a usage error.
    """
    output_dir = tmp_path / "output"
    exit_code = main(
        [
            "export",
            "ro-crate",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--claim-id",
            new_urn("claim"),
            "--claim-id",
            new_urn("claim"),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 2  # unreachable database, not a usage error


def test_crate_id_without_artifact_root_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "export",
            "ro-crate",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--crate-id",
            new_urn("evidence-crate"),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--crate-id requires --artifact-root" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output_dir.exists()


def test_claim_id_with_artifact_root_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    exit_code = main(
        [
            "export",
            "ro-crate",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--claim-id",
            new_urn("claim"),
            "--artifact-root",
            str(artifact_root),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--artifact-root is forbidden with --claim-id/--all-claims" in err
    assert "cannot reach the PostgreSQL database" not in err
    assert not output_dir.exists()


def test_all_claims_with_artifact_root_is_a_dependency_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "output"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    exit_code = main(
        [
            "export",
            "ro-crate",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--all-claims",
            "--artifact-root",
            str(artifact_root),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "--artifact-root is forbidden with --claim-id/--all-claims" in err
    assert not output_dir.exists()


def test_artifact_root_shape_check_runs_before_the_existence_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MRR-NFR-012 ordering: the pure argument-consistency check (no I/O at
    all) fires even when ``--artifact-root`` would ALSO fail its own
    existence check — proof the shape check runs first, not incidentally.
    """
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "export",
            "ro-crate",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--crate-id",
            new_urn("evidence-crate"),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "requires --artifact-root" in err
    assert "does not exist or is not a directory" not in err
