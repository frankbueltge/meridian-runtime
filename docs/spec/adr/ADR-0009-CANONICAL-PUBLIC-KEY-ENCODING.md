# ADR-0009 — Canonical public-key string encoding

Status: accepted (2026-07-20, decision delegated by the repository owner per the
ADR-0003 precedent)

## Context

A public key is stringified in two places, both typed as plain strings with no
pinned encoding:

- `NodeManifest.public_keys` (`list[str]`, schema `{"type": "string"}`, no format
  constraint) — docs/spec/02_DOMAIN_MODEL.md section 2.2;
- `Practice` / `PublicKeyDescriptor.encoded_public_key` (E5-T01) — the key a kid
  is derived from and a signature is verified under.

Three incompatible forms emerged for the SAME 32-byte Ed25519 key:

1. `did:key:z6Mk…` — the docs/spec/02 section 2.2 example and the committed
   `examples/node-manifest.example.json` placeholder. No DID/multibase decoder
   exists in the codebase (`_public_key_reference`'s own docstring notes this).
2. `ed25519-raw-base64:<base64>` — the production manifest builder
   (`_public_key_reference` in `services/.../cli/orchestration.py`), a
   self-describing prefix + standard base64 of the raw 32 bytes.
3. `<base64>` (plain) — E5-T01's `mrr.crypto.keys.encode_public_key` and every
   `PublicKeyDescriptor.encoded_public_key`.

E5-T02's trust resolver condition (d) — "the descriptor's public key is one of
the manifest's declared `public_keys`" — compares `descriptor.encoded_public_key`
(form 3) against `manifest.public_keys` by STRING equality. That only ever
matches form 3, so a manifest built by the production builder (form 2) or carried
by the example (form 1) is wrongly rejected as `key_not_declared`, even though it
is legitimate. The E5-T02 tests are green only because they construct manifests
with form-3 `public_keys`; the production encoding is never exercised. This is a
fail-closed-safe defect (it over-rejects), but it would block E2E-002 the moment
`receive()` is wired to real, production-built manifests, and federation needs
ONE comparable encoding for keys crossing node boundaries.

## Decision (accepted)

The canonical public-key string is the standard base64 (RFC 4648 section 4, with
padding) of the raw 32 Ed25519 public-key bytes — the exact convention ADR-0003
already pins for `signature.value`. It is used identically in
`NodeManifest.public_keys` and `PublicKeyDescriptor.encoded_public_key`.

The algorithm is known from context — every `signature` carries
`algorithm: "Ed25519"` and `Ed25519` is the single supported algorithm
(`mrr.crypto.signatures.SUPPORTED_ALGORITHMS`) — so a self-describing prefix is
unnecessary, and a bare base64 blob is unambiguous today. `did:key` is NOT used
until a resolver for it exists; introducing one is a later ADR.

Key comparison is by KEY IDENTITY, not string identity: two encodings of the same
key ARE the same key. Condition (d) therefore compares the descriptor's key
against each `manifest.public_keys` entry by DECODING both to their raw 32 bytes
(via `mrr.crypto.keys.decode_public_key`) and comparing bytes; an entry that does
not decode to a valid Ed25519 key simply does not match (fail closed). This keeps
(d) correct even against padding/whitespace drift, and makes any future encoding
mismatch fail loudly in a test rather than silently over-reject.

## Consequences

- `_public_key_reference` (the production manifest builder) drops its
  `ed25519-raw-base64:` prefix and emits the canonical plain base64 (i.e. reuses
  `mrr.crypto.keys.encode_public_key`); the `examples/node-manifest.example.json`
  `did:key:` placeholder becomes a real plain-base64 key. E5-T01 descriptors
  already comply and are unchanged.
- E5-T02's condition (d) is hardened to compare decoded raw key identity, so it
  accepts a legitimately-declared key regardless of the base64 spelling and
  rejects a genuinely-absent one. A test exercises the production-built encoding
  end to end.
- No `kid`, content-hash, or signature semantics change: a kid derives from the
  raw public-key bytes (not the string), and hashing/signing already canonicalize
  the object independently of this string's spelling.
- Federation gets one implementation-neutral public-key encoding, consistent with
  ADR-0003's signature-value encoding.

## Status note

Accepted now and applied by the follow-up packet E5-T02b, which reconciles the
production builder and the example to the canonical encoding and hardens the
E5-T02 condition-(d) comparison — landing before `receive()` is wired into
E2E-002. E5-T02's trust-anchoring core (the five fail-closed conditions,
verification against the ring's key, the coarse-reason rejected event) is
unchanged and correct; this ADR only fixes which bytes condition (d) compares.
