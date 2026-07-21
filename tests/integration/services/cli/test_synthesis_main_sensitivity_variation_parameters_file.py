"""Integration test for the MRR-MTH-018 CLI wiring end to end
(task-packets/K1-T04c.yaml): ``mrr synthesis run --sensitivity-variation-
parameters-file`` (via ``mrr.services.cli.main.main``), pointed at small,
synthetic, tmp-path-written fixtures for ALL six ``--*-file`` flags (no real
atlas corpus — that stays the e2e tier's own headline test,
tests/e2e/test_k1_t04_first_real_run.py), against a real, throwaway
PostgreSQL test schema.

Uses a LOCAL ``postgres_url`` fixture — this codebase's own established
"small, self-contained piece duplicated across sibling test-tier modules"
convention (see tests/e2e/conftest.py's own docstring and
tests/e2e/test_k1_t04_first_real_run.py's own identically-shaped local
fixture) — rather than importing from tests/integration/conftest.py's own
``postgres_engine`` fixture, which yields an ``Engine`` rather than the
``--database-url`` STRING a real CLI invocation needs.

This file NEVER reads from, writes to, or otherwise touches the sealed
``mrr_k1t04_real_run_v2`` schema — ``postgres_url`` mints a brand-new,
uniquely-named schema per test, dropped afterward.

Acceptance-test mapping (task-packets/K1-T04c.yaml):

- "[integration, CLI wiring, small synthetic fixtures — no real corpus]" ->
  ``test_cli_run_with_all_six_fixture_files_completes_and_runs_the_declared_variation``.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from mrr.persistence.repositories import PostgresObjectRepository
from mrr.services.cli.main import main as mrr_main

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"
ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"

_TEST_CODE_REVISION = "git:k1-t04c-cli-wiring-test-fixture"


def _require_test_database_url_or_skip() -> str:
    """A local copy of tests/e2e/conftest.py's/tests/integration/conftest.py's
    identical helper — see those modules' own docstrings for why this is
    duplicated rather than imported (this codebase's own established
    convention).
    """
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
    options_value = quote(f"-c search_path={schema}", safe="")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={options_value}"


def _run_alembic_upgrade_head(database_url: str) -> None:
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_cfg.attributes[_ATTRIBUTES_URL_KEY] = database_url
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
def postgres_url() -> Iterator[str]:
    """Yields a schema-scoped ``--database-url`` STRING (migrations already
    applied) — mirrors tests/e2e/test_k1_t04_first_real_run.py's own
    identically-named, identically-shaped fixture.
    """
    base_url = _require_test_database_url_or_skip()
    schema = f"mrr_test_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(base_url)
    try:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        scoped_url = _schema_scoped_url(base_url, schema)
        _run_alembic_upgrade_head(scoped_url)
        yield scoped_url
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


def _question_model_body() -> dict[str, Any]:
    return {
        "raw_question": "Do the fixture works instantiate the mechanism or just reference it?",
        "claim_type_sought": "interpretive",
        "scope": {"population": "test-fixture works", "conditions": []},
        "load_bearing_terms": ["mechanism", "instantiate", "reference"],
    }


def _concept_charter_body() -> dict[str, Any]:
    return {
        "entries": [
            {
                "entry_id": "instantiate-vs-reference-v1",
                "term": "instantiate",
                "definition": "test definition of instantiate",
                "scope_note": None,
            }
        ]
    }


def _method_protocol_body() -> dict[str, Any]:
    return {
        "extraction_fields": ["claim_relevant_finding"],
        "inclusion_criteria": ["catalogued"],
        "exclusion_criteria": ["derived"],
        "sensitivity_variations": ["variant-x"],
        "planned_analyses": ["fixture-analysis"],
        "kill_conditions": ["fewer than 2 -> stop_insufficient_evidence"],
    }


def _corpus_entry(entry_id: str, *, applies_to_analysis: str) -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "applies_to_analysis": applies_to_analysis,
        "claim_type": "interpretive",
        "evidence_relation": "supports",
        "verification_status": "verified",
        "unverifiable_reason": None,
        "claim_relevant_finding": f"Finding for {entry_id}.",
        "extraction": {},
        "source_family_id": None,
        "title": f"Fixture source {entry_id}",
        "creators": ["Fixture Author"],
        "retrieval_timestamp": "2026-07-21T12:00:00Z",
        "retrieval_method": "test-fixture-direct-read",
        "source_type": "test-fixture-artifact",
        "primary_secondary_derived": "primary",
    }


def _fixture_corpus() -> list[dict[str, Any]]:
    return [
        _corpus_entry("entry-1", applies_to_analysis="fixture-analysis"),
        _corpus_entry("entry-2", applies_to_analysis="fixture-analysis"),
    ]


def _protocol_parameters_sidecar() -> dict[str, Any]:
    return {
        "protocol_id": "placeholder-overwritten-at-run-time",
        "protocol_lock_content_hash": "placeholder-overwritten-at-run-time",
        "inclusion_filter": {},
        "eligibility_rules": {
            "supported": {"min_independent_source_families": 2},
            "contested": {"min_independent_source_families": 1},
        },
        "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 2}},
        "non_applicability_conditions": ["Applies only to the fixture corpus."],
    }


def _sensitivity_variation_parameters_file_body() -> dict[str, Any]:
    return {
        "variant-x": {
            "protocol_id": "placeholder-overwritten-at-run-time",
            "protocol_lock_content_hash": "placeholder-overwritten-at-run-time",
            "variation_entry_id": "variant-x",
            "inclusion_filter": {},
            "eligibility_rules": {
                "supported": {"min_independent_source_families": 2},
                "contested": {"min_independent_source_families": 1},
            },
            "kill_conditions": {"stop_insufficient_evidence": {"min_included_sources": 2}},
            "source_family_overrides": {},
        }
    }


def _write_json(path: Path, content: Any) -> Path:
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_cli_run_with_all_six_fixture_files_completes_and_runs_the_declared_variation(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    question_model_file = _write_json(tmp_path / "question-model.json", _question_model_body())
    concept_charter_file = _write_json(tmp_path / "concept-charter.json", _concept_charter_body())
    method_protocol_file = _write_json(tmp_path / "method-protocol.json", _method_protocol_body())
    corpus_file = _write_json(tmp_path / "corpus-entries.json", _fixture_corpus())
    protocol_parameters_file = _write_json(
        tmp_path / "protocol-parameters.json", _protocol_parameters_sidecar()
    )
    sensitivity_variation_parameters_file = _write_json(
        tmp_path / "sensitivity-variation-parameters.json",
        _sensitivity_variation_parameters_file_body(),
    )

    exit_code = mrr_main(
        [
            "synthesis",
            "run",
            "--database-url",
            postgres_url,
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--question-model-file",
            str(question_model_file),
            "--concept-charter-file",
            str(concept_charter_file),
            "--method-protocol-file",
            str(method_protocol_file),
            "--corpus-file",
            str(corpus_file),
            "--protocol-parameters-file",
            str(protocol_parameters_file),
            "--sensitivity-variation-parameters-file",
            str(sensitivity_variation_parameters_file),
            "--code-revision",
            _TEST_CODE_REVISION,
            "--json",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_state"] == "completed"
    assert out["evidence_matrix_id"] is not None

    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        matrix = object_repository.get_latest(out["evidence_matrix_id"])
        sensitivity_results = matrix.body["sensitivity_analysis_results"]
        assert sensitivity_results is not None
        assert any(entry["variation_entry_id"] == "variant-x" for entry in sensitivity_results)
    finally:
        engine.dispose()
