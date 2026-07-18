# ADR-0006 — Lifecycle status is excluded from content hash and signature

Status: WITHDRAWN (2026-07-18) — superseded by ADR-0007 before implementation.

## Withdrawal note

This ADR treated "exclude `status` from the hashed/signed payload" as the fix
for signatures breaking across Task Bundle lifecycle transitions. During
implementation the helper agent showed the premise is incomplete: a transition
also advances the object `revision` (a core identity field, part of the signed
payload), so excluding `status` alone does NOT stabilize the signature — the
revision increment reintroduces the same instability. Excluding revision too
would unravel the "signature covers the content hash" property (§1.3).

The correct root cause is deeper and narrower: only the Task Bundle is both
lifecycle-bearing AND signed (ResearchScore, Claim, CorrectionEvent carry a
status but have no signature field, so treating their transitions as new
revisions is harmless). ADR-0007 fixes it at the right lever — Task Bundle
lifecycle transitions become domain events, not new signed revisions — so a
signed bundle revision is immutable and its signature always verifies against
the current content. The hashing_policy change this ADR proposed is not made;
its work-in-progress branch is discarded. The text below is retained for the
record.

---

Status (original): accepted (2026-07-18) — see withdrawal note above.

## Context

ADR-0005 gave TaskBundle a schema `status` field so its persisted body is
schema-valid. Because `mrr.domain.hashing_policy` computed the content hash and
the signed payload over every field except `content_hash`/`signature`, `status`
fell UNDER both the content hash and the origin signature. But a Task Bundle's
status changes on every lifecycle transition, while its origin signature is
produced once. The E2-T03 implementation reconciled this by carrying the
signature forward and verifying it against the historical revision it was made
for (`_find_signed_revision`, a scan for the oldest revision bearing the same
`signature.value`).

That reconciliation has a verification-semantics weakness: it proves "some
ancestor revision was validly signed", not "the current revision's content
matches what was signed". A later revision whose substantive content was altered
while the old `signature.value` was carried forward would still find an
untampered signed ancestor and pass — the current content would never be checked
against the signature. It is not exploitable through the E2-T03 service API in
the single-node slice (nothing executes the bundle yet, and tampering requires
bypassing the service), but it becomes load-bearing at E2-T04 (execution
consumes the current revision) and E5 (signed objects cross practice
boundaries). It also sits in tension with MRR-FR-034 ("a task revision MUST
receive a new content hash and signature"): carrying a signature across status
transitions means a "revision" without a new signature.

## Decision

Treat lifecycle `status` as lifecycle metadata, not substantive content: exclude
`status` from BOTH the hashed payload and the signed payload in
`mrr.domain.hashing_policy` (alongside the already-excluded `content_hash` and
`signature`).

Consequences of the rule:

- The content hash and the signature cover an object's substantive, immutable
  content, not its lifecycle position. A signature therefore verifies directly
  against the CURRENT revision regardless of its status — no historical-revision
  scan — and any substantive content change IS detected.
- A pure status transition is a lifecycle event that preserves the content hash
  (and lets the existing signature keep verifying). It still creates a new
  append-only revision for history; the revision number and the store row stay in
  lockstep (ADR-0005), but the content hash repeats across status-only revisions,
  which is correct because the content did not change.
- A genuine content change (TaskBundle `propose_modification`) changes the
  content hash and requires a new signature by the modifying party — MRR-FR-034
  satisfied cleanly, with no carried-forward signature.

## Scope

Applies to all lifecycle-bearing objects uniformly (ResearchScore, TaskBundle,
Claim, CorrectionEvent). NodeManifest has no status field and is unaffected. The
exclusion is implemented once in the shared `hashing_policy` so it cannot drift
per object.

## Consequences and follow-through

- Refines ADR-0005: status is still a schema field and transitions still create
  revisions, but status is not part of content identity.
- E2-T03 verification simplifies to verify-against-current-revision;
  `_find_signed_revision` is removed.
- The already-merged E2-T01 ResearchScore service is aligned: status-only
  transitions now preserve the content hash (ResearchScore is not
  cross-practice-signed, so only the hash behavior changes). Its tests are
  updated accordingly.
- Interacts with the still-proposed ADR-0004 (canonical serialization): both
  concern what bytes constitute "the object" for hashing/signing and should be
  read together when E5 federation is built.
