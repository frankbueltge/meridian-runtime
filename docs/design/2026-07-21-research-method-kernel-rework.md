# Research Method Kernel — Rework and Decision Record

**Date:** 2026-07-21
**Status:** proposal — awaiting Frank's plain-language go-ahead before any kernel code.
**Inputs:** `docs/design/2026-07-21-research-method-kernel-handoff.md` (prior-session
handoff); the external v0.2.0 specification package
(`frankbueltge.de/docs/meridian-research-runtime-spec-v0.2.0/`, produced with an external
drafting tool); a full audit of the current repository state; a full critical inventory of
the v0.2.0 package.
**Outputs:** this decision record; `docs/spec/08_RESEARCH_METHOD_KERNEL.md` (proposed
normative spec); `docs/design/2026-07-21-research-method-kernel-plan.md` (implementation
plan, light-first).

This document records the *fresh-eyes rework* the handoff asked for. It was written after
independently re-verifying the repository state and the v0.2.0 package rather than taking
either the handoff's or the package's framing as given.

---

## 1. The question as posed, and why it is slightly wrong

Both prior inputs pose the decision as **"full kernel vs. light version."** After reading
the actual artifacts, that framing hides the real structure of the choice:

- The **handoff** frames the full kernel as a frontier build (automated causal inference —
  months, may not converge) and the light profile as ~80 % of the value at ~10 % of the
  cost. True as far as it goes.
- The **v0.2.0 package** quietly *redefines* "full kernel" to make it buildable: its entire
  M0–M7 program is contracts, state machines, and gates running **exclusively on synthetic
  fixtures**, with every actually-hard verb — compile, scout, audit, falsify — deferred to
  an unscheduled M8. The "Question Compiler" in M0–M7 compiles nothing; a human authors a
  `QuestionModel` JSON fixture and the machine validates transitions over it.

So the package resolved the frontier risk by never touching it. That is honest engineering,
but it has a cost the package does not name: **16 task packets of plumbing before the
machine produces its first real research output** — and the first demo at the end is "the
machine refuses a rigged synthetic claim." That is precisely the multi-month-build-with-
nothing-real-to-show outcome Frank fears, arrived at prudently.

**The rework:** the kernel is not one thing to build or skip. It is three separable layers
with different cost/uncertainty profiles, and the decision should be made per layer:

| Layer | What it is | Effort | Uncertainty | Verdict |
|---|---|---|---|---|
| **L1 — Method-governance contracts** | claim ceilings, protocol locks, kill conditions, `insufficient_evidence` as success, synthetic-fixture isolation | small (folds into existing object/edge/policy model) | **low** | **build** |
| **L2 — First Method Profile as a real executor task** | `systematic_evidence_synthesis` running on the two atlases, producing a sealed, real claim landscape | moderate (≈ one epic) | **low** | **build — this is the first real output** |
| **L3 — Causal organs** (estimands, causal models, identification audits, analysis/falsification/replication engines) | as *contracts + human rulings*: bounded; as *autonomous engines*: open research problems + a data swamp | contracts: small · engines: unbounded | contracts: medium · engines: **high** | **contracts later, engines not scheduled** |

The distinctive, notable thing about this project — the machine that **refuses the
confident answer and shows its epistemic ceiling** — lives in L1+L2 and is fully
deliverable there. L3-as-engines adds the most risk and the least distinctiveness.

## 2. What the v0.2.0 package gets right (adopt)

These are genuinely good and are carried into `08_RESEARCH_METHOD_KERNEL.md`:

1. **The claim-ceiling vocabulary** — `causal_bounded, causal_local, associational_adjusted,
   associational_unadjusted, descriptive, mechanism_hypothesis, insufficient_evidence` —
   a clean, profile-independent taxonomy for "how strong may this claim's language be."
2. **`insufficient_evidence` as a first-class successful terminal state** — "we cannot know
   this, and here is why" is a *complete* research outcome, never a failure to hide. (This
   is the same ethic as the Protokoll's "Feststellung entfällt.")
3. **Locked plans with explicit amendments** — confirmatory commitments bound to a content
   hash before results are seen; every post-lock change is a new revision recording whether
   outcome information had been observed. Generalized here beyond causal pre-analysis
   plans to any profile's *protocol* (a systematic review locks its inclusion criteria
   exactly the same way).
4. **Kill conditions as mandatory structured fields**, not post-hoc caveats.
5. **Synthetic-fixture isolation as a hard gate** — test data must be technically incapable
   of becoming empirical evidence.
