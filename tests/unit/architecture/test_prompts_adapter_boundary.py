"""Explicit AST-based check that mrr.adapters.prompts never imports a web
framework, workflow engine, model-provider SDK, network client, or model
adapter (task-packets/E4-T06.yaml acceptance test: "the mrr.adapters.prompts
package imports no provider SDK, no network client, and no model adapter"),
independent of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess. Mirrors tests/unit/architecture/test_llm_adapter_boundary.py's
own precedent for a newly registered adapters/ namespace root.

The "no model adapter" check is specific to this package: unlike
mrr.adapters.llm (which legitimately reaches a model through an injected
mrr.domain.model_adapter.ModelAdapter port), mrr.adapters.prompts calls no
model at all -- it only resolves and renders committed template files -- so
it has no occasion to import mrr.domain.model_adapter or mrr.adapters.llm
either, and this test holds it to that stricter bar.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPTS_ADAPTER_ROOT = REPO_ROOT / "adapters" / "prompts" / "mrr" / "adapters" / "prompts"

#: Mirrors pyproject.toml's [[tool.importlinter.contracts]] forbidden_modules
#: for the "Core packages stay framework- and provider-free (MRR-NFR-010)"
#: contract, which mrr.adapters.prompts is now a member of.
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
#: directly -- this package reads only committed local files and opens no
#: network of any kind.
_FORBIDDEN_NETWORK_MODULE_PREFIXES = ("httpx", "requests", "urllib3", "aiohttp", "socket")

#: This package calls no model at all, so it must not import the model
#: adapter port or a concrete model adapter implementation either
#: (task-packets/E4-T06.yaml acceptance test: "... and no model adapter").
_FORBIDDEN_MODEL_ADAPTER_MODULE_PREFIXES = ("mrr.domain.model_adapter", "mrr.adapters.llm")


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


def _offending_imports(prefixes: tuple[str, ...]) -> dict[str, set[str]]:
    python_files = sorted(PROMPTS_ADAPTER_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {PROMPTS_ADAPTER_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name, prefixes)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits
    return offending


def test_prompts_adapter_package_has_no_framework_or_provider_sdk_imports() -> None:
    offending = _offending_imports(_FORBIDDEN_MODULE_PREFIXES)
    assert not offending, (
        "mrr.adapters.prompts must stay framework- and provider-SDK-free "
        f"(MRR-NFR-004, MRR-NFR-010); forbidden imports found: {offending}"
    )


def test_prompts_adapter_package_has_no_network_client_imports() -> None:
    offending = _offending_imports(_FORBIDDEN_NETWORK_MODULE_PREFIXES)
    assert not offending, (
        "mrr.adapters.prompts must open no network -- it only reads committed "
        f"local template files; forbidden imports found: {offending}"
    )


def test_prompts_adapter_package_has_no_model_adapter_imports() -> None:
    offending = _offending_imports(_FORBIDDEN_MODEL_ADAPTER_MODULE_PREFIXES)
    assert not offending, (
        "mrr.adapters.prompts calls no model and must not import the model adapter "
        f"port or an implementation; forbidden imports found: {offending}"
    )
