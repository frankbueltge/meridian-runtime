"""K1-T04 (first real run — the model-collapse question over the two real
atlases) — task-packets/K1-T04.yaml. Drives
``mrr.services.cli.synthesis_setup.establish_and_run_synthesis`` end to end
against a real PostgreSQL (this directory's own ``postgres_engine`` fixture)
and a real, tmp-path-backed ``LocalFilesystemArtifactStore``, using the REAL,
committed atlas-derived fixtures at ``corpora/model-collapse/`` — the
runtime's FIRST REAL research output.

Acceptance-test mapping (task-packets/K1-T04.yaml):

- "[headline real run, e2e tier]" ->
  ``test_headline_real_run_over_the_pinned_atlas_corpus``.
- "[CLI reproduction]" -> ``test_cli_reproduces_the_real_run``.
- "[regression]" -> covered by the SAME ``make test-e2e`` run also
  collecting ``test_e2e_001_single_node_evidence_loop.py``,
  ``test_k0_t02_capability_dispatch.py``, and
  ``test_k1_t03_synthesis_evidence_loop.py`` unmodified — not duplicated
  here.
- "MRR-FR-004, --deny-score-approval's own CLI plumbing" ->
  ``test_cli_deny_score_approval_flag_gates_the_run_via_mrr_fr_004`` (review
  follow-up: ``mrr run``'s own identical flag has never had a CLI-level
  test either — ``run_local_evidence_loop`` is only exercised directly, per
  ``tests/e2e/test_e2e_001_single_node_evidence_loop.py``'s own
  ``test_unapproved_score_aborts_at_the_gate`` — so this is this packet's
  own new coverage, not a regression fix).
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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.services.cli.main import main as mrr_main
from mrr.services.cli.synthesis_setup import establish_and_run_synthesis
from sqlalchemy import Engine

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_DIR = _REPO_ROOT / "corpora" / "model-collapse"
_TEST_CODE_REVISION = "git:k1-t04-first-real-run-test"

_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"
ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"


def _require_test_database_url_or_skip() -> str:
    """A local copy of tests/e2e/conftest.py's identical helper — see that
    module's own docstring for why this is duplicated rather than imported
    (no shared ``tests/conftest.py`` root exists; this codebase's own
    established convention duplicates small, self-contained pieces across
    sibling test-tier modules).
    """
    base_url = os.environ.get(_TEST_DATABASE_URL_ENV_VAR)
    if base_url:
        return base_url
    if os.environ.get("CI"):
        pytest.fail(
            f"{_TEST_DATABASE_URL_ENV_VAR} is unset in CI — an e2e test run without a real "
            "PostgreSQL database must never look green."
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
    """Like this directory's own ``postgres_engine`` fixture, but yields the
    schema-scoped URL STRING itself (with migrations already applied) rather
    than an ``Engine`` — needed for the CLI reproduction test, which builds
    its own engine from a ``--database-url`` string exactly like a real
    operator invocation would.
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


def _artifact_store(tmp_path: Path) -> LocalFilesystemArtifactStore:
    return LocalFilesystemArtifactStore(tmp_path / "artifacts")


def _load_json(name: str) -> Any:
    return json.loads((_CORPUS_DIR / name).read_text(encoding="utf-8"))