6. The **housing-affordability reference case** as future *fixture material* (not as the
   first build target), and the four Gherkin acceptance features as behavioral pins —
   after correcting the defects below.
7. A curated subset of its **MRR-MTH requirements** (renumbered; mapping table in the spec).

## 3. What the v0.2.0 package gets wrong (rework or reject)

Found by direct inspection; none of this is visible from the package's own validator,
which passes cleanly (it checks structure, not cross-document semantics).

1. **It is written for a repository that does not exist.** Its task packets and layout
   assume a greenfield multi-package monorepo (`packages/method-kernel/…`,
   `apps/api/…`, Temporal, FastAPI) and even include "E1-T01 bootstrap the monorepo."
   This repo is a working single-distribution Python project (`mrr`, 39 merged PRs,
   ~1,340 tests) whose architecture the package would fork, not extend.
2. **All 14 new object schemas silently drop the identity/signature contract.** Every
   existing MRR schema composes `common.schema.json#/$defs/baseObject` (identity,
   revision, content hash, **signature**). None of the 14 new schemas does — no
   signature field at all, `additionalProperties: false` flat objects, no
   `supersedes`/`labels`. Adopted objects MUST be rewritten to compose `baseObject`
   like every other first-class object, or they are second-class citizens outside the
   trust machinery the whole runtime is built on.
3. **The load-bearing linkage is unspecified.** The package declares 16 new edge types in
   prose but ships no edge schema, and its `claim.schema.json` is untouched — so *how a
   Claim is actually bound to the audit that licenses its language* (the single most
   important mechanism) has no machine-checkable shape. This repo already has the answer:
   the typed `edges` layer (`EDGE_VOCABULARY`) plus service-level policy — the same way
   "supported claims require verification" is already enforced. The spec folds ceiling
   binding into that existing mechanism.
4. **Internal drift that would propagate as bugs:** the acceptance features use error
   codes absent from the normative catalog (`METHOD_CLAIM_CEILING_EXCEEDED` vs.
   `CLAIM_CEILING_EXCEEDED`, `PREANALYSIS_PLAN_NOT_LOCKED` vs. `PREANALYSIS_NOT_LOCKED`,
   `SYNTHETIC_EVIDENCE_FORBIDDEN` vs. `SYNTHETIC_FIXTURE_NOT_EVIDENCE`); the flagship
   example misquotes the gate catalog (`G-M03`/`G-M08`/`G-M09` labels do not match the
   gate definitions, one cited gate does not exist); MRR-MTH-125 names **seven**
   replication-independence dimensions while the schema requires **six** (no
   `prompt_lineage`); and the rule mapping the independence booleans to a
   `replication_type` — the actual classification function — is nowhere defined.
   The spec adopts one canonical error-code set and the 7-dimension vector, and requires
   the type to be *derived*, not asserted.
5. **~11 % of its requirements have zero task coverage** (all of qualitative/mixed
   methods) — aspiration presented in the same register as scheduled work. The spec here
   marks qualitative explicitly as a *future profile* aligned with epic E7, not silently.
6. **Duplicated governance.** The package re-specifies verification, skepticism, and
   independence at the method layer; this runtime already has SkepticalChallenge,
   VerificationResult, the independence validator, and model adapters with
   no-authoritative-state rules (E3/E4). The kernel must *reuse* those organs, not
   parallel them.

## 4. The reframe that carries the design

**A Method Profile is an executor task family plus a rulebook — not a researcher.**

The runtime's executor boundary (`Executor` protocol; dispatch by
`TaskBundle.capability` explicitly left to "a future dispatch layer") is the slot the
kernel plugs into. A profile contributes:

1. **contracts** — the objects its investigations produce (all composing `baseObject`);
2. **an executor task family** — what actually runs, with deterministic substance;
3. **gates** — which claim ceilings its designs can license, enforced by the existing
   claim service;
4. **a protocol form** — what must be locked before confirmatory work.

Everything a model proposes flows through the existing E4 adapters and remains
non-authoritative until verified or explicitly marked as proposal — the kernel adds **no
new model authority anywhere**. Where the package says "Compiler/Scout/Auditor," this
spec says **proposer + human or deterministic ruling**, which is not a downgrade: it is
the practice's constitution (human steering via REQUESTS.md / the site's Seed-Engine),
and it is the honest version of what M0–M7 builds anyway.

