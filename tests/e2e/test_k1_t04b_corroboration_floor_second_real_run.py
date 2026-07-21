"""K1-T04b (second real run — the model-collapse question under a
corroboration-floor sensitivity variation) — task-packets/K1-T04b.yaml.

--- EXECUTION MECHANISM: THE CLI/COMPOSITION PATH, PER THE PACKET'S OWN
    REVIEWER_RESOLUTION (the REDIRECTED ruling, binding, supersedes
    derived_decisions (f)/(g)) ---

The packet's own original ``derived_decisions`` (f)/(g) proposed a new,
local, standalone composition helper re-implementing
``mrr.services.cli.synthesis_setup.establish_and_run_synthesis``'s own
six-step choreography inside this test file, because that function had no
``sensitivity_variation_parameters`` passthrough. The packet's own
``reviewer_resolution`` REJECTED that workaround: it would make this second
run a second-class citizen relative to run 1 (not CLI-reproducible, no
durable named schema, results surviving only in test assertions) and would
reintroduce exactly the local-copy pattern the same day's E9-T00b
consolidation retired. The ruling instead mandated a new micro code packet,
K1-T04c (merged — ``services/control_plane/mrr/services/cli/
synthesis_setup.py``'s ``establish_and_run_synthesis`` and ``services/
control_plane/mrr/services/cli/synthesis_main.py``'s ``mrr synthesis run``
both now carry an additive, default-``None``
``sensitivity_variation_parameters``/``--sensitivity-variation-parameters-
file`` passthrough), so that THIS packet's own test drives the run through
the SAME CLI/composition path
``tests/e2e/test_k1_t04_first_real_run.py`` already established for run 1 —
one test calling ``establish_and_run_synthesis`` directly, one test calling
``mrr synthesis run`` via ``mrr.services.cli.main.main`` — now with the new
variation-parameters keyword/flag supplied. No local re-implementation of
``establish_and_run_synthesis``'s own choreography exists anywhere in this
file.

This file never touches, reads from, or writes to the sealed
``mrr_k1t04_real_run_v2`` schema (forbidden_changes) — every test here gets
its own fresh, throwaway PostgreSQL schema via this directory's own
``postgres_engine``/local ``postgres_url`` fixtures, exactly like run 1's
own test file.

--- HONEST REPORTING (MRR-MTH-011) ---

The real, actual classification outcome under the raised
``corroboration-floor-v1`` floor — for BOTH ``applies_to_analysis`` groups —
is never hardcoded as a required pass/fail condition here. The only
outcome-shaped assertions below are STRUCTURAL: that a
``sensitivity_analysis_results`` entry exists per base analysis group, that
its fields are well-formed, and that ``matches_base_outcome`` is a bool
consistent with the two outcome strings it compares. The actual observed
outcome/family-count values are printed (captured by ``pytest -s`` /
``capsys``) so a reviewer can read the real research result directly out of
a test run, and are additionally recorded verbatim in this PR's own body
(the packet's own ADDITIONAL REQUIREMENT from reviewer_resolution: "the
research result must live in the Git trail").

Acceptance-test mapping (task-packets/K1-T04b.yaml):

- "[proposal fixture validity, DB-free]" -> exercised implicitly by every
  test below successfully parsing/validating the four new
  ``run2-corroboration-floor/`` fixtures through the real contracts
  (``establish_and_run_synthesis``'s own internal ``_build_*`` calls raise
  on a schema violation).
- "[run1 untouched]" -> a manual, operational sha256 recomputation over the
  eight files directly under ``corpora/model-collapse/`` (not committed
  test code — see this PR's own body for the recorded values), since this
  file's own ``allowed_paths`` entry only covers itself and the new
  fixture directory, not a new test-infra file for hashing run 1's own
  fixtures.
- "[second real run completes]", "[honest reporting, outcome not
  hardcoded]" ->
  ``test_second_real_run_completes_under_the_raised_corroboration_floor``.
- "[determinism]" -> the SAME test's own final section (a second, direct
  ``run_synthesis_evidence_loop`` call reusing the already-locked ids).
- "[claim/ruling isolation]" ->
  ``test_claim_and_ruling_ids_are_unaffected_by_the_sensitivity_variation``.
- "[operationalizes-term convention]" ->
  ``test_operationalizes_term_convention_run2_charter_terms_match_question_model``
  (DB-free, a plain assertion, not a database constraint).
- "[regression]" -> covered by the SAME ``make test-e2e`` run also
  collecting ``test_k1_t04_first_real_run.py``,
  ``test_k1_t03b_sensitivity_variation_execution.py``, and
  ``test_k1_t03_synthesis_evidence_loop.py`` unmodified — not duplicated
  here.
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
from mrr.persistence.repositories import PostgresObjectRepository
from mrr.services.cli.main import main as mrr_main
from mrr.services.cli.synthesis_orchestration import run_synthesis_evidence_loop
from mrr.services.cli.synthesis_setup import establish_and_run_synthesis
from sqlalchemy import Engine

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_CORPUS_DIR = _REPO_ROOT / "corpora" / "model-collapse"
_RUN2_DIR = _BASE_CORPUS_DIR / "run2-corroboration-floor"
_VARIATION_ENTRY_ID = "corroboration-floor-v1"
_TEST_CODE_REVISION = "git:k1-t04b-second-real-run-test"

_TEST_DATABASE_URL_ENV_VAR = "MRR_TEST_DATABASE_URL"
_ATTRIBUTES_URL_KEY = "sqlalchemy_url"
ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = _REPO_ROOT / "migrations"

_OUTCOMES = {"supported", "contested", "unsupported", "insufficient_evidence"}


def _require_test_database_url_or_skip() -> str:
    """A local copy of tests/e2e/conftest.py's identical helper — see that
    module's own docstring (and tests/e2e/test_k1_t04_first_real_run.py's
    own identical duplication) for why this is duplicated rather than
    imported.
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
    """Like tests/e2e/test_k1_t04_first_real_run.py's own identical fixture
    — yields a fresh, throwaway, schema-scoped URL string (never
    ``mrr_k1t04_real_run_v2``) for the CLI-reproduction test below.
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


def _load_base_json(name: str) -> Any:
    """Run 1's own committed fixtures, reused by direct path reference —
    never copied, never re-extracted (derived_decisions (b)).
    """
    return json.loads((_BASE_CORPUS_DIR / name).read_text(encoding="utf-8"))


def _load_run2_json(name: str) -> Any:
    return json.loads((_RUN2_DIR / name).read_text(encoding="utf-8"))


def _sensitivity_variation_parameters() -> dict[str, dict[str, Any]]:
    """The committed sidecar's own top-level shape is ALREADY the
    ``Mapping[str, dict[str, Any]]`` ``establish_and_run_synthesis``'s
    ``sensitivity_variation_parameters`` keyword (and the CLI's own
    ``--sensitivity-variation-parameters-file``) expects directly — one
    JSON object keyed by ``variation_entry_id``, so the SAME file also
    works unwrapped as the CLI flag's argument with zero transformation
    (mirrors the other five ``--*-file`` flags' own "point the flag at the
    file" simplicity).
    """
    body: dict[str, dict[str, Any]] = _load_run2_json(
        "sensitivity-variation-parameters.corroboration-floor-v1.sidecar.json"
    )
    return body


def test_operationalizes_term_convention_run2_charter_terms_match_question_model() -> None:
    """DB-free, plain-assertion check (invariants: "checked by a plain
    assertion in the new test, not a database constraint") — every
    ``ConceptCharterEntry.term`` in run 2's own new
    ``concept-charter.proposal.json`` equals one of run 2's own new
    ``question-model.proposal.json``'s ``load_bearing_terms`` strings.
    """
    question_model = _load_run2_json("question-model.proposal.json")
    concept_charter = _load_run2_json("concept-charter.proposal.json")
    load_bearing_terms = set(question_model["load_bearing_terms"])

    assert load_bearing_terms == {
        "model-collapse mechanism",
        "instantiate",
        "reference",
        "independent corroboration",
    }
    entry_ids = set()
    for entry in concept_charter["entries"]:
        assert entry["term"] in load_bearing_terms
        entry_ids.add(entry["entry_id"])
    assert entry_ids == {
        "model-collapse-mechanism-v1",
        "instantiate-vs-reference-v1",
        "corroboration-floor-v1",
    }


def test_method_protocol_delta_is_exactly_one_field() -> None:
    """DB-free — run 2's own ``method-protocol.proposal.json`` differs from
    run 1's own committed file in exactly one field,
    ``sensitivity_variations`` (derived_decisions (e)).
    """
    base = _load_base_json("method-protocol.proposal.json")
    run2 = _load_run2_json("method-protocol.proposal.json")

    assert run2["sensitivity_variations"] == [_VARIATION_ENTRY_ID]
    assert base["sensitivity_variations"] == ["model-collapse-mechanism-v1"]

    base_without_variations = {k: v for k, v in base.items() if k != "sensitivity_variations"}
    run2_without_variations = {k: v for k, v in run2.items() if k != "sensitivity_variations"}
    assert base_without_variations == run2_without_variations


def test_sidecar_delta_is_exactly_the_contested_floor(tmp_path: Path) -> None:
    """DB-free — run 2's own sensitivity-variation-parameters sidecar
    differs from the base ``protocol-parameters.sidecar.json`` only in
    ``eligibility_rules.contested.min_independent_source_families``
    (1 -> 2), holding ``supported`` at 2 and every other field
    byte-identical (derived_decisions (d)).
    """
    base = _load_base_json("protocol-parameters.sidecar.json")
    variation = _load_run2_json(
        "sensitivity-variation-parameters.corroboration-floor-v1.sidecar.json"
    )[_VARIATION_ENTRY_ID]

    assert base["eligibility_rules"]["contested"]["min_independent_source_families"] == 1
    assert base["eligibility_rules"]["supported"]["min_independent_source_families"] == 2
    assert variation["eligibility_rules"]["contested"]["min_independent_source_families"] == 2
    assert variation["eligibility_rules"]["supported"]["min_independent_source_families"] == 2
    assert variation["inclusion_filter"] == base["inclusion_filter"]
    assert variation["kill_conditions"] == base["kill_conditions"]
    assert variation["source_family_overrides"] == {}
    assert variation["variation_entry_id"] == _VARIATION_ENTRY_ID


def test_second_real_run_completes_under_the_raised_corroboration_floor(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """The headline acceptance test: ``establish_and_run_synthesis`` (K1-T04,
    UNCHANGED except for K1-T04c's own additive passthrough) establishes a
    fresh ``MethodProfile``/``QuestionModel``/``ConceptCharter`` (with a real
    ``operationalizes`` edge)/locked ``MethodProtocol`` in its own fresh
    schema, reusing run 1's own ``corpus_entries``/``protocol_parameters``
    unchanged by direct path reference, plus this run's own new
    ``sensitivity_variation_parameters={"corroboration-floor-v1": ...}``.
    Unlike run 1's own two now-fail-closed tests (whose declaration,
    ``model-collapse-mechanism-v1``, has no authored sidecar), this run
    DOES supply a sidecar for its own sole declared variation, so it
    completes.
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
        question_model=_load_run2_json("question-model.proposal.json"),
        concept_charter=_load_run2_json("concept-charter.proposal.json"),
        method_protocol=_load_run2_json("method-protocol.proposal.json"),
        corpus_entries=_load_base_json("corpus-entries.json"),
        protocol_parameters=_load_base_json("protocol-parameters.sidecar.json"),
        sensitivity_variation_parameters=_sensitivity_variation_parameters(),
        code_revision=_TEST_CODE_REVISION,
    )

    assert result.run_state == "completed"
    assert result.is_deterministic is True
    assert result.evidence_matrix_id is not None
    assert result.output_hash is not None

    protocol = object_repository.get_latest(result.method_protocol_id)
    assert protocol.body["status"] == "locked"
    assert protocol.body["sensitivity_variations"] == [_VARIATION_ENTRY_ID]

    matrix = object_repository.get_latest(result.evidence_matrix_id)
    # `EvidenceMatrix.body` itself carries only per-SOURCE `rows`, never the
    # per-analysis-group base classification (with its own family counts) —
    # that only ever existed in the executor's own in-memory `output` dict,
    # which `run_synthesis_evidence_loop` persists verbatim, content-
    # addressed, to the SAME artifact store under `result.output_hash`
    # (services/control_plane/mrr/services/cli/synthesis_orchestration.py,
    # `artifact_store.put(execution_result.output, ...)`) — retrieved here
    # exactly the way any other caller would resolve it, never a database
    # bypass.
    raw_output = json.loads(store.get(result.output_hash).decode("utf-8"))
    base_analyses = {a["applies_to_analysis"]: a for a in raw_output["analyses"]}
    sensitivity_results = matrix.body["sensitivity_analysis_results"]
    assert sensitivity_results is not None
    # Same content as the persisted EvidenceMatrix's own field — modulo the
    # contract's universal `exclude_none=True` round-trip convention, which
    # drops each entry's own `decision_rationale: None` key on persistence.
    assert {e["applies_to_analysis"] for e in sensitivity_results} == {
        e["applies_to_analysis"] for e in raw_output["sensitivity_analysis_results"]
    }

    # --- STRUCTURAL invariants only (task-packets/K1-T04b.yaml
    # acceptance_tests "[honest reporting, outcome not hardcoded]"): exactly
    # one sensitivity_analysis_results entry per base applies_to_analysis
    # group, all naming this run's own sole declared variation, every field
    # well-formed. No outcome string is asserted as a REQUIRED value.
    assert len(base_analyses) == 2
    assert len(sensitivity_results) == len(base_analyses)
    seen_analyses: set[str] = set()
    for entry in sensitivity_results:
        assert entry["variation_entry_id"] == _VARIATION_ENTRY_ID
        applies_to = entry["applies_to_analysis"]
        assert applies_to in base_analyses
        seen_analyses.add(applies_to)
        assert entry["outcome"] in _OUTCOMES
        assert isinstance(entry["matches_base_outcome"], bool)
        assert entry["matches_base_outcome"] == (
            entry["outcome"] == base_analyses[applies_to]["outcome"]
        )
        if entry["outcome"] == "insufficient_evidence":
            assert entry.get("decision_rationale") is not None
        else:
            # `exclude_none=True` (this contract's universal round-trip
            # convention) drops a null `decision_rationale` key entirely on
            # persistence rather than keeping it present with value `None`.
            assert entry.get("decision_rationale") is None
    assert seen_analyses == set(base_analyses)

    # --- HONEST REPORTING (MRR-MTH-011): print the REAL, actual result
    # table — whichever way it goes, never smoothed over. Captured by
    # `pytest -s`/capsys; also transcribed verbatim into this PR's own body
    # per reviewer_resolution's additional requirement.
    print(f"\n=== K1-T04b actual sensitivity result table (variation={_VARIATION_ENTRY_ID}) ===")
    for applies_to in sorted(base_analyses):
        base = base_analyses[applies_to]
        variation = next(
            entry for entry in sensitivity_results if entry["applies_to_analysis"] == applies_to
        )
        base_supporting = base["distinct_independent_supporting_family_count"]
        base_contradicting = base["distinct_independent_contradicting_family_count"]
        variation_supporting = variation["distinct_independent_supporting_family_count"]
        variation_contradicting = variation["distinct_independent_contradicting_family_count"]
        print(
            f"{applies_to}: base={base['outcome']} "
            f"(supporting_families={base_supporting}, "
            f"contradicting_families={base_contradicting}) "
            f"-> variation={variation['outcome']} "
            f"(supporting_families={variation_supporting}, "
            f"contradicting_families={variation_contradicting}) "
            f"matches_base_outcome={variation['matches_base_outcome']}"
        )

    print("=== claim landscape ===")
    for claim_id in result.claim_ids:
        claim = object_repository.get_latest(claim_id)
        print(f"Claim {claim_id}: status={claim.body['status']}")
    for ruling_id in result.method_ruling_ids:
        ruling = object_repository.get_latest(ruling_id)
        print(f"MethodRuling {ruling_id}: status={ruling.body['status']}")
    for decision_id in result.research_decision_ids:
        decision = object_repository.get_latest(decision_id)
        print(f"ResearchDecision {decision_id}: status={decision.body['status']}")

    # --- DETERMINISM (acceptance_tests "[determinism]"): reusing the SAME
    # already-locked method_protocol_id/question_model_id and
    # byte-identical corpus_entries/protocol_parameters/
    # sensitivity_variation_parameters content, a second, direct call to
    # run_synthesis_evidence_loop (K1-T03/K1-T03b, imported UNCHANGED — the
    # SAME function establish_and_run_synthesis's own step 6 already calls
    # internally) produces a byte-identical output_hash.
    #
    # establish_and_run_synthesis itself has NO parameter to reuse an
    # already-persisted question_model_id/method_protocol_id (only
    # method_profile_id) — every call mints a FRESH QuestionModel/
    # ConceptCharter/MethodProtocol with a fresh random urn
    # (mrr.domain.identity.new_urn), and that urn is embedded verbatim in
    # the executor's own hashed output ("protocol_id"/"question_id" —
    # services/node_runtime/mrr/services/node_runtime/synthesis_executor.py
    # `execute`'s own `output` dict). So a SECOND establish_and_run_synthesis
    # (or `mrr synthesis run` CLI) invocation could never be byte-identical
    # to the first run's own hash, by construction — not a gap in this
    # test's own design, just what "byte-identical output_hash" can
    # literally mean once ids are involved. Calling
    # run_synthesis_evidence_loop directly a second time, reusing the ids
    # this SAME establish_and_run_synthesis call already produced, is
    # exactly the acceptance test's own literal wording ("two calls to
    # run_synthesis_evidence_loop, reusing the SAME already-locked
    # method_protocol_id/question_model_id") and mirrors this codebase's own
    # established convention for proving this kind of determinism
    # (tests/e2e/test_e2e_001_single_node_evidence_loop.py's own
    # `test_deterministic_replay_same_inputs_yield_same_output_hash`: "two
    # independent loop runs declaring the SAME input artifact... produce the
    # same executor output hash, even though every OTHER id/timestamp each
    # run mints... is fresh").
    protocol_parameters_repeat = dict(_load_base_json("protocol-parameters.sidecar.json"))
    protocol_parameters_repeat["protocol_id"] = result.method_protocol_id
    protocol_parameters_repeat["protocol_lock_content_hash"] = protocol.content_hash

    variation_repeat = dict(
        _load_run2_json("sensitivity-variation-parameters.corroboration-floor-v1.sidecar.json")[
            _VARIATION_ENTRY_ID
        ]
    )
    variation_repeat["protocol_id"] = result.method_protocol_id
    variation_repeat["protocol_lock_content_hash"] = protocol.content_hash

    repeat_result = run_synthesis_evidence_loop(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model_id=result.question_model_id,
        method_protocol_id=result.method_protocol_id,
        corpus_entries=_load_base_json("corpus-entries.json"),
        protocol_parameters=protocol_parameters_repeat,
        sensitivity_variation_parameters={_VARIATION_ENTRY_ID: variation_repeat},
        code_revision=_TEST_CODE_REVISION,
    )

    assert repeat_result.run_state == "completed"
    assert repeat_result.output_hash == result.output_hash
    # Two independent loop invocations, not one memoized call — distinct
    # crates/manifests/tasks/matrices, mirroring
    # test_e2e_001_single_node_evidence_loop.py's own identical convention.
    assert repeat_result.evidence_crate_id != result.evidence_crate_id
    assert repeat_result.run_manifest_id != result.run_manifest_id
    assert repeat_result.task_id != result.task_id
    print(f"=== determinism: output_hash={result.output_hash!r} (matched on repeat) ===")


def test_claim_and_ruling_ids_are_unaffected_by_the_sensitivity_variation(
    postgres_engine: Engine, tmp_path: Path
) -> None:
    """acceptance_tests "[claim/ruling isolation]": the SAME governance
    objects, run once WITH ``sensitivity_variation_parameters`` supplied and
    once with it omitted entirely, mint the IDENTICAL set of
    Claim/MethodRuling/ResearchDecision ids — the variation's own outcome
    influences nothing beyond ``EvidenceMatrix.sensitivity_analysis_results``
    itself (K1-T03b's own inherited invariant, re-verified here against
    real, not synthetic, data). Two SEPARATE ``establish_and_run_synthesis``
    calls necessarily mint two different ``question_model_id``/
    ``method_protocol_id`` pairs (fresh urns, as in the determinism test
    above) — the isolation property under test is about the SHAPE of what
    gets minted (ids present/absent, never influenced by an outcome), not
    about the two runs sharing identical ids.
    """
    store = _artifact_store(tmp_path)
    origin_key = Ed25519PrivateKey.generate()
    node_key = Ed25519PrivateKey.generate()

    with_variation = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_load_run2_json("question-model.proposal.json"),
        concept_charter=_load_run2_json("concept-charter.proposal.json"),
        method_protocol=_load_run2_json("method-protocol.proposal.json"),
        corpus_entries=_load_base_json("corpus-entries.json"),
        protocol_parameters=_load_base_json("protocol-parameters.sidecar.json"),
        sensitivity_variation_parameters=_sensitivity_variation_parameters(),
        code_revision=_TEST_CODE_REVISION,
    )

    method_protocol_without_variation = dict(_load_run2_json("method-protocol.proposal.json"))
    method_protocol_without_variation["sensitivity_variations"] = []
    without_variation = establish_and_run_synthesis(
        engine=postgres_engine,
        artifact_store=store,
        origin_signing_key=origin_key,
        node_signing_key=node_key,
        question_model=_load_run2_json("question-model.proposal.json"),
        concept_charter=_load_run2_json("concept-charter.proposal.json"),
        method_protocol=method_protocol_without_variation,
        corpus_entries=_load_base_json("corpus-entries.json"),
        protocol_parameters=_load_base_json("protocol-parameters.sidecar.json"),
        sensitivity_variation_parameters=None,
        code_revision=_TEST_CODE_REVISION,
    )

    assert with_variation.run_state == "completed"
    assert without_variation.run_state == "completed"
    assert with_variation.evidence_matrix_id is not None
    assert without_variation.evidence_matrix_id is not None
    assert with_variation.output_hash is not None
    assert without_variation.output_hash is not None

    object_repository = PostgresObjectRepository(postgres_engine)
    with_matrix = object_repository.get_latest(with_variation.evidence_matrix_id)
    without_matrix = object_repository.get_latest(without_variation.evidence_matrix_id)

    assert "sensitivity_analysis_results" in with_matrix.body
    assert "sensitivity_analysis_results" not in without_matrix.body

    # The claim/ruling/decision LANDSCAPE SHAPE (statuses, count, and
    # base-classification outcomes) is identical whether or not the
    # variation ran — the variation only ever adds the ONE extra
    # sensitivity_analysis_results key, never a different base outcome. The
    # per-analysis base outcome only ever exists in the executor's own raw
    # output (see the headline test's own identical comment) — resolved via
    # the SAME content-addressed artifact store both runs shared.
    with_raw_output = json.loads(store.get(with_variation.output_hash).decode("utf-8"))
    without_raw_output = json.loads(store.get(without_variation.output_hash).decode("utf-8"))
    with_analyses = {a["applies_to_analysis"]: a["outcome"] for a in with_raw_output["analyses"]}
    without_analyses = {
        a["applies_to_analysis"]: a["outcome"] for a in without_raw_output["analyses"]
    }
    assert with_analyses == without_analyses
    assert len(with_variation.claim_ids) == len(without_variation.claim_ids)
    assert len(with_variation.method_ruling_ids) == len(without_variation.method_ruling_ids)
    assert len(with_variation.research_decision_ids) == len(without_variation.research_decision_ids)


