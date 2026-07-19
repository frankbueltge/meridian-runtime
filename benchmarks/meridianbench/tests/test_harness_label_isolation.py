"""Label isolation (task-packets/E4-T07.yaml crux invariant): a system under
test receives ONLY a case's input, never its expected label — and, for the
scripted agent-under-test specifically, the prompt built from a case never
contains that case's expected label (task-packets/E4-T07.yaml acceptance
test, taken literally).
"""

from __future__ import annotations

import inspect

from benchmarks.meridianbench.harness import BenchmarkCase, run_case, run_suite
from benchmarks.meridianbench.suites.mb_cit import MB_CIT_CASES, CitationCaseInput
from benchmarks.meridianbench.suites.mb_num import MB_NUM_CASES, NumericCaseInput
from benchmarks.meridianbench.systems.scripted_agent import (
    build_citation_prompt,
    build_numeric_prompt,
    make_citation_agent,
    make_numeric_agent,
)

# ---------------------------------------------------------------------------
# The structural guarantee: run_case forwards only .input, never the case
# or the label, to whatever callable it is given.
# ---------------------------------------------------------------------------


def test_run_case_passes_only_the_input_never_the_case_or_the_label() -> None:
    received: list[object] = []

    def spy(value: object) -> str:
        received.append(value)
        return "ok"

    case: BenchmarkCase[str, str] = BenchmarkCase(
        case_id="isolation-probe",
        input="the-only-thing-a-system-may-see",
        expected="the-secret-label-a-system-must-never-see",
    )

    result = run_case(spy, case)

    assert result == "ok"
    assert received == ["the-only-thing-a-system-may-see"]
    assert case.expected not in received


def test_run_suite_forwards_only_inputs_in_order() -> None:
    received: list[object] = []

    def spy(value: object) -> str:
        received.append(value)
        return "ok"

    cases: list[BenchmarkCase[str, str]] = [
        BenchmarkCase(case_id="a", input="input-a", expected="label-a"),
        BenchmarkCase(case_id="b", input="input-b", expected="label-b"),
    ]

    results = run_suite(spy, cases)

    assert results == ["ok", "ok"]
    assert received == ["input-a", "input-b"]
    assert "label-a" not in received
    assert "label-b" not in received


# ---------------------------------------------------------------------------
# Prompt builders are typed over the case's INPUT type only, structurally —
# neither takes a BenchmarkCase or an Expectation/label type.
# ---------------------------------------------------------------------------


def test_build_numeric_prompt_signature_accepts_only_the_input_type() -> None:
    # eval_str=True: every module here uses `from __future__ import
    # annotations`, so raw signature annotations are unevaluated strings —
    # this resolves them back to the real type objects.
    (parameter,) = inspect.signature(build_numeric_prompt, eval_str=True).parameters.values()
    assert parameter.annotation is NumericCaseInput


def test_build_citation_prompt_signature_accepts_only_the_input_type() -> None:
    (parameter,) = inspect.signature(build_citation_prompt, eval_str=True).parameters.values()
    assert parameter.annotation is CitationCaseInput


# ---------------------------------------------------------------------------
# The literal acceptance test: the scripted agent's prompt, built from each
# case, never contains that case's expected label.
# ---------------------------------------------------------------------------


def test_numeric_prompts_never_contain_the_case_expected_label() -> None:
    for case in MB_NUM_CASES:
        prompt = build_numeric_prompt(case.input)
        assert case.expected.correct_value not in prompt


def test_citation_prompts_never_contain_the_case_expected_label() -> None:
    for case in MB_CIT_CASES:
        prompt = build_citation_prompt(case.input)
        assert str(case.expected.must_not_report_pass) not in prompt


def test_scripted_numeric_agent_end_to_end_never_leaks_a_label_into_a_request() -> None:
    """Runs the actual scripted agent-under-test (its ModelAdapter fake)
    over every MB-NUM case via ``run_suite`` and inspects every recorded
    ``ModelInvocationRequest.prompt_text`` — not just the prompt-builder
    function in isolation. Each request is matched back to the ONE case that
    produced it (``run_suite`` preserves case order) — a different case's
    label incidentally sharing a digit with THIS case's own numbers is not a
    leak, so this only ever checks a case's own label against its own request.
    """
    scripted = make_numeric_agent(MB_NUM_CASES, correct=True)

    run_suite(scripted.system, MB_NUM_CASES)

    assert len(scripted.adapter.requests) == len(MB_NUM_CASES)
    for case, request in zip(MB_NUM_CASES, scripted.adapter.requests, strict=True):
        assert request.prompt_text == build_numeric_prompt(case.input)
        assert case.expected.correct_value not in request.prompt_text


def test_scripted_citation_agent_end_to_end_never_leaks_a_label_into_a_request() -> None:
    scripted = make_citation_agent(MB_CIT_CASES, faithful=True)

    run_suite(scripted.system, MB_CIT_CASES)

    assert len(scripted.adapter.requests) == len(MB_CIT_CASES)
    for case, request in zip(MB_CIT_CASES, scripted.adapter.requests, strict=True):
        assert request.prompt_text == build_citation_prompt(case.input)
        assert str(case.expected.must_not_report_pass) not in request.prompt_text
