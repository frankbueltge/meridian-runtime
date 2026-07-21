"""K1-T04 (first real run — the model-collapse question over the two real
atlases) — task-packets/K1-T04.yaml. Drives
``mrr.services.cli.synthesis_setup.establish_and_run_synthesis`` end to end
against a real PostgreSQL (this directory's own ``postgres_engine`` fixture)
and a real, tmp-path-backed ``LocalFilesystemArtifactStore``, using the REAL,
committed atlas-derived fixtures at ``corpora/model-collapse/`` — the
runtime's FIRST REAL research output.

--- AMENDMENT (task-packets/K1-T03b.yaml, reviewer_resolution, commit 72d2d37) ---

``corpora/model-collapse/method-protocol.proposal.json`` declares
``sensitivity_variations: ["model-collapse-mechanism-v1"]`` — a real,
non-empty MRR-MTH-018 declaration that has existed since K1-T01, but whose
EXECUTION was never wired into the runtime until K1-T03b. No
``SensitivityVariationParameters`` sidecar for that declared entry has ever
been authored (K1-T03b's own derivation (4): the declared entry_id
operationalizes the question's own core term, not a second, alternate
classification a meaningful comparison could run against — authoring a
genuine alternate operationalization is a separate, named future task,
K1-T03b specification_gaps, not designed or stubbed here). Once K1-T03b
actually ENFORCES MRR-MTH-018 (a declared-but-uncovered sensitivity
variation now fails closed, symmetrically, rather than being silently
skipped), the two tests below that re-run this real corpus FRESH on every
invocation necessarily start reporting ``run_state == "failed"`` instead of
``"completed"`` — an HONEST exposure of a real gap the corpus's own
declaration always had, not a regression in this test suite. This does NOT
retroactively unmeet K1's own exit criteria (assessed against the code
version the ORIGINAL run actually used) and does NOT touch the sealed
archive schema ``mrr_k1t04_real_run_v2`` in any way — these two tests
always run against their own fresh, throwaway PostgreSQL schema, never that
one. The e2e happy path over a genuinely completing, multi-variation run is
covered by K1-T03b's own new fixture test
(``tests/e2e/test_k1_t03b_sensitivity_variation_execution.py``) and by the
existing, unmodified K1-T03 loop test
(``tests/e2e/test_k1_t03_synthesis_evidence_loop.py``).

Acceptance-test mapping (task-packets/K1-T04.yaml):

- "[headline real run, e2e tier]" ->
  ``test_headline_real_run_fails_closed_on_the_unexecuted_sensitivity_declaration``
  (renamed from ``test_headline_real_run_over_the_pinned_atlas_corpus`` by
  the K1-T03b amendment above).
- "[CLI reproduction]" ->
  ``test_cli_fails_closed_on_the_unexecuted_sensitivity_declaration``
  (renamed from ``test_cli_reproduces_the_real_run`` by the K1-T03b
  amendment above).
- "[regression]" -> covered by the SAME ``make test-e2e`` run also
  collecting ``test_e2e_001_single_node_evidence_loop.py``,
  ``test_k0_t02_capability_dispatch.py``, and
  ``test_k1_t03_synthesis_evidence_loop.py`` unmodified — not duplicated
  here.
- "MRR-FR-004, --deny-score-approval's own CLI plumbing" ->
  ``test_cli_deny_score_approval_flag_gates_the_run_via_mrr_fr_004``
  (UNCHANGED by the K1-T03b amendment — this flag aborts before the run
  ever reaches Task Bundle negotiation/execution, so it never reaches the
  new MRR-MTH-018 coverage check either; review follow-up: ``mrr run``'s
  own identical flag has never had a CLI-level test either —
  ``run_local_evidence_loop`` is only exercised directly, per
  ``tests/e2e/test_e2e_001_single_node_evidence_loop.py``'s own
  ``test_unapproved_score_aborts_at_the_gate`` — so this is K1-T04's own
  new coverage, not a regression fix).
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


def test_headline_real_run_fails_closed_on_the_unexecuted_sensitivity_declaration(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """The pinned atlas corpus's own locked MethodProtocol
    (``corpora/model-collapse/method-protocol.proposal.json``) declares
    ``sensitivity_variations: ["model-collapse-mechanism-v1"]`` — but no
    ``SensitivityVariationParameters`` sidecar for that declared entry has
    ever been authored. MRR-MTH-018 enforcement (task-packets/
    K1-T03b.yaml) now refuses to silently skip a declared-but-uncovered
    sensitivity analysis, so this run fails closed with
    ``SensitivityVariationDeclarationMismatchError``. Authoring a genuine
    alternate operationalization for the model-collapse question, so this
    capability could eventually run a meaningful comparison on the real
    corpus, remains a separate, explicitly named future task
    (task-packets/K1-T03b.yaml specification_gaps) — not designed or
    stubbed here. This test always runs the corpus fixture files fresh,
    against its own throwaway PostgreSQL schema; the sealed archive schema
    ``mrr_k1t04_real_run_v2`` (the ORIGINAL run, already reviewed and
    accepted under K1's own exit criteria, assessed against the code
    version it actually ran under) is entirely unaffected.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()
    object_repository = PostgresObjectRepository(postgres_engine)

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

    assert result.run_state == "failed"
    assert result.is_deterministic is True

    # No research object is ever minted from a failed run (MRR-MTH-013:
    # executor output never becomes authoritative state on its own).
    assert result.evidence_matrix_id is None
    assert result.claim_ids == ()
    assert result.method_ruling_ids == ()
    assert result.research_decision_ids == ()

    # The governance objects establish_and_run_synthesis creates BEFORE
    # ever calling run_synthesis_evidence_loop still exist and are still
    # real — only the SYNTHESIS EXECUTION itself fails closed, confirming
    # the declaration this whole story is about.
    protocol = object_repository.get_latest(result.method_protocol_id)
    assert protocol.body["status"] == "locked"
    assert protocol.body["sensitivity_variations"] == ["model-collapse-mechanism-v1"]

    # The sealed EvidenceCrate still exists (RunManifest/EvidenceCrate
    # sealing is unconditional, MTH-020) and honestly names the exact typed
    # error and the exact missing entry id — never a silent skip.
    crate = object_repository.get_latest(result.evidence_crate_id)
    assert crate.body["sealed"] is True
    assert crate.body["run_state"] == "failed"
    failure_messages = " ".join(entry["message"] for entry in crate.body["failures"])
    assert "SensitivityVariationDeclarationMismatchError" in failure_messages
    assert "model-collapse-mechanism-v1" in failure_messages


