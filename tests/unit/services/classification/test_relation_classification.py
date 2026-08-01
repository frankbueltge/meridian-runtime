"""Unit tests for the model-assisted evidence-relation classifier
(task-packets/N1-T04.yaml AT1-AT6).

Driven entirely by an in-test scripted fake ``ModelAdapter`` — never a real
provider, never a network call, never an API key. Mirrors the identical
discipline in tests/unit/adapters/llm/test_structured_generation.py and
tests/unit/services/node_runtime/test_synthesis_executor_model_assisted.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    TokenUsage,
    apply_redaction,
)
from mrr.domain.relation_proposal import (
    DOWNGRADED_TO_PROPOSAL,
    FORBIDDEN_CASE_KEYS,
    RELATION_CATEGORIES,
    RelationCase,
    RelationProposal,
    RelationProposalSet,
    render_json,
)
from mrr.services.classification.relation_service import (
    PROMPT_TEMPLATE,
    CasesNotBlindError,
    ClassificationInputError,
    RelationClassificationService,
    derive_model_profile_urn,
    prompt_contract_sha256,
)
from mrr.services.cli import classification_main
from mrr.services.validation.gold_service import GoldValidityService

_VALID_HASH = "sha256:" + "e" * 64

REPO_ROOT = Path(__file__).resolve().parents[4]
COMMISSION = REPO_ROOT / "corpora" / "gold-classification" / "commission.v2.json"
CRITERIA = REPO_ROOT / "benchmarks" / "meridianbench" / "fixtures" / "mb-cls-criteria.v3.json"


class _ScriptedFakeAdapter:
    """Deterministic in-memory fake — no network of any kind."""

    def __init__(self, script: list[ModelInvocationOutcome]) -> None:
        self._script = script
        self.calls: list[ModelInvocationRequest] = []

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        self.calls.append(request)
        return self._script[len(self.calls) - 1]


def _completed(payload: dict[str, Any]) -> ModelInvocationOutcome:
    response_hash, raw = apply_redaction("raw_permitted", json.dumps(payload))
    return ModelInvocationOutcome(
        status="completed",
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        redaction_policy="raw_permitted",
        response_hash=response_hash,
        raw_response_text=raw,
    )


def _terminal(status: str) -> ModelInvocationOutcome:
    return ModelInvocationOutcome(
        status=status,  # type: ignore[arg-type]
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=0, total_tokens=10),
        redaction_policy="hashes_only",
    )


def _verdict(relation: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "relation": relation,
        "rationale": "The excerpt reports an external checker fenced to its own benchmark.",
        "decided_by": "qualifies definition",
        "tie_with": None,
    }
    payload.update(overrides)
    return payload


def _criteria() -> Any:
    return RelationClassificationService().load_criteria(CRITERIA)


def _cases_file(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    path = tmp_path / "commission.json"
    path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    return path


def _case_doc(case_id: str, **overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "case_id": case_id,
        "title": f"A source titled {case_id}",
        "claim_text": "Systems that automate the research cycle verify their own outputs.",
        "excerpt": "The framework validates each generated step with an external solver.",
        "excerpt_sha256": "sha256:" + "a" * 64,
    }
    doc.update(overrides)
    return doc


# --- AT1: the disposition is structurally honest -----------------------------


def test_a_proposal_is_downgraded_because_nothing_verified_it_against_a_source(
    tmp_path: Path,
) -> None:
    """AT1. The one disposition this path can earn.

    MRR-MTH-016 defines `verified` as verified AGAINST THE ANCHORED SOURCE.
    This path sends an excerpt to a model and records the answer; no source
    is consulted to check it. So the honest MTH-016 value is
    `downgraded-to-proposal`, and this test is what keeps a later change from
    quietly upgrading the word.
    """
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1")]))
    adapter = _ScriptedFakeAdapter([_completed(_verdict("qualifies"))])

    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
    )

    assert [p.verification_disposition for p in result.proposals] == [DOWNGRADED_TO_PROPOSAL]


def test_no_proposal_in_this_module_can_ever_say_verified_or_rejected(tmp_path: Path) -> None:
    """AT1. The absence, asserted rather than assumed.

    `rejected` is reserved for a caller-side verification step this packet
    does not build, and `verified` is unreachable for the reason above. Both
    stay in the type, because the vocabulary is MTH-016's and narrowing it
    here would be the mirror of the mistake this packet refuses to make:
    changing a closed MUST vocabulary to fit one implementation.
    """
    service = RelationClassificationService()
    docs = [_case_doc(f"c{i}") for i in range(4)]
    cases = service.load_cases(_cases_file(tmp_path, docs))
    adapter = _ScriptedFakeAdapter(
        [_completed(_verdict(relation)) for relation in RELATION_CATEGORIES]
    )

    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
    )

    dispositions = {p.verification_disposition for p in result.proposals}
    assert dispositions == {DOWNGRADED_TO_PROPOSAL}
    assert "verified" not in dispositions
    assert "rejected" not in dispositions


def test_a_proposal_claiming_verified_is_rejected_by_the_type_itself() -> None:
    """AT1. The guard is on the model, not only in the code path that fills it."""
    with pytest.raises(ValueError, match="downgraded-to-proposal"):
        RelationProposal(
            case_id="c1",
            generation_status="proposal",
            proposed_relation="supports",
            rationale="anything",
            decided_by="supports definition",
            verification_disposition="verified",
        )


# --- AT2: label isolation ----------------------------------------------------


def test_the_case_type_has_no_field_that_could_carry_a_label() -> None:
    """AT2, mirroring N1-T02's AT5 and E4-T07's structural guarantee.

    Asserted over the declared fields rather than by inspecting one instance:
    the point is that no instance CAN carry a label, not that this one does
    not.
    """
    declared = set(RelationCase.model_fields)
    assert declared == {"case_id", "title", "claim_text", "excerpt", "excerpt_sha256"}
    assert not (declared & FORBIDDEN_CASE_KEYS)


def test_a_cases_file_carrying_labels_is_refused_and_not_quietly_stripped(
    tmp_path: Path,
) -> None:
    """AT2. Dropping the key would produce a run that reports itself as blind
    without having been — the one failure this exercise cannot survive.
    """
    doc = _case_doc("c1", expected_relation="supports", decided_by="supports definition")
    with pytest.raises(CasesNotBlindError) as excinfo:
        RelationClassificationService().load_cases(_cases_file(tmp_path, [doc]))

    assert "expected_relation" in str(excinfo.value)
    assert "decided_by" in str(excinfo.value)


def test_the_real_gold_set_cannot_be_used_as_a_commission() -> None:
    """AT2. The strongest form of the same check, on the real file.

    The re-stamped gold set carries every label. Handing it to the classifier
    would produce a perfect score and mean nothing at all.
    """
    gold = REPO_ROOT / "corpora" / "gold-classification" / "mb-cls-ulysses-v1-restamped.json"
    with pytest.raises(CasesNotBlindError):
        RelationClassificationService().load_cases(gold)


def test_the_real_commission_loads_and_carries_sixty_blind_cases() -> None:
    """AT2. The positive half: the committed commission is accepted."""
    loaded = RelationClassificationService().load_cases(COMMISSION)
    assert len(loaded.cases) == 60
    assert loaded.commission_sha256.startswith("sha256:")


def test_the_prompt_carries_the_criteria_and_the_excerpt_and_no_label() -> None:
    """AT2. What actually reaches the model."""
    service = RelationClassificationService()
    criteria = _criteria()
    case = RelationCase(
        case_id="c1",
        title="A source",
        claim_text="a claim",
        excerpt="An external solver checks each step.",
        excerpt_sha256="sha256:" + "a" * 64,
    )

    prompt = service.build_prompt(case, criteria)

    assert "An external solver checks each step." in prompt
    assert criteria.claim_text in prompt
    assert "R-excerpt-only" in prompt
    assert "R-undecidable-is-a-finding" in prompt
    for category in RELATION_CATEGORIES:
        assert category in prompt
    assert "expected_relation" not in prompt


# --- AT3: every failure kind survives as itself ------------------------------


def test_each_model_failure_keeps_its_own_name_and_none_is_collapsed(tmp_path: Path) -> None:
    """AT3. Five distinct failure kinds, five distinct statuses.

    Collapsing them is on AGENTS.md's list of prohibited shortcuts, and for a
    measurement run it is the difference between "the model would not answer"
    and "the network broke".
    """
    service = RelationClassificationService()
    docs = [_case_doc(f"c{i}") for i in range(5)]
    cases = service.load_cases(_cases_file(tmp_path, docs))
    adapter = _ScriptedFakeAdapter(
        [
            # schema_invalid: two calls (initial + one repair), both unparseable.
            _completed({"relation": "not-a-category"}),
            _completed({"relation": "still-not-a-category"}),
            _terminal("refused"),
            _terminal("content_filtered"),
            _terminal("error"),
            _terminal("timed_out"),
        ]
    )

    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
        max_repair_attempts=1,
    )

    assert [p.generation_status for p in result.proposals] == [
        "schema_invalid",
        "refused",
        "content_filtered",
        "error",
        "timed_out",
    ]
    assert result.predictions() == {}
    assert len(result.failed_case_ids()) == 5
    assert all(p.verification_disposition is None for p in result.proposals)


def test_the_command_refuses_on_incomplete_coverage_and_writes_nothing(tmp_path: Path) -> None:
    """AT3. One success does not rescue the exit code, and no file appears.

    Refusing where the gap is created names WHICH case broke and why;
    `mrr validate gold` would refuse a step later with only
    MismatchedRatersError to say.
    """
    service = RelationClassificationService()
    cases_path = _cases_file(tmp_path, [_case_doc("good"), _case_doc("bad")])
    output = tmp_path / "proposals.json"
    adapter = _ScriptedFakeAdapter([_completed(_verdict("qualifies")), _terminal("error")])

    args = argparse.Namespace(
        cases=cases_path,
        criteria=CRITERIA,
        output=output,
        system_id="test-system",
        adapter="none",
        model_name="fake-model",
        max_repair_attempts=0,
        pause_seconds=0.0,
    )
    exit_code = classification_main.run_relations_command(args, adapter=adapter)

    assert exit_code == 3
    assert not output.exists()
    assert service is not None  # the service under test built nothing on disk either


# --- AT4: undecidable is not a failure ---------------------------------------


def test_an_undecidable_case_yields_no_prediction_and_does_not_fail_the_run(
    tmp_path: Path,
) -> None:
    """AT4. R-undecidable-is-a-finding, applied to the system as to the standard.

    The gold set holds three of its sixty out of the matrix on this rule. A
    system that cannot do the same has a coverage number that cannot be
    compared with the standard's.
    """
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1"), _case_doc("c2")]))
    adapter = _ScriptedFakeAdapter(
        [
            _completed(_verdict("contradicts")),
            _completed(
                _verdict(
                    "undecidable",
                    rationale="The excerpt says checking happens but never says what checks.",
                    decided_by="R-undecidable-is-a-finding",
                )
            ),
        ]
    )

    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
    )

    assert result.predictions() == {"c1": "contradicts"}
    assert result.undecidable_case_ids() == ("c2",)
    assert result.failed_case_ids() == ()
    # An undecidable case is a decided outcome: it carries a disposition.
    assert result.proposals[1].verification_disposition == DOWNGRADED_TO_PROPOSAL


def test_an_undecidable_case_may_not_also_name_a_relation() -> None:
    """AT4. The criteria keep undecidable cases out of the four labels
    entirely; the type does too.
    """
    with pytest.raises(ValueError, match="undecidable"):
        RelationProposal(
            case_id="c1",
            generation_status="proposal",
            proposed_relation="supports",
            undecidable=True,
            rationale="anything",
            decided_by="R-undecidable-is-a-finding",
            verification_disposition=DOWNGRADED_TO_PROPOSAL,
        )


def test_a_recorded_tie_is_visible_in_the_artefact(tmp_path: Path) -> None:
    """AT4's neighbour: R-record-any-tie.

    The labelling practice's finding 4.3 was that a tie the record cannot see
    is a decision nobody can argue with. The same duty binds a system.
    """
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1")]))
    adapter = _ScriptedFakeAdapter([_completed(_verdict("qualifies", tie_with="supports"))])

    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
    )

    assert result.tie_broken_case_ids() == ("c1",)


# --- AT5: the artefact IS a predictions file ---------------------------------


def test_the_artefact_loads_through_the_measurement_services_own_reader(
    tmp_path: Path,
) -> None:
    """AT5. Asserted by CALLING load_predictions, not by re-describing its format.

    One file, two readers: the measurement reads `predictions`, a person
    reads the rest, and nothing has to be kept in agreement with anything.
    """
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1"), _case_doc("c2")]))
    adapter = _ScriptedFakeAdapter(
        [_completed(_verdict("supports")), _completed(_verdict("contextualizes"))]
    )
    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
    )

    artefact = tmp_path / "proposals.json"
    artefact.write_text(render_json(result), encoding="utf-8")

    system_id, predictions = GoldValidityService().load_predictions(artefact)

    assert system_id == "test-system"
    assert predictions == {"c1": "supports", "c2": "contextualizes"}


# --- AT6: determinism --------------------------------------------------------


def test_two_runs_over_identical_inputs_render_identical_bytes(tmp_path: Path) -> None:
    """AT6. No wall clock anywhere.

    A hand-typed timestamp inside an apparatus that gates on time was
    mistyped within hours on 2026-08-01 and blocked its own order gate. When
    a run happened is a property of its commit, not of a field that can come
    loose from the act it describes.
    """
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1")]))
    criteria = _criteria()

    def _run() -> str:
        adapter = _ScriptedFakeAdapter([_completed(_verdict("qualifies"))])
        return render_json(
            service.classify(
                adapter=adapter,
                cases=cases,
                criteria=criteria,
                model_name="fake-model",
                system_id="test-system",
            )
        )

    first, second = _run(), _run()
    assert first == second
    assert "2026-" not in first


def test_the_model_profile_id_is_derived_from_the_configuration_not_minted() -> None:
    """AT6. Same configuration, same identifier; changed prompt, changed one.

    A minted ULID would make two identical runs incomparable and put a random
    value in a byte-deterministic artefact.
    """
    base = derive_model_profile_urn(
        model_name="fake-model", prompt_template=PROMPT_TEMPLATE, criteria_sha256=_VALID_HASH
    )
    same = derive_model_profile_urn(
        model_name="fake-model", prompt_template=PROMPT_TEMPLATE, criteria_sha256=_VALID_HASH
    )
    other_prompt = derive_model_profile_urn(
        model_name="fake-model", prompt_template="something else", criteria_sha256=_VALID_HASH
    )

    assert base == same
    assert base != other_prompt
    # It has to be an MRR urn, or ModelInvocationRequest refuses it.
    from mrr.domain.identity import is_valid_urn

    assert is_valid_urn(base)


def test_the_request_the_adapter_receives_pins_the_derived_profile(tmp_path: Path) -> None:
    """AT6. The derivation is not decorative — it is what the model call carries."""
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1")]))
    criteria = _criteria()
    adapter = _ScriptedFakeAdapter([_completed(_verdict("qualifies"))])

    result = service.classify(
        adapter=adapter,
        cases=cases,
        criteria=criteria,
        model_name="fake-model",
        system_id="test-system",
    )

    assert len(adapter.calls) == 1
    request = adapter.calls[0]
    assert request.model_profile_id == result.model_profile_id
    assert request.model_profile_hash == result.model_profile_hash
    assert request.operation_kind == "stochastic"
    assert request.redaction_policy == "raw_permitted"


# --- The service's own boundaries --------------------------------------------


def test_the_service_refuses_a_criteria_file_it_cannot_read(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(ClassificationInputError, match="cannot read file"):
        RelationClassificationService().load_criteria(missing)


def test_a_proposal_set_needs_distinct_case_ids() -> None:
    proposal = RelationProposal(
        case_id="c1",
        generation_status="proposal",
        proposed_relation="supports",
        rationale="r",
        decided_by="supports definition",
        verification_disposition=DOWNGRADED_TO_PROPOSAL,
    )
    with pytest.raises(ValueError, match="distinct"):
        RelationProposalSet(
            system_id="s",
            model_profile_id="urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV",
            model_profile_hash=_VALID_HASH,
            model_name="m",
            prompt_template_sha256=_VALID_HASH,
            commission_sha256=_VALID_HASH,
            criteria_sha256=_VALID_HASH,
            criteria_version="v3",
            claim_text="a claim",
            proposals=(proposal, proposal),
        )


# --- The workflow's own invariants -------------------------------------------

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gold-classification.yml"


def test_the_workflow_has_no_schedule_trigger() -> None:
    """N1-T04 invariant, as a test rather than as a comment.

    Two standing rules forbid it — "kein Nightly, das dasselbe neu rechnet"
    and "keine dritte naechtliche Routine" — and both bite here: the sixty
    cases and the criteria are frozen, so a nightly would spend quota to
    produce a scatter of numbers indistinguishable from a finding.

    Asserted on the file rather than through a YAML parser because pyyaml is
    not a declared dependency of this project and this packet adds none.
    """
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    triggers = [line for line in lines if line.startswith("  ") and line.strip().endswith(":")]
    assert not any(line.strip() == "schedule:" for line in triggers)
    assert any(line.strip() == "workflow_dispatch:" for line in triggers)
    assert any(line.strip() == "push:" for line in triggers)


def test_the_workflow_pins_the_gold_hash_when_it_measures() -> None:
    """--expect-sha256 is optional in the CLI and is not optional here.

    Without it the run measures against whatever that path contains rather
    than against a named, held standard — which is the hole K1 found and the
    reason the registry gained an entry.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--expect-sha256" in text
    assert "check_gold_freeze.py" in text


