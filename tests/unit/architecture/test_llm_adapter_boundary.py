"""Explicit AST-based check that mrr.adapters.llm never imports a web
framework, workflow engine, or model-provider SDK (task-packets/E4-T02.yaml
acceptance test: "provider neutrality - adapters/llm imports no provider SDK
and makes no network call"), independent of the import-linter contract in
pyproject.toml that tests/unit/architecture/test_import_boundaries.py
already runs as a subprocess. Mirrors tests/unit/architecture/
test_object_store_boundary.py's own precedent for a newly registered
adapters/ namespace root ("no SQLAlchemy imports leak into mrr.domain
(import-linter covers it; keep the explicit test)", task-packets/E1-T05.yaml).

This module's own package docstring
(adapters/llm/mrr/adapters/llm/__init__.py) explains why it is registered in
the *same* "framework- and provider-free" contract as the other core
packages: the structured-generation layer implemented here reaches a model
SOLELY through the caller-injected mrr.domain.model_adapter.ModelAdapter
port, so the ban costs it nothing today. A future concrete vendor-SDK
adapter would need its own namespace root and its own contract treatment,
precisely because it would need to import one of the modules this test
forbids.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LLM_ADAPTER_ROOT = REPO_ROOT / "adapters" / "llm" / "mrr" / "adapters" / "llm"

#: Mirrors pyproject.toml's [[tool.importlinter.contracts]] forbidden_modules
#: for the "Core packages stay framework- and provider-free (MRR-NFR-010)"
#: contract, which mrr.adapters.llm is now a member of.
_FORBIDDEN_MODULE_PREFIXES = (
    "fastapi",
    "starlette",
    "temporalio",
    "openai",
    "anthropic",
    "boto3",
    "botocore",
)

#: Network-client libraries a provider-neutral adapter must never import
#: directly (task-packets/E4-T02.yaml: "no network egress" -- this layer
#: reaches a model SOLELY through the injected ModelAdapter port). Not part
#: of the shared import-linter contract (other core packages have no
#: occasion to make an HTTP call at all), so checked here explicitly.
_FORBIDDEN_NETWORK_MODULE_PREFIXES = ("httpx", "requests", "urllib3", "aiohttp", "socket")


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


def _is_forbidden(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes)


def test_llm_adapter_package_has_no_framework_or_provider_sdk_imports() -> None:
    python_files = sorted(LLM_ADAPTER_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {LLM_ADAPTER_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name, _FORBIDDEN_MODULE_PREFIXES)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits

    assert not offending, (
        "mrr.adapters.llm must stay framework- and provider-SDK-free "
        f"(MRR-NFR-004, MRR-NFR-010); forbidden imports found: {offending}"
    )


def test_llm_adapter_package_has_no_network_client_imports() -> None:
    python_files = sorted(LLM_ADAPTER_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {LLM_ADAPTER_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {
            name for name in imported if _is_forbidden(name, _FORBIDDEN_NETWORK_MODULE_PREFIXES)
        }
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits

    assert not offending, (
        "mrr.adapters.llm must open no network of its own -- it reaches a model "
        f"SOLELY through the injected ModelAdapter port; forbidden imports found: {offending}"
    )
