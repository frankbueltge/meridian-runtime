# ADR-0011 — ReleaseRecord and the A4 publication-approval event

**Status:** ACCEPTED (2026-07-22)
**Deciders:** project owner via the standing build-program delegation of 2026-07-22
(docs/design/2026-07-22-verifikations-entscheidung-und-bauprogramm.md, Entscheidung 2:
"E8 komplett"); accepted by the main session as reviewing instance because E8-T04
("publication approval and immutable release bundle") is impossible to implement
without an object kind the domain model does not define, and AGENTS.md rule 14 forbids
guessing one inside an implementation packet. **This ADR defines the MECHANISM of the
A4 gate only. It approves no release. Every actual release requires the owner's own,
explicit, per-release approval input at release time — that is the entire point of the
mechanism, and nothing in this ADR or its packets pre-supplies it.**
**Technical story:** Stage 11 (MRR-FR-101/-102) and the E8 exit criterion ("external
publication is impossible without A4 approval") name an approval event and durable
releases; docs/spec/02_DOMAIN_MODEL.md §2 defines no release or approval object.

## Context

- **MRR-FR-101** defines what a publication bundle must contain; **MRR-FR-102**: "External
  publication MUST require an A4 human approval event"; Stage-11 acceptance: "An
  unapproved bundle cannot be published through any first-party connector" and "Removing
  a claim from the graph removes it from regenerated projections without altering
  historical releases" — historical releases must therefore be durable records, not
  regenerable projections.
- The autonomy model (§5) marks `publish` as **A4 — External Act — explicit human or
  dual approval**; **MRR-FR-110** forbids inferring A4 permission from lower levels;
  **MRR-FR-112** defaults unclassified actions to deny.
- Existing machinery that does NOT fit: `ResearchDecision.decision_type` is spec 08 §3's
  closed seven-value method-kernel vocabulary (continue/revise/…) — repurposing it for
  publication approval would put method-kernel semantics on an A4 act (rule 3 violation).
  `EvidenceCrate` seals RUN output, not publication output. No schema carries an approval.

## Decision

1. **New object kind `ReleaseRecord`** (schema `schemas/release-record.schema.json`,
   contract `mrr.contracts.ReleaseRecord`), persisted in the generic object store like
   every sibling kind. Required fields beyond `baseObject`: `kind` (const
   `ReleaseRecord`); `crate_id` (the sealed EvidenceCrate the release is rooted on);
   `disclosure` (`internal` | `public` — the report projection actually rendered);
   `bundle` (the deterministic content manifest: relative path + `sha256:<hex>` per
   file, sorted, plus `root_hash` = sha256 over the sorted per-file hash lines — the
   bundle's own identity); `approval` (object: `approved_by` — a **person** URN, never
   an agent-role; `approval_statement` — the human's own words, non-empty;
   `approval_mode` — `single_human` | `dual`, this practice uses `single_human` and the
   value is recorded, not defaulted silently); `status` (`released` | `superseded`).
   Lifecycle: `released -> superseded` is the ONLY transition (E8-T05 drives it);
   release records are never edited, deleted, or re-released.
2. **The A4 approval event is the `release.approved` domain event**, written atomically
   with the ReleaseRecord revision-1 insert (the existing one-revision-one-event
   unit-of-work primitive). Its `actor` is the approving **person URN** — the human act
   is the event; no service, model, or CLI default can stand in for it. The CLI
   (`mrr release create`) therefore has NO default for `--approved-by` and
   `--approval-statement-file`; absence is a refusal (MRR-FR-112 default-deny), and the
   flags' help text states that supplying them IS the recorded human act.
3. **The bundle bytes are assembled deterministically** from already-shipped machinery —
   the E8-T01/T02 RO-Crate export and the E8-T03 report renders — into one directory;
   the ReleaseRecord stores the manifest and hashes, the object store keeps holding the
   underlying content-addressed artifacts, and git/dist keeps the record. Rebuilding a
   bundle from a ReleaseRecord and the archive must reproduce the `root_hash` or fail
   loudly — that is the "immutable release" acceptance made checkable.
4. **No publishing connector exists and none is built by E8** — "external publication"
   remains impossible in this codebase not by a guard but by absence; the ReleaseRecord
   is the precondition any future connector MUST verify (and such a connector is its own
   A4-classified, owner-approved future work).

## Consequences

- E8-T04 implements: schema + contract + `ReleaseService` (create; the atomic approval
  event) + bundle assembly + `mrr release create` + `mrr release verify` (rebuild and
  compare `root_hash`). E8-T05 implements the correction banner and
  `released -> superseded`.
- The domain model gains a kind not in 02_DOMAIN_MODEL.md §2; this ADR is the record of
  that extension (the spec file itself is not edited by an implementation packet — a
  future spec revision folds it in).
- Nothing in the existing archive changes; no release exists until the owner creates one.
