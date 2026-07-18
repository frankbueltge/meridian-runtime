"""conftest for the e2e test tier (task-packets/E2-T07.yaml).

Requires a real PostgreSQL reachable via ``MRR_TEST_DATABASE_URL``, with the
exact same skip/fail-hard guard as ``tests/integration/conftest.py``:

- outside CI: skip visibly with an explicit reason (never a silent pass);
- inside CI (the ``CI`` environment variable is truthy): fail hard. A CI run
  without a database must never look green.

This is a deliberate, small duplication of ``tests/integration/conftest.py``
's ``postgres_engine`` fixture, not a shared import: pytest conftest modules
are not a natural place to share code across sibling test-tier directories
without a new top-level ``tests/conftest.py`` (which is outside this task's
``allowed_paths`` — only ``tests/e2e/**`` and ``tests/integration/**``
individually are listed, not ``tests/`` itself), and this codebase already
has an established convention of duplicating small, self-contained pieces
across sibling modules rather than reaching for a shared root (see, e.g.,
every service module's own local copy of ``bind_unit_of_work``). E2E-001
(E2 scope) needs its own real Postgres schema exactly like the integration
tier does, for exactly the same reason: the merged E2 services persist
through the real ``mrr.persistence`` engine, not a fake.
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

#: Matches migrations/env.py's `_ATTRIBUTES_URL_KEY` — see
#: tests/integration/conftest.py's own docstring for why this bypasses
#: `Config.set_main_option`/`get_main_option`/`get_section`.
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"


def _require_test_database_url_or_skip() -> str:
    base_url = os.environ.get(_TEST_DATABASE_URL_ENV_VAR)
    if base_url:
        return base_url
    if os.environ.get("CI"):
        pytest.fail(
            f"{_TEST_DATABASE_URL_ENV_VAR} is unset in CI — an e2e test run without a "
            "real PostgreSQL database must never look green."
        )
    pytest.skip(reason=f"no PostgreSQL available ({_TEST_DATABASE_URL_ENV_VAR} unset)")


def _schema_scoped_url(base_url: str, schema: str) -> str:
    options_value = quote(f"-c search_path={schema}", safe="")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={options_value}"


def _run_alembic_upgrade_head(database_url: str) -> None:
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_cfg.attributes[_ATTRIBUTES_URL_KEY] = database_url
    command.upgrade(alembic_cfg, "head")


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
