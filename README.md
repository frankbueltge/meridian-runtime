# Meridian Research Runtime

**A research system that refuses to take an AI's word for anything.**

AI systems can now run a research project end to end — read the literature, analyse
data, write it up. The documented catch is that they *fabricate*: they invent
citations, synthesise data when the real data is missing, and average away findings
that disagree. Meridian Research Runtime (MRR) is built so that this cannot happen
unnoticed. Every claim is tied to the evidence behind it, checked by something *other
than* the component that produced it, and where evidence conflicts, the conflict stays
on the record instead of being resolved away.

MRR is the successor architecture to Meridian Classic. It is deterministic at its core:
orchestration, evidence anchoring, and verification contain no model step. Where models
are used at all, they are declared, hashed, logged tools of individual steps whose
output is independently verified or explicitly marked as an estimate — never
orchestrators, never sole judges.

---

## The problem, quantified

The design responds to measured failure modes, not to a general unease. The figures
below come from the research records in `docs/design/` and are cited there with their
sources; each was adversarially verified before being used.

| Finding | Source |
|---|---|
| Citation **accuracy** of deep-research systems ranges **40–80 %** across systems | DeepTRACE, arXiv 2509.04499 |
| Machine-written report statements judged accurate: **79.4 %** overall, but only **57.9 %** for synthesis and interpretation | Kosmos, arXiv 2511.02824 |
| **42 %** of experiments failed on coding errors; the system's own literature review declared **all 12** generated ideas novel | Beel et al., arXiv 2502.14297v3 |
| Given empty or insufficient datasets, **all seven** frontier models tested fabricated synthetic data rather than reporting impossibility | SciIntegrity-Bench, arXiv 2605.10246 |
| LLM judges stayed **below 85 %** accuracy even with scenario-specific checklists and a full execution trace | ibid., §6 |

Two consequences shape the architecture. Source failure must terminate deterministically
*before* a model sees it — a model handed a gap fills it. And an LLM cannot be the
verifier of an LLM.

## What has actually been demonstrated

This section is deliberately separate from what is *designed*. Numbers are from the
committed archive dumps in `archive/dumps/`, readable offline without a database.

Two real runs exist, both on a model-collapse classification question:

| | run 1 | run 2 |
|---|---:|---:|
| Objects in the archive | 67 | 125 |
| Source records | 18 | 36 |
| Evidence anchors | 17 | 34 |
| Claims | 4 | 8 |
| Independent verification results | 3 | 6 |
| Evidence crates sealed | 1 | 2 |

The chain is complete in both: question model → concept charter → method protocol →
task bundle → run manifest → sources → evidence anchors → claims → evidence matrix →
verification → sealed crate.

**The two-voice verification design produced a real finding.** On 2026-07-22 a second,
separately constituted practice reviewed a Meridian claim line and disagreed with it.
The disagreement — the *Hammond dissent* — was preserved rather than adjudicated, and
remains open in the archive. Dissent preservation is the property this system is built
around, and it has occurred, not merely been specified.

