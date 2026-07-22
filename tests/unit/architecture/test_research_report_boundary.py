"""Explicit AST-based check that ``mrr.domain.research_report`` imports no
repository/service/adapter type (task-packets/E8-T03.yaml R1: "no repository
types"), independent of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess (that contract only bans ``mrr.services``/framework imports from
every ``mrr.domain`` module collectively; this test is scoped to
``mrr.domain.research_report`` alone, mirroring
tests/unit/architecture/test_ro_crate_boundary.py's identical narrower-than-
the-whole-package precedent).

``mrr.domain.artifacts``/``mrr.crypto.canonical``/``mrr.domain.identity`` are
deliberately NOT in the forbidden list, unlike ``test_ro_crate_boundary.py``'s
own (maximally strict for a module that needs none of them): this module
legitimately imports ``mrr.domain.artifacts.Classification`` (a plain
``Literal`` type alias, not the ``ArtifactStore`` Protocol/adapter surface —
task-packets/E8-T03.yaml's own "read mrr.domain.artifacts (Classification
literal)" instruction), the same pure ``JSONValue`` type alias ``mrr.domain
.ro_crate``/``mrr.domain.prov_mapping`` already import from ``mrr.crypto
.canonical``, and the same pure, compiled ``URN_PATTERN`` regex ``mrr.domain
.prov_mapping`` already imports from ``mrr.domain.identity`` — none of the
three is a repository/service/adapter type.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RESEARCH_REPORT_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "research_report.py"

#: Repository/service/adapter/store modules — and the framework/filesystem
#: imports the rest of mrr.domain is already held to (MRR-NFR-010) — a pure
#: report-model builder/renderer must never import. Also bans every
#: markdown/HTML templating library this codebase could conceivably add in
#: the future (task-packets/E8-T03.yaml R1(b): "hand-rolled ... NO markdown/
#: html library").
_FORBIDDEN_MODULE_PREFIXES = (
    "mrr.domain.repositories",
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
    "markdown",
    "mistune",
    "jinja2",
    "bs4",
    "lxml",
    "html5lib",
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


def test_research_report_module_imports_no_repository_service_or_adapter_type() -> None:
    assert RESEARCH_REPORT_MODULE.is_file(), f"expected {RESEARCH_REPORT_MODULE} to exist"

    imported = _imported_module_names(RESEARCH_REPORT_MODULE.read_text())
    hits = {name for name in imported if _is_forbidden(name)}

    assert not hits, (
        "mrr.domain.research_report must import no repository/service/adapter type/markdown-"
        f"html library (task-packets/E8-T03.yaml R1); forbidden imports found: {hits}"
    )