def test_the_workflow_never_pushes_to_main() -> None:
    """A measured number lands on its branch; a human decides if it joins the
    record. field-watch.yml lands on main because an observation is not a
    claim — this is the other kind of artefact.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "HEAD:main" not in text
    assert 'git push origin "HEAD:${GITHUB_REF_NAME}"' in text


# --- The two defects the first online run exposed ----------------------------


def test_the_request_permits_raw_text_because_otherwise_no_proposal_is_possible(
    tmp_path: Path,
) -> None:
    """The defect the first online run found, pinned so it cannot come back.

    `generate_structured` validates `outcome.raw_response_text`, and
    `ModelInvocationOutcome` FORBIDS that field under "hashes_only"
    (MRR-FR-045). So under "hashes_only" there is never text to validate,
    structured_generation.py's else branch records "no response text
    available", and `status == "proposal"` is unreachable. The redaction
    policy does not merely redact the evidence — it decides the outcome.

    The first online run returned schema_invalid for every case that reached
    the provider, which is what that unreachability looks like from outside.
    """
    service = RelationClassificationService()
    cases = service.load_cases(_cases_file(tmp_path, [_case_doc("c1")]))
    adapter = _ScriptedFakeAdapter([_completed(_verdict("qualifies"))])

    service.classify(
        adapter=adapter,
        cases=cases,
        criteria=_criteria(),
        model_name="fake-model",
        system_id="test-system",
    )

    assert adapter.calls[0].redaction_policy == "raw_permitted"


def test_a_hashes_only_request_can_never_yield_a_proposal() -> None:
    """The mechanism itself, asserted directly rather than inferred.

    This is what makes the test above a fact about the library and not a
    preference of this module. It is also why the existing extraction arm in
    synthesis_executor.py — which requests "hashes_only" and then branches on
    `status == "proposal"` to stamp "verified" — can never reach that branch
    against a conforming adapter.
    """
    from mrr.adapters.llm.structured_generation import generate_structured
    from mrr.domain.model_adapter import ModelInvocationRequest as _Request

    hashes_only_outcome = ModelInvocationOutcome(
        status="completed",
        prompt_config_hash=_VALID_HASH,
        token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        redaction_policy="hashes_only",
        response_hash=_VALID_HASH,
    )
    adapter = _ScriptedFakeAdapter([hashes_only_outcome, hashes_only_outcome])
    request = _Request(
        model_profile_id="urn:mrr:model-profile:01ARZ3NDEKTSV4RRFFQ69G5FAV",
        model_profile_hash=_VALID_HASH,
        prompt_text="anything",
        operation_kind="stochastic",
        redaction_policy="hashes_only",
    )

    from mrr.services.classification.relation_service import _RelationVerdict

    result = generate_structured(adapter, request, _RelationVerdict, max_repair_attempts=1)

    assert result.status == "schema_invalid"
    assert result.proposal is None
    assert any("no response text available" in error for error in result.validation_errors)


def test_the_prompt_states_the_target_schema_so_the_first_call_can_succeed() -> None:
    """The other half of the same failure.

    `generate_structured` puts the schema into its REPAIR prompt only. A
    first call that never says "return this JSON" is asking a language model
    for prose and then failing it for not being JSON. The schema is
    interpolated from the target model itself, so the instruction and the
    validation cannot drift apart.
    """
    service = RelationClassificationService()
    case = RelationCase(
        case_id="c1",
        title="A source",
        claim_text="a claim",
        excerpt="An external solver checks each step.",
        excerpt_sha256="sha256:" + "a" * 64,
    )

    prompt = service.build_prompt(case, _criteria())

    assert "no markdown code fence" in prompt
    for field in ("relation", "rationale", "decided_by", "tie_with"):
        assert f'"{field}"' in prompt
    assert "undecidable" in prompt


def test_the_prompt_hash_covers_the_schema_not_only_the_template() -> None:
    """A changed schema changes the ask as surely as a changed sentence."""
    assert prompt_contract_sha256().startswith("sha256:")
    assert prompt_contract_sha256() != "sha256:" + "0" * 64
