# 08 — Research Method Kernel

**Status: PROPOSED.** This document is a draft specification awaiting the project owner's
approval. Nothing in it is implemented; no task packet may cite it as source of truth
until its status is `accepted`. It supersedes the external v0.2.0 package's docs 08–19 as
the normative statement of the method layer for THIS repository (mapping in §9).

## 1. Position in the architecture

Three things are deliberately not the same:

1. **Meridian** — the research practice, with its local epistemic commitments.
2. **Meridian Research Runtime** — this infrastructure: governance, execution, provenance,
   federation, correction. It is not itself a researcher.
3. **Research Method Profiles** — versioned, local methodological engines that define how
   a given kind of question is investigated.

A Method Profile is **an executor task family plus a rulebook, not a researcher**. It
plugs into the runtime's existing executor boundary (the `Executor` protocol and the
capability-based dispatch layer, which this kernel introduces). It adds **no new model
authority anywhere**: model outputs remain proposals under the existing E4 rules, and all
profile work flows through the unchanged governance chain
Research Score → Task Bundle → Run Manifest → Evidence Crate.

A new Practice is not required for a new profile. A new Practice is warranted only by a
genuinely different epistemic regime, not by a new software module.

## 2. The Method Profile interface

Every Method Profile MUST declare, in a versioned `MethodProfile` object:

- `profile_key` and semantic version;
- the claim types it can produce and the **maximum claim ceiling** it can ever license;
- the **protocol form** that must be locked before its confirmatory work;
- its **executor task family** (capability names the dispatch layer routes to);
- its deterministic steps vs. model-assisted steps (per MRR-MTH-016);
- its inappropriate uses (questions this profile must refuse).

## 3. Method-governance objects (Layer 1)

New first-class object kinds. Each one composes `common.schema.json#/$defs/baseObject`
exactly like every existing MRR object — identity, revision, content hash, signature,
`supersedes`, `labels`. A method object without the baseObject contract is invalid.

| Kind | Purpose | Lifecycle |
|---|---|---|
| `MethodProfile` | §2 declaration | draft → accepted → superseded |
| `QuestionModel` | decomposition of a question: claim type sought, population/scope/time, load-bearing terms flagged for operationalization | draft → accepted → superseded |
| `ConceptCharter` | versioned local operationalizations of load-bearing terms; a local map, never a global ontology | draft → accepted → superseded |
| `MethodProtocol` | the lockable commitment document of a profile (a synthesis protocol, a pre-analysis plan, …): criteria, rules, analyses, kill conditions | draft → reviewed → **locked** → amended \| executed |
| `EvidenceMatrix` | structured evidence: one row per source×claim-relevant finding, each row anchored to a resolvable `SourceRecord`/`EvidenceAnchor` with verification status and source family | draft → active → frozen → superseded |
| `MethodRuling` | the ruling that licenses claim language: which ceiling applies to which branch, and why; issued deterministically where rules suffice, by attributed human review where they do not | pending → issued → superseded |
| `ResearchDecision` | adaptive decision record: continue, revise, narrow_scope, kill_branch, replicate, escalate_human_review, stop_insufficient_evidence | issued (append-only) |

Causal-profile contracts (`Estimand`, `CausalModel`, `ResearchDesign`,
`IdentificationAudit`, `FalsificationPlan`, `ReplicationPlan`, `GeneralizationMap`) are
**Layer 3**: specified only when a causal profile packet is derived, as
propose-plus-human-ruling forms, and not before (§7).

**Edges.** The claim/evidence graph carries the method linkage through the existing typed
edge layer. `EDGE_VOCABULARY` is extended by exactly four types (one migration):

- `operationalizes` — ConceptCharter entry → QuestionModel term;
- `governed_by_protocol` — EvidenceMatrix / Claim / EvidenceCrate → locked MethodProtocol;
- `ruled_by` — Claim → MethodRuling;
- `decided_by` — a killed or stopped branch's objects → ResearchDecision.

**Error codes** (canonical; the acceptance features MUST use exactly these):
`CLAIM_CEILING_EXCEEDED`, `PROTOCOL_NOT_LOCKED`, `PROTOCOL_LOCK_VIOLATION`,
`SYNTHETIC_FIXTURE_NOT_EVIDENCE`, `KILL_CONDITION_TRIGGERED`,
`REPLICATION_NOT_INDEPENDENT`.

## 4. Normative requirements

Claim ceilings, ordered weakest-language-permitted first:

