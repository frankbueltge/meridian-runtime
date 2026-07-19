# ADR-0008 — Structured generation must inspect candidate content that the default redaction policy withholds

Status: PROPOSED (2026-07-19) — decision deferred; to be resolved before E4-T05 (executor wiring of model calls).

## Context

E4-T01 gave every model call an auditable record. `ModelInvocationOutcome`
(and the persisted `ModelInvocation`) records the response by SHA-256 hash by
default; raw response text is present only when the request's
`redaction_policy` is `"raw_permitted"`, and the default is `"hashes_only"`
(MRR-FR-045 "subject to local redaction policy"; AGENTS.md rule 11 "no secret
is ever recorded"). This is deliberately safe-by-default.

E4-T02 built structured generation on that port: it must parse the model's
response text and validate it against a caller-supplied Pydantic model, and —
on failure — repair up to a bounded number of times. To validate content, it
needs the content. The only channel the port offers is
`ModelInvocationOutcome.raw_response_text`, which under the default
`"hashes_only"` policy is structurally `None`. A hash cannot be validated
against a schema.

E4-T02 handled this correctly and conservatively: it does **not** silently
escalate the caller's redaction policy (that would weaken privacy without
consent), and it never infers validity from an unobservable candidate.
Instead, a `"completed"` call with no observable text is treated exactly like a
schema-validation failure and recorded as such. The direct consequence: under
the safe default, structured generation deterministically yields
`schema_invalid` and produces no proposals; a caller must pass
`redaction_policy="raw_permitted"` to get real output.

The root tension is that E4-T01's value object conflates two concerns in one
field: **transient working data** the caller needs in-process (the candidate
text, required to validate it) and the **durable audit record** that must be
redactable (hash-only by default). Requiring `"raw_permitted"` to inspect
content forces raw retention in the audit trail as the price of validation —
so every structured call under the current shape either produces nothing or
persists the model's raw output text.

This is not load-bearing yet (nothing runs structured generation inside the
executor in the single-node slice), but it becomes load-bearing at E4-T05
(execution consumes model output) and again at E5 (records cross practice
boundaries).

## Decision

Deferred. Recording the tension and the options; no code changes are implied by
this ADR. E4-T01 and E4-T02 stay as merged. The resolution is chosen before
E4-T05 wires model calls into the executor.

### Options

- **A — Separate transient content from the redacted record.** `invoke` returns
  the candidate text to the caller transiently (always available in-process for
  validation), while the persisted `ModelInvocation` record stays
  redaction-governed (hash-only by default). Decouples inspection from
  retention. Requires revisiting the E4-T01 `ModelInvocationOutcome` shape (its
  own task/ADR), e.g. a transient `candidate_text` distinct from the persisted
  `raw_response_text`.
- **B — In-process-only inspection exception.** The structured layer (or the
  executor) is permitted to inspect raw text in-process for validation even
  under `"hashes_only"`, then discards it; the persisted record remains
  hash-only. Keeps the E4-T01 record shape but adds an explicit, audited
  in-process inspection channel that must be proven never to persist or return
  the text.
- **C — Accept the current behavior.** Structured generation requires
  `"raw_permitted"`; document that structured calls retain raw model output in
  their audit record by necessity. Simplest and already true, but weakens the
  hash-only default for every structured call and puts model output text in the
  audit trail as the normal case.

### Leaning

A or B — the model's proposal must be inspected to be validated, but that
inspection need not force raw persistence. A keeps the privacy boundary at the
record shape (clean, but touches a merged contract); B keeps the merged shape
but must carry a strong, tested guarantee that in-process raw text never
escapes. C is the fallback if neither proves worth the change, but it should be
a conscious choice, not a default reached by omission.

## Consequences and follow-through

- No implementation change now. E4-T02's behavior (respect the caller's
  redaction policy; fail honestly when no content is observable) is the correct
  conservative placeholder under every option.
- Resolve before E4-T05. Whichever option is chosen, the executor's structured
  calls must record what MRR-FR-045 requires without recording a secret
  (AGENTS.md rule 11) and without treating unvalidated model output as
  authoritative (MRR-FR-046).
- Interacts with ADR-0004 (canonical serialization) and the E4-T01 record
  shape: all three concern what bytes constitute "the recorded call" and should
  be read together when E5 federation carries these records across nodes.
