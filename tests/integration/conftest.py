"""conftest for the integration test tier (task-packets/E1-T05.yaml).

Requires a real PostgreSQL reachable via ``MRR_TEST_DATABASE_URL``. If that
variable is unset:

- outside CI: skip visibly with an explicit reason (never a silent pass —
  ``scripts/run_test_tier.py`` would otherwise treat an empty/skipped tier as
  a failure unless it is declared in tests/EMPTY_TIERS.txt, which this tier
  no longer is once populated);
- inside CI (the ``CI`` environment variable is truthy): fail hard. A CI run
  without a database must never look green.

Each test that requests the ``postgres_engine`` fixture gets a freshly
created, uniquely named PostgreSQL schema with ``alembic upgrade head`` run
against it via the Alembic Python API (not a subprocess), and the schema is
dropped afterward regardless of test outcome. Isolation is via ``search_path``
(a ``-csearch_path=<schema>`` libpq option appended to the connection URL,
task-packets/E1-T05.yaml's suggested "simplest robust approach") rather than
per-test databases, so a single MRR_TEST_DATABASE_URL/database suffices even
when pytest runs tests concurrently.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"

_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_DATABASE_URL_ENV_VAR = "MRR_DATABASE_URL"


def _require_test_database_url_or_skip() -> str:
    base_url = os.environ.get(_TEST_DATABASE_URL_ENV_VAR)
    if base_url:
        return base_url
    if os.environ.get("CI"):
        pytest.fail(
            f"{_TEST_DATABASE_URL_ENV_VAR} is unset in CI — an integration test run without a "
            "real PostgreSQL database must never look green."
        )
    pytest.skip(reason=f"no PostgreSQL available ({_TEST_DATABASE_URL_ENV_VAR} unset)")


def _schema_scoped_url(base_url: str, schema: str) -> str:
    """Append a libpq ``options=-c search_path=<schema>`` query parameter so
    every connection made against the returned URL — both Alembic's and the
    repository classes' — resolves unqualified table names inside `schema`
    without needing per-table schema-qualification anywhere in
    mrr.persistence.tables or migrations/versions/.
    """
    options_value = quote(f"-c search_path={schema}", safe="")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={options_value}"


def _run_alembic_upgrade_head(database_url: str) -> None:
    """Run ``alembic upgrade head`` via the Alembic Python API against
    `database_url`, by setting MRR_DATABASE_URL for the duration of the call
    — migrations/env.py reads that variable exclusively, exactly as it does
    outside tests, so this exercises the real upgrade path rather than a
    test-only shortcut.
    """
    previous = os.environ.get(_DATABASE_URL_ENV_VAR)
    os.environ[_DATABASE_URL_ENV_VAR] = database_url
    try:
        alembic_cfg = Config(str(ALEMBIC_INI))
        alembic_cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        command.upgrade(alembic_cfg, "head")
    finally:
        if previous is None:
            os.environ.pop(_DATABASE_URL_ENV_VAR, None)
        else:
            os.environ[_DATABASE_URL_ENV_VAR] = previous


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    base_url = _require_test_database_url_or_skip()

    schema = f"mrr_test_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(base_url)
    try:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))

        scoped_url = _schema_scoped_url(base_url, schema)
        _run_alembic_upgrade_head(scoped_url)

        engine = sa.create_engine(scoped_url)
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