```
insufficient_evidence < mechanism_hypothesis < descriptive
  < associational_unadjusted < associational_adjusted
  < causal_local < causal_bounded
```

- **MRR-MTH-001** A question addressed through the runtime MUST be represented as an
  accepted `QuestionModel` before an executor task for it is negotiated. A raw
  natural-language question MUST NOT directly create an executable analysis task.
- **MRR-MTH-002** Every load-bearing term of an accepted `QuestionModel` MUST reference a
  versioned `ConceptCharter` operationalization.
- **MRR-MTH-003** Every profile MUST declare its maximum claim ceiling; no claim produced
  under a profile may exceed it.
- **MRR-MTH-004** Every claim produced under a profile MUST carry a `ruled_by` edge to a
  `MethodRuling`; the claim service MUST reject claim language above the ruled ceiling
  with `CLAIM_CEILING_EXCEEDED`, at submission and at projection rendering.
- **MRR-MTH-005** Statistical significance, model confidence, or output fluency MUST NOT
  raise a ceiling.
- **MRR-MTH-006** A synthesis or descriptive design MUST NOT license causal language;
  its maximum ceiling is `associational_unadjusted`.
- **MRR-MTH-007** Confirmatory work requires a **locked** `MethodProtocol`. Locking binds
  the exact content hash, actor, and time. Executor tasks for confirmatory branches MUST
  reference the lock hash and fail with `PROTOCOL_NOT_LOCKED` otherwise.
- **MRR-MTH-008** Post-lock changes MUST be amendments: a new revision recording reason,
  actor, and whether outcome information had been observed. An outcome-informed amendment
  demotes affected analyses to exploratory. Silent overwrite MUST be impossible.
- **MRR-MTH-009** Exploratory work is permitted, MUST be labeled exploratory, and MUST
  NOT appear as confirmatory retroactively.
- **MRR-MTH-010** Every `MethodProtocol` MUST declare at least one kill condition. A
  triggered kill condition MUST deterministically transition the affected branch and its
  dependent claims (`KILL_CONDITION_TRIGGERED` as a state transition and domain event,
  never as report text). Killed branches remain addressable and inspectable.
- **MRR-MTH-011** `insufficient_evidence` outcomes and `stop_insufficient_evidence`
  decisions are successful terminal results. Projections MUST render them as findings,
  never as errors, and they MUST NOT be silently omitted from any claim landscape.
- **MRR-MTH-012** Synthetic fixtures MUST be technically incapable of entering empirical
  evidence paths: fixture-classified inputs propagate their classification, and any
  attempt to derive an empirical claim from them fails closed with
  `SYNTHETIC_FIXTURE_NOT_EVIDENCE`.
- **MRR-MTH-013** Model outputs inside a profile are proposals. Authoritative transitions
  happen only through schema validation, deterministic domain rules, policy, and
  attributed human or independent review events (unchanged E4 discipline).
- **MRR-MTH-014** Replication independence MUST be assessed as a seven-dimension vector —
  principal, code path, model lineage, **prompt lineage**, retrieval path, data path,
  execution node — and the replication type MUST be **derived** from that vector by a
  published deterministic rule. An identical code-and-data rerun is computational
  reproduction and MUST NOT count as independent replication
  (`REPLICATION_NOT_INDEPENDENT`).
- **MRR-MTH-015** Every `EvidenceMatrix` row MUST anchor a resolvable source with a
  verification status and a source family; unverifiable rows are marked, never dropped;
  copied or derivative sources MUST NOT count as independent evidence.
- **MRR-MTH-016** A profile MUST declare which executor steps are deterministic and which
  are model-assisted; every model-assisted step records a `ModelInvocation` and a
  verification disposition (verified / downgraded-to-proposal / rejected).
- **MRR-MTH-017** Claims above `descriptive` MUST carry an explicit scope of validity and
  at least one named non-applicability condition.
- **MRR-MTH-018** Where a protocol declares sensitivity analyses over classifications
  (party families, cluster taxonomies, period boundaries), the varied classifications
  MUST be versioned charter entries, and results under each variation MUST be reported.
- **MRR-MTH-019** Profile activation, protocol lock, ruling issuance, kill triggers, and
  research decisions MUST each emit a domain event on the existing envelope.
- **MRR-MTH-020** The kernel MUST NOT introduce any path around Research Score approval,
  Task Bundle negotiation, Run Manifests, or Evidence Crate sealing.

## 5. Profile: `systematic_evidence_synthesis` v1 (the first profile)

