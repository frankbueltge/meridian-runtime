"""DB-free unit tests for ``mrr synthesis run``'s new, additive
``--sensitivity-variation-parameters-file`` flag (task-packets/
K1-T04c.yaml). Mirrors tests/unit/services/cli/test_synthesis_main.py's own
"no PostgreSQL required" discipline: MRR-NFR-012's explicit-degradation path
is itself exercised without a real database, by pointing ``--database-url``
at a port nothing is listening on. That file is NOT modified by this packet
(forbidden_changes) — this is a new, sibling file.

Acceptance-test mapping (task-packets/K1-T04c.yaml):

- "[unit, CLI option parsing] ... --help output lists
  --sensitivity-variation-parameters-file" ->
  ``test_synthesis_run_help_lists_the_new_flag``.
- "... parsing the run subcommand without that flag resolves
  args.sensitivity_variation_parameters_file is None" ->
  ``test_flag_defaults_to_none_when_omitted``.
- "... passing the new flag alongside an unreachable --database-url still
  exits non-zero with the SAME 'cannot reach the PostgreSQL database'
  stderr message ... proving the new flag parses cleanly and does not shift
  MRR-NFR-012's own check ordering" ->
  ``test_run_with_the_new_flag_still_reports_the_same_unreachable_database_message``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.services.cli.main import build_parser, main

_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"


def test_synthesis_run_help_lists_the_new_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["synthesis", "run", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--sensitivity-variation-parameters-file" in out


def test_flag_defaults_to_none_when_omitted(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "synthesis",
            "run",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )
    assert args.sensitivity_variation_parameters_file is None


def test_flag_accepts_a_path_when_given(tmp_path: Path) -> None:
    variation_file = tmp_path / "sensitivity-variation-parameters.json"
    parser = build_parser()
    args = parser.parse_args(
        [
            "synthesis",
            "run",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--sensitivity-variation-parameters-file",
            str(variation_file),
        ]
    )
    assert args.sensitivity_variation_parameters_file == variation_file


def test_run_with_the_new_flag_still_reports_the_same_unreachable_database_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The file need not exist: the --database-url reachability check
    # (MRR-NFR-012) runs BEFORE any fixture file is ever read, so an
    # unreachable database is reported first regardless.
    variation_file = tmp_path / "sensitivity-variation-parameters.json"
    exit_code = main(
        [
            "synthesis",
            "run",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--sensitivity-variation-parameters-file",
            str(variation_file),
        ]
    )

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
