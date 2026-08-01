"""task-packets/N1-T02.yaml AT4 (fail-closed on absence) and AT6 (determinism)
at the command line, plus the exit-code map ``mrr validate gold`` promises.

The central assertion of this file: when the standard is missing or has moved,
the command produces NO report — not a partial one, not a provisional one, not
one with a warning at the top. A measuring instrument that emits a number when
its reference is gone is worse than one that emits nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mrr.services.cli import validation_main
from mrr.services.validation.gold_service import compute_sha256

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures"
FIXTURE = FIXTURES / "mb-cls-v3.synthetic.json"
PREDICTIONS = FIXTURES / "mb-cls-v3.synthetic.predictions.json"

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3


def _run(*argv: str) -> int:
    return validation_main.main(["gold", *argv])


def test_at4_a_missing_gold_set_exits_two_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    exit_code = _run(
        "--gold-set",
        str(tmp_path / "absent.json"),
        "--predictions",
        str(PREDICTIONS),
        "--output",
        str(output),
    )
    assert exit_code == _EXIT_DEPENDENCY_UNAVAILABLE
    assert not output.exists()
    assert "cannot read file" in capsys.readouterr().err


def test_a_moved_standard_exits_three_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    exit_code = _run(
        "--gold-set",
        str(FIXTURE),
        "--predictions",
        str(PREDICTIONS),
        "--expect-sha256",
        "sha256:" + "0" * 64,
        "--allow-synthetic",
        "--output",
        str(output),
    )
    assert exit_code == _EXIT_REFUSED
    assert not output.exists()
    assert "is not frozen" in capsys.readouterr().err


def test_a_synthetic_set_without_the_opt_in_exits_three(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run("--gold-set", str(FIXTURE), "--predictions", str(PREDICTIONS))
    assert exit_code == _EXIT_REFUSED
    assert "--allow-synthetic" in capsys.readouterr().err


def test_an_existing_output_is_refused_before_anything_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "taken.md"
    output.write_text("do not overwrite me", encoding="utf-8")
    exit_code = _run(
        "--gold-set",
        str(FIXTURE),
        "--predictions",
        str(PREDICTIONS),
        "--allow-synthetic",
        "--output",
        str(output),
    )
    assert exit_code == _EXIT_REFUSED
    assert output.read_text(encoding="utf-8") == "do not overwrite me"
    assert "already exists" in capsys.readouterr().err


def test_a_successful_run_writes_the_report_and_a_json_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.md"
    pinned = compute_sha256(FIXTURE.read_bytes())
    exit_code = _run(
        "--gold-set",
        str(FIXTURE),
        "--predictions",
        str(PREDICTIONS),
        "--expect-sha256",
        pinned,
        "--allow-synthetic",
        "--output",
        str(output),
    )
    assert exit_code == 0

    confirmation = json.loads(capsys.readouterr().out)
    assert confirmation["fixture_set_id"] == f"mb-cls-v3-synthetic@{pinned}"
    assert confirmation["n"] == 20
    assert confirmation["observed_agreement"] == 0.7
    assert confirmation["majority_baseline"] == 0.5
    assert confirmation["below_power"] is True

    rendered = output.read_text(encoding="utf-8")
    assert "# Gold-standard validity report" in rendered
    assert "| Accuracy (observed agreement) | 0.7000 |" in rendered
    assert "| Majority-class baseline | 0.5000 |" in rendered
    assert "| False-support rate | 0.3000 (3/10) |" in rendered


def test_at6_two_runs_produce_byte_identical_output(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    for output in (first, second):
        assert (
            _run(
                "--gold-set",
                str(FIXTURE),
                "--predictions",
                str(PREDICTIONS),
                "--allow-synthetic",
                "--format",
                "json",
                "--output",
                str(output),
            )
            == 0
        )
    assert first.read_bytes() == second.read_bytes()


def test_the_agreement_subcommand_still_routes_to_its_own_handler() -> None:
    # N1-T01's command must not have been captured by the new sibling: both
    # live under `validate`, and run_command now dispatches between them.
    parser = validation_main.build_parser()
    args = parser.parse_args(["agreement", "--analysis-set", "x.json"])
    assert validation_main._selected_subcommand(args) == "agreement"

    gold_args = parser.parse_args(["gold", "--gold-set", "g.json", "--predictions", "p.json"])
    assert validation_main._selected_subcommand(gold_args) == "gold"
