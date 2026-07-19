"""The MeridianBench harness (task-packets/E4-T07.yaml): a ``BenchmarkCase``
that STRUCTURALLY separates a case's ``input`` from its ``expected`` label,
and a runner typed so a system under test can never receive either the case
or the label — only the input.

--- Why this is structural, not a convention -------------------------------

``SystemUnderTest`` is a plain type alias for ``Callable[[InputT], OutputT]``
— a system under test is ANY callable that maps an input to an output. There
is no ``BenchmarkCase`` parameter anywhere in that signature, so a system
under test could not accept one even if a caller tried to pass it — the
label is not merely unused, it is UNREACHABLE by the type ``run_case`` and
``run_suite`` require. ``run_case`` itself makes this concrete: it destructures
``case.input`` before ever calling ``system``, and never reads
``case.expected`` at all. Only a suite's own scorer function (which is
handed the whole ``BenchmarkCase``, never a system under test) can see the
label — docs/spec/05_EVALUATION_AND_ACCEPTANCE.md section 8's "no use of the
test labels in prompts or retrieval sources", and docs/spec/06's own section
7 stop condition, "benchmark labels would leak into model prompts".

--- No contract entity, no persistence --------------------------------------

``BenchmarkCase`` is a plain, frozen, in-memory value object — not an
``mrr.contracts`` entity, not schema-registered, not persisted anywhere
(task-packets/E4-T07.yaml forbidden_changes: "any new authoritative state,
DB schema, migration, persistence, or contract entity/JSON Schema"). A
benchmark case's ``metadata`` is free-form only for a human-readable case
label/category (e.g. "numerator/denominator swap") — never a channel for
anything a scorer needs to determine correctness.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkCase[InputT, ExpectedT]:
    """One benchmark fixture. ``input`` is everything a system under test may
    see; ``expected`` is the label a scorer alone may see (never passed to a
    system under test by any code in this package). ``metadata`` is a plain,
    free-text, human-readable map (e.g. ``{"category": "source cited but
    inaccessible"}``) for reporting only.
    """

    case_id: str
    input: InputT
    expected: ExpectedT
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id must not be empty")


#: A system under test: a plain callable from a case's INPUT to an output —
#: never a ``BenchmarkCase``, never a label. This is the crux invariant's
#: type-level enforcement (task-packets/E4-T07.yaml derived_decisions: "the
#: runner is typed so a system under test is a callable receiving ONLY the
#: input"). Both the scripted agent-under-test (``systems.scripted_agent``)
#: and the non-agent baseline (``systems.baselines``) conform to this alias.
type SystemUnderTest[InputT, OutputT] = Callable[[InputT], OutputT]


def run_case[InputT, ExpectedT, OutputT](
    system: SystemUnderTest[InputT, OutputT], case: BenchmarkCase[InputT, ExpectedT]
) -> OutputT:
    """Invoke ``system`` with ONLY ``case.input`` — never ``case`` itself,
    never ``case.expected``. This function is the whole of the structural
    label-isolation guarantee at the call site: there is no other value it
    could pass to ``system`` even if it wanted to, since ``system``'s own
    type accepts exactly one ``InputT`` argument.
    """
    return system(case.input)


def run_suite[InputT, ExpectedT, OutputT](
    system: SystemUnderTest[InputT, OutputT],
    cases: Sequence[BenchmarkCase[InputT, ExpectedT]],
) -> list[OutputT]:
    """Run ``system`` over every case in ``cases``, in order, returning one
    output per case. Deterministic: the same ``system`` and the same
    ``cases`` always yield the same list of outputs, since ``run_case``
    performs no I/O of its own beyond calling ``system``.
    """
    return [run_case(system, case) for case in cases]


__all__ = [
    "BenchmarkCase",
    "SystemUnderTest",
    "run_case",
    "run_suite",
]
