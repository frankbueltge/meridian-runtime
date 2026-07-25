"""Explicit AST-based check that ``mrr.domain.field_observation``/``mrr
.domain.field_observation_report`` import no repository/service/adapter/
framework module (task-packets/R2-T01.yaml R6: "architecture (lint-imports):
field_observation.py and field_observation_report.py import no framework/
service module (the report may import the domain citation_audit_report type
only)"), independent of the import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess (that contract bans ``mrr.services``/framework imports from every
``mrr.domain`` module collectively; this test is scoped to these two modules
alone, mirroring tests/unit/architecture/test_citation_audit_boundary.py's
identical narrower-than-the-whole-package precedent).

``mrr.contracts.common`` is deliberately NOT in the forbidden list:
``mrr.domain.field_observation_report`` legitimately imports ``MRRModel``
from it (task-packets/R2-T01.yaml R2's own "a Pydantic v2 MRRModel"
requirement) — the same established, non-circular pattern
``mrr.domain.citation_audit_report``/``mrr.domain.agreement_report`` already
use. Likewise, ``mrr.domain.citation_audit_report`` itself is deliberately
NOT forbidden for ``field_observation_report.py`` alone: the packet's own
R2 requires embedding the frozen N2 evaluator's OWN, unchanged report type
(task-packets/R2-T01.yaml R2: "the EMBEDDED
mrr.domain.citation_audit_report.CitationAuditReport ... a domain->domain
import") — mirroring the established precedent that a domain report module
may import a sibling domain module for a legitimate embedding/reuse (e.g.
``citation_audit_report.py`` importing ``citation_audit.py``).

``mrr.domain.field_observation`` (R1's pure gate/intake core) imports
nothing from ``mrr.contracts`` or any other ``mrr.domain.*`` module at all
— checked directly below as a stronger, additional guarantee for that
module specifically, mirroring ``mrr.domain.citation_audit``'s own identical
guarantee.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIELD_OBSERVATION_MODULE = (
    REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "field_observation.py"
)
FIELD_OBSERVATION_REPORT_MODULE = (
    REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "field_observation_report.py"
)

#: Repository/service/adapter/framework modules neither pure domain module
#: may import — mirrors tests/unit/architecture/test_citation_audit_boundary
#: .py's own list.
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
    "hashlib",
)

#: ``mrr.domain.field_observation`` (R1's pure gate/intake core) additionally
#: may not import ANY ``mrr.contracts`` or ``mrr.domain.*`` module at all —
#: it has no need for a Pydantic model or another domain module, unlike
#: ``field_observation_report.py``.
_FIELD_OBSERVATION_MODULE_EXTRA_FORBIDDEN = ("mrr.contracts", "mrr.domain.citation_audit")


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


def test_field_observation_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(FIELD_OBSERVATION_MODULE, _FORBIDDEN_MODULE_PREFIXES)
    _assert_no_forbidden_import(FIELD_OBSERVATION_MODULE, _FIELD_OBSERVATION_MODULE_EXTRA_FORBIDDEN)


def test_field_observation_report_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(FIELD_OBSERVATION_REPORT_MODULE, _FORBIDDEN_MODULE_PREFIXES)


def test_field_observation_module_uses_only_stdlib_and_python_builtins() -> None:
    """task-packets/R2-T01.yaml R1: "hand-rolled, no new dependency" —
    checked explicitly here (also covered by ``_FORBIDDEN_MODULE_PREFIXES``
    above) as a standalone, clearly-named test so a reviewer sees this
    specific guarantee without reading the shared helper's full list.
    ``collections.abc``, ``dataclasses``, and ``typing`` are all
    standard-library modules already used elsewhere in ``mrr.domain``.
    """
    imported = _imported_module_names(FIELD_OBSERVATION_MODULE)
    allowed_prefixes = ("collections.abc", "dataclasses", "typing")
    for name in imported:
        if name.startswith("__future__"):
            continue
        assert any(
            name == prefix or name.startswith(prefix + ".") for prefix in allowed_prefixes
        ), (
            f"unexpected import {name!r} in {FIELD_OBSERVATION_MODULE} — expected only one of "
            f"{allowed_prefixes!r}"
        )


def test_field_observation_report_module_imports_only_the_allowed_domain_modules() -> None:
    """The report module's only ``mrr.*`` imports are ``mrr.contracts.common``
    (for ``MRRModel``), its own sibling core ``mrr.domain.field_observation``
    (for ``AnchorCheckResult``/``BatchRole``), and the embedded
    ``mrr.domain.citation_audit_report`` (for the frozen N2 report type) —
    never a fourth ``mrr.*`` module.
    """
    imported = _imported_module_names(FIELD_OBSERVATION_REPORT_MODULE)
    allowed_mrr_prefixes = (
        "mrr.contracts.common",
        "mrr.domain.field_observation",
        "mrr.domain.citation_audit_report",
    )
    for name in imported:
        if not name.startswith("mrr."):
            continue
        assert any(
            name == prefix or name.startswith(prefix + ".") for prefix in allowed_mrr_prefixes
        ), f"unexpected mrr.* import {name!r} in {FIELD_OBSERVATION_REPORT_MODULE}"
