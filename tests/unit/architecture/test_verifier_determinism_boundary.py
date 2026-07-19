"""Explicit AST-based check that mrr.services.verifier never imports a model
adapter, a structured-generation layer, a provider SDK, or a network client
(task-packets/E4-T05.yaml: this package is the deterministic gate the E4
exit criterion names — "no model can mutate authoritative state directly" —
so it must not even be ABLE to reach a model or the network). Mirrors
tests/unit/architecture/test_llm_adapter_boundary.py's own precedent for
mrr.adapters.llm exactly, extended with a third forbidden group specific to
this package's own invariant: no ``mrr.domain.model_adapter`` (the E4-T01
port), no ``mrr.adapters.llm`` (the E4-T02 structured-generation layer).

Independent of the shared "framework- and provider-free" import-linter
contract in pyproject.toml (which ``mrr.services`` is never a
``source_module`` of — see that contract's own comment for why
``mrr.services`` is instead the target of a SEPARATE "nothing inward imports
services" contract) — this package's own no-model/no-network guarantee has
no existing import-linter contract to lean on, so this test is its sole
machine-checked form, exactly like
tests/unit/architecture/test_object_store_boundary.py's own precedent for a
newly registered namespace with no contract of its own yet
(task-packets/E1-T05.yaml: "no SQLAlchemy imports leak into mrr.domain
(import-linter covers it; keep the explicit test)" — the inverse situation
here, where NO contract covers it, makes this test load-bearing rather than
a belt-and-suspenders double-check).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFIER_ROOT = REPO_ROOT / "services" / "control_plane" / "mrr" / "services" / "verifier"

#: Mirrors pyproject.toml's [[tool.importlinter.contracts]] forbidden_modules
#: for the "Core packages stay framework- and provider-free (MRR-NFR-010)"
#: contract — this package is not itself a member of that contract (it is
#: not a core/persistence package), so this list is checked here explicitly.
_FORBIDDEN_MODULE_PREFIXES = (
    "fastapi",
    "starlette",
    "temporalio",
    "openai",
    "anthropic",
    "boto3",
    "botocore",
)

#: Network-client libraries this deterministic tool must never import
#: directly — MRR-FR-072's own "LOCAL inspection only" language. Mirrors
#: tests/unit/architecture/test_llm_adapter_boundary.py's own list exactly.
_FORBIDDEN_NETWORK_MODULE_PREFIXES = ("httpx", "requests", "urllib3", "aiohttp", "socket")

#: The two model-reaching layers this package must never import — the whole
#: point of task-packets/E4-T05.yaml: "the verification DECISION is
#: DETERMINISTIC — a checked tool, never a model oracle."
_FORBIDDEN_MODEL_MODULE_PREFIXES = ("mrr.domain.model_adapter", "mrr.adapters.llm")


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
    python_files = sorted(VERIFIER_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {VERIFIER_ROOT}"

    offending: dict[str, set[str]] = {}
    for path in python_files:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name, prefixes)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits
    return offending


def test_verifier_package_has_no_framework_or_provider_sdk_imports() -> None:
    offending = _offending_imports(_FORBIDDEN_MODULE_PREFIXES)
    assert not offending, (
        "mrr.services.verifier must stay framework- and provider-SDK-free "
        f"(MRR-NFR-004, MRR-NFR-010); forbidden imports found: {offending}"
    )


def test_verifier_package_has_no_network_client_imports() -> None:
    offending = _offending_imports(_FORBIDDEN_NETWORK_MODULE_PREFIXES)
    assert not offending, (
        "mrr.services.verifier must open no network of its own — MRR-FR-072 is LOCAL "
        f"inspection only; forbidden imports found: {offending}"
    )


def test_verifier_package_has_no_model_adapter_or_llm_imports() -> None:
    offending = _offending_imports(_FORBIDDEN_MODEL_MODULE_PREFIXES)
    assert not offending, (
        "mrr.services.verifier is the deterministic gate (E4 exit criterion: 'no model can "
        "mutate authoritative state directly') — it must not import a model adapter or the "
        f"structured-generation layer at all; forbidden imports found: {offending}"
    )
