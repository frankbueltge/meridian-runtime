# Run-2 verification mirror — the reserved act, taken

**Task identifier:** none (not a packet) — this is the one-command act the K1-T06 packet
report reserved ("R5 resolved as FALSE": "mirroring the recording there is a one-command
act left for a future session if wanted, not silently assumed") and the candidate-d
record's scope note repeated. Taken deliberately 2026-07-22, same day, so no durable
schema shows an emptier verification layer than the archive actually has.

## What was done

The three K1-T06 VerificationResults (candidate c ×2, candidate d ×1) were mirrored into
the run-2 schema `mrr_run2_corroboration_floor_v1` via `mrr verification record` — six
recordings, because the schema holds **two** peer claim pairs (K1-T04b's within-run
invariance proof executed the run twice; no supersession link exists between the
executions). Mirroring only one pair would have silently privileged one execution.

Each mirror keeps the source verification's content verbatim except: fresh id, run-2
target_id, fresh created_at, one appended `checks_performed` line declaring the mirror
(naming the original verification URN and schema), recomputed content hash. The mirror is
a **recording act, not a new verification** — the run-2 corpus and charter criterion are
the same material the originals judged.

## Recorded (all exit 0; 6/6 `verification.recorded` events confirmed by schema inspection)

| Run-2 claim | Mirrors | New VerificationResult | Rec. | Status after |
|---|---|---|---|---|
| `…01KY35BP43WR5J45ZNFFP09Q0K` (instantiation, A) | c-instantiation | `…01KY4SWM9RJ74NBETW8ND6PKCS` | pass | contested (unchanged) |
| `…01KY35BP43WR5J45ZNFFP09Q0K` (instantiation, A) | d-Ulysses | `…01KY4SWM9XD1QQWKCW5Z964JT1` | fail | contested (no-op branch) |
| `…01KY35BP6Q89XGTC17SRCFB11F` (theory, A) | c-theory | `…01KY4SWM9Z537GEYQS3WNSDVBS` | pass | draft (unchanged) |
| `…01KY35CS8KZ12FJC71H3XCMEQH` (instantiation, B) | c-instantiation | `…01KY4SWMA0XNEWWZS3G7CKHGBJ` | pass | contested (unchanged) |
| `…01KY35CS8KZ12FJC71H3XCMEQH` (instantiation, B) | d-Ulysses | `…01KY4SWMA2K5GDTR006VF9TE3K` | fail | contested (no-op branch) |
| `…01KY35CSBGPJ7Z02N6NDY25TBH` (theory, B) | c-theory | `…01KY4SWMA38QZ3WPDPYWFDR4BX` | pass | draft (unchanged) |

Policies: `policy-mrr-k1-t06-blind-verification-run2-mirror` (×4),
`policy-mrr-k1-t06-candidate-d-ulysses-run2-mirror` (×2). Actor (transport):
`urn:mrr:agent-role:01KY4RMN5CACRH52BEKZ54RXYK` (same recording session as the
candidate-d record). Run-executor ids supplied per execution pair (rule-8 gate):
`…01KY35BNQBC4JXSAA24PT58NMN` (A), `…01KY35CRPPECQGP0G6K5T7PDYG` (B).

The disagreement structure of run 1 is therefore now faithfully present in run 2 as
well: each instantiation claim carries one pass (blind, candidate c) and one fail
(external, candidate d), unadjudicated by design (MRR-FR-077).

## Files changed

Only `corpora/model-collapse/run2-corroboration-floor/verification/` (new: six mirror
JSONs + `cli-output-mirrors.json`) and this note. No code, no schemas, no migrations,
no sealed artifact touched.

## Commands executed

`mrr verification record` ×6 — exit 0 each (outputs committed). No code changed; no
test tier re-run required beyond the still-green state on main.

## Security / privacy implications

None — no new code paths, no secrets, no external calls.

## Known limitations

1. The mirror multiplies records, not evidence: six documents carry three verifications'
   substance. Each mirror names its original; nothing counts double (the AGENTS
   prohibition on copied sources as independent evidence applies to READING these
   records too, and the declaration line is written to make that unmistakable).
2. The claim→execution pairing (A/B) was established from proposer/executor ULID
   correlation in the schema, not from an explicit claim→run field (claims do not carry
   one); the run-executor id only feeds the rule-8 gate, which passed on substance
   either way.
