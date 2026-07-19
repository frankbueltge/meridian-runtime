"""MeridianBench — the versioned evaluation corpus and runner
(docs/spec/05_EVALUATION_AND_ACCEPTANCE.md section 3). Seventh and final
task of Epic E4 (task-packets/E4-T07.yaml): populates the ``meridianbench``
test tier (``scripts/run_test_tier.py`` already maps it to
``benchmarks/meridianbench``; ``make benchmark`` runs it).

--- The crux: LABEL ISOLATION is structural, not a convention ---------------

``harness.BenchmarkCase`` carries an ``input`` (what a system under test may
see) and an ``expected`` label (what only the scorer may see) as two
SEPARATE fields, and ``harness.SystemUnderTest`` is typed as
``Callable[[InputT], OutputT]`` — a plain function of the input alone. There
is no code path in ``harness.run_case``/``harness.run_suite`` through which a
``BenchmarkCase`` or its ``expected`` label ever reaches a system under test;
the label is reachable only inside a suite's own scorer function, which
receives the whole ``BenchmarkCase`` (docs/spec/05 section 8: "no use of the
test labels in prompts or retrieval sources"; docs/spec/06 section 7 stop
condition: "benchmark labels would leak into model prompts").

--- Two populated benchmark families, reusing E4-T05 verbatim --------------

- ``suites.mb_num`` (MB-NUM, numeric fidelity) scores a system's claimed
  numeric answer with ``mrr.services.verifier.numeric.recompute_numeric_claim``
  — never reimplemented here.
- ``suites.mb_cit`` (MB-CIT, citation-anchor resolution) scores a system's
  citation verdict with ``mrr.services.verifier.source.validate_evidence_anchor``
  — never reimplemented here.

``targets`` declares the docs/spec/05 section 4 calibrated targets as named,
ADR-updatable constants. ``promotion`` is a pure, deterministic function from
a metrics report and those targets to a promote/hold decision plus a
per-target pass/fail and an evaluation-profile attribution — it enacts
nothing (no live promotion, no state mutation; docs/spec/06 section 5.6 is a
separate, gated action; AGENTS.md rule 15).

``systems.baselines`` is the non-agent baseline (trivial, deterministic,
always abstains) run on the same suites as ``systems.scripted_agent`` (a
scripted fake ``mrr.domain.model_adapter.ModelAdapter``-backed
agent-under-test whose prompt is built from a case's INPUT only) — the E4
exit criterion: "MB-CIT and MB-NUM targets are evaluated against a non-agent
baseline."

No provider SDK, no network, no real model call anywhere in this package —
only a scripted, in-memory fake adapter and two deterministic verifier
tools.
"""

from __future__ import annotations
