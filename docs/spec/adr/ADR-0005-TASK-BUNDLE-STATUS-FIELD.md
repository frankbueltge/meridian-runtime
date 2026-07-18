# ADR-0005 — Add a status field to the Task Bundle schema

Status: accepted (2026-07-18, decision delegated by the repository owner)

## Context

`docs/spec/01_SYSTEM_SPEC.md` section 6.2 defines a Task Bundle lifecycle with
fourteen states (CREATED, OFFERED, ACCEPTED, MODIFICATION_PROPOSED, DEFERRED,
REJECTED, QUEUED, EXPIRED, CANCELLED, RUNNING, FAILED, COMPLETED, SEALED,
INVALID_RESULT). Three of the four lifecycle-bearing objects — ResearchScore,
Claim, CorrectionEvent — carry their current lifecycle state in a schema-defined
`status` field. TaskBundle alone does not: `schemas/task-bundle.schema.json` has
no `status` field. E1-T04 already flagged this asymmetry (its lifecycle
drift-protection test had to exclude TaskBundle for lack of a schema enum).

E2-T03 surfaced the concrete cost. To persist a bundle's lifecycle state it added
`status` as an extra key on the stored object body. Because every entity schema
sets `unevaluatedProperties: false`, that body is NOT schema-valid and does NOT
round-trip through the `TaskBundle` Pydantic model — a direct violation of the
E1-T03 invariant that persisted objects validate against both their JSON Schema
and their contract model. It also forced a second anomaly: the persistence store's
row-revision counter diverging from the bundle's own `revision`, since status
flips were stored as rows without being modeled as revisions.

## Decision

Add a required `status` field to `schemas/task-bundle.schema.json` whose enum is
exactly the fourteen TASK_BUNDLE_LIFECYCLE states, and mirror it in the
`TaskBundle` contract model — making all four lifecycle-bearing objects uniformly
carry their state in a schema-defined field.

Consequently, a Task Bundle lifecycle transition is a genuine new object revision
(the `status` field changed), exactly as it already is for ResearchScore. This
keeps the contract-model revision and the persistence-store revision in lockstep
and eliminates E2-T03's out-of-band status key.

## Consequences

- The persisted Task Bundle body is schema-valid again and round-trips through
  the contract model; exports (E8) and federated validation (E5) can treat it
  like every other object.
- TaskBundle joins the E1-T04 lifecycle drift-protection test (its state set is
  now checkable against a schema enum), closing the exclusion that task noted.
- The revision-counter divergence disappears: one revision per transition.
- This is a schema addition, not a semantic change to existing fields; existing
  example fixtures gain a `status` value. It is realized as a standalone
  contracts amendment (schema + model + example + drift test) that E2-T03 then
  builds on, so E2-T03's persisted bodies are valid from the first merge.
- Scope note: only TaskBundle is affected. NodeManifest deliberately keeps no
  status field — its validity is temporal (valid_from/valid_until), not a state
  machine.
