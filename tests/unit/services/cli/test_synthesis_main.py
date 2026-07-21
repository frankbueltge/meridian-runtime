"""DB-free unit/smoke tests for ``mrr synthesis run`` (task-packets/
K1-T04.yaml) — mirrors ``tests/unit/services/cli/test_main.py``'s own
"no PostgreSQL required" discipline: MRR-NFR-012's explicit-degradation path
is itself exercised without a real database, by pointing ``--database-url``
at a port nothing is listening on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mrr.services.cli.main import build_parser, main
from mrr.services.cli.synthesis_main import (
    _DEFAULT_CONCEPT_CHARTER_FILE,
    _DEFAULT_CORPUS_FILE,
    _DEFAULT_METHOD_PROTOCOL_FILE,
    _DEFAULT_PROTOCOL_PARAMETERS_FILE,
    _DEFAULT_QUESTION_MODEL_FILE,
    _resolve_code_revision,
)
from mrr.services.cli.synthesis_main import build_parser as build_synthesis_parser
from mrr.services.cli.synthesis_main import main as synthesis_main

_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://mrr:mrr@127.0.0.1:1/mrr_test"


def test_top_level_help_mentions_the_synthesis_subcommand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "synthesis" in out


def test_synthesis_run_subcommand_help_exits_zero_and_lists_fixture_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["synthesis", "run", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--database-url" in out
    assert "--artifact-root" in out
    assert "--question-model-file" in out
    assert "--concept-charter-file" in out
    assert "--method-protocol-file" in out
    assert "--corpus-file" in out
    assert "--protocol-parameters-file" in out


def test_synthesis_without_run_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["synthesis"])
    assert excinfo.value.code != 0


def test_default_fixture_paths_resolve_to_the_committed_corpora_directory() -> None:
    for path in (
        _DEFAULT_QUESTION_MODEL_FILE,
        _DEFAULT_CONCEPT_CHARTER_FILE,
        _DEFAULT_METHOD_PROTOCOL_FILE,
        _DEFAULT_CORPUS_FILE,
        _DEFAULT_PROTOCOL_PARAMETERS_FILE,
    ):
        assert path.is_file(), f"expected committed fixture at {path}"
        assert path.parts[-3:-1] == ("corpora", "model-collapse")


def test_run_reports_an_explicit_degraded_message_when_database_is_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "synthesis",
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


def test_build_parser_prog_name_is_mrr_synthesis() -> None:
    parser = build_synthesis_parser()
    assert parser.prog == "mrr synthesis"


def test_standalone_synthesis_main_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        synthesis_main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "run" in out


def test_main_parser_registers_synthesis_group() -> None:
    parser = build_parser()
    assert parser.prog == "mrr"
    # Sanity: parsing "synthesis run --help" doesn't blow up with an
    # unrecognized-subcommand error (argparse would exit 2, not 0).
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["synthesis", "run", "--help"])
    assert excinfo.value.code == 0


def test_resolve_code_revision_is_none_when_neither_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MRR_CODE_COMMIT", raising=False)
    assert _resolve_code_revision(None) is None


def test_resolve_code_revision_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MRR_CODE_COMMIT", "env-value")
    assert _resolve_code_revision(None) == "env-value"
