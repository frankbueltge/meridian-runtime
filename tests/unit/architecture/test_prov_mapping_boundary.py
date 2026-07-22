"""Explicit AST-based check that ``mrr.domain.prov_mapping`` imports no
repository/service/adapter type (task-packets/E8-T02.yaml AT5:
"prov_mapping.py imports no repository/service/adapter type"), independent
of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess — mirrors tests/unit/architecture/test_ro_crate_boundary.py's
own identical narrower-than-the-whole-package precedent for its sibling
module, one directory over.

``mrr.crypto.canonical`` and ``mrr.domain.projection`` are deliberately NOT
in the forbidden list: the former is the same pure, I/O-free ``JSONValue``
type alias ``mrr.domain.ro_crate`` already imports; the latter is E3-T07's
plain, frozen ``ProvenanceEdge`` dataclass (no I/O, no persistence import of
its own) that ``mrr.domain.prov_mapping.group_derived_from_targets`` reuses
rather than inventing a parallel edge shape — see that module's own
docstring for the full rationale. Neither is a repository/service/adapter
type.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROV_MAPPING_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "prov_mapping.py"

#: Repository/service/adapter/store modules — and the framework/filesystem
#: imports the rest of mrr.domain is already held to (MRR-NFR-010) — a pure
#: PROV-mapping module must never import. Mirrors
#: tests/unit/architecture/test_ro_crate_boundary.py's own identical list.
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


def test_prov_mapping_module_imports_no_repository_service_or_adapter_type() -> None:
    assert PROV_MAPPING_MODULE.is_file(), f"expected {PROV_MAPPING_MODULE} to exist"

    imported = _imported_module_names(PROV_MAPPING_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.domain.prov_mapping must import no repository/service/adapter type "
        f"(task-packets/E8-T02.yaml AT5); forbidden imports found: {hits}"
    )
