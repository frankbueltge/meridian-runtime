"""The scripted agent-under-test (task-packets/E4-T07.yaml): a fake
``mrr.domain.model_adapter.ModelAdapter`` (E4-T01) — never a real provider,
never a network call — whose prompt is built from a ``BenchmarkCase``'s
INPUT ONLY.

--- Label isolation, concretely --------------------------------------------

``build_numeric_prompt``/``build_citation_prompt`` each take a case's
``NumericCaseInput``/``CitationCaseInput`` — never a ``BenchmarkCase``, never
an ``NumericExpectation``/``CitationExpectation`` label — and every one of
``make_numeric_agent``/``make_citation_agent``'s returned callables builds
its ``ModelInvocationRequest.prompt_text`` by calling one of those two
functions on the ``harness.SystemUnderTest`` argument it receives (which is
always just the case's ``input``, per ``harness.run_case``). See
``tests/test_harness_label_isolation.py`` for the literal assertion that
neither prompt ever contains a case's expected label.

--- Why the script is keyed by prompt text, not by case ---------------------

``ScriptedModelAdapter`` (a ``mrr.domain.model_adapter.ModelAdapter``
conformer) looks up its canned response by the EXACT ``prompt_text`` a
``ModelInvocationRequest`` carries — never by a case id, never by any
BenchmarkCase reference. This mirrors
``tests/unit/adapters/llm/test_structured_generation.py``'s own
``_ScriptedFakeAdapter`` precedent (a deterministic, in-memory fake with no
provider SDK and no network of any kind) while additionally guaranteeing
that ``invoke`` itself has no way to key its answer on anything but the
literal prompt text a caller supplies — the same structural guarantee
``harness.run_case`` gives the outer benchmark loop, one layer further in.

--- Two scripted behaviors per suite, built from CASE INPUT alone ----------

The "correct"/"faithful" scripted agents below compute their own canned
responses by calling the SAME reused E4-T05 verifier tools
(``recompute_numeric_claim``/``validate_evidence_anchor``) against each
case's ``input`` at SCRIPT-CONSTRUCTION time — never against ``case.expected``,
and never at ``invoke``-time (the adapter itself only ever does a dict
lookup). This is ordinary fixture authoring (the benchmark author already
knows, from a case's own input, what a correct answer looks like — exactly
like every existing scripted-fake precedent in this codebase bakes in a
correct payload), not a runtime label read: the resulting script is a plain
``Mapping[str, str]`` handed to ``ScriptedModelAdapter``, with no residual
connection to the ``BenchmarkCase`` objects used to build it. The
"incorrect"/"overclaiming" variants exist purely to exercise the harness's
own scoring (MB-NUM's "all-wrong system" acceptance case; MB-CIT's
false-support acceptance case).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from mrr.crypto.hashing import content_hash
from mrr.domain.identity import new_urn
from mrr.domain.model_adapter import (
    ModelInvocationOutcome,
    ModelInvocationRequest,
    RedactionPolicy,
    TokenUsage,
    apply_redaction,
)
from mrr.services.verifier.numeric import recompute_numeric_claim
from mrr.services.verifier.source import validate_evidence_anchor

from benchmarks.meridianbench.harness import SystemUnderTest
from benchmarks.meridianbench.suites.mb_cit import (
    CitationCase,
    CitationCaseInput,
    CitationSystemOutput,
    CitationVerdict,
)
from benchmarks.meridianbench.suites.mb_num import (
    NumericCase,
    NumericCaseInput,
    NumericSystemOutput,
)

#: A fixed, valid model-profile identity for every request this scripted
#: agent builds — this is a benchmark fixture, not a real ``ModelProfile``
#: revision, so a single constant pair suffices (mirrors
#: ``tests/unit/adapters/llm/test_structured_generation.py``'s own
#: ``_VALID_PROFILE_URN``/``_VALID_HASH`` constants).
_MODEL_PROFILE_ID = new_urn("model-profile")
_MODEL_PROFILE_HASH = "sha256:" + "b" * 64

#: ``"raw_permitted"``, not the safer default ``"hashes_only"`` — this
#: in-memory fake needs its own canned response text back out of the
#: ``ModelInvocationOutcome`` it returns so the wrapping agent function can
#: decode a structured output from it (exactly the same reason
#: ``tests/unit/adapters/llm/test_structured_generation.py``'s own
#: ``_completed_outcome`` helper defaults to ``"raw_permitted"``).
_SCRIPTED_REDACTION_POLICY: RedactionPolicy = "raw_permitted"


def build_numeric_prompt(case_input: NumericCaseInput) -> str:
    """Build a prompt from a MB-NUM case's INPUT alone — never its label.
    Deterministic (sorted input names) so the same input always yields the
    same prompt text, letting ``ScriptedModelAdapter`` key its script by it.
    """
    lines = [
        f"Verify this numeric claim: {case_input.claim_text}",
        f"operation: {case_input.operation}",
    ]
    for name, value in sorted(case_input.numeric_inputs.items()):
        lines.append(f"{name}: {value}")
    return "\n".join(lines)


def build_citation_prompt(case_input: CitationCaseInput) -> str:
    """Build a prompt from a MB-CIT case's INPUT alone — never its label."""
    lines = [
        f"Judge this citation: {case_input.claim_text}",
        f"anchor_kind: {case_input.anchor.anchor_kind}",
        f"relation: {case_input.anchor.relation}",
    ]
    return "\n".join(lines)


