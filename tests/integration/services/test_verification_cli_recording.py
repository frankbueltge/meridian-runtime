"""Integration tests for ``mrr verification record`` (task-packets/
K1-T05.yaml), driven end to end through the REAL console-script entry point
(``mrr.services.cli.main.main``, exactly as ``tests/integration/services/cli/
test_synthesis_main_sensitivity_variation_parameters_file.py`` already
exercises ``mrr synthesis run``) against a real, throwaway PostgreSQL test
schema.

Uses a LOCAL ``postgres_url`` fixture — this codebase's own established
"small, self-contained piece duplicated across sibling test-tier modules"
convention (see ``tests/integration/conftest.py``'s own ``postgres_engine``
fixture, which yields an ``Engine`` rather than the ``--database-url``
STRING a real CLI invocation needs; and
``test_synthesis_main_sensitivity_variation_parameters_file.py``'s
identically-shaped local fixture) — rather than the shared ``postgres_engine``
fixture.

Claims are seeded with a REAL ``mrr.services.claim.service.ClaimService``
(never a raw ``_seed_generic`` insert) bound over a second ``Engine`` opened
against the same schema-scoped URL, mirroring
``tests/integration/services/verification/test_service.py``'s own wiring.

Acceptance-test mapping (task-packets/K1-T05.yaml, integration tier):

- AT1 ("a valid VerificationResult file for a stored claim records
  successfully; the verification row and its verification.recorded event
  exist atomically; the printed JSON names id, revision, and claim status")
  -> ``test_cli_records_a_valid_verification_and_prints_id_revision_and_claim_status``.
- AT2 ("the same file with reviewer_id equal to the claim's proposer_id is
  refused via the service's rule-8 gate; exit code 3; no row, no event") ->
  ``test_self_verification_by_proposer_is_refused_and_persists_nothing``.
- AT3 ("recommendation=='fail' observably drives the claim's status through
  ClaimService ..., visible in the CLI's printed claim status") ->
  ``test_failing_recommendation_drives_the_claim_status_via_claim_service``.
- AT5's "unknown-claim URN produces exit 3 with the claim id named" half
  (moved here from the unit tier — see
  ``tests/unit/cli/test_verification_cli_args.py``'s own module docstring
  for why) -> ``test_unknown_claim_id_produces_exit_3_naming_the_claim_id``.

An additional, non-numbered test
(``test_claim_id_resolving_to_a_non_claim_object_is_refused``) exercises
``record_verification``'s own "resolved object is not of kind Claim" guard —
not a named acceptance test, but a wiring-safety check this packet's own
implementation adds (see ``verification_orchestration``'s module docstring).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from mrr.contracts import Claim
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.claim.service import ClaimService
from mrr.services.claim.service import bind_edge_unit_of_work as bind_claim_edge_uow
from mrr.services.claim.service import bind_unit_of_work as bind_claim_uow
from mrr.services.cli.main import main as mrr_main
from sqlalchemy import Engine

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"
ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"

_POLICY_VERSION = "policy-k1-t05-cli-test"


def _require_test_database_url_or_skip() -> str:
    """A local copy of tests/integration/conftest.py's identical helper —
    see that module's own docstring for why this is duplicated rather than
    imported (this codebase's own established convention).
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
    applied) — mirrors ``test_synthesis_main_sensitivity_variation_parameters_
    file.py``'s own identically-named, identically-shaped fixture.
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


# ---------------------------------------------------------------------------
# Fixture factories — mirrors tests/integration/services/verification/
# test_service.py's own helpers.
# ---------------------------------------------------------------------------


def _independence_profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "principal": new_urn("person"),
        "model_family": "human-reviewer (no model invoked)",
        "prompt_family": "n/a — manual review checklist v3",
        "retrieval_path": "independent re-fetch via publisher API, not the original crawl",
        "code_path": "independent recomputation script, not the original analysis notebook",
        "data_access_path": "read-only snapshot corpus, separate credential from the proposer's",
    }
    profile.update(overrides)
    return profile


