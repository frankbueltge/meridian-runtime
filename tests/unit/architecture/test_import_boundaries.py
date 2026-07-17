"""Import-boundary acceptance test for E1-T01 (MRR-NFR-010).

Runs import-linter as a subprocess against the contract declared in
pyproject.toml and asserts that it reports no violations. This is the named
acceptance test for the task packet: core packages (mrr.domain, mrr.crypto,
mrr.contracts, mrr.policy, mrr.provenance, mrr.observability) must not import
FastAPI, Temporal, a model-provider SDK, or an object-store client SDK.

Invocation note: import-linter ships no ``__main__.py``, so ``python -m
importlinter`` imports the package without running anything and always
exits 0 — that would make this test a no-op that always passes. The
``lint-imports`` console script (the same one ``make lint`` runs) is the
entry point that actually executes the check, so it is invoked here instead.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_import_boundaries_hold() -> None:
    lint_imports = shutil.which("lint-imports")
    assert lint_imports is not None, "lint-imports console script not found on PATH"

    result = subprocess.run(
        [lint_imports],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "import-linter reported a contract violation:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
