"""Explicit AST-based check that ``mrr.services.cli.release_main`` contains
no bundle-assembly/manifest/export/report composition logic of its own
(task-packets/E8-T04.yaml's own CLI-law parallel to E8-T01's AT5: "the CLI
module contains no closure or metadata logic"): it must never import
``mrr.services.export.service``, ``mrr.services.report.service``,
``mrr.domain.ro_crate``, ``mrr.domain.research_report``, or
``mrr.crypto.canonical`` directly — every one of those concerns is reached
exclusively through ``mrr.services.release.bundle.assemble_and_release`` and
``mrr.services.release.verify.verify_rebuild``/``verify_bundle_dir``, exactly
as ``mrr.services.cli.export_main``/``report_main`` reach their own domain
logic only through ``ExportService``/``ReportService`` (task-packets/
E2-T07.yaml's CLI law: "no domain behavior in the CLI module").

Mirrors tests/unit/architecture/test_export_cli_boundary.py's/
test_report_cli_boundary.py's identical AST-based, single-module-scoped
technique.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE_CLI_MODULE = (
    REPO_ROOT / "services" / "control_plane" / "mrr" / "services" / "cli" / "release_main.py"
)

#: The application-layer composition logic mrr.services.release.bundle/
#: verify/service already own — reachable only through them, never directly
#: from the CLI transport layer.
_FORBIDDEN_MODULE_PREFIXES = (
    "mrr.services.export.service",
    "mrr.services.report.service",
    "mrr.domain.ro_crate",
    "mrr.domain.research_report",
    "mrr.domain.prov_mapping",
    "mrr.crypto.canonical",
    "mrr.services.release.manifest",
    "mrr.services.release.service",
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


def test_release_cli_module_imports_no_bundle_assembly_or_manifest_logic() -> None:
    assert RELEASE_CLI_MODULE.is_file(), f"expected {RELEASE_CLI_MODULE} to exist"

    imported = _imported_module_names(RELEASE_CLI_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.services.cli.release_main must contain no bundle-assembly/manifest/export/report "
        f"composition logic (task-packets/E8-T04.yaml); forbidden imports found: {hits}"
    )