def test_cli_second_real_run_completes_under_the_raised_corroboration_floor(
    postgres_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``mrr synthesis run`` (K1-T04c's own new
    ``--sensitivity-variation-parameters-file`` flag) reproduces this second
    run through the CLI end to end — the exact same command shape the
    archival run (this PR's own body) uses against a durable, named schema,
    just pointed at a throwaway per-invocation schema here.
    """
    result = mrr_main(
        [
            "synthesis",
            "run",
            "--database-url",
            postgres_url,
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--question-model-file",
            str(_RUN2_DIR / "question-model.proposal.json"),
            "--concept-charter-file",
            str(_RUN2_DIR / "concept-charter.proposal.json"),
            "--method-protocol-file",
            str(_RUN2_DIR / "method-protocol.proposal.json"),
            "--corpus-file",
            str(_BASE_CORPUS_DIR / "corpus-entries.json"),
            "--protocol-parameters-file",
            str(_BASE_CORPUS_DIR / "protocol-parameters.sidecar.json"),
            "--sensitivity-variation-parameters-file",
            str(_RUN2_DIR / "sensitivity-variation-parameters.corroboration-floor-v1.sidecar.json"),
            "--code-revision",
            _TEST_CODE_REVISION,
            "--json",
        ]
    )

    assert result == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run_state"] == "completed"
    assert out["evidence_matrix_id"]

    engine = sa.create_engine(postgres_url)
    try:
        object_repository = PostgresObjectRepository(engine)
        matrix = object_repository.get_latest(out["evidence_matrix_id"])
        sensitivity_results = matrix.body["sensitivity_analysis_results"]
        assert sensitivity_results is not None
        assert {entry["variation_entry_id"] for entry in sensitivity_results} == {
            _VARIATION_ENTRY_ID
        }
        # Re-resolve the executor's own raw output (the same content-
        # addressed store `mrr synthesis run` itself just wrote to under
        # --artifact-root) to confirm one sensitivity result per base
        # analysis group — see the direct-call headline test's own
        # identical comment for why `analyses` lives there, not on the
        # persisted EvidenceMatrix.
        cli_store = LocalFilesystemArtifactStore(tmp_path / "artifacts")
        raw_output = json.loads(cli_store.get(out["output_hash"]).decode("utf-8"))
        assert len(sensitivity_results) == len(raw_output["analyses"])
    finally:
        engine.dispose()