def test_cli_fails_closed_on_the_unexecuted_sensitivity_declaration(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``mrr synthesis run --database-url ... --artifact-root ...`` (no
    further flags — the five new fixture flags default to the committed
    ``corpora/model-collapse/*.json`` paths) exits 0 (a reported outcome,
    not a CLI usage error — ``SystematicEvidenceSynthesisExecutor`` never
    raises for a task-level outcome) and prints ``"run_state": "failed"``:
    the real corpus's own locked protocol declares
    ``sensitivity_variations: ["model-collapse-mechanism-v1"]``, and no
    ``SensitivityVariationParameters`` sidecar for that entry has ever been
    authored. MRR-MTH-018 enforcement (task-packets/K1-T03b.yaml) now
    refuses to silently skip a declared-but-uncovered sensitivity analysis
    — an honest exposure of a real, previously-latent gap in the real
    run's own declaration, not a CLI regression. Authoring a genuine
    alternate operationalization for the model-collapse question so this
    capability could eventually run a meaningful comparison on the real
    corpus remains a separate, explicitly named future task
    (task-packets/K1-T03b.yaml specification_gaps) — not designed or
    stubbed here. This test always runs against its own fresh, throwaway
    PostgreSQL schema; the sealed archive schema ``mrr_k1t04_real_run_v2``
    (the ORIGINAL run, already reviewed and accepted under K1's own exit
    criteria) is entirely unaffected.
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
    assert out["run_state"] == "failed"
    assert out["evidence_crate_id"]

    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        crate = object_repository.get_latest(out["evidence_crate_id"])
        assert crate.body["sealed"] is True
        assert crate.body["run_state"] == "failed"
        failure_messages = " ".join(entry["message"] for entry in crate.body["failures"])
        assert "SensitivityVariationDeclarationMismatchError" in failure_messages
        assert "model-collapse-mechanism-v1" in failure_messages
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