def _verification_payload(*, target_id: str, reviewer_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": new_urn("verification"),
        "api_version": "mrr/v1alpha1",
        "kind": "VerificationResult",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": reviewer_id,
        "content_hash": "sha256:" + "b" * 64,
        "target_id": target_id,
        "target_kind": "claim",
        "reviewer_id": reviewer_id,
        "reviewer_role": "independent reviewer",
        "independence_profile": _independence_profile(),
        "verification_type": "skeptic",
        "checks_performed": ["Searched for counterevidence and alternative explanations"],
        "evidence_inspected": [],
        "numeric_recomputation": None,
        "findings": [],
        "recommendation": "pass",
        "confidence": 0.8,
        "rationale": "Fixture rationale for a CLI integration test.",
        "conflicts_of_interest": [],
        "adjudication_relation": None,
    }
    payload.update(overrides)
    return payload


def _write_json(path: Path, content: dict[str, Any]) -> Path:
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _claim_service_for(engine: Engine) -> ClaimService:
    object_repository = PostgresObjectRepository(engine)
    event_log = PostgresEventLog(engine)
    edge_repository = PostgresEdgeRepository(engine)
    claim_record = bind_claim_uow(engine, object_repository, event_log)
    claim_record_edge = bind_claim_edge_uow(engine, event_log)
    return ClaimService(
        object_repository, event_log, edge_repository, claim_record, claim_record_edge
    )


def _kwargs(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "actor": new_urn("agent"),
        "policy_version": _POLICY_VERSION,
        "correlation_id": new_urn("research-run"),
    }
    data.update(overrides)
    return data


def _claim_draft(*, proposer_id: str | None = None) -> Claim:
    data: dict[str, Any] = {
        "id": new_urn("claim"),
        "api_version": "mrr/v1alpha1",
        "kind": "Claim",
        "practice_id": new_urn("practice"),
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": new_urn("agent-role"),
        "content_hash": "sha256:" + "a" * 64,
        "assertion": "Does this fixture assertion satisfy the schema's minimum length rule?",
        "claim_type": "statistical",
        "scope": {},
        "status": "draft",
        "evidence_relations": [],
        "counterevidence_relations": [],
        "dependencies": [],
        "source_family_ids": [],
        "uncertainty": [],
        "known_unknowns": [],
        "proposer_id": proposer_id or new_urn("agent-role"),
        "verification_ids": [],
        "correction_ids": [],
    }
    return Claim.model_validate(data)


def _seed_claim_under_review(engine: Engine, *, proposer_id: str | None = None) -> Claim:
    claim_service = _claim_service_for(engine)
    claim = _claim_draft(proposer_id=proposer_id)
    claim_service.create(claim, **_kwargs())
    stored = claim_service.submit_for_review(claim.id, **_kwargs())
    return Claim.model_validate(stored.body)


# ---------------------------------------------------------------------------
# AT1: the happy path.
# ---------------------------------------------------------------------------


