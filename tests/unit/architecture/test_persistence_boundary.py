"""Explicit AST-based check that mrr.domain never imports SQLAlchemy, a
database driver, or Alembic (E1-T05), independent of the import-linter
contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess. task-packets/E1-T05.yaml asks for this explicit test even though
import-linter covers the same ground: "no SQLAlchemy imports leak into
mrr.domain (import-linter covers it; keep the explicit test)".

Parsing imports via ``ast`` (rather than just grepping for "sqlalchemy")
also catches indirect forms such as ``import sqlalchemy.orm`` or
``from psycopg import connect`` without false-positiving on unrelated
substrings.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_ROOT = REPO_ROOT / "packages" / "domain" / "mrr" / "domain"

#: mrr.domain must stay framework-independent (MRR-NFR-010); these are the
#: persistence-specific modules E1-T05 introduces one layer up, in
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


def test_domain_package_has_no_sqlalchemy_or_driver_imports() -> None:
    python_files = sorted(DOMAIN_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {DOMAIN_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits

    assert not offending, (
        "mrr.domain must stay framework- and driver-free (MRR-NFR-010); "
        f"forbidden imports found: {offending}"
    )
