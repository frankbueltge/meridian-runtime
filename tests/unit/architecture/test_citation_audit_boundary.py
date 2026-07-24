"""Explicit AST-based check that ``mrr.domain.citation_audit``/``mrr.domain
.citation_audit_report`` import no repository/service/adapter/framework
module (task-packets/N2-T01.yaml R6: "architecture (lint-imports):
citation_audit.py and citation_audit_report.py import no framework/service
module"), independent of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess (that contract bans ``mrr.services``/framework imports from every
``mrr.domain`` module collectively; this test is scoped to these two modules
alone, mirroring tests/unit/architecture/test_agreement_boundary.py's
identical narrower-than-the-whole-package precedent).

``mrr.contracts.common`` is deliberately NOT in the forbidden list:
``mrr.domain.citation_audit_report`` legitimately imports ``MRRModel`` from
it (task-packets/N2-T01.yaml R2's own "a Pydantic v2 MRRModel" requirement)
— the same established, non-circular pattern ``mrr.domain.agreement_report``
already uses. ``mrr.domain.citation_audit`` (R1's pure classification core)
imports nothing from ``mrr.contracts`` at all — checked directly below as a
stronger, additional guarantee for that module specifically, mirroring
``mrr.domain.agreement``'s own identical guarantee.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CITATION_AUDIT_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "citation_audit.py"
CITATION_AUDIT_REPORT_MODULE = (
    REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "citation_audit_report.py"
)

#: Repository/service/adapter/framework modules neither pure domain module
#: may import — mirrors tests/unit/architecture/test_agreement_boundary.py's
#: own list.
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
    "jinja2",
    "numpy",
    "scipy",
    "sklearn",
    "statsmodels",
    "urllib.request",
    "http.client",
    "socket",
    "requests",
    "httpx",
)

#: ``mrr.domain.citation_audit`` (R1's pure classification core) additionally
#: may not import ANY ``mrr.contracts`` module at all — it has no need for a
#: Pydantic model, unlike ``citation_audit_report.py``.
_CITATION_AUDIT_MODULE_EXTRA_FORBIDDEN = ("mrr.contracts",)


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _assert_no_forbidden_import(path: Path, forbidden_prefixes: tuple[str, ...]) -> None:
    imported = _imported_module_names(path)
    for name in imported:
        for forbidden in forbidden_prefixes:
            if name == forbidden or name.startswith(forbidden + "."):
                raise AssertionError(f"{path}: forbidden import {name!r} (matches {forbidden!r})")


def test_citation_audit_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(CITATION_AUDIT_MODULE, _FORBIDDEN_MODULE_PREFIXES)
    _assert_no_forbidden_import(CITATION_AUDIT_MODULE, _CITATION_AUDIT_MODULE_EXTRA_FORBIDDEN)


def test_citation_audit_report_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(CITATION_AUDIT_REPORT_MODULE, _FORBIDDEN_MODULE_PREFIXES)


def test_citation_audit_module_uses_only_stdlib_and_python_builtins() -> None:
    """task-packets/N2-T01.yaml derived_decisions (d): hand-rolled identifier
    checks + title comparison, no new dependency — checked explicitly here
    (also covered by ``_FORBIDDEN_MODULE_PREFIXES`` above) as a standalone,
    clearly-named test so a reviewer sees this specific guarantee without
    reading the shared helper's full list. ``re``, ``string``,
    ``dataclasses``, ``collections.abc``, ``typing``, and
    ``urllib.parse`` are all standard-library modules already used
    elsewhere in ``mrr.domain`` (e.g. ``urllib.parse`` is stdlib, not a new
    dependency, and carries no network capability of its own).
    """
    imported = _imported_module_names(CITATION_AUDIT_MODULE)
    allowed_prefixes = ("re", "string", "dataclasses", "collections.abc", "typing", "urllib.parse")
    for name in imported:
        if name.startswith("__future__"):
            continue
        assert any(
            name == prefix or name.startswith(prefix + ".") for prefix in allowed_prefixes
        ), (
            f"unexpected import {name!r} in {CITATION_AUDIT_MODULE} — expected only one of "
            f"{allowed_prefixes!r}"
        )
