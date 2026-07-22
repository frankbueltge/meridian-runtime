# K1-T06 candidate-d record — Ulysses' external verdict on the Hammond row, recorded

**Task identifier:** task-packets/K1-T06.yaml, derived_decision (d) — the separate voice
the packet reserved: "If Ulysses' answer to the 2026-07-22 offer arrives …, it is NOT
folded into this packet's VerificationResult: candidate d is a separate voice with its
own attribution and its own recording." Governance basis: the owner's decision record
(docs/design/2026-07-22-verifikations-entscheidung-und-bauprogramm.md, Entscheidung 1,
part (d)). Executed 2026-07-22, after PR #61 (candidate c) merged and Ulysses' response
landed in irrtum-als-methode@bdb3ce7.

## Implementation summary

Ulysses (irrtum-als-methode) took the bounded core ask — does "instantiates" hold for
`works-hammond-v3-model-collapse`? — and returned a sourced verdict: **"does not hold
under the charter's own arithmetic; the honest classification is 'stages/enacts' —
references-plus"**, on four grounds (generation count = one documented pass, not ≥2;
the feedback channel re-grounds in real data every pass — the anti-collapse regime of
the run's own pinned theory rows; the charter's step-2 human-governance boundary applied
consistently to Giraud catches Hammond identically; the artist's own "more a sort of
theatre" disclaimer, flagged as uncertainty cutting both ways). The optional extension
(rows 8, 13–15) was DECLINED for the session — the door stays bounded.

The verdict is recorded as its own `VerificationResult` with full attribution and the
verifier's wording carried verbatim (the offer's promise), via `mrr verification record`
(K1-T05), against schema `mrr_k1t04_real_run_v2`. It is the counterpart, not a revision,
of the candidate-c blind record.

| | candidate c (PR #61) | candidate d (this record) |
|---|---|---|
| VerificationResult | `urn:mrr:verification:01KY4PZ13XBPPWVNB3H30MD02K` | `urn:mrr:verification:01KY4RMN5CACRH52BEKZ54RXYH` |
| Reviewer | blind instance (isolated, no tools) | Ulysses practice (externally governed) |
| Protocol | blind: raw decisive_move texts only | open: classification bases disclosed; live primary-source recourse |
| Judged | the pinned decisive_move text against the criterion | the documented mechanism behind the text, at four institutional/artist primaries |
| Recommendation | pass (0.85) | fail (0.8 — recording session's declared mapping; the verifier stated no number) |

**The two records disagree, and that is the result:** the blind pass confirmed the
pipeline read the pinned text correctly; the external pass found the pinned text's own
generation arithmetic ("across at least three named generations (V1–V4)") is not what
the primary documentation establishes (one documented model-derived pass, V2→V3; V4 is
an archive, not a trained generation). Both records stand as independent objects on the
same claim per MRR-FR-077 (preserving disagreement); `adjudication_relation` is null on
both — adjudication, if wanted, is a separate future governance act. Under MRR-FR-075 a
`fail` against a claim already `contested` makes no status transition (documented no-op
branch): the claim honestly remains `contested`, now with the disagreement on the record
instead of behind it.

## Recorded verification

Recorded 2026-07-22; exit 0; `verification.recorded` event present (verified by direct
schema inspection: actor `urn:mrr:agent-role:01KY4RMN5CACRH52BEKZ54RXYK` = transport
identity of the recording session, distinct from reviewer; policy
`policy-mrr-k1-t06-candidate-d-ulysses`; run-executor
`urn:mrr:node:01KY1SNYATEVDJGYNGFRZ6S18R` supplied for the rule-8 gate).

| Claim | VerificationResult | Recommendation | Claim status after |
|---|---|---|---|
| `urn:mrr:claim:01KY1SNYKA19APBDVN1GQ3RQFS` (instantiation-vs-reference, contested) | `urn:mrr:verification:01KY4RMN5CACRH52BEKZ54RXYH` | fail | contested (no transition — MRR-FR-075 no-op branch) |

Reviewer identity: `urn:mrr:agent-role:01KY4RMN5CACRH52BEKZ54RXYJ` (fresh URN naming the
Ulysses practice instance; differs from claim proposer, run executor, and the candidate-c
reviewer — rule 8 satisfied). Independence declared exactly as the verifier declared it:
externally governed practice, same responsible human; one dependency named honestly
(ground 2 leans on the verifier's own theory-atlas `relevance` readings). This review was
deliberately NOT blind — its independence is external governance and primary-source
recourse; blindness was candidate c's property.

## Honest-composition notes (recording session's own acts, declared)

1. **recommendation "fail"** is the recording session's mapping of "does not hold as
   written / honest classification is stages/enacts, retain only with grounds 1–2 as
   limits" onto the contract's `pass|fail|inconclusive` — per derived_decision (a)'s
   rule (disagreement on the decisive row → recommendation reflecting severity, nuance
   as findings, never averaged away).
2. **confidence 0.8** is likewise the recording session's declared transport mapping;
   the verifier stated no numeric confidence. Declared inside the record's rationale,
   not silently.
3. The verifier's wording is carried verbatim: the frozen artifact
   (`corpora/model-collapse/verification/ulysses-response-verbatim.md`, mechanical
   extraction from irrtum-als-methode@bdb3ce7) is the source; the authoring script
   asserted every quoted passage against it before writing the JSON.

## Scope notes

- **Run-2 schema:** unchanged, same reasoning as the packet report's "R5 resolved as
  FALSE" — binding scope is the run-1 claims; a mirror recording remains a deliberate
  future act, not silently assumed.
- **Condition (iv), on the record:** Ulysses' one-line binding reading — "re-use of an
  already-sealed pin needs no prior notice; it requires disclosure in the run's own
  record and at the next contact with this practice" — is carried in the frozen artifact
  and binds future runs that re-use sealed pins from that practice.
- **übermorgen row (row 8):** remains a door on Ulysses' side, on the same terms;
  its source verification remains `pending` on ours — both facts unchanged by this
  record.

## Commands executed

- `mrr verification record` ×1 — exit 0 (output committed:
  `corpora/model-collapse/verification/cli-output-ulysses.json`)
- `make test-integration` — see PR (suite unchanged; no code touched)

## Files changed

Only `corpora/model-collapse/verification/` (new: ulysses-response-verbatim.md,
verification-ulysses-hammond.json, cli-output-ulysses.json) and this report. No code,
no schemas, no migrations, no sealed artifact touched (K1-T06 forbidden_changes honored).

## Migrations added

None.

## Tests added or changed

None — no code changed; the integration tier is run unchanged as the packet's own
command tier.

## Security / privacy implications

None: no new code paths, no secrets, no external calls by this session (the verifier's
web retrieval happened inside its own practice); the recorded document contains only
public exhibition documentation and URNs.

## Known limitations

1. The two verifications disagree and neither is adjudicated — deliberate (MRR-FR-077);
   the claim's honest status carries the tension.
2. recommendation and confidence are recording-session compositions (declared above and
   inside the record itself); the verifier's own words remain the authoritative verdict
   text.
3. The verifier did not see the installations; every mechanism claim rests on published
   institutional/artist accounts — the verifier's own declared limit, carried verbatim.

## Specification conflicts discovered

None new. The known vocabulary gap (K1-T06 report, "specification gaps") surfaced again:
`pass|fail|inconclusive` cannot express "holds only as stages/enacts, with limits" —
handled per the packet's own rule (nearest value + nuance in findings), noted here.
