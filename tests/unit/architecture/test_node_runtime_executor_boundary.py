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

--- K1-T03: EXTENDED, not replaced (task-packets/K1-T03.yaml invariant) ------

``mrr.services.node_runtime.synthesis_executor`` is a NEW, sibling
``Executor`` implementation with the IDENTICAL framework-freedom
requirement ("SystematicEvidenceSynthesisExecutor.execute() imports no
SQLAlchemy driver, no HTTP/FastAPI, no provider SDK, and performs no
network I/O — identical framework-freedom to ReferenceTaskExecutor, verified
by the same style of import-boundary test ... extended, not replaced"). Also
checked here (module-level imports only — the OPTIONAL, separately-tested
``build_model_assisted_extraction_callable`` deliberately defers its own
``mrr.adapters.llm.structured_generation`` import to inside its own function
body, precisely so importing this module at all never pulls in that
dependency chain unless a caller actually exercises the model-assisted
slice) additionally bans a raw SQLAlchemy import — a stricter bar than
``executor.py``'s own check, since ``synthesis_executor.py`` must ALSO never
import a DB driver directly (derived_decisions (a)'s own central,
load-bearing design point).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_NODE_RUNTIME_DIR = REPO_ROOT / "services" / "node_runtime" / "mrr" / "services" / "node_runtime"
EXECUTOR_MODULE = _NODE_RUNTIME_DIR / "executor.py"
SYNTHESIS_EXECUTOR_MODULE = _NODE_RUNTIME_DIR / "synthesis_executor.py"

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

#: K1-T03's own additional bar for synthesis_executor.py: no raw SQLAlchemy
#: import at module level either (derived_decisions (a)).
_SYNTHESIS_EXECUTOR_ADDITIONAL_FORBIDDEN_PREFIXES = ("sqlalchemy",)


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


def test_executor_module_has_no_framework_or_provider_sdk_imports() -> None:
    assert EXECUTOR_MODULE.is_file(), f"expected {EXECUTOR_MODULE} to exist"

    imported = _imported_module_names(EXECUTOR_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name, _FORBIDDEN_MODULE_PREFIXES)}

    assert not hits, (
        "mrr.services.node_runtime.executor must import no framework and no "
        f"provider SDK (task-packets/E2-T04.yaml); forbidden imports found: {hits}"
    )


def test_synthesis_executor_module_has_no_framework_or_provider_sdk_imports() -> None:
    assert SYNTHESIS_EXECUTOR_MODULE.is_file(), f"expected {SYNTHESIS_EXECUTOR_MODULE} to exist"

    imported = _imported_module_names(SYNTHESIS_EXECUTOR_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name, _FORBIDDEN_MODULE_PREFIXES)}

    assert not hits, (
        "mrr.services.node_runtime.synthesis_executor must import no framework and no "
        f"provider SDK (task-packets/K1-T03.yaml); forbidden imports found: {hits}"
    )


def test_synthesis_executor_module_has_no_sqlalchemy_import() -> None:
    """derived_decisions (a): the executor's own ``execute()`` must never
    import SQLAlchemy directly — persistence is exclusively
    ``run_synthesis_evidence_loop``'s job.
    """
    imported = _imported_module_names(SYNTHESIS_EXECUTOR_MODULE.read_text())
    hits = {
        name
        for name in imported
        if _is_forbidden(name, _SYNTHESIS_EXECUTOR_ADDITIONAL_FORBIDDEN_PREFIXES)
    }

    assert not hits, (
        "mrr.services.node_runtime.synthesis_executor must import no SQLAlchemy driver "
        f"(task-packets/K1-T03.yaml derived_decisions (a)); forbidden imports found: {hits}"
    )
