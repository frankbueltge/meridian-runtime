# Research Method Kernel — Implementation Plan (light-first)

**Date:** 2026-07-21
**Status:** approved (2026-07-21, owner delegation in session) together with
`docs/spec/08_RESEARCH_METHOD_KERNEL.md`. Task packets are derived one at a time in the
established `task-packets/` idiom (`docs/spec/07_AGENT_TASK_TEMPLATE.md`).
**Sequencing context:** recommended order across epics is
E5 close-out → E6 → **K0–K1** → decide K2 → E7/E8 → E9
(reasoning in `2026-07-21-research-method-kernel-rework.md` §6).

Numbering: `K` epics to keep the method layer visibly distinct from the governance
cathedral (`E` epics). Every K-task follows the same discipline as E-tasks: one packet,
one branch, one PR, all tiers green, no merge without the owner's go-ahead.

---

## K0 — Method Profile interface and executor dispatch (2 tasks)

The smallest slice that makes "profile" a real runtime concept.

- **K0-T01 — MethodProfile contract and registry.**
  `schemas/method-profile.schema.json` composing `baseObject`; Pydantic contract;
  example; registry service storing accepted profiles in the generic `objects` table.
  No migration (generic object store suffices). Acceptance: schema round-trip; a profile
  without max-ceiling or protocol-form declaration is rejected; profile acceptance emits
  a domain event (MTH-019).
- **K0-T02 — Capability dispatch layer.**
  The "future dispatch layer" the executor module already names: select an `Executor`
  implementation by `TaskBundle.capability`, fail closed on unknown capability with a
  typed error, keep `ReferenceTaskExecutor` as the fallback for the existing reference
  capability. Acceptance: dispatch table driven by registered profiles; unknown
  capability produces `policy_denied`-style explicit failure, never a silent fallback;
  existing E2E-001 loop unchanged.

## K1 — `systematic_evidence_synthesis` v1 on the atlases (4 tasks)

- **K1-T01 — Kernel governance contracts.**
  `QuestionModel`, `ConceptCharter`, `MethodProtocol` (with lock/amendment lifecycle),
  `EvidenceMatrix`, `MethodRuling`, `ResearchDecision`: schemas (baseObject-composed),
  Pydantic contracts, lifecycle state machines, examples, contract tests. Lock semantics
  per MTH-007/008 (lock hash binding; amendment revisions; outcome-informed demotion).
  No migration.
- **K1-T02 — Edges, ceilings, and gates.**
  `EDGE_VOCABULARY` + CHECK-constraint migration for `operationalizes`,
  `governed_by_protocol`, `ruled_by`, `decided_by`; claim-service enforcement of
  MTH-004/005/006 (`CLAIM_CEILING_EXCEEDED`); synthetic-fixture classification and the
  fail-closed gate (MTH-012, `SYNTHETIC_FIXTURE_NOT_EVIDENCE`); kill-condition transition
  plumbing (MTH-010) on `MethodProtocol`/branches. Acceptance: the four v0.2.0 Gherkin
  behaviors, re-pinned with the canonical error codes from spec 08 §3.
- **K1-T03 — Synthesis executor task family.**
  The real executor: pinned-snapshot ingestion (content-hash-verified copies of the two
  atlases as corpus fixtures), machine-checkable inclusion filtering, matrix assembly,
  independence validation via the existing validator, eligibility/ceiling rules,
  deterministic `MethodRuling` issuance, `ResearchDecision` including
  `stop_insufficient_evidence`, crate sealing through the unchanged E2/E5 machinery.
  Model-assisted extraction steps go through the existing structured-generation adapter
  and record verification dispositions (MTH-016); v1 MUST be able to run
  **model-free** (extraction fields empty or human-supplied) so the deterministic path
  is provable on its own — mirroring how E2E-001 runs without any LLM.
- **K1-T04 — First real run: the model-collapse question.**
  QuestionModel + ConceptCharter ("instantiates the mechanism" vs. "references the
  theme" is a charter distinction) + locked protocol + run over the pinned atlas
  snapshots → evidence matrix, ceiling-capped claim landscape with honest statuses, and
  a sealed crate. This packet also defines where the output lands for reuse (site
  coupling is Frank's call, out of scope here). Acceptance: the produced claim landscape
  reproduces or improves on the hand-done micro-inquiry's shape (claim table with
  evidence, independence check, honest status per claim); at least one claim MUST be
  capable of ending `contested` or `insufficient_evidence` on the real material — a run
  that can only produce supported claims fails the packet.

**Exit criteria for K1:** a third party can follow one sealed crate from question to
claim landscape entirely through recorded objects; every claim's language is at or below
its ruled ceiling; the protocol lock predates all extraction; the run is reproducible
from the pinned snapshots.

## K2 — decision gate (not scheduled)

After K1-T04's output exists, decide with it in hand whether causal contracts
(propose-plus-human-ruling forms per spec 08 §7) are worth deriving. No causal engine
work is scheduled regardless (spec 08 §7's non-goals). The synthetic
housing-affordability case from the v0.2.0 package becomes fixture material for K2's
contract tests if and when K2 opens.

## Explicitly not in this plan

- Autonomous identification/causal-validity engines, DAG correctness judgment,
  falsification/replication automation, external-data scouting (spec 08 §8, high row).
- The v0.2.0 package's M0–M7 sequence and its 16 packets (superseded by K0–K1).
- Any change to Das Protokoll's deterministic register or other site pieces.

## Effort and uncertainty, honestly

K0+K1 is comparable to one mid-sized E-epic (E3 or E5 scale): ~6 packets, all on known
machinery, all locally testable, no new infrastructure, one small migration (edge
vocabulary). The only genuinely new runtime concept is dispatch-by-capability, which the
executor module was already designed to receive. The model-assisted extraction step is
the only medium-uncertainty element and is packet-isolated (K1-T03) with a required
model-free path, so its failure cannot sink the epic.
