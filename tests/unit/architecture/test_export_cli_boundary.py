"""Explicit AST-based check that ``mrr.services.cli.export_main`` contains
no closure/metadata logic of its own (task-packets/E8-T01.yaml AT5: "the CLI
module contains no closure or metadata logic"): it must never import
``mrr.domain.ro_crate`` (the RO-Crate metadata/plan builder) or
``mrr.crypto.canonical`` (the canonicalization helper the closure/export
path uses) directly — every one of those concerns is reached exclusively
through ``mrr.services.export.service.ExportService``, exactly as
task-packets/E2-T07.yaml's CLI law requires ("no domain behavior in the CLI
module").

Mirrors tests/unit/architecture/test_ro_crate_boundary.py's identical
AST-based, single-module-scoped technique.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORT_CLI_MODULE = (
    REPO_ROOT / "services" / "control_plane" / "mrr" / "services" / "cli" / "export_main.py"
)

#: The two modules that actually hold this task's closure/metadata logic —
#: reachable only through mrr.services.export.service, never directly from
#: the CLI transport layer.
_FORBIDDEN_MODULE_PREFIXES = (
    "mrr.domain.ro_crate",
    "mrr.crypto.canonical",
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


def test_export_cli_module_imports_no_closure_or_metadata_logic() -> None:
    assert EXPORT_CLI_MODULE.is_file(), f"expected {EXPORT_CLI_MODULE} to exist"

    imported = _imported_module_names(EXPORT_CLI_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.services.cli.export_main must contain no closure or metadata logic "
        f"(task-packets/E8-T01.yaml AT5); forbidden imports found: {hits}"
    )
