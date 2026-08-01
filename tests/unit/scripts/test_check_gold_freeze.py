"""Tests for ``scripts/check_gold_freeze.py`` (task-packets/N1-T04.yaml AT7,
and N1-T02's own AT2 discharged retroactively).

The script has been running in CI since N1-T02 (.github/workflows/ci.yml)
without a test of its own — the packet that introduced it asserted its
behaviour in prose and never executed the assertion. This file executes it.

The occasion is N1-T04's finding K1: the registry held the three criteria
versions and the three SYNTHETIC fixtures, but not the real standard, so the
one mechanism built to catch a moved gold set did not cover the gold set. It
does now, and this file is what keeps it that way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_gold_freeze

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "FROZEN.json"
RESTAMPED_SET_ID = "mb-cls-ulysses-v1-restamped"


def _registry() -> dict[str, dict[str, str]]:
    frozen: dict[str, dict[str, str]] = json.loads(REGISTRY.read_text(encoding="utf-8"))["frozen"]
    return frozen


def test_the_real_standard_is_registered_not_only_the_synthetic_fixtures() -> None:
    """K1's repair, asserted on the registry itself.

    Before N1-T04 the six entries were criteria v1/v2/v3 and the three
    synthetic fixtures. The set the first real measurement was taken against
    was absent, so `check_gold_freeze` — whose entire purpose is to notice a
    moved standard — did not cover the standard.
    """
    frozen = _registry()
    assert RESTAMPED_SET_ID in frozen
    entry = frozen[RESTAMPED_SET_ID]
    assert entry["path"] == "corpora/gold-classification/mb-cls-ulysses-v1-restamped.json"
    assert entry["sha256"].startswith("sha256:")


def test_the_committed_registry_passes_against_the_committed_files() -> None:
    """The script, actually run, against the real tree."""
    assert check_gold_freeze.main() == 0


def test_a_moved_version_fails_and_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The behaviour the whole file exists for: a byte moves, the check fails.

    Run against a synthetic tree rather than by editing a sealed corpus file
    — the archive discipline holds inside tests too.
    """
    standard = tmp_path / "standard.json"
    standard.write_text('{"set_id": "s"}', encoding="utf-8")
    registry = tmp_path / "FROZEN.json"
    registry.write_text(
        json.dumps({"frozen": {"s": {"path": "standard.json", "sha256": "sha256:" + "0" * 64}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_gold_freeze, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_gold_freeze, "REGISTRY_PATH", registry)

    assert check_gold_freeze.main() == 1
    assert "s" in capsys.readouterr().err


def test_an_unchanged_version_passes_in_the_same_synthetic_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same assertion — otherwise "it failed" proves
    only that the script fails.
    """
    import hashlib

    standard = tmp_path / "standard.json"
    standard.write_text('{"set_id": "s"}', encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(standard.read_bytes()).hexdigest()
    registry = tmp_path / "FROZEN.json"
    registry.write_text(
        json.dumps({"frozen": {"s": {"path": "standard.json", "sha256": digest}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(check_gold_freeze, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_gold_freeze, "REGISTRY_PATH", registry)

    assert check_gold_freeze.main() == 0


def test_a_missing_registry_is_a_distinct_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2, not 1: an unusable registry is a different situation from a moved
    standard, and the script's own docstring promises the distinction.
    """
    monkeypatch.setattr(check_gold_freeze, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_gold_freeze, "REGISTRY_PATH", tmp_path / "absent.json")

    assert check_gold_freeze.main() == 2