def test_headline_real_run_over_the_pinned_atlas_corpus(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)
    edge_repository = PostgresEdgeRepository(postgres_engine)

    result = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_load_json("question-model.proposal.json"),
        concept_charter=_load_json("concept-charter.proposal.json"),
        method_protocol=_load_json("method-protocol.proposal.json"),
        corpus_entries=_load_json("corpus-entries.json"),
        protocol_parameters=_load_json("protocol-parameters.sidecar.json"),
        code_revision=_TEST_CODE_REVISION,
    )

    # The run completed — a "not enough evidence" finding would STILL be
    # "completed" (MRR-MTH-011); only a genuine execution failure is not.
    assert result.run_state == "completed"
    assert result.is_deterministic is True

    # A frozen EvidenceMatrix with every included row (all 18: the
    # inclusion_filter excludes nothing from this corpus).
    assert result.evidence_matrix_id is not None
    matrix = object_repository.get_latest(result.evidence_matrix_id)
    assert matrix.body["status"] == "frozen"
    assert len(matrix.body["rows"]) == 18

    # [MRR-MTH-015 / derived_decisions (k) property 2] >= 1 row whose
    # verification_status != "verified" — the naturally-occurring UBERMORGEN
    # "toVerify"/"pending" row, never dropped.
    non_verified_rows = [r for r in matrix.body["rows"] if r["verification_status"] != "verified"]
    assert len(non_verified_rows) >= 1
    for row in non_verified_rows:
        assert row["verification_status"] == "pending"

    # [derived_decisions (k) property 1] >= 2 distinct applies_to_analysis
    # outcomes — a landscape, not one monolithic claim. Read generically from
    # whichever real objects this run actually produced (rulings and/or
    # decisions), never assuming which one is which.
    analysis_names: set[str] = set()
    for ruling_id in result.method_ruling_ids:
        ruling = object_repository.get_latest(ruling_id)
        analysis_names.add(ruling.body["applies_to_analysis"])
    for decision_id in result.research_decision_ids:
        decision = object_repository.get_latest(decision_id)
        analysis_names.add(decision.body["applies_to_analysis"])
    assert len(analysis_names) >= 2

    # [derived_decisions (k) property 4 / the plan's own mandatory acceptance
    # criterion] the run's own SET of outcomes is NOT uniformly
    # supported/positive-only — checked as a property over whichever
    # outcomes the real classification actually produced, never as
    # "claim X has status Y". Under the binding reviewer_resolution
    # (synthesis_orchestration's own module docstring), a "supported"-track
    # finding mints a Claim left at status "draft" (never promoted further by
    # this run); only a "contested"/"unsupported" finding is driven to that
    # same-named Claim.status. A uniformly-supported landscape would
    # therefore show up here as every claim staying "draft" AND no
    # ResearchDecision at all.
    claim_statuses = {
        object_repository.get_latest(claim_id).body["status"] for claim_id in result.claim_ids
    }
    decision_types = {
        object_repository.get_latest(decision_id).body["decision_type"]
        for decision_id in result.research_decision_ids
    }
    uniformly_supported_track_only = claim_statuses <= {"draft"} and not decision_types
    assert not uniformly_supported_track_only, (
        "STOP CONDITION: the real, honest classification of the pinned atlas corpus produced a "
        "uniformly supported landscape (claim_statuses="
        f"{claim_statuses!r}, decision_types={decision_types!r}) — per the plan's own words, "
        "'a run that can only produce supported claims fails the packet'; revise the "
        "ConceptCharter's own operationalization or inclusion criteria rather than silently "
        "loosening what 'supported' means"
    )
    # At least one claim MUST be capable of ending contested/unsupported, or
    # the run must produce a stop_insufficient_evidence decision (spec 08's
    # own acceptance criterion, MRR-MTH-011).
    assert claim_statuses & {"contested", "unsupported"} or "stop_insufficient_evidence" in (
        decision_types
    )

    # [MRR-MTH-017 / derived_decisions (k) property 3] every persisted Claim
    # carries a ruled_by edge to a MethodRuling whose scope_of_validity /
    # non_applicability_conditions are non-empty.
    assert len(result.claim_ids) == len(result.method_ruling_ids)
    for claim_id in result.claim_ids:
        ruled_by_edges = edge_repository.edges_from(claim_id, "ruled_by")
        assert len(ruled_by_edges) == 1
        ruling = object_repository.get_latest(ruled_by_edges[0].target_id)
        assert ruling.body["status"] == "issued"
        assert ruling.body["non_applicability_conditions"]
        assert ruling.body["scope_of_validity"]

    # [MRR-MTH-011] every claim/decision this run produces carries a ruling
    # or a decision, never silently omitted.
    assert len(result.claim_ids) + len(result.research_decision_ids) == len(analysis_names)

    # [K1's own stated exit criterion] the sealed EvidenceCrate is
    # independently traceable via governed_by_protocol/ruled_by/
    # operationalizes edges back through the MethodProtocol to the
    # QuestionModel/ConceptCharter.
    crate_edges = edge_repository.edges_from(result.evidence_crate_id, "governed_by_protocol")
    assert [e.target_id for e in crate_edges] == [result.method_protocol_id]
    operationalizes_edges = edge_repository.edges_from(result.concept_charter_id, "operationalizes")
    assert [e.target_id for e in operationalizes_edges] == [result.question_model_id]

    protocol = object_repository.get_latest(result.method_protocol_id)
    assert protocol.body["status"] == "locked"
    assert protocol.body["profile_id"] == result.method_profile_id
    question_model = object_repository.get_latest(result.question_model_id)
    assert question_model.body["status"] == "accepted"
    concept_charter = object_repository.get_latest(result.concept_charter_id)
    assert concept_charter.body["status"] == "accepted"

    crate = object_repository.get_latest(result.evidence_crate_id)
    assert crate.body["sealed"] is True
    assert crate.body["run_state"] == "completed"


