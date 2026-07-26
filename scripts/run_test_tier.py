"""Run pytest for one or more test tiers, honoring declared-empty tiers.

An empty test suite must never be reported as meaningful feature completion
(E1-T01 invariant). A tier that collects zero tests, or whose directory does
not exist yet, is only acceptable when the tier is listed in
``tests/EMPTY_TIERS.txt``. In that case the script prints an explicit
"EXPECTED EMPTY" line and exits 0. Any other empty or missing tier is a
failure, not a silent pass.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 # no shell=True anywhere in this module; see run_tier below
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EMPTY_TIERS_FILE = REPO_ROOT / "tests" / "EMPTY_TIERS.txt"
PYTEST_NO_TESTS_COLLECTED = 5

TIER_DIRECTORIES: dict[str, Path] = {
    "unit": REPO_ROOT / "tests" / "unit",
    "property": REPO_ROOT / "tests" / "property",
    "contract": REPO_ROOT / "tests" / "contract",
    "integration": REPO_ROOT / "tests" / "integration",
    "e2e": REPO_ROOT / "tests" / "e2e",
    "adversarial": REPO_ROOT / "tests" / "adversarial",
    "meridianbench": REPO_ROOT / "benchmarks" / "meridianbench",
}


def load_declared_empty_tiers() -> set[str]:
    """Read the tier names listed in tests/EMPTY_TIERS.txt (comments/blank lines skipped)."""
    if not EMPTY_TIERS_FILE.exists():
        return set()
    declared: set[str] = set()
    for line in EMPTY_TIERS_FILE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        declared.add(stripped)
    return declared


def run_tier(tier: str, declared_empty: set[str]) -> int:
    """Run pytest for a single tier and return its effective exit code."""
    directory = TIER_DIRECTORIES[tier]

    if not directory.is_dir():
        if tier in declared_empty:
            print(
                f"{tier}: EXPECTED EMPTY — declared in tests/EMPTY_TIERS.txt, "
                "not feature completion"
            )
            return 0
        print(
            f"error: tier '{tier}' has no directory at {directory} and is not "
            "declared in tests/EMPTY_TIERS.txt",
            file=sys.stderr,
        )
        return 1

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(directory)],
        cwd=REPO_ROOT,
        check=False,  # nosec B603 # no shell=True; args from a closed set of known test tiers
    )

    if result.returncode == PYTEST_NO_TESTS_COLLECTED:
        if tier in declared_empty:
            print(
                f"{tier}: EXPECTED EMPTY — declared in tests/EMPTY_TIERS.txt, "
                "not feature completion"
            )
            return 0
        print(
            f"error: tier '{tier}' collected no tests and is not declared in tests/EMPTY_TIERS.txt",
            file=sys.stderr,
        )
        return 1

    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tiers", nargs="+", choices=sorted(TIER_DIRECTORIES))
    args = parser.parse_args(argv)
    tiers: list[str] = args.tiers

    declared_empty = load_declared_empty_tiers()

    exit_code = 0
    for tier in tiers:
        tier_exit_code = run_tier(tier, declared_empty)
        if tier_exit_code != 0:
            exit_code = tier_exit_code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
