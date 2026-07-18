"""Explicit AST-based check that mrr.adapters.object_store never imports a
web framework, workflow engine, model-provider SDK, or object-storage SDK
(E1-T07), independent of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess (task-packets/E1-T05.yaml's persistence-boundary test set the
precedent this mirrors: "no SQLAlchemy imports leak into mrr.domain
(import-linter covers it; keep the explicit test)").

This root's own package docstring
(adapters/object_store/mrr/adapters/object_store/__init__.py) explains why
it is registered in the *same* "framework- and provider-free" contract as
the other core packages: the local filesystem adapter implemented here uses
only the standard library, so the ban costs it nothing today. A future
MinIO/S3-compatible adapter would need its own namespace root and its own
contract treatment, precisely because it would need to import one of the
modules this test forbids.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OBJECT_STORE_ROOT = REPO_ROOT / "adapters" / "object_store" / "mrr" / "adapters" / "object_store"

#: Mirrors pyproject.toml's [[tool.importlinter.contracts]] forbidden_modules
#: for the "Core packages stay framework- and provider-free (MRR-NFR-010)"
#: contract, which mrr.adapters.object_store is now a member of.
_FORBIDDEN_MODULE_PREFIXES = (
    "fastapi",
    "starlette",
    "temporalio",
    "openai",
    "anthropic",
    "boto3",
    "botocore",
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


def test_object_store_package_has_no_framework_or_provider_sdk_imports() -> None:
    python_files = sorted(OBJECT_STORE_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {OBJECT_STORE_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits

    assert not offending, (
        "mrr.adapters.object_store must stay framework- and provider-SDK-free "
        f"(MRR-NFR-010); forbidden imports found: {offending}"
    )