def test_cli_reproduces_the_real_run(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``mrr synthesis run --database-url ... --artifact-root ...`` (no
    further flags — the five new fixture flags default to the committed
    ``corpora/model-collapse/*.json`` paths) exits 0 and prints an
    ``evidence_crate_id`` resolvable via the same test database.
    """
    exit_code = mrr_main(
        [
            "synthesis",
            "run",
            "--database-url",
            postgres_url,
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--code-revision",
            _TEST_CODE_REVISION,
            "--json",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_state"] == "completed"
    assert out["evidence_crate_id"]

    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        crate = object_repository.get_latest(out["evidence_crate_id"])
        assert crate.body["sealed"] is True
    finally:
        engine.dispose()


def test_cli_deny_score_approval_flag_gates_the_run_via_mrr_fr_004(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MRR-FR-004: ``--deny-score-approval`` threads through
    ``synthesis_main.run_command``'s own ``approve_score = not
    args.deny_score_approval`` plumbing, into
    ``establish_and_run_synthesis``'s ``approve_score`` parameter, into
    ``run_synthesis_evidence_loop``'s identically-named parameter, and
    finally into ``ResearchScoreService`` — never approving/activating the
    Research Score. The CLI aborts with a non-zero exit and an explicit
    message naming the typed ``ScoreNotApprovedError``, never a fabricated
    success (mirrors ``mrr run``'s own identical flag/parameter chain,
    exercised at the function level, not the CLI level, by
    ``tests/e2e/test_e2e_001_single_node_evidence_loop.py``'s own
    ``test_unapproved_score_aborts_at_the_gate``).
    """
    exit_code = mrr_main(
        [
            "synthesis",
            "run",
            "--database-url",
            postgres_url,
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--code-revision",
            _TEST_CODE_REVISION,
            "--deny-score-approval",
        ]
    )

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "aborted" in err
    assert "ScoreNotApprovedError" in err

    # establish_and_run_synthesis's OWN governance-object establishment
    # (MethodProfile/QuestionModel/ConceptCharter/MethodProtocol) happens
    # unconditionally, BEFORE it ever calls run_synthesis_evidence_loop —
    # --deny-score-approval only affects that LATER function's own
    # ResearchScore/TaskBundle-negotiation gate (TaskBundleService.create's
    # own ensure_can_start_work check, task-packets/E2-T03.yaml), confirmed
    # directly: those four governance objects DO exist here. What the gate
    # actually prevents is the run ever reaching execution — no
    # EvidenceMatrix/Claim/ResearchDecision is ever persisted, and no crate
    # is ever sealed.
    engine = sa.create_engine(postgres_url)
    try:
        event_log = PostgresEventLog(engine)
        event_types = {appended.event.event_type for appended in event_log.read_all()}
        assert "question_model.accepted" in event_types
        assert "concept_charter.accepted" in event_types
        assert "method_protocol.locked" in event_types
        assert "evidence_matrix.created" not in event_types
        assert not any(event_type.startswith("claim.") for event_type in event_types)
    finally:
        engine.dispose()
