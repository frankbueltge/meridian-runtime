"""Explicit AST-based check that ``scripts/fetch_citation_resolutions.py``
imports nothing from ``mrr.*`` (task-packets/N2-T02a.yaml R1: "a standalone
Python module under scripts/, NOT importable from packages/** or
services/** and importing nothing from mrr.* — the runtime must stay
no-network by construction"). Mirrors
tests/unit/architecture/test_citation_audit_boundary.py's own AST-based
precedent, narrower here: this script may import ONLY the standard library
(the module docstring's own "stdlib-only" claim), never a third-party or
``mrr`` module of any kind.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FETCH_SCRIPT_MODULE = REPO_ROOT / "scripts" / "fetch_citation_resolutions.py"

#: The exact stdlib top-level modules this script's own docstring declares
#: it needs (task-packets/N2-T02a.yaml R1: "Stdlib only (urllib.request,
#: urllib.parse, xml.etree.ElementTree, json, argparse, hashlib); NO new
#: dependency"). ``hashlib`` is listed in the packet but this script does
#: not itself hash anything (the frozen evaluator computes the snapshot's
#: sha256), so it is allowed but not required to appear.
_ALLOWED_TOP_LEVEL_MODULES = frozenset(sys.stdlib_module_names) | {"__future__"}


def _imported_top_level_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module.split(".")[0])
    return names


def test_fetch_script_imports_no_mrr_module() -> None:
    imported = _imported_top_level_module_names(FETCH_SCRIPT_MODULE)
    assert "mrr" not in imported, (
        f"{FETCH_SCRIPT_MODULE} imports the mrr namespace — the runtime must stay "
        "no-network by construction (task-packets/N2-T02a.yaml R1)"
    )


def test_fetch_script_imports_only_the_standard_library() -> None:
    imported = _imported_top_level_module_names(FETCH_SCRIPT_MODULE)
    unexpected = imported - _ALLOWED_TOP_LEVEL_MODULES
    assert not unexpected, (
        f"{FETCH_SCRIPT_MODULE} imports non-stdlib module(s) {sorted(unexpected)!r} — "
        "task-packets/N2-T02a.yaml R1 requires stdlib only, no new dependency"
    )


def test_fetch_script_is_outside_the_runtime_source_trees() -> None:
    """Belt-and-braces path check: the module actually lives under
    ``scripts/``, not ``packages/`` or ``services/`` (task-packets/
    N2-T02a.yaml R1).
    """
    relative = FETCH_SCRIPT_MODULE.relative_to(REPO_ROOT)
    assert relative.parts[0] == "scripts"
