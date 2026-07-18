# ADR-0003 — Signature value encoding

Status: accepted (2026-07-18, decision delegated by the repository owner)

## Context

`schemas/common.schema.json` defines `signature.value` only as a string with
`minLength: 40`. Neither the domain model (docs/spec/02, section 1.3) nor the
security specification (docs/spec/04, section 8) states how the 64 raw Ed25519
signature bytes are encoded as that string. The example fixtures in
`examples/` all carry 88-character values whose character set is not valid
lowercase hex, which is consistent only with standard base64 with padding
(64 bytes → 88 base64 characters).

E1-T02 implemented standard base64 (RFC 4648 section 4, with padding) on this
inference and flagged the gap in its pull request, as required by AGENTS.md
rule 14. E1-T03 validated the same fixtures. The inference is currently
load-bearing without a normative anchor.

## Decision (proposed)

`signature.value` is the standard base64 encoding (RFC 4648 section 4, with
padding) of the raw 64-byte Ed25519 signature. Verifiers decode with strict
validation and fail closed on any non-base64 input.

On acceptance, `schemas/common.schema.json#/$defs/signature/properties/value`
gains a documentation-only `description` stating this encoding (no semantic
schema change; the `minLength: 40` constraint is unchanged).

## Consequences

- Removes the only encoding ambiguity in the cross-practice signature path
  before federation work (E5) builds on it.
- Existing E1-T02 behavior is confirmed, not changed.
- Interoperability note: base64url is deliberately NOT accepted; a single
  canonical encoding keeps signature strings byte-comparable.
