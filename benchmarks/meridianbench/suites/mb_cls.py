"""MB-CLS — evidence-relation classification against a frozen gold standard
(task-packets/N1-T02.yaml R2).

This suite measures the ONE judgement no model in this repository makes today
and that an automatic literature channel would have to make: given a source's
own words and a claim, does the source support the claim, contradict it,
qualify it, or merely contextualise it?

--- Why this judgement and not another -------------------------------------

``mrr.services.node_runtime.synthesis_executor`` counts only ``supports`` and
``contradicts`` toward the corroboration buckets that cap what a claim may say
(its own ``_SUPPORTING_RELATION``/``_CONTRADICTING_RELATION``). Today that
label is set by a human curating ``corpus-entries.json``, before the machine
sees anything — ``corpora/README.md`` says so plainly: "Sie ordnet nicht ein."
Automating it is step 3 of the owner's ordering; measuring whether an
automated version would be any good is step 1, and this suite is that
measurement.

--- Label isolation is INHERITED, never re-implemented -----------------------

``benchmarks.meridianbench.harness`` already guarantees structurally that a
system under test cannot see a label: ``SystemUnderTest`` is
``Callable[[InputT], OutputT]``, so there is no parameter through which a
``BenchmarkCase`` — and therefore no ``expected`` — could reach it. This module
adds nothing to that and must weaken nothing: :class:`MbClsInput` carries only
what a classifier legitimately reads, :class:`MbClsExpected` carries the answer,
and ``metadata`` stays what the harness says it is — free-text for reporting,
never a side channel.

The one live hazard worth naming: the gold set's on-disk form has the answer
and the excerpt in the SAME JSON object. :func:`load_cases` is the single place
that object is split, and it is the reason the split cannot be got wrong
elsewhere — nothing else in this module reads the raw document.

--- Scoring lives in the service, not here ----------------------------------

The metrics are computed by
``mrr.services.validation.gold_service.GoldValidityService`` over
``mrr.domain.agreement``'s existing core. This module builds cases and runs a
system over them; it re-decides no statistic of its own. (``mb_cit`` follows
the same shape, reusing ``mrr.services.verifier.source`` rather than
re-deciding whether an anchor resolves.)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mrr.services.validation.gold_service import GoldLabelSet, GoldValidityService

from benchmarks.meridianbench.harness import BenchmarkCase, SystemUnderTest, run_suite


@dataclass(frozen=True, slots=True, kw_only=True)
class MbClsInput:
    """Everything a classifying system under test may see for one case, and
    nothing else.

    There is deliberately no field here from which the answer could be
    reconstructed: no ``expected_relation``, no gold rationale, no per-case
    metadata. ``categories`` and ``criteria`` ARE included because they are the
    question, not the answer — a classifier that is not told the label space
    and the definitions is being asked to guess a convention, which measures
    nothing about its reading.
    """

    case_id: str
    excerpt: str
    claim_text: str
    categories: tuple[str, ...]
    criteria: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class MbClsExpected:
    """The gold answer for one case. Only a scorer ever sees this — see the
    module docstring's label-isolation section.

    Both fields are ``None`` for a case the criteria could not settle
    (criteria v2, ``R-undecidable-is-a-finding``): there is no gold answer, and
    a placeholder would be a fabricated one. ``undecidable_reason`` carries
    what the criteria failed to decide.
    """

    relation: str | None
    rationale: str | None
    undecidable: bool = False
    undecidable_reason: str | None = None


def load_cases(gold_set: GoldLabelSet) -> tuple[BenchmarkCase[MbClsInput, MbClsExpected], ...]:
    """Split a loaded gold set into benchmark cases.

    This is the ONLY place the on-disk case object — which holds the excerpt
    and the answer together — is taken apart. Everything downstream sees either
    an :class:`MbClsInput` or an :class:`MbClsExpected`, never both.

    ``gold_set`` must already have passed
    :meth:`mrr.services.validation.gold_service.GoldValidityService.
    load_gold_set`'s hash check, order gate and provenance quarantine; that is
    what having a :class:`GoldLabelSet` in hand means.
    """
    cases: list[BenchmarkCase[MbClsInput, MbClsExpected]] = []
    for raw in gold_set.cases:
        case_id = str(raw["case_id"])
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                input=MbClsInput(
                    case_id=case_id,
                    excerpt=str(raw["excerpt"]),
                    claim_text=str(raw["claim_text"]),
                    categories=gold_set.categories,
                    criteria=dict(gold_set.criteria),
                ),
                expected=MbClsExpected(
                    relation=(
                        None if raw.get("undecidable", False) else str(raw["expected_relation"])
                    ),
                    rationale=(
                        None if raw.get("undecidable", False) else str(raw["expected_rationale"])
                    ),
                    undecidable=bool(raw.get("undecidable", False)),
                    undecidable_reason=(
                        str(raw["undecidable_reason"])
                        if raw.get("undecidable_reason") is not None
                        else None
                    ),
                ),
                metadata={
                    key: str(value)
                    for key, value in (raw.get("metadata") or {}).items()
                    # 'gold_class' would be the answer under another name. A
                    # fixture may carry it for human reporting; it never
                    # travels into a case a system under test could reach.
                    if key != "gold_class"
                },
            )
        )
    return tuple(cases)


def collect_predictions(
    system: SystemUnderTest[MbClsInput, str],
    cases: Sequence[BenchmarkCase[MbClsInput, MbClsExpected]],
) -> dict[str, str]:
    """Run ``system`` over ``cases`` via the harness and collect
    ``case_id -> predicted relation``.

    ``system``'s type is the point, not an annotation formality: a
    ``SystemUnderTest[MbClsInput, str]`` is a callable from an
    :class:`MbClsInput` to a relation string, so there is no parameter through
    which a case or its label could reach it — the guarantee is
    :mod:`benchmarks.meridianbench.harness`'s, and this signature inherits it
    rather than restating it.
    """
    outputs = run_suite(system, cases)
    return {case.case_id: output for case, output in zip(cases, outputs, strict=True)}


def load_gold_set_for_tests(path: Path) -> GoldLabelSet:
    """Load a gold set with the synthetic-provenance quarantine lifted.

    Named for what it is. The quarantine exists so a fixture cannot be mistaken
    for a standard in real use; tests are precisely the case where using the
    fixture is correct, and making that an explicit, obviously-named call is
    better than a flag threaded through production code paths.
    """
    return GoldValidityService().load_gold_set(path, allow_synthetic=True)


__all__ = [
    "MbClsExpected",
    "MbClsInput",
    "collect_predictions",
    "load_cases",
    "load_gold_set_for_tests",
]