This also answers the model-collapse worry from the handoff: the profiles' inputs (the
human-curated atlases, the Saat channel) are exactly the external grounding the
self-consumption literature prescribes, and they stay load-bearing by design.

## 5. The first profile: `systematic_evidence_synthesis` on the two atlases

Chosen over `audit_reanalysis` and everything causal because it is the only candidate
that is **real on day one** with material already in the house:

- **Theory atlas** (`irrtum-als-methode/atlas/atlas.json`): 87 provenance-verified
  sources — id, author, work, year, type, url, tags, summary, status.
- **Works atlas** (`frankbueltge.de/src/data/atlas/werke.json`): 214 verified data/AI
  artworks — title, artist, year, clusters, decisive_move, source_url, verify_status.

Both are effectively evidence ledgers with resolvable identifiers. The profile's first
real question is the one already prototyped by hand in the originating conversation:
*do artworks catalogued under "AI feeding on itself" actually instantiate the model-collapse
mechanism the technical literature describes, or merely reference it?* — producing an
evidence matrix and a claim landscape with honest per-claim statuses (supported /
partial / contested / insufficient_evidence), each claim ceiling-capped at
`descriptive`/`associational_unadjusted` (a synthesis can never license causal language).

What is deterministic: atlas ingestion pinned by content hash, identifier resolution,
cross-referencing, matrix assembly, independence checks (existing validator), ceiling
enforcement. What may use a model: extraction/classification *proposals*, via the
existing structured-generation adapter, verified against the source or downgraded to
marked proposals. That division is the entire answer to "how is this not the
hallucination machine."

## 6. Sequencing (divergence from the handoff, flagged)

The handoff's decided sequence was "finish the whole cathedral (E6–E9) first, then the
light profile." **Recommended revision:**

```
now      E5 close-out (PR #40 fix ✓, E5-T07b)
next     E6  — transfers/obligations/corrections   (cathedral; the federation backbone
              research-ecology's D-JI-03 explicitly depends on)
then     K0–K1 — Method Profile interface + systematic_evidence_synthesis
              (first REAL research output of the runtime)
then     decide K2 (causal contracts, propose+verify) with K1's output in hand
later    E7 (qualitative profile — reframed as a Method Profile), E8 (exports — now has
              real content to export), E9 (hardening)
```

Reasons: E7 without any real research capability would be another empty-slot epic; K1 is
cheaper than E7 and produces the visible, couplable output Frank actually values; E8/E9
gain substance by coming after K1. This is a recommendation, not a decision — Frank
decides by approving packets in this or another order.

## 7. Answers to the handoff's open questions (§10)

1. **Full kernel worth it?** As autonomous engines: no — least notability per unit risk.
   As L1 contracts + L3 human-ruled vocabulary: yes, cheaply, later. The notable gesture
   (machine-enforced epistemic humility) ships with L1+L2.
2. **Propose + human-verify?** Yes — adopted as the *definition* of every frontier organ,
   not as a fallback. It matches the existing rule "no model output becomes authoritative
   state" and the practice's human-steering constitution.
3. **First profile + I/O contract:** `systematic_evidence_synthesis`; contract specified
   in `08_RESEARCH_METHOD_KERNEL.md` §5 (QuestionModel + locked SynthesisProtocol +
   pinned atlas snapshots in → EvidenceMatrix + ceiling-capped claims + sealed crate out).
4. **Real executor substance:** deterministic ingestion/resolution/assembly + verified
   model-assisted extraction, per §5 above.
5. **Data swamp:** v1 is deliberately literature/atlas-only; no external dataset is
   promised. A causal profile would name its 1–2 datasets in its own packet or admit
   literature-only scope — that admission is itself an honest, publishable result.
6. **Framing:** artistic/critical research staging epistemic honesty, engineered with
   scientific discipline — a science-shaped instrument deployed in an art context.
   "Done/good" = a sealed, honest, reusable claim landscape on a real question; not
   methodological novelty in causal inference.

## 8. Defects in the current build noted along the way

- PR #40's two integration failures were diagnosed and fixed (RETURNING instead of
  `CursorResult.rowcount`; see PR comment for the full mechanism). All tiers green
  locally; CI pending at the time of writing.
- The event-log hash chain is sensitive to the database session timezone (fails against
  a non-UTC server because the rendered `timestamptz` differs on read-back). CI is UTC,
  so it never surfaces there. Filed for E9 hardening; noted on PR #40.
