"""Explicit AST-based check that mrr.provenance never imports SQLAlchemy, a
database driver, or Alembic (E1-T06), independent of the import-linter
contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess. Mirrors tests/unit/architecture/test_persistence_boundary.py's
approach for mrr.domain — task-packets/E1-T06.yaml asks for this explicit
test even though import-linter covers the same ground: "no framework
imports in provenance (import-linter covers; keep explicit test consistent
with existing pattern)".

Parsing imports via ``ast`` (rather than just grepping for "sqlalchemy")
also catches indirect forms such as ``import sqlalchemy.orm`` or
``from psycopg import connect`` without false-positiving on unrelated
substrings.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROVENANCE_ROOT = REPO_ROOT / "packages" / "provenance" / "mrr" / "provenance"

#: mrr.provenance must stay framework-independent (MRR-NFR-010); concrete
#: PostgreSQL implementations of these interfaces live one layer up, in
#: mrr.persistence.
_FORBIDDEN_MODULE_PREFIXES = ("sqlalchemy", "psycopg", "psycopg2", "alembic")


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in _FORBIDDEN_MODULE_PREFIXES
    )


def test_provenance_package_has_no_sqlalchemy_or_driver_imports() -> None:
    python_files = sorted(PROVENANCE_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {PROVENANCE_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits

    assert not offending, (
        "mrr.provenance must stay framework- and driver-free (MRR-NFR-010); "
        f"forbidden imports found: {offending}"
    )
