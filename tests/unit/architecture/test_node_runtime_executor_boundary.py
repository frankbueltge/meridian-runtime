"""Explicit AST-based check that ``mrr.services.node_runtime.executor``
imports no framework and no provider SDK (task-packets/E2-T04.yaml
invariant: "the executor interface imports no framework and no provider
SDK"), independent of the import-linter contracts in pyproject.toml.

Unlike ``mrr.domain``/``mrr.crypto``/``mrr.contracts``/``mrr.policy``/
``mrr.provenance``/``mrr.observability``/``mrr.persistence``, the
``mrr.services`` root is not held to the "Core packages stay framework- and
provider-free" import-linter contract (``services/control_plane``'s own
``mrr.services.research_score`` package docstring: "this root is not
required to stay framework-free"). E2-T04's own executor Protocol is
narrower and explicitly framework-free by design regardless, so this test
pins that as its own dedicated check, scoped to this one module — the same
pattern ``tests/unit/architecture/test_artifacts_boundary.py`` uses for
``mrr.domain.artifacts``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EXECUTOR_MODULE = (
    REPO_ROOT / "services" / "node_runtime" / "mrr" / "services" / "node_runtime" / "executor.py"
)

#: The same framework/provider-SDK ban the core packages are held to
#: (MRR-NFR-010's forbidden_modules in pyproject.toml).
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


def test_executor_module_has_no_framework_or_provider_sdk_imports() -> None:
    assert EXECUTOR_MODULE.is_file(), f"expected {EXECUTOR_MODULE} to exist"

    imported = _imported_module_names(EXECUTOR_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.services.node_runtime.executor must import no framework and no "
        f"provider SDK (task-packets/E2-T04.yaml); forbidden imports found: {hits}"
    )