One result is public: **[On Record](https://frankbueltge.de/on-record)** shows a dispute
in which thirteen catalogue entries contradict each other, with all thirteen kept
visible.

## Current limitations

Stated plainly, because a system built on verifiability has no business burying them.

- **The evidence bytes of both real runs are not recoverable.** All 51 evidence anchors
  carry `anchor_validation_status: "validated"`, and that status is currently *not
  falsifiable*: the snapshot bytes they hash are not findable. Cause: no object recorded
  where artifact bytes were written. Fixed for future runs
  (`docs/design/2026-07-26-a1-fact-lock-artifact-bytes.md`); not recoverable for these
  two. Internal anchoring integrity is intact — the graph resolves without a single
  dangling reference — but that is anchoring *integrity*, not anchoring
  *redeemability*.
- **A model adapter exists, but no run uses one.** `mrr.adapters.llm.gemini` is a
  concrete `ModelAdapter` against a pinned model, and one real invocation has been made
  and counter-checked — its first answer was wrong in a checkable way, which is recorded
  rather than quietly dropped (`docs/design/2026-07-27-erster-realer-modellaufruf.md`).
  What is still missing is composition: no research run invokes a model, because the
  synthesis executor's optional model-assisted step is not wired to any adapter. And
  that step proposes only extraction *prose* — the `supports`/`contradicts`
  classification a claim rests on stays human and is never touched by a model.
- **No exchange has taken place yet, though the path is now complete.** A real archived
  object — a `VerificationResult` recording a preserved dissent — has been carried
  through envelope, bundle, file and back, and accepted by the unchanged receiving
  validation. Meridian's own `Practice` identity exists and is signed
  (`practices/meridian.json`). Since I1-T01 a correction can also be recorded,
  impact-analysed and notified from the command line, delivered offline as a
  bundle-ready envelope. What remains is not code: a counterpart practice must agree its
  node id and declare trust in Meridian's key.
- **Claim-support checking reaches abstract level only.** `mrr audit support` verifies
  that a cited figure or verbatim quotation is present in the source's abstract, with
  anchor terms guarding against coincidental numeric matches. Measured coverage of the
  audited record's claims by abstracts: roughly 28 %. Absence from the checked excerpt
  is reported as an *observation*, never as refutation.
- **Novelty is bounded deliberately.** The combination of hash anchoring, independent
  machine counter-verification, and enforced dissent preservation has, *on the evidence
  gathered*, no occupant. The stronger blanket claim of absence was tested and refuted
  by the project's own verifier, and is not made here.

## Try it in two minutes

The audit commands run offline — no database, no network, no API key — over research
data committed in this repository. This one resolves every evidence anchor of two real
runs against the sources they claim to point at:

```bash
git clone https://github.com/frankbueltge/meridian-runtime
cd meridian-runtime
uv sync
uv run mrr audit anchoring --batch corpora/archive-integrity/anchoring-batch.v1.json
```

Real output, both runs:

```text
violations:   { anchor_dangling: 0, claim_reference_dangling: 0 }
observations: { anchor_unreferenced: 0, source_unanchored: 1 }   # run 1
observations: { anchor_unreferenced: 0, source_unanchored: 2 }   # run 2
```

That split is the point. A dangling anchor is a **violation** — a claim citing evidence
that is not there. A source that ended up carrying no evidence is an **observation** —
a corpus entry that was gathered and not used, which is not a defect. The two are
counted separately and never summed. Collapsing them would report three failures where
there are none.

Before anything is evaluated, a fail-closed gate compares each declared input's sha256
against the value pinned in the committed descriptor. If a byte has moved, the run
halts and names both hashes.

## What you can run

```bash
uv run mrr --help
```

| Command | What it does |
|---|---|
| `mrr run` | One complete local evidence loop: approve a research score, register a signed capability manifest, negotiate and execute a deterministic task bundle, record the run manifest, seal the evidence crate. |
| `mrr validate agreement` | Stratified, hash-anchored inter-instance agreement over a blind-vs-pipeline classification set — Cohen's and weighted kappa, Krippendorff's alpha, per-category F1, majority baseline, below-power flag. Reported as **reliability, not validity**. |
| `mrr audit citations` | Do the cited references exist and resolve? |
| `mrr audit anchoring` | Does every evidence anchor point at a really archived source, and every claim reference at a real anchor? Violations are kept strictly apart from observations. |
| `mrr audit support` | Does the source's abstract carry the figure or quotation attributed to it? |
| `mrr audit artifacts` | Are a run's artifact bytes present where the run recorded them? |
| `mrr export` / `report` / `release` | Portable research export, PROV graph, release gate with supersession. |
| `mrr federation envelope sign` | Wrap any archive object that carries its own content hash into a signed transport envelope. Refuses a payload without one — such an envelope could never pass the receiver's consistency check. |
| `mrr federation outbox` / `inbox` | Write and fail-closed validate signed offline bundles for exchange between practices. |
| `mrr observe field` | Read-only field observation behind a fail-closed integrity gate. |
| `mrr correction record` / `impact` / `notify` / `status` | Record a correction to a published result, compute which claims downstream it touches, and deliver a signed notification to each affected practice's outbox — offline, bundle-ready. A correction is always a new revision, never an overwrite. |

The audit commands run read-only, without network or database access, over committed
inputs behind fail-closed hash gates: if a declared input's bytes no longer match its
pinned anchor, the run halts before anything is evaluated.

## Design commitments

These are enforced in code and tests, not merely documented. `AGENTS.md` binds every
change in this repository.

- **No model output becomes authoritative state.** Model results are proposals subject
  to schema and domain validation.
- **No executor verifies its own result.** Verification is performed by a separately
  constituted instance with a declared independence profile.
- **Distinct failure states are never collapsed.** `unknown`, `not_found`,
  `contradicted`, and `failed` remain distinct — as do, throughout, *violations* and
  *observations*. Reporting an absence of knowledge as a defect is treated as a defect
  in itself.
- **Source failure fails closed**, deterministically, before any model step.
- **Counting copied sources as independent evidence is prohibited**, as is letting an
  agent cite a source it did not retrieve and anchor.
- **Git is authoritative** for code, schemas, prompts, policies, and specification
  versions; the append-only event log is authoritative for audit history.

## Quality commands

All commands run through `uv` and are wired up in the `Makefile`.

| Command | What it does |
|---|---|
| `make format` / `make format-check` | Format and auto-fix with Ruff; check without modifying. |
| `make lint` | Ruff lint plus the import-boundary contract (import-linter). |
| `make typecheck` | mypy in strict mode. |
| `make test` | The `unit` and `property` tiers. |
| `make test-contract` | The `contract` tier. |
| `make test-integration` / `make test-e2e` / `make test-adversarial` | The remaining tiers. |
| `make benchmark` | The `meridianbench` tier. |
| `make security-check` | `pip-audit` against locked dependencies, and `bandit` across `packages`, `adapters`, `services` and `scripts`. |

Test tiers with no tests yet are declared in `tests/EMPTY_TIERS.txt` and reported as
explicitly expected-empty, never as passing feature coverage.

Core packages (`mrr.domain`, `mrr.crypto`, `mrr.contracts`, `mrr.policy`,
`mrr.provenance`, `mrr.observability`) must not import a web framework, a workflow
engine, a model-provider SDK, or an object-store client SDK. This is machine-enforced
by the import-linter contract and checked in `make lint`.

## Layout

```text
meridian-runtime/
├── packages/            # mrr.domain, mrr.crypto, mrr.contracts, mrr.policy,
│                        # mrr.provenance, mrr.observability
├── services/            # control_plane (the mrr CLI and orchestration), node_runtime
├── adapters/            # provider-facing edges: llm, object_store, prompts, federation
├── corpora/             # committed research corpora and verification fixtures
├── archive/dumps/       # committed archive snapshots of the real runs
├── tests/               # unit, property, contract, integration, e2e, adversarial
├── docs/spec/           # the governing specification and ADRs
├── docs/design/         # derivations, reviews, research records, decision records
├── schemas/, examples/  # JSON Schemas and example objects for external contracts
└── task-packets/        # approved task packets, implemented one at a time
```

## How this repository is developed

Each change is one approved task packet. Before it is built, a *derivation* fixes the
factual basis by inspecting the real code, data, and APIs — and is committed before any
implementation. Acceptance criteria are fixed in advance. The instance that derives a
packet does not build it, and the instance that builds it does not verify it.

Derivations routinely correct the task they were given; those corrections are recorded
rather than quietly applied. `docs/design/` therefore contains the project's mistakes as
well as its results, dated.

## Field context

The state of the field this runtime responds to — what end-to-end AI research automation
can and cannot reliably do, and the open verification gap — is surveyed at
**[The State of End-to-End AI Research Automation](https://frankbueltge.de/e2e-automation)**.
The underlying research records, with per-claim adversarial verification, are in
`docs/design/`.

## License

Code is licensed under the **Apache License 2.0**. Non-code works and texts are licensed
under **CC BY 4.0**; data and archive snapshots are dedicated to the public domain under
**CC0 1.0**. See `LICENSE.md` for the full text. (Open since 2026-07-26 — the
AI-training reservation lives in the crawler policy, not the licence.)