def test_cli_records_a_valid_verification_and_prints_id_revision_and_claim_status(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        claim = _seed_claim_under_review(engine)
    finally:
        engine.dispose()

    reviewer_id = new_urn("person")
    verification_id = new_urn("verification")
    payload = _verification_payload(target_id=claim.id, reviewer_id=reviewer_id, id=verification_id)
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = mrr_main(
        [
            "verification",
            "record",
            "--database-url",
            postgres_url,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            claim.id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            _POLICY_VERSION,
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verification_id"] == verification_id
    assert out["revision"] == 1
    assert out["claim_id"] == claim.id
    assert out["claim_status"] == "under_review"  # a "pass" recommendation changes nothing

    verify_engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(verify_engine)
        stored = object_repository.get_latest(verification_id)
        assert stored.revision == 1
        assert stored.body["recommendation"] == "pass"

        event_log = PostgresEventLog(verify_engine)
        matching_events = [a for a in event_log.read_all() if a.event.object_id == verification_id]
        assert len(matching_events) == 1
        assert matching_events[0].event.event_type == "verification.recorded"
    finally:
        verify_engine.dispose()


# ---------------------------------------------------------------------------
# AT2: the rule-8 self-verification gate.
# ---------------------------------------------------------------------------


def test_self_verification_by_proposer_is_refused_and_persists_nothing(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proposer_id = new_urn("agent-role")
    engine = sa.create_engine(postgres_url)
    try:
        claim = _seed_claim_under_review(engine, proposer_id=proposer_id)
    finally:
        engine.dispose()

    verification_id = new_urn("verification")
    payload = _verification_payload(target_id=claim.id, reviewer_id=proposer_id, id=verification_id)
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = mrr_main(
        [
            "verification",
            "record",
            "--database-url",
            postgres_url,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            claim.id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            _POLICY_VERSION,
        ]
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "SelfVerificationError" in err

    verify_engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(verify_engine)
        assert object_repository.list_revisions(verification_id) == []

        event_log = PostgresEventLog(verify_engine)
        assert [a for a in event_log.read_all() if a.event.object_id == verification_id] == []

        # The claim itself is untouched — still under_review, still at the
        # same revision `_seed_claim_under_review` already left it at (create
        # -> revision 1 "draft", submit_for_review -> revision 2
        # "under_review" — every ClaimService lifecycle method mints a new
        # revision; see mrr.services.claim.service's own module docstring).
        updated_claim = object_repository.get_latest(claim.id)
        assert updated_claim.revision == claim.revision
        assert updated_claim.body["status"] == "under_review"
    finally:
        verify_engine.dispose()


# ---------------------------------------------------------------------------
# AT3: recommendation == "fail" drives the claim's status.
# ---------------------------------------------------------------------------


def test_failing_recommendation_drives_the_claim_status_via_claim_service(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        claim = _seed_claim_under_review(engine)
    finally:
        engine.dispose()

    reviewer_id = new_urn("person")
    verification_id = new_urn("verification")
    payload = _verification_payload(
        target_id=claim.id,
        reviewer_id=reviewer_id,
        id=verification_id,
        recommendation="fail",
    )
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = mrr_main(
        [
            "verification",
            "record",
            "--database-url",
            postgres_url,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            claim.id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            _POLICY_VERSION,
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claim_status"] == "contested"  # under_review -> contested (MRR-FR-075)

    verify_engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(verify_engine)
        updated_claim = object_repository.get_latest(claim.id)
        assert updated_claim.body["status"] == "contested"

        event_log = PostgresEventLog(verify_engine)
        claim_events = [
            a.event.event_type for a in event_log.read_all() if a.event.object_id == claim.id
        ]
        assert claim_events[-1] == "claim.contested"
    finally:
        verify_engine.dispose()


# ---------------------------------------------------------------------------
# AT5 (reclassified half): an unknown claim id produces exit 3, naming the id.
# ---------------------------------------------------------------------------


def test_unknown_claim_id_produces_exit_3_naming_the_claim_id(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unknown_claim_id = new_urn("claim")
    reviewer_id = new_urn("person")
    payload = _verification_payload(target_id=unknown_claim_id, reviewer_id=reviewer_id)
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = mrr_main(
        [
            "verification",
            "record",
            "--database-url",
            postgres_url,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            unknown_claim_id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            _POLICY_VERSION,
        ]
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "ObjectNotFoundError" in err
    assert unknown_claim_id in err


# ---------------------------------------------------------------------------
# Extra wiring-safety check: --claim-id resolving to a non-Claim object.
# ---------------------------------------------------------------------------


def test_claim_id_resolving_to_a_non_claim_object_is_refused(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        not_a_claim_id = new_urn("run-manifest")
        not_a_claim = StoredObject(
            id=not_a_claim_id,
            api_version="mrr/v1alpha1",
            kind="RunManifest",
            practice_id=new_urn("practice"),
            revision=1,
            created_at=datetime.now(UTC),
            created_by=new_urn("agent-role"),
            content_hash="sha256:" + "c" * 64,
            supersedes=None,
            labels=None,
            body={"not": "a claim"},
        )
        object_repository.insert_revision(not_a_claim, expected_current_revision=None)
    finally:
        engine.dispose()

    reviewer_id = new_urn("person")
    payload = _verification_payload(target_id=not_a_claim_id, reviewer_id=reviewer_id)
    verification_file = _write_json(tmp_path / "verification.json", payload)

    exit_code = mrr_main(
        [
            "verification",
            "record",
            "--database-url",
            postgres_url,
            "--verification-file",
            str(verification_file),
            "--claim-id",
            not_a_claim_id,
            "--actor",
            new_urn("agent"),
            "--policy-version",
            _POLICY_VERSION,
        ]
    )

    assert exit_code == 3
    err = capsys.readouterr().err
    assert "RunManifest" in err
