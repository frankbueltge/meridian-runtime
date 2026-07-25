"""Explicit AST-based check that ``mrr.domain.archive_dump``/``mrr.domain
.anchoring_integrity``/``mrr.domain.anchoring_integrity_report`` import no
repository/service/adapter/framework module (task-packets/N2-T02b.yaml R7:
"architecture (lint-imports): the two domain modules import no framework/
service module; the service imports no sqlalchemy"), independent of the
import-linter contract in pyproject.toml that
tests/unit/architecture/test_import_boundaries.py already runs as a
subprocess (that contract bans ``mrr.services``/framework imports from every
``mrr.domain`` module collectively; this test is scoped to these three
modules alone, mirroring tests/unit/architecture/test_field_observation_boundary
.py's identical narrower-than-the-whole-package precedent).

``mrr.contracts.common`` is deliberately NOT in the forbidden list:
``mrr.domain.anchoring_integrity_report`` legitimately imports ``MRRModel``
from it (task-packets/N2-T02b.yaml R3's own "a Pydantic v2 MRRModel"
requirement). Likewise, ``mrr.domain.archive_dump``/``mrr.domain
.anchoring_integrity`` are deliberately allowed to import EACH OTHER — the
established "sibling domain module" reuse pattern (e.g.
``citation_audit_report.py`` importing ``citation_audit.py``) —
``mrr.domain.anchoring_integrity`` legitimately consumes ``mrr.domain
.archive_dump``'s typed rows (``ClaimRow``, ``EvidenceAnchorRow``,
``SourceRecordRow``), and ``mrr.domain.anchoring_integrity_report``
legitimately consumes ``mrr.domain.anchoring_integrity``'s verdict types.

``mrr.domain.archive_dump`` (R1's pure, stdlib-only parser) imports nothing
from ``mrr.contracts`` or any other ``mrr.domain.*`` module at all — checked
directly below as a stronger, additional guarantee for that module
specifically, mirroring ``mrr.domain.citation_audit``'s own identical
guarantee. Neither ``mrr.domain.archive_dump`` nor ``mrr.domain
.anchoring_integrity`` may import ``hashlib`` — hashing a file's bytes is
the SERVICE's job (mirrors ``mrr.domain.field_observation``'s identical
"no hashlib" guarantee); both modules only ever COMPARE already-computed
hash strings.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_DUMP_MODULE = REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "archive_dump.py"
ANCHORING_INTEGRITY_MODULE = (
    REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "anchoring_integrity.py"
)
ANCHORING_INTEGRITY_REPORT_MODULE = (
    REPO_ROOT / "packages" / "domain" / "mrr" / "domain" / "anchoring_integrity_report.py"
)
ANCHORING_INTEGRITY_SERVICE_MODULE = (
    REPO_ROOT
    / "services"
    / "control_plane"
    / "mrr"
    / "services"
    / "anchoring_integrity"
    / "service.py"
)

#: Repository/service/adapter/framework modules no domain module here may
#: import — mirrors tests/unit/architecture/test_field_observation_boundary
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

#: ``mrr.domain.archive_dump`` (R1's pure parser) additionally may not
#: import ANY ``mrr.contracts`` or ``mrr.domain.*`` module at all.
_ARCHIVE_DUMP_EXTRA_FORBIDDEN = ("mrr.contracts", "mrr.domain")


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


def test_archive_dump_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(ARCHIVE_DUMP_MODULE, _FORBIDDEN_MODULE_PREFIXES)
    _assert_no_forbidden_import(ARCHIVE_DUMP_MODULE, _ARCHIVE_DUMP_EXTRA_FORBIDDEN)


def test_anchoring_integrity_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(ANCHORING_INTEGRITY_MODULE, _FORBIDDEN_MODULE_PREFIXES)


def test_anchoring_integrity_report_module_imports_no_framework_or_service_module() -> None:
    _assert_no_forbidden_import(ANCHORING_INTEGRITY_REPORT_MODULE, _FORBIDDEN_MODULE_PREFIXES)


def test_archive_dump_module_uses_only_stdlib_and_python_builtins() -> None:
    """task-packets/N2-T02b.yaml R1: "pure, no-IO, stdlib only" — checked
    explicitly here as a standalone, clearly-named test."""
    imported = _imported_module_names(ARCHIVE_DUMP_MODULE)
    allowed_prefixes = ("json", "re", "collections.abc", "dataclasses", "typing")
    for name in imported:
        if name.startswith("__future__"):
            continue
        assert any(
            name == prefix or name.startswith(prefix + ".") for prefix in allowed_prefixes
        ), (
            f"unexpected import {name!r} in {ARCHIVE_DUMP_MODULE} — expected only one of "
            f"{allowed_prefixes!r}"
        )


def test_anchoring_integrity_module_imports_only_archive_dump_among_mrr_modules() -> None:
    imported = _imported_module_names(ANCHORING_INTEGRITY_MODULE)
    for name in imported:
        if not name.startswith("mrr."):
            continue
        assert name == "mrr.domain.archive_dump" or name.startswith("mrr.domain.archive_dump."), (
            f"unexpected mrr.* import {name!r} in {ANCHORING_INTEGRITY_MODULE}"
        )


def test_anchoring_integrity_report_module_imports_only_the_allowed_domain_modules() -> None:
    imported = _imported_module_names(ANCHORING_INTEGRITY_REPORT_MODULE)
    allowed_mrr_prefixes = ("mrr.contracts.common", "mrr.domain.anchoring_integrity")
    for name in imported:
        if not name.startswith("mrr."):
            continue
        assert any(
            name == prefix or name.startswith(prefix + ".") for prefix in allowed_mrr_prefixes
        ), f"unexpected mrr.* import {name!r} in {ANCHORING_INTEGRITY_REPORT_MODULE}"


def test_anchoring_integrity_service_imports_no_sqlalchemy() -> None:
    """task-packets/N2-T02b.yaml invariant: "sqlalchemy is never imported by
    this packet's code" — checked directly against the service module too,
    not only the domain layer."""
    imported = _imported_module_names(ANCHORING_INTEGRITY_SERVICE_MODULE)
    for name in imported:
        assert name != "sqlalchemy" and not name.startswith("sqlalchemy."), (
            f"forbidden import {name!r} in {ANCHORING_INTEGRITY_SERVICE_MODULE}"
        )
