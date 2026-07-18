"""DB-free unit/smoke tests for the ``mrr`` console-script entry point
(task-packets/E2-T07.yaml). No PostgreSQL is required anywhere in this
module — that is the point: "``mrr`` console script is invocable and drives
the flow (a smoke invocation, e.g. --help and a dry sub-command, works)" is a
DB-free acceptance test, and MRR-NFR-012's "explicit degraded message, never
a fabricated substitute" is itself exercised without a real database, by
pointing ``--database-url`` at a port nothing is listening on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.services.cli.main import build_parser, main
from mrr.services.cli.orchestration import LocalEvidenceLoopResult, run_local_evidence_loop

#: A URL nothing is listening on — 127.0.0.1:1 is a reserved, unassigned port
#: that refuses connections immediately on every CI/dev platform this project
#: targets, so this exercises the "database unreachable" path fast, without
#: waiting for a real connect timeout.
_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"


def test_orchestration_module_is_importable() -> None:
    # The mere fact these names resolve is the acceptance test: the CLI and
    # tests/e2e both import this exact function/dataclass.
    assert callable(run_local_evidence_loop)
    assert LocalEvidenceLoopResult is not None


def test_top_level_help_exits_zero_and_mentions_the_run_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "run" in out


def test_run_subcommand_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["run", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--database-url" in out
    assert "--artifact-root" in out


def test_no_subcommand_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code != 0


def test_build_parser_prog_name_is_mrr() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"


def test_run_reports_an_explicit_degraded_message_when_database_is_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MRR-NFR-012: an unreachable dependency fails with an explicit message
    and a non-zero exit code — never a fabricated substitute result.
    """
    exit_code = main(
        [
            "run",
            "--database-url",
            _UNREACHABLE_DATABASE_URL,
            "--artifact-root",
            str(tmp_path / "artifacts"),
        ]
    )

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "cannot reach the PostgreSQL database" in err