class ScriptedModelAdapter:
    """A deterministic, in-memory ``mrr.domain.model_adapter.ModelAdapter``
    conformer. Looks up its response by the exact ``prompt_text`` a request
    carries — never by a case id or any ``BenchmarkCase`` reference. No
    network, no provider SDK: this class imports neither.
    """

    def __init__(self, script: Mapping[str, str]) -> None:
        self._script = dict(script)
        #: Every request this adapter was called with, in call order — for
        #: tests to inspect (label-isolation assertions), mirroring
        #: ``tests/unit/adapters/llm/test_structured_generation.py``'s own
        #: ``_ScriptedFakeAdapter.calls``.
        self.requests: list[ModelInvocationRequest] = []

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationOutcome:
        self.requests.append(request)
        response_text = self._script.get(request.prompt_text)
        if response_text is None:
            raise KeyError(
                "ScriptedModelAdapter has no scripted response for prompt "
                f"{request.prompt_text!r} — every prompt a benchmark suite can build must be "
                "scripted up front"
            )
        response_hash, raw_response_text = apply_redaction(request.redaction_policy, response_text)
        return ModelInvocationOutcome(
            status="completed",
            prompt_config_hash=content_hash(request.prompt_text.encode("utf-8")),
            token_usage=TokenUsage(
                prompt_tokens=len(request.prompt_text.split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(request.prompt_text.split()) + len(response_text.split()),
            ),
            redaction_policy=request.redaction_policy,
            response_hash=response_hash,
            raw_response_text=raw_response_text,
        )


def _scripted_request(prompt_text: str) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model_profile_id=_MODEL_PROFILE_ID,
        model_profile_hash=_MODEL_PROFILE_HASH,
        prompt_text=prompt_text,
        operation_kind="stochastic",
        redaction_policy=_SCRIPTED_REDACTION_POLICY,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptedNumericAgent:
    """A scripted agent-under-test for MB-NUM, plus the adapter it drives —
    exposed together so a test can inspect ``adapter.requests`` for label
    isolation.
    """

    system: SystemUnderTest[NumericCaseInput, NumericSystemOutput]
    adapter: ScriptedModelAdapter


def make_numeric_agent(cases: Sequence[NumericCase], *, correct: bool) -> ScriptedNumericAgent:
    """Build a scripted MB-NUM agent-under-test. When ``correct`` is
    ``True``, every case's canned response is the value
    ``recompute_numeric_claim`` itself yields for that case's own
    ``operation``/``numeric_inputs`` (the input alone — never
    ``case.expected``); when ``False``, every response is deliberately a
    different number, so the resulting system never matches.
    """
    script: dict[str, str] = {}
    for case in cases:
        prompt = build_numeric_prompt(case.input)
        # ``claimed_value="0"`` is a dummy — ``recomputed_value`` is computed
        # purely from ``operation``/``numeric_inputs`` and never depends on
        # it; only the (here unused) ``matches_claimed_value`` would.
        recomputation = recompute_numeric_claim(
            operation=case.input.operation,
            claimed_value="0",
            inputs=case.input.numeric_inputs,
            tolerance=case.input.tolerance,
        )
        if recomputation.recomputed_value is None:
            raise ValueError(
                f"case {case.case_id!r} has no recomputable value to script a scripted agent from"
            )
        if correct:
            script[prompt] = str(recomputation.recomputed_value)
        else:
            wrong_value = Decimal(recomputation.recomputed_value) + Decimal(1000)
            script[prompt] = str(wrong_value)
    adapter = ScriptedModelAdapter(script)

    def agent(case_input: NumericCaseInput) -> NumericSystemOutput:
        prompt = build_numeric_prompt(case_input)
        outcome = adapter.invoke(_scripted_request(prompt))
        return NumericSystemOutput(claimed_value=outcome.raw_response_text)

    return ScriptedNumericAgent(system=agent, adapter=adapter)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScriptedCitationAgent:
    """A scripted agent-under-test for MB-CIT, plus the adapter it drives."""

    system: SystemUnderTest[CitationCaseInput, CitationSystemOutput]
    adapter: ScriptedModelAdapter


def _ground_truth_verdict(case_input: CitationCaseInput) -> CitationVerdict:
    """The verdict a faithful agent would report for one case's INPUT
    alone, by running the reused E4-T05 source verifier over it — this is
    fixture authoring (see the module docstring), never a run-time label
    read: ``case.expected`` plays no part here. A TOTAL, documented mapping
    over ``AnchorValidationStatus``'s exact three values.
    """
    outcome = validate_evidence_anchor(
        case_input.anchor,
        local_text_artifact=case_input.local_text_artifact,
        local_computational_artifact=case_input.local_computational_artifact,
    )
    if outcome.anchor_validation_status == "validated":
        return "pass"
    if outcome.anchor_validation_status == "invalid":
        return "fail"
    return "unknown"


def make_citation_agent(cases: Sequence[CitationCase], *, faithful: bool) -> ScriptedCitationAgent:
    """Build a scripted MB-CIT agent-under-test. When ``faithful`` is
    ``True``, every case's canned response is the ground-truth verdict
    :func:`_ground_truth_verdict` computes from that case's own INPUT; when
    ``False``, every response is ``"pass"`` regardless — an overclaiming
    agent that raises the false-support rate (task-packets/E4-T07.yaml's own
    acceptance test).
    """
    script: dict[str, str] = {}
    for case in cases:
        prompt = build_citation_prompt(case.input)
        script[prompt] = "pass" if not faithful else _ground_truth_verdict(case.input)
    adapter = ScriptedModelAdapter(script)

    def agent(case_input: CitationCaseInput) -> CitationSystemOutput:
        prompt = build_citation_prompt(case_input)
        outcome = adapter.invoke(_scripted_request(prompt))
        verdict = outcome.raw_response_text
        if verdict == "pass":
            return CitationSystemOutput(verdict="pass")
        if verdict == "fail":
            return CitationSystemOutput(verdict="fail")
        if verdict == "unknown":
            return CitationSystemOutput(verdict="unknown")
        raise ValueError(f"scripted adapter returned an unrecognized verdict {verdict!r}")

    return ScriptedCitationAgent(system=agent, adapter=adapter)


__all__ = [
    "ScriptedCitationAgent",
    "ScriptedModelAdapter",
    "ScriptedNumericAgent",
    "build_citation_prompt",
    "build_numeric_prompt",
    "make_citation_agent",
    "make_numeric_agent",
]
