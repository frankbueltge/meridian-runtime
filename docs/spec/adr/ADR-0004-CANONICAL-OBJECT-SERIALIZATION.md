# ADR-0004 — Canonical object serialization for hashing and signing

Status: proposed (open question — surfaced by E2-T02, decision deferred)

## Context

E1-T02 established `mrr.domain.hashing_policy` with `prepare_for_hash` and
`prepare_for_signature`, which operate on *a dict the caller supplies*. Neither
the domain model (docs/spec/02 section 1.2/1.3) nor E1-T02 pins HOW that dict is
produced from an object — specifically, whether absent optional fields appear as
explicit JSON `null` or are omitted.

E2-T02 made a concrete choice and documented it: the Node Manifest signature is
computed over `NodeManifest.model_dump(mode="json")`, which INCLUDES optional
fields as `null` (e.g. `supersedes: null`, `labels: null`). The persisted object
`body`, by contrast, is built with `exclude_none=True` so it stays schema-valid.
The registry is internally consistent — it signs and verifies the same way — and
CI is green. But two gaps remain:

1. **Cross-language / cross-implementation interoperability (E5 federation).**
   The "include nulls via Pydantic `model_dump`" convention is Python- and
   Pydantic-specific. An external or non-Python signer would not know to emit
   null keys before RFC 8785 canonicalization, and its otherwise-valid
   signatures would be rejected. Federation (E5) needs a language-neutral rule
   for which keys are present before canonicalization.

2. **Signed payload vs. persisted body vs. content hash.** The signed payload
   (nulls included) and the persisted body (nulls excluded) are different byte
   strings for the same object. Whichever convention a signer used to compute
   the object's own `content_hash` must match whatever a verifier recomputes, or
   content-hash verification of a stored object will not reproduce the stored
   value. This convention is currently unpinned.

RFC 8785 canonicalizes key ORDER and number formatting, but it does not decide
key PRESENCE — null-inclusion is a pre-canonicalization decision that sits
upstream of the E1-T02 primitives.

## Decision (proposed — not yet accepted)

Pin one canonical pre-canonicalization form for every first-class MRR object,
used identically for content hashing, signing, and verification. Candidate rule:

> The canonical object is the JSON object containing exactly the fields defined
> by the object's schema that are present with a non-null value; optional fields
> that are absent or null are OMITTED (not emitted as `null`). Signature and
> transport-only fields are then excluded per the existing `prepare_for_hash` /
> `prepare_for_signature` policy, and the result is canonicalized per RFC 8785.

This would make the signed payload, the persisted body (`exclude_none=True`), and
the content-hash input the SAME byte string, and would be reproducible by any
implementation that can emit schema-conformant JSON.

## Consequences (if accepted)

- E2-T02's registry would sign/verify over the `exclude_none=True` form instead
  of the null-including form; its tests and any stored fixtures would need to be
  regenerated. Small, localized change.
- Content hashing, signing, and persistence would share one definition of "the
  object's bytes" — closing gap 2 before it can silently corrupt hash checks.
- Federation (E5) gets an implementation-neutral rule to build on.

## Status note

Left `proposed` deliberately: this is a cross-cutting serialization decision that
touches E1-T02, E2-T02, and the whole E5 federation path. It should be accepted
(and E2-T02 aligned) before E5 signed-manifest exchange is built, not
retrofitted after external signatures exist. It does not block the remaining E2
single-node tasks, which control both sides of every signature.
