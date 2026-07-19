"""conftest for the meridianbench tier (task-packets/E4-T07.yaml).

``benchmarks`` is a plain on-disk package, not an installed distribution
(unlike ``mrr``, which is always importable via the project's own editable
install regardless of the current working directory — see
``benchmarks/__init__.py``'s own docstring for why this package stays
outside that install). ``scripts/run_test_tier.py`` runs pytest with
``cwd=REPO_ROOT`` via ``python -m pytest ...``, which already puts the repo
root on ``sys.path`` in the common case — but this conftest makes that
explicit and unconditional, so ``import benchmarks.meridianbench....``
resolves correctly however this tier's tests are invoked (bare ``pytest
benchmarks/meridianbench``, a different working directory, an IDE test
runner, ...), never relying on an incidental side effect of ``-m``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
