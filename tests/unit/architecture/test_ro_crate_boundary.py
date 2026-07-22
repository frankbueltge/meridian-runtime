"""Explicit AST-based check that ``mrr.domain.ro_crate`` imports no
repository/service/adapter type (task-packets/E8-T01.yaml AT5: "mrr.domain
.ro_crate imports no repository/service/adapter type"), independent of the
import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess (that contract only bans ``mrr.services``/framework imports from
every ``mrr.domain`` module collectively; this test is scoped to
``mrr.domain.ro_crate`` alone, mirroring
tests/unit/architecture/test_artifacts_boundary.py's identical narrower-
than-the-whole-package precedent).

``mrr.crypto.canonical`` is deliberately NOT in the forbidden list: it is
the same pure, I/O-free RFC 8785 canonicalization helper
``mrr.domain.hashing_policy`` already imports (see that module's own
import), not a repository/service/adapter type.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RO_CRATE_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "ro_crate.py"

#: Repository/service/adapter/store modules — and the framework/filesystem
#: imports the rest of mrr.domain is already held to (MRR-NFR-010) — a pure
#: RO-Crate builder must never import.
_FORBIDDEN_MODULE_PREFIXES = (
    "mrr.domain.repositories",
    "mrr.domain.artifacts",
    "mrr.services",
    "mrr.persistence",
    "mrr.adapters",
    "mrr.provenance",
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


def test_ro_crate_module_imports_no_repository_service_or_adapter_type() -> None:
    assert RO_CRATE_MODULE.is_file(), f"expected {RO_CRATE_MODULE} to exist"

    imported = _imported_module_names(RO_CRATE_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.domain.ro_crate must import no repository/service/adapter type "
        f"(task-packets/E8-T01.yaml AT5); forbidden imports found: {hits}"
    )
