# ADR-0010 — Object-level data classification for non-TaskBundle objects

**Status:** ACCEPTED (2026-07-21)
**Deciders:** project owner via explicit in-session delegation ("volle Autonomie");
accepted when K1-T02's derivation confirmed the synthetic-fixture gate (MRR-MTH-012)
genuinely requires the baseObject classification slot, joining the two earlier
independent findings. Additive and fail-closed: nothing weakens while objects do
not yet carry the field. Staged adoption per below; step 1 is its own task packet.
**Technical story:** two independent findings converged on the same gap on the same day.

## Context

The data-classification vocabulary (docs/spec/02_DOMAIN_MODEL.md §4: `PUBLIC`,
`INTERNAL`, `RESTRICTED`, `SENSITIVE`, `PARTICIPANT_IDENTIFIABLE`) is normative, but only
two places in the contracts can carry it today: `TaskBundle.classification` (required)
and the optional `artifactRef.classification` in `common.schema.json`. No other object
kind — `Claim`, `SourceRecord`, `EvidenceAnchor`, `CorrectionEvent`, `TransferContract`'s
referenced objects — has any classification slot.

Two enforcement points already need one:

1. **E6-T01 (merged, #41):** `TransferService.create()` rejects transfers referencing
   `PARTICIPANT_IDENTIFIABLE` objects (MRR-NFR-006) — but can only see a top-level
   `classification` field in the stored body, which only TaskBundle has. The gate is
   honest but practically a no-op for the object kinds transfers will actually
   reference. (Self-disclosed in the implementation; confirmed by independent review.)
2. **E6-T05 (derived):** the public unresolved-correction projection cannot decide
   "public" from stored state at all and falls back to a caller-supplied attestation
   map with fail-closed redaction.

A third consumer is coming: the Research Method Kernel's synthetic-fixture isolation
(docs/spec/08_RESEARCH_METHOD_KERNEL.md, MRR-MTH-012) needs a machine-checkable marker
that test fixtures can never become empirical evidence — the same kind of
object-level classification, one more value.

## Decision

Add one **optional** `classification` property to `common.schema.json#/$defs/baseObject`
(and `BaseObject` in `packages/contracts/mrr/contracts/common.py`), with the §4
vocabulary plus one new value:

```
classification?: PUBLIC | INTERNAL | RESTRICTED | SENSITIVE
                | PARTICIPANT_IDENTIFIABLE | SYNTHETIC_TEST_FIXTURE
```

Semantics:

- **Absent means unclassified, and unclassified is never a pass.** Every consumer MUST
  fail closed: an unclassified object never satisfies a PUBLIC gate, never satisfies a
  "known classification" requirement, and is redacted by public projections exactly like
  a non-PUBLIC one. Absence is compatible with every existing stored object (no
  migration, no re-signing — the field is additive and optional).
- `SYNTHETIC_TEST_FIXTURE` marks fixture-derived objects; MRR-MTH-012's gate rejects any
  attempt to derive an empirical claim from such an object
  (`SYNTHETIC_FIXTURE_NOT_EVIDENCE`), and the classification propagates to derivatives.
- Classification of an object is asserted at creation by its creating service and is
  part of the signed/hashed body like any other field; it is not a mutable label.

Staged adoption (each its own task, no big-bang):

1. Schema/contract addition (additive; all existing examples/fixtures stay valid).
2. E6-T05's projection consumes the stored field where present; the attestation map
   remains as an override/bridge for legacy objects.
3. A follow-up task hardens `TransferService.create()`: first accept stored
   classifications when present (closing the practical no-op), later — separately
   decided — require known classification for transfer-referenced objects.
4. K1-T02 uses `SYNTHETIC_TEST_FIXTURE` for the kernel's fixture-isolation gate.

## Alternatives considered

- **Required field on every schema** — breaks every existing object and example, forces
  re-signing history, and overstates what we know about old objects. Rejected.
- **`labels.classification`** — labels are unsigned-adjacent, weakly typed, and
  deliberately non-semantic; policy MUST NOT hang on them. Rejected.
- **External attestation only (status quo of E6-T05)** — keeps schemas frozen but has no
  single source of truth, pushes the burden to every caller forever, and cannot serve
  the transfer gate or the synthetic-fixture gate. Rejected as the end state; retained
  as the bridge.

## Consequences

- One additive schema/contract change serves three consumers (transfer gate, public
  projection, kernel fixture isolation) with one vocabulary.
- Fail-closed-on-absent means adopting the field never weakens any gate; it can only
  tighten behavior as objects start carrying classifications.
- The `SYNTHETIC_TEST_FIXTURE` value intentionally lives in the same enum: "this is not
  empirical" is a disclosure class, and keeping one vocabulary prevents two parallel
  marker mechanisms from drifting.
