# ADR-0007 — Task Bundle lifecycle transitions are domain events, not new signed revisions

Status: accepted (2026-07-18, decision delegated by the repository owner)

Supersedes: ADR-0006 (withdrawn). Refines: ADR-0005.

## Context

A first-class object carries a `revision` and (for cross-practice objects) an
origin `signature`; both `revision` and the derived `content_hash` are part of
the signed payload (docs/spec/02 section 1.2/1.3). MRR-FR-034 states that a task
*revision* MUST receive a new content hash AND a new signature.

E2-T01/T02/T03 modeled every lifecycle transition (approve, offer, accept, ...)
as a new object revision. For the three lifecycle objects that have NO signature
field — ResearchScore, Claim, CorrectionEvent — this is harmless: their content
hash changing per transition matters to nobody, because nothing verifies a
signature over them.

The Task Bundle is different: it is the only object that is BOTH lifecycle-bearing
AND signed (MRR-FR-031). Modeling its transitions as new revisions breaks the
origin signature on the first transition, because the signed payload's `revision`
(and `content_hash`) change while the origin signs only once. The E2-T03 first
implementation worked around this by scanning for the historical revision the
signature was made for (`_find_signed_revision`), which verified "some ancestor
was signed" rather than "the current content matches what was signed" — a
verification-semantics weakness that becomes load-bearing at execution (E2-T04)
and federation (E5). ADR-0006 tried to fix it by excluding `status` from the
signed payload but missed that `revision` also moves; it is withdrawn.

## Decision

For the Task Bundle only, a lifecycle transition (CREATED→OFFERED, OFFERED→
ACCEPTED, OFFERED→DEFERRED, OFFERED→REJECTED, and the execution-side transitions
in later epics) is recorded as an append-only **domain event**, NOT a new signed
object revision. Concretely:

- The origin-signed Task Bundle **content record** is created once at revision 1
  and is immutable. Its signature therefore always verifies directly against the
  current content record — no historical scan, and any content tamper is caught.
- Lifecycle transitions append a domain event (event log, E1-T06) via an
  **event-only** path that does not mint a new object content revision. The
  authoritative **current status** is derived from the latest lifecycle event for
  the bundle (falling back to the content body's creation status when there are no
  transition events yet).
- A genuine content change — `propose_modification` — IS a new content revision
  with a new content hash and a new signature by the modifying party, satisfying
  MRR-FR-034 cleanly (no carried-forward signature).

The status field remains in the schema (ADR-0005 holds); the value stored in a
content record is that record's creation-time status, a historical snapshot. The
live status is always the event-derived one.

## Scope and rationale for the asymmetry

Only the Task Bundle changes. ResearchScore/Claim/CorrectionEvent keep
transitions-as-revisions because they are unsigned, so the signature problem does
not arise and reworking them would be churn without benefit. The asymmetry is
justified by a real difference (signed vs unsigned) and documented here so it is
not mistaken for drift. NodeManifest has no lifecycle status and is unaffected.

## Consequences

- E1-T06 gains a minimal, additive event-only append primitive (append a domain
  event, atomically with its outbox row, without a new object content revision).
- E2-T03 is reworked: transitions use the event path and an event-derived current
  status; `_find_signed_revision` is removed; the origin signature is verified
  against the immutable content record before any node decision; propose_modification
  re-signs. A test asserts the signature still verifies after arbitrarily many
  transitions and that content tamper is rejected.
- The proposed ADR-0004 (canonical serialization) remains open and orthogonal.
- Correctness is now easy to state: a signed bundle record is written once and
  never mutated, so its signature verification is trivially sound.
