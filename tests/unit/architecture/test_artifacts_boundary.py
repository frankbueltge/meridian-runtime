"""Explicit AST-based check that mrr.domain.artifacts imports no
filesystem-specific or framework code (E1-T07 invariant: "the interface in
packages/domain imports no filesystem-specific or framework code"),
independent of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess, and independent of
tests/unit/architecture/test_persistence_boundary.py (which checks the same
directory for SQLAlchemy/driver/Alembic imports, not filesystem or object-
storage-SDK imports).

The concrete local filesystem adapter this interface's first implementation
lives behind (``mrr.adapters.object_store.local``) legitimately imports
``os``/``pathlib``/``tempfile``/``json`` — that is a separate namespace root
checked by its own boundary test
(tests/unit/architecture/test_object_store_boundary.py). This test is scoped
to ``mrr.domain.artifacts`` alone, not the whole ``mrr.domain`` package,
since that broader ban is out of this task's scope.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "artifacts.py"

#: Filesystem-specific stdlib modules a framework-free domain interface must
#: not import, plus the same framework/object-storage-SDK ban the rest of
#: mrr.domain is already held to (MRR-NFR-010).
_FORBIDDEN_MODULE_PREFIXES = (
    "os",
    "pathlib",
    "tempfile",
    "shutil",
    "io",
    "fastapi",
    "starlette",
    "temporalio",
    "openai",
    "anthropic",
    "boto3",
    "botocore",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "alembic",
)


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


def test_artifacts_module_has_no_filesystem_or_framework_imports() -> None:
    assert ARTIFACTS_MODULE.is_file(), f"expected {ARTIFACTS_MODULE} to exist"

    imported = _imported_module_names(ARTIFACTS_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.domain.artifacts must stay framework- and filesystem-free "
        f"(MRR-NFR-010); forbidden imports found: {hits}"
    )
