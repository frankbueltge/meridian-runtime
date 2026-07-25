"""Import-boundary checks for task-packets/E5-T08.yaml (R7's "architecture"
tier): ``mrr.adapters.federation`` imports no service module, and NOTHING in
this packet (the new adapter package plus the new federation CLI module)
imports a socket/TLS/HTTP client-or-server module, a web/workflow framework,
a model-provider or object-storage SDK, or ``sqlalchemy`` — this packet
opens no network connection and no database connection anywhere
(task-packets/E5-T08.yaml invariants: "No network connection, no socket, no
TLS/mTLS, no HTTP client or server, and no database connection anywhere in
this packet").

Independent of ``lint-imports`` (tests/unit/architecture/
test_import_boundaries.py, run as part of ``make lint``) — mirrors
tests/unit/architecture/test_object_store_boundary.py's/
test_llm_adapter_boundary.py's own precedent of an explicit AST-based check
alongside the shared import-linter contract for a newly registered
``adapters/`` namespace root.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FEDERATION_ADAPTER_ROOT = REPO_ROOT / "adapters" / "federation" / "mrr" / "adapters" / "federation"
FEDERATION_CLI_MODULE = (
    REPO_ROOT / "services" / "control_plane" / "mrr" / "services" / "cli" / "federation_main.py"
)

#: No network, no TLS, no web/workflow framework, no model-provider or
#: object-storage SDK, and no database — task-packets/E5-T08.yaml's own
#: invariants list.
_FORBIDDEN_MODULE_PREFIXES = (
    "socket",
    "ssl",
    "http",
    "httpx",
    "requests",
    "urllib3",
    "aiohttp",
    "fastapi",
    "starlette",
    "temporalio",
    "openai",
    "anthropic",
    "boto3",
    "botocore",
    "sqlalchemy",
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


def _is_forbidden(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes)


def _forbidden_imports(paths: list[Path], prefixes: tuple[str, ...]) -> dict[str, set[str]]:
    offending: dict[str, set[str]] = {}
    for path in paths:
        imported = _imported_module_names(path.read_text())
        hits = {name for name in imported if _is_forbidden(name, prefixes)}
        if hits:
            offending[str(path.relative_to(REPO_ROOT))] = hits
    return offending


def test_federation_adapter_has_no_forbidden_imports() -> None:
    python_files = sorted(FEDERATION_ADAPTER_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {FEDERATION_ADAPTER_ROOT}"

    offending = _forbidden_imports(python_files, _FORBIDDEN_MODULE_PREFIXES)
    assert not offending, (
        "mrr.adapters.federation must open no network/TLS/HTTP/database connection "
        f"(task-packets/E5-T08.yaml); forbidden imports found: {offending}"
    )


def test_federation_cli_has_no_forbidden_imports() -> None:
    assert FEDERATION_CLI_MODULE.is_file(), f"expected {FEDERATION_CLI_MODULE} to exist"

    offending = _forbidden_imports([FEDERATION_CLI_MODULE], _FORBIDDEN_MODULE_PREFIXES)
    assert not offending, (
        "mrr.services.cli.federation_main must open no network/TLS/HTTP/database connection "
        f"(task-packets/E5-T08.yaml); forbidden imports found: {offending}"
    )


def test_federation_adapter_imports_no_service_module() -> None:
    python_files = sorted(FEDERATION_ADAPTER_ROOT.rglob("*.py"))
    assert python_files, f"expected at least one .py file under {FEDERATION_ADAPTER_ROOT}"

    offending = _forbidden_imports(python_files, ("mrr.services",))
    assert not offending, (
        "mrr.adapters.federation must import no mrr.services module (task-packets/"
        f"E5-T08.yaml R1); forbidden imports found: {offending}"
    )
