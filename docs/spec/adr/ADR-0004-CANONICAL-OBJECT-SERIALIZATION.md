# ADR-0004 — Canonical object serialization for hashing and signing

Status: accepted (2026-07-19, decision delegated by the repository owner per the ADR-0003 precedent)

## Context

E1-T02 established `mrr.domain.hashing_policy` with `prepare_for_hash` and
`prepare_for_signature`, which operate on *a dict the caller supplies*. Neither
the domain model (docs/spec/02 section 1.2/1.3) nor E1-T02 pins HOW that dict is
produced from an object — specifically, whether absent optional fields appear as
explicit JSON `null` or are omitted.

E2-T02 made a concrete choice and documented it: the Node Manifest signature is
computed over `NodeManifest.model_dump(mode="json")`, which INCLUDES optional
fields as `null` (e.g. `supersedes: null`, `labels: null`). The same include-null
convention was subsequently adopted by every other signed object — TaskBundle
(E2-T03) and EvidenceCrate (E2-T06) sign and verify the same way — so all three
cross-practice signed objects share this Pydantic-specific form. The persisted
object `body`, by contrast, is built with `exclude_none=True` so it stays schema-valid.
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

## Decision (accepted)

Pin one canonical pre-canonicalization form for every first-class MRR object,
used identically for content hashing, signing, and verification. The pinned rule:

> The canonical object is the JSON object containing exactly the fields defined
> by the object's schema that are present with a non-null value; optional fields
> that are absent or null are OMITTED (not emitted as `null`). Signature and
> transport-only fields are then excluded per the existing `prepare_for_hash` /
> `prepare_for_signature` policy, and the result is canonicalized per RFC 8785.

This makes the signed payload, the persisted body (`exclude_none=True`), and
the content-hash input the SAME byte string, and reproducible by any
implementation that can emit schema-conformant JSON.

## Consequences

Scope correction (the original note understated this): the include-null form is
used by ALL THREE cross-practice signed objects — NodeManifest (sign
`services/.../cli/orchestration.py`, verify `.../capability_registry`),
TaskBundle (sign `.../cli/orchestration.py`, verify `.../task_bundle`), and
EvidenceCrate (sign `.../node_runtime/evidence_crate.py`; its verify path lands
in E5-T05). That is five live call sites today — three sign, two verify.
Accepting this ADR flips all of them to the `exclude_none=True` form and binds
every later verify path (EvidenceCrate from E5-T05 on) to the same form.

- Signing joins the already-universal `exclude_none=True` content-hashing form,
  so content hashing, signing, and persistence share ONE definition of "the
  object's bytes" — closing gap 2 before it can silently corrupt hash checks.
- The committed `examples/*.json` carry placeholder signature values (`BBBB…`,
  `CCCC…`, `DDDD…`), not real Ed25519 signatures, and contract tests validate
  schema SHAPE only — so no committed fixture is regenerated. Only test/e2e
  helpers that construct REAL verifiable signatures switch to the new form.
- Adding a future optional field no longer changes the signed bytes of objects
  that leave it unset (omit-nulls) — safer for schema evolution than the
  include-null form, where every absent optional must be emitted as explicit
  `null` by every signer, including non-Python ones.
- Federation (E5) gets an implementation-neutral rule to build on: any signer
  that emits schema-conformant JSON (absent optionals omitted) reproduces the
  bytes, with no Pydantic-specific null-emission step.

## Status note

Accepted now, as the E5 federation gate: unifying the canonical signed form is a
cross-cutting serialization decision (E1-T02 primitives, the E2-T02/T03/T06
signature paths, and the whole E5 federation path) that MUST be settled before
any object crosses a real node boundary, not retrofitted after external
signatures exist. The alignment is applied by a dedicated precursor packet,
E5-T00, which flips the live sign/verify call sites (three sign, two verify
today; EvidenceCrate verify follows in E5-T05) to the `exclude_none=True`
form and adds a property test that the signed bytes equal the persisted-body
bytes (minus the signature) for every signed object. E5-T01..T07 (the federation
feature tasks) then build on the single unified form. Every new signed object
introduced from E5-T01 on MUST sign/verify over the `exclude_none=True` form
(reuse the persisted-body dict), never a second `model_dump(mode="json")`.

One pre-existing NON-signed residual — the CLI orchestration helper
`_finalize_content_hash`, which computed `ResearchScore.content_hash` over the
null-including form while its persisted body is `exclude_none` — is aligned to
`exclude_none=True` by the completeness follow-up packet E5-T00b, so
`content_hash == hash(persisted body)` holds for EVERY first-class object, not
only the signed three. (E5-T00 itself scoped to the sign/verify paths; E5-T00b
closes the last content-hash path. Not E5-blocking: ResearchScore is not a
cross-practice signed object and is not re-hash-verified until export/E8.)
