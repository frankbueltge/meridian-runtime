# Handoff — Research Method Kernel (Direction & Rework Brief)

**Date:** 2026-07-21
**Status:** thinking/handoff document — NOT a spec, NOT approved work. Written to be
reworked in a fresh session (model switch: the prior reasoning session was Opus; this
handoff is meant to be re-thought with Fable).
**Purpose:** hand the full context of a long design conversation to a fresh session so it
can (a) rethink/rework the `RESEARCH_METHOD_KERNEL` proposal below with clear eyes, and
(b) produce the *next* artifact Frank asked for: **a concrete implementation plan in this
repo for the full kernel** (see §11 for where that plan should live and what it must
contain).

This document is self-contained: a session with zero prior context should be able to act
from it alone. Read it top to bottom before proposing anything.

---

## 0. The ask, in one paragraph

The Meridian Research Runtime (this repo) is today an excellent **Research Governance and
Evidence Operating System** — it specifies *under what conditions research may happen* and
*how results are handled*, but it does **not** yet specify *how an open question becomes a
scientifically viable research program*. Frank (project owner) drafted, with ChatGPT, a
proposal to fill exactly that gap: a three-way separation (Meridian / Runtime / Research
Method Profiles) plus a `RESEARCH_METHOD_KERNEL` (14 components, a 10-phase pipeline, and
~10 new normative `MRR-MTH-*` requirements). The Opus session pressure-tested it and gave
an honest sizing + a novelty read (both below). **Decisions already made:** finish the
current "cathedral" (the governance OS, E6–E9) first; then build ONE *light* method profile
(`systematic_evidence_synthesis` / `audit_reanalysis`); only then decide on the *full*
kernel. Frank now wants to **rework the full-kernel design with fresh eyes and land a
concrete repo implementation plan for it.** That is your job.

---

## 1. Frank — who he is, and how to serve him (read this first; it changes everything)

Getting the *register* right matters more than any technical detail here.

- **Data & AI Engineer.** His site (frankbueltge.de) is a public **field of experiments in
  artistic research with data and AI.** meridian-runtime is one of three autonomous "engine
  collectives" (field/**Meridian** = this runtime's practice; studio/Ensemble; atelier/Ulysses).
- **This is a path, not a goal.** In his own words: *"ich verfolge mit diesem Projekt kein
  Ziel, sondern bin auf einem Weg."* The experiment/play is the driver; he learns a lot along
  the way. He explicitly does **not** claim to want to do scientific research, and has **no
  fixed plan** for the artistic research either.
- **His stated fear (take it seriously):** spending **months building something that turns
  out to be useless gimmickry** ("Dünnschiss"). Any proposal that quietly commits him to a
  multi-month frontier build without flagging the cost/uncertainty betrays this.
- **His value model is COUPLING, not completion.** The payoff he actually points to:
  generated works get **reused in a completely different context** (already done with
  data-snack.com and datavism.org). meridian is one organ in a larger metabolism, not an
  end-product that must be "finished."
- **His compass for "not Dünnschiss":** does an output **point at something real in the
  world and reframe it?** (His already-existing work "Kiosk — Skandal in den Niederlanden"
  from the studio is a good example: a kind of monument / originally-prepared history.)
- **Human-in-the-loop is by design, not an afterthought.** It was *never* meant to be a
  fully autonomous system. There is a deliberate outside channel — `REQUESTS.md` and the new
  **Seed-Engine (Saat)** on the site — through which humans steer the machine. (This is also
  the anti-model-collapse mechanism; see §5 note.)
- **Honesty over reassurance.** He repeatedly tested and rewarded straight talk in this
  conversation, and caught imprecisions. Do not flatter, do not oversell, flag tensions.
- **He is not a git/CI expert** and did not author the repo's heavy process rules; don't
  quote them back at him as "your rules" or make him arbitrate git mechanics — pick the
  sensible default and explain it plainly. **One hard safety line:** never merge to `main`
  or deploy without his clear, plain-language go-ahead.

---

## 2. Where meridian-runtime stands (facts)

