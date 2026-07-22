"""Explicit AST-based check that ``mrr.services.cli.report_main`` contains
no closure/discovery/redaction composition logic of its own (task-packets/
E8-T03.yaml's own CLI-law parallel to E8-T01's AT5: "the CLI module contains
no closure or metadata logic"): it must never import ``mrr.services.export
.service``/``mrr.services.projection.service``/``mrr.domain
.public_correction_view`` directly — every one of those concerns is reached
exclusively through ``mrr.services.report.service.ReportService``, exactly
as ``mrr.services.cli.export_main`` reaches ``mrr.domain.ro_crate`` only
through ``ExportService`` (task-packets/E2-T07.yaml's CLI law: "no domain
behavior in the CLI module").

``mrr.domain.research_report``'s own ``render_markdown``/``render_html``/
``Disclosure`` are deliberately NOT forbidden: unlike ``export_main`` (which
never renders anything itself — ``ExportService`` writes the whole tree),
this CLI performs the render-and-write step itself (task-packets/
E8-T03.yaml R4's own ordering: "exactly one ReportService call" for the
MODEL, then this module picks the format and writes the bytes), so calling
the pure renderer directly is this module's own legitimate job, not a
boundary violation — only building the MODEL (closure resolution, correction
discovery, redaction) must stay exclusively inside ``ReportService``.

Mirrors tests/unit/architecture/test_export_cli_boundary.py's identical
AST-based, single-module-scoped technique.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_CLI_MODULE = (
    REPO_ROOT / "services" / "control_plane" / "mrr" / "services" / "cli" / "report_main.py"
)

#: The application-layer composition logic ``ReportService`` already owns —
#: reachable only through it, never directly from the CLI transport layer.
_FORBIDDEN_MODULE_PREFIXES = (
    "mrr.services.export.service",
    "mrr.services.projection.service",
    "mrr.domain.public_correction_view",
    "mrr.domain.ro_crate",
    "mrr.domain.prov_mapping",
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


def test_report_cli_module_imports_no_closure_discovery_or_redaction_logic() -> None:
    assert REPORT_CLI_MODULE.is_file(), f"expected {REPORT_CLI_MODULE} to exist"

    imported = _imported_module_names(REPORT_CLI_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.services.cli.report_main must contain no closure/discovery/redaction "
        f"composition logic (task-packets/E8-T03.yaml); forbidden imports found: {hits}"
    )