Maximum ceiling: `associational_unadjusted`. Protocol form: **synthesis protocol** —
corpus definition as content-hash-pinned snapshots, inclusion/exclusion criteria,
extraction fields, independence rules, per-status claim-eligibility rules (minimum
independent source families per status), sensitivity variations, kill conditions
(e.g. "fewer than N included sources → stop_insufficient_evidence").

**I/O contract of the executor task family:**

```
in:   QuestionModel (accepted) · ConceptCharter (accepted)
      MethodProtocol (locked)  · corpus snapshots (content-hash-pinned)
      TaskBundle (negotiated under an approved Research Score)
out:  EvidenceMatrix (rows source-anchored, verification-statused)
      Claims (each ruled_by a MethodRuling; ceiling-capped per MTH-006)
      ResearchDecision (incl. possible stop_insufficient_evidence)
      RunManifest + sealed EvidenceCrate (unchanged E2/E5 machinery)
```

Deterministic steps: snapshot loading and hash verification; machine-checkable inclusion
filtering; matrix assembly; independence validation (existing validator); eligibility and
ceiling rules; crate sealing. Model-assisted steps (each per MTH-016): extraction and
classification proposals against protocol-declared fields, verified against the anchored
source or downgraded to marked proposals.

Claim statuses produced: `supported_within_scope`, `partially_supported`, `contested`,
`unsupported`, `insufficient_evidence`.

**First corpus and first question.** The profile's first real inputs are the two existing
human-curated evidence ledgers — the theory atlas (87 provenance-verified sources) and
the works atlas (214 verified data/AI artworks) — and its first question is whether works
catalogued under AI-self-consumption actually instantiate the model-collapse mechanism
the technical literature describes, or merely reference it. These corpora are external,
human-curated ground truth by construction, which is the model-collapse mitigation this
architecture depends on.

## 6. Qualitative and mixed methods

Not specified here. A qualitative profile is expected to be the Method-Profile reframing
of epic E7 and will be specified in its own packet series when E7 is derived. Declaring
this openly replaces the external package's uncovered qualitative requirements.

## 7. Layer 3 — causal profiles: contracts later, engines not scheduled

When (and only when) a causal profile packet series is derived, its objects enter as
**propose-plus-ruling forms**: models and humans may draft estimands, causal models,
designs, and identification audits; every audit `pass`/`downgrade`/`fail` that licenses
or denies causal language is an attributed **human ruling** (`MethodRuling`), not a
computed verdict. The following are explicitly **out of scope and not scheduled**:
autonomous identification-validity certification, autonomous DAG correctness judgment,
autonomous analysis compilation over external real-world datasets, and any component
whose correctness is itself an open research problem. If such a component is ever
proposed, its packet MUST label it frontier work with unbounded uncertainty; presenting
it as bounded engineering is a specification violation.

## 8. Tiering and honesty annex

| Component | Nature | Uncertainty |
|---|---|---|
| L1 governance objects, edges, ceilings, locks, kills (§3–4) | bounded engineering on existing machinery | low |
| Executor dispatch layer (capability → executor) | bounded, already designed for | low |
| `systematic_evidence_synthesis` v1 (§5) | bounded; model-assisted steps verified | low–medium |
| Causal contracts as human-ruled forms (§7) | bounded contracts, uncertain usefulness | medium |
| Causal/falsification/replication **engines**, data scouting over real external data | open research problems + data swamp | high — not scheduled |

## 9. Relation to the external v0.2.0 package

Adopted with rework: claim-ceiling taxonomy; insufficient-evidence-as-success; plan
locking with amendments (generalized to `MethodProtocol`); mandatory kill conditions;
synthetic-fixture isolation; the four acceptance features (error codes corrected to §3's
canonical set); the housing-affordability case as future fixture material; requirement
intents remapped as MRR-MTH-001–020 above.

Rejected: the greenfield monorepo layout and its task-packet paths; the 14 schemas that
drop `baseObject` composition (all kernel objects here MUST compose it); the prose-only
edge types (replaced by four `EDGE_VOCABULARY` additions); the M0–M7 synthetic-only build
order (replaced by the light-first plan in
`docs/design/2026-07-21-research-method-kernel-plan.md`); the seven-vs-six replication
dimension inconsistency (seven, with prompt lineage, per MTH-014); the undefined
replication-type classification (must be derived, MTH-014); duplicated method-level
verification/skepticism organs (existing E3/E4 organs are reused).