- It is the **field/Meridian** engine: a research-orchestration system built on explicit
  provenance, policy-gated execution, and verifiable claims — implemented as vertical slices,
  not an agent framework with provenance bolted on. Python 3.12, `uv`, hatchling, single
  distribution `mrr`. See `README.md`, `AGENTS.md`, `docs/spec/`.
- **Epic roadmap E0–E9** (see `docs/spec/06_IMPLEMENTATION_PLAN.md`): E0 baseline, E1
  contracts/domain, E2 single-node vertical slice (the runnable evidence loop), E3
  claim/evidence/correction, E4 agent roles & model adapters, **E5 federation (current)**,
  E6 transfers/obligations/corrections across practices, E7 qualitative/field-research mode,
  E8 portable exports & projections, E9 hardening.
- **Progress ≈ 5.5 / 10 epics.** E5-T06 (offline bundles) merged to `main` (#39). **E5-T07
  (durable replay/idempotency store) is open as PR #40 with 2 failing integration tests** —
  the `record_processed` return-value ("newly recorded") logic returns `False` on the first
  insert under real Postgres (`ON CONFLICT` rowcount semantics); local tiers were green,
  the bug only surfaces against real Postgres in CI. Federation is 6/7 planned tasks;
  **E5-T07b** was split off (durable revocation record + `trust_revoked_after_creation`
  annotation) as a deliberate follow-up.
- There is a working **`mrr` CLI** (`mrr run`) that drives one complete local evidence loop
  end to end (approve Research Score → register signed capability manifest → negotiate &
  execute a **deterministic reference** Task Bundle → record Run Manifest → seal Evidence
  Crate), with **no model/LLM dependency**. Needs Postgres + an artifact dir to run.

---

## 3. The honest diagnosis (agreed in the conversation)

The runtime today is a very good **governance / provenance / orchestration layer** — a
Research Governance & Evidence OS. Concretely it is: a governance system, a permission &
security model, a provenance system, a workflow orchestrator, a claim/evidence ledger, and
a correction/federation protocol. Metaphor: **skeleton + nervous system, not the scientific
metabolism.** It is the referee + court-of-record + lab notebook — **not the lab.**

**Architectural truth about the gap:** the spec design puts the actual empirical work into a
**sandboxed executor that runs pluggable *containerized tasks*** ("OCI containers for sandbox
tasks", AGENTS.md). Data connectors (data-plane component 5) and the sandboxed executor
(component 4) are *concept components* but are **stubbed / a deterministic reference
placeholder**; E7 (field-research) is roadmapped but unbuilt. So: **the empirical organs are
designed as empty slots, and the hard "how do you actually produce a finding" content is
pushed outside the runtime and not built.** "Can it empirically research?" → today, no; the
concept *provides for* it (pluggable tasks + connectors + E7) but the slots are empty.

Note for honesty: the impressive multi-session walkthrough of the housing question (§7) was
**finished-cathedral behavior** (the runtime's designed logic applied), not what today's
build does. Today it would gate the score and structure a Task Bundle, then the *placeholder*
executor would run — no real data gathered.

---

## 4. The load-bearing architectural proposal — the three-way separation

This is the part of Frank's proposal most worth keeping. Do **not** conflate three things:

1. **Meridian** — the research *practice* with its local epistemic commitments.
2. **Meridian Research Runtime** — the *infrastructure* that executes, logs, secures,
   federates, and keeps projects correctable. (What is built today.)
3. **Research Method Profiles** — the concrete *methodological engines* that define **how a
   given kind of question is investigated.**

The runtime is **not itself "the researcher."** Meridian *researches with* the runtime and,
for a concrete question, activates one or more Method Profiles. Candidate profile names from
the proposal:

```
audit_reanalysis
causal_observational
quasi_experimental
computational_experiment
systematic_evidence_synthesis
qualitative_field
mixed_methods
```

A **new Practice is not needed** for this — a Method Profile is (architecturally) exactly the
"pluggable containerized task-family" that fills the runtime's empty executor slot. A new
Practice is warranted only if a genuinely *different epistemic regime / responsibility /
relation to evidence* emerges, not merely because another software module is needed. This
maps cleanly onto what is already built — it is a way to *fill the empty slot*, not a rewrite.

---

## 5. The `RESEARCH_METHOD_KERNEL` proposal (faithful capture — the thing to rework)

Worked reference question used throughout:
> *"To what extent does rising housing-cost unaffordability causally increase support for
> right-populist / anti-establishment parties in European cities, 2015–2025?"*

The runtime must **not** hand this straight to a research agent. It must first decompose it
into precise, testable research objects, across ~10 phases:

1. **Question Compiler** → a versioned `QuestionModel`: `claim_type` (e.g. `causal`, which
   triggers stronger requirements), and the undefined terms flagged for operationalization.
2. **Concept & Measurement Charter** (local, versioned): each load-bearing term gets multiple
   *non-interchangeable* operationalizations (e.g. "housing unaffordability" = income share /
   overburden rate / rent-to-income / residual income / price-to-income / subjective burden /
   displacement risk — a price rise can enrich owners while burdening renters; a single
   "affordability" measure hides opposite effects). Party classification is split
   (`right_populist` / `radical_right` / `anti_establishment` / `eurosceptic` /
   `incumbent_opposition` / `protest_party`), versioned by country/party/time. This stays a
   **local project classification, not a global ontology** (matches Meridian's constitution:
   the field map is a local map).
3. **Estimand Builder** → several concretely-defined effects (individual-level, city-level,
   composition/sorting effect, heterogeneity estimands), pre-declared (no post-hoc subgroup
   fishing).
4. **Causal Model Builder** → multiple *competing* versioned DAGs with confounders /
   mediators / selection mechanisms, and named rival models (material-pressure /
   status-threat / political-accountability / residential-sorting / reverse-selection /
   null-or-context-dependent). These competing models are the actual `Hypothesis Forest`.
5. **Evidence & Data Scout** → two separate searches: (a) literature → an **evidence matrix**
   (study / cities / period / unit / treatment / outcome / design / identification assumption
   / effect / limitations / replication status / **source family**); (b) data candidates,
   each scored on coverage / resolution / access / identifier compatibility / missingness /
   measurement consistency / licence / cost / personal-data risk / causal usefulness. A
   realistic first result: "a Europe-wide city-level causal estimate is not defensible with
   public harmonized data" — this is **not failure**; it narrows scope honestly.
6. **Design Generator** → several concrete designs (individual panel / city panel / DiD / IV /
   synthetic control / replication / qualitative-mechanism), each with what it can answer and
   its central weakness. Each causal design gets an **`IdentificationAudit`** with explicit
   assumptions, falsification tests, and **kill conditions** — weak designs are **killed**,
   not caveated-and-computed-anyway.
7. **Pre-analysis Compiler** → seals the confirmatory plan (estimands, sample, exclusions,
   classifications, models, FE, SE strategy, missing-data strategy, falsification/robustness/
   heterogeneity analyses, stopping conditions) **before** results are seen. Exploratory work
   stays possible but marked exploratory, never re-labeled confirmatory later.
8. **Executable Research** → the plan compiles to workflows (ingest → crosswalk → harmonize →
   construct measures → build panel → diagnostics → primary models → event studies → placebos
   → heterogeneity → claim candidates), each step with inputs/hashes, code version, runtime,
   transforms, exclusions, errors, outputs, tests.
9. **Adversarial Research Loop** → a skeptic spawns new tasks (is it just anti-incumbent
   mood? does it vanish under a different party taxonomy? only capitals? sorting vs
   preference change? pre-existing before the shock? other financial burdens too?) →
   placebos, negative controls, leave-one-country-out, alt taxonomies/measures/windows/
   boundaries, and **independent re-implementation by a second executor on a separate code
   path.**
10. **Mixed-Methods Fork** → quantitative shows *whether/where*; qualitative field work (docs,
    media, interviews, observation in contrast-selected cities) probes *how/why* and finds
    missing variables — but must not be treated as delivering the same kind of effect size.

**Result form:** not "housing raises populism by X%," but a **structured claim landscape**
with honest statuses, e.g. `supported_in_bounded_scope`, `contested`,
`contradicted_or_unsupported`, `supported_in_selected_cases`, `unsupported` — plus effect
sizes/intervals, identification assumptions, scope of validity, null results,
counter-findings, sensitivities, unavailable data, open mechanisms, replication status.

**The proposed `RESEARCH_METHOD_KERNEL` = 14 components:** (1) Question Compiler, (2) Concept
& Measurement Charter, (3) Estimand Builder, (4) Causal Model Builder, (5) Evidence
Cartographer, (6) Data Scout, (7) Design Generator, (8) Identification Auditor, (9)
Pre-analysis Compiler, (10) Analysis Compiler, (11) Falsification Engine, (12) Replication
Engine, (13) Generalization Mapper, (14) Adaptive Research Manager.

**Proposed new normative requirements (`MRR-MTH-001..010`, verbatim intent):**
- 001 every causal question MUST compile to ≥1 explicit estimand.
- 002 every load-bearing term MUST have a versioned local operationalization.
- 003 a causal branch MUST declare identification assumptions, confounders, mediators,
  selection mechanisms.
- 004 a causal claim MUST NOT be derived from a purely descriptive/correlational design.
- 005 every confirmatory branch MUST carry refutation criteria + kill conditions before it runs.
- 006 if identification fails, the claim MUST be downgraded to associational/descriptive/unresolved.
- 007 central analyses MUST get an independent re-implementation or a documented reason it's impossible.
- 008 party/population/space/time definitions MUST be versioned and varied in sensitivity analysis.
- 009 exploratory results MUST NOT retroactively appear as pre-registered confirmatory results.
- 010 every causal claim MUST carry an explicit generalization scope + ≥1 non-applicability condition.

---

## 6. The Opus session's honest assessment (carry this into the rework)

**Effort, in rough "cathedral units" — the number that matters is UNCERTAINTY, not hours:**

| | Effort vs. current cathedral | Uncertainty | Output |
|---|---|---|---|
| Finish cathedral (E6–E9) | ~0.5–1× more of the same | **low** — converges, known terrain | full governance/federation |
| **Light profile** (`systematic_evidence_synthesis` + Tier-1 scaffolding) | **~0.2–0.3×** | **low** | first *real* research output; couplable; visible |
| **Full kernel** (Tier-2: Data Scout, Causal Model Builder, Identification Auditor, Analysis/Falsification/Replication engines) | ~2–4× raw | **high — the point** | at best honest "contested" maps; at worst plausible-but-wrong |

- The cathedral is **big but known** (bounded engineering, converges). The full kernel is
  **big AND unknown**: its hardest organs are **open research problems** (automating
  identification-strategy validity, DAG correctness) + a **data swamp** (real, heterogeneous,
  access-restricted sources). It is not "2× more work" — it is *work that may not converge*.
  Without a genuinely computing executor on real data, an LLM-driven Identification Auditor is
  exactly the **hallucination machine the governance layer exists to prevent.**
- **Tier-1 (achievable, cathedral-like):** Question Compiler, Concept/Measurement Charter,
  Estimand Builder, Evidence Cartographer, Pre-analysis Compiler (mostly reuses existing
  provenance/sealing). This IS the light profile — and it's essentially what was done **by
  hand** as a proof (see §8 pointer to the step-1 example).
- **Tier-2 (frontier / data-gated):** Data Scout, Causal Model Builder, Design Generator,
  Identification Auditor, Analysis Compiler, Falsification/Replication engines, Adaptive
  Manager.

**Novelty / "taken seriously or ridiculed?" — honest read:**
- *As automated science:* respected-if-humble (methodologists value the epistemic humility),
  but not "revolutionizing science," and at real risk of **ridicule if it overclaims**; and
  enormously hard.
- *As critical / artistic research:* a rigorous machine that, on a hot-button political
  question, **refuses the confident pundit answer** and shows the honest "we cannot causally
  claim this" is a **pointed, original cultural gesture** — same register as Forensic
  Architecture. This can genuinely attract attention in critical-AI / STS / digital-art
  discourse.
- **Key liberating insight:** the thing that would make it *notable* is the **epistemic-honesty
  gesture — which the LIGHT version already delivers.** The full causal frontier is the part
  *least* likely to add the notability Frank is curious about and *most* likely to eat months
  and invite ridicule-by-overclaim. **The distinctiveness lives in the framing and honesty,
  not in solving causal inference.**

Note (ties the whole conversation together): run over very many sessions, an LLM executor
that increasingly feeds on its *own* prior crates becomes a closed self-training loop — i.e.
**the runtime's own long-run failure mode is model collapse**, the exact phenomenon the
studio/atelier atlases study. Frank already built the mitigation *by instinct*: the
`REQUESTS.md` / Seed-Engine (Saat) human channel is the "external ground-truth point" the
model-collapse literature (Shumailov 2023; Gerstgrasser 2024; Jangjoo 2025) prescribes.
Whatever the kernel becomes, that external channel is load-bearing.

---

## 7. Decisions already made + recommended sequence

1. **Finish the cathedral (E6–E9) first.** Frank's call, and the right one — it's the
   convergent foundation. Immediate loose end: **fix PR #40's 2 failing tests** (§2) whenever
   he wants it merged.
2. **Then build ONE light method profile** — `systematic_evidence_synthesis` /
   `audit_reanalysis`. Cheap, low-uncertainty, produces a *visible, couplable* first real
   research output, and ≈80% of the potential notability.
3. **Only then decide on the full kernel**, with real light-version output in hand.
4. **This handoff's job:** rework the full-kernel design with fresh eyes and produce a
   concrete repo implementation plan for it (§11) — so that decision (3) can be made against a
   real plan, not a vibe. Reworking ≠ committing to build it.

---

## 8. Pointers (files worth reading)

- `README.md`, `AGENTS.md` (binding rules), `docs/spec/06_IMPLEMENTATION_PLAN.md` (epics),
  `docs/spec/01_SYSTEM_SPEC.md` (data-plane components, MRR-FR/NFR), `docs/spec/07_AGENT_TASK_TEMPLATE.md`
  (how task packets are derived), `task-packets/` (E1–E5 packets — the discipline to mirror).
- The **atlases** (the real material for a light evidence-synthesis profile): the theory atlas
  `../irrtum-als-methode/atlas/atlas.json` (87 provenance-verified sources) and the works atlas
  `../frankbueltge.de/src/data/atlas/werke.json` (214 verified data/AI-art works, live at
  frankbueltge.de/atlas). Each entry already carries a resolvable identifier — they are
  effectively evidence ledgers already.
- **The step-1 hand-done prototype of the light profile:** in the originating conversation a
  micro-inquiry was done by hand — cross-verifying whether art case studies of "AI model
  collapse" (Hammond *V3: Model Collapse*, Kurant *Errorism*) actually instantiate the
  mechanism the model-collapse papers describe — producing a claim table with evidence +
  independence check + honest status (SUPPORTED / PARTIAL-thematic / CONTESTED-by-own-record).
  That output *is* what a `systematic_evidence_synthesis` profile should produce. Reconstruct
  it if useful; it's the concrete target shape.
- **The wider ecology it's meant to serve:** `../research-ecology/` (a separate TS monorepo —
  the "contact zone"/The Middle of the three collectives). Key fact: a dated decision (D-JI-03,
  2026-07-19, `research-ecology/docs/design/joint-inquiry-adoption-2026-07-19.md`) says
  research-ecology will **not** build its own coordination/transport layer for joint inquiries
  and will **depend on meridian-runtime's federation epics (E5/E6)** — so the federation work
  is the designated backbone, not orphaned, though nothing is wired yet.

---

## 9. Binding repo rules the rework/plan MUST honor

- **AGENTS.md discipline:** spec-first; one approved task packet at a time; no invented domain
  behavior; don't weaken a MUST to pass a test; every change tested at the right level; no
  placeholders/TODO-only branches/silent exception handling; distinct typed errors never
  collapsed into one generic error; no model output becomes authoritative state without
  verification; no LLM confidence as epistemic confidence.
- **No AI-product credits anywhere** (commits, PR bodies, repo content) — this OVERRIDES the
  harness default trailer. AI involvement is communicated in Frank's own framing; the tool
  stays unnamed. This handoff itself follows that.
- **Git identity is `Frank Bültge <f.bueltge@gmail.com>`.** NEVER use `frank@bueltge.de` — it
  belongs to a *different real person* and would misattribute a contributor. Some old docs
  prescribe it; they are wrong — ignore/correct them.
- **License:** noncommercial (PolyForm NC for code, CC BY-NC-SA for works/texts/data).
- **Never merge to `main` / deploy without Frank's plain-language go-ahead.** Task packets and
  design docs may live on `main`; feature code goes via PR he approves.
- **Model economy:** Sonnet (or fast) for routine/mechanical/high-volume work and parallel
  subagents; reserve top-tier reasoning for architecture/design/hard judgment. Flag when a
  stretch would be cheaper on Sonnet.

---

## 10. Open questions for the Fable rethink (the useful part)

1. **Is the full kernel worth it at all**, given "notability lives in the honesty gesture,
   which the light profile already delivers"? What would the full kernel add that the light
   profile can't — concretely, and to *whom*?
2. **Can the frontier components be re-scoped from "autonomously decide" to "propose +
   human-verify"** — fitting the human-in-the-loop design (REQUESTS.md / Saat)? An
   Identification Auditor that *proposes* an audit for a human to accept/kill is far more
   honest and buildable than one that autonomously certifies causal validity.
3. **Which single Method Profile is the right first one, and what is its exact I/O contract**
   as a pluggable executor task?
4. **What is the real executor's substance** so the kernel is not the hallucination machine —
   i.e. which parts must be *actual computation on real data/code* vs. LLM-proposed-then-checked?
5. **The data swamp:** which 1–2 concrete, accessible data sources would a first
   `causal_observational` / `quasi_experimental` profile actually integrate? (Name them, or
   admit the profile is literature-only for v1.)
6. **Framing decision that colors everything:** is this "science" (chasing methodological
   respect) or "critical/artistic research staging epistemic honesty" (Frank's actual
   register)? The answer changes what "done" and "good" mean.

---

## 11. The next artifact — what the concrete implementation plan must contain, and where

Frank wants to "point to a concrete implementation plan in the repo for the full kernel."
Recommended shape (mirror the existing spec/plan style):

- **A spec doc** for the kernel, e.g. `docs/spec/08_RESEARCH_METHOD_KERNEL.md` (numbered to
  sit alongside the existing `0x_*` spec files), defining: the three-way separation as
  architecture; the Method Profile interface (how a profile plugs into the executor slot);
  the 14 components as capabilities; the `MRR-MTH-*` requirements folded into the requirement
  namespace; and — crucially — an explicit **tiering** (Tier-1 achievable vs Tier-2 frontier)
  with honest per-component *uncertainty*, not just effort.
- **An implementation plan** in the `06_IMPLEMENTATION_PLAN.md` idiom (or a sibling
  `docs/design/…-research-method-kernel-plan.md`): an epic/task breakdown, ordered
  light-first, each task in the `task-packets/` derivation style (objective / forbidden
  changes / allowed paths / acceptance tests / stop conditions), with the *first* deliverable
  being the light `systematic_evidence_synthesis` profile as a runnable executor task on the
  atlases (a real, testable first crate).
- The plan MUST be honest about which components are frontier/uncertain and MUST NOT present
  the full kernel as bounded engineering. Where a component can only be "propose + human
  verify," say so — that is a feature, not a limitation, for this project.

Do the *rework and the plan as documents first* (design, reviewable, cheap). Do not start
implementing the full kernel; the immediate buildable thing (after the cathedral) is the
single light profile. Get Frank's go-ahead before any code.

---

*End of handoff. If anything here reads as more certain or more finished than it is, treat
that as an error in the handoff, not a fact about the world — and say so.*
