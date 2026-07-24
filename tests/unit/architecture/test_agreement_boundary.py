"""Explicit AST-based check that ``mrr.domain.agreement``/``mrr.domain
.agreement_report`` import no repository/service/adapter/framework module
(task-packets/N1-T01.yaml R6: "architecture (lint-imports): domain/
agreement.py and agreement_report.py import no framework/service modules; no
forbidden import introduced"), independent of the import-linter contract in
pyproject.toml that tests/unit/architecture/test_import_boundaries.py already
runs as a subprocess (that contract bans ``mrr.services``/framework imports
from every ``mrr.domain`` module collectively; this test is scoped to these
two modules alone, mirroring tests/unit/architecture
/test_research_report_boundary.py's identical narrower-than-the-whole-package
precedent).

``mrr.contracts.common`` is deliberately NOT in the forbidden list:
``mrr.domain.agreement_report`` legitimately imports ``MRRModel`` from it
(task-packets/N1-T01.yaml R2's own "a Pydantic v2 MRRModel" requirement) —
the same established, non-circular pattern several other ``mrr.domain``
modules already use for specific ``mrr.contracts`` types (e.g.
``mrr.domain.claim_ceiling``'s ``CLAIM_CEILING_ORDER``, ``mrr.domain
.task_trust``'s ``TaskBundle``). ``mrr.domain.agreement`` (R1's pure metric
core) imports nothing from ``mrr.contracts`` at all — checked directly below
as a stronger, additional guarantee for that module specifically.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
AGREEMENT_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "agreement.py"
AGREEMENT_REPORT_MODULE = (
    REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "agreement_report.py"
)

#: Repository/service/adapter/framework modules neither pure domain module
#: may import — mirrors tests/unit/architecture/test_research_report_boundary
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
)

#: ``mrr.domain.agreement`` (R1's pure metric core) additionally may not
#: import ANY ``mrr.contracts`` module at all — it has no need for a
#: Pydantic model, unlike ``agreement_report.py``.
_AGREEMENT_MODULE_EXTRA_FORBIDDEN = ("mrr.contracts",)


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


def test_agreement_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(AGREEMENT_MODULE, _FORBIDDEN_MODULE_PREFIXES)
    _assert_no_forbidden_import(AGREEMENT_MODULE, _AGREEMENT_MODULE_EXTRA_FORBIDDEN)


def test_agreement_report_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(AGREEMENT_REPORT_MODULE, _FORBIDDEN_MODULE_PREFIXES)


def test_agreement_module_has_no_numeric_dependency_imports() -> None:
    """task-packets/N1-T01.yaml derived_decisions (d): hand-rolled metrics,
    no numpy/scipy/scikit-learn/statsmodels — checked explicitly here (also
    covered by ``_FORBIDDEN_MODULE_PREFIXES`` above, asserted a second time
    as a standalone, clearly-named test so a reviewer sees this specific
    guarantee without reading the shared helper's full list).
    """
    imported = _imported_module_names(AGREEMENT_MODULE)
    for banned in ("numpy", "scipy", "sklearn", "statsmodels"):
        assert not any(name == banned or name.startswith(banned + ".") for name in imported)
