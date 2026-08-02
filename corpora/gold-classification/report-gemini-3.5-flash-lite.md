# Gold-standard validity report

This report measures VALIDITY against a frozen gold standard: a fixed set of correct answers, set outside the practice being measured and pinned by content hash before any system was run against it. That is a different and stronger claim than the RELIABILITY reported by mrr validate agreement, which compares two independent instances to each other and can be perfectly stable while both are wrong. This report is honest exactly as far as its gold standard is: read label_provenance before reading any number below.

## The standard

- Gold set: `mb-cls-ulysses-v1-restamped`
- Gold sha256: `sha256:f927deb1e569554276ee491543eb4353ddab94b76a8bdda3b058f01a1366ba1f`
- Fixture set id: `mb-cls-ulysses-v1-restamped@sha256:f927deb1e569554276ee491543eb4353ddab94b76a8bdda3b058f01a1366ba1f`
- Labels produced by: ulysses
- Provenance: Labelled blind by the Ulysses practice on 2026-08-01 against mb-cls-criteria-v2 as delivered, from the sixty excerpts in commission.v2.json and nothing else; no access to the classifications being measured. Study record and the frame used: projects/2026-08-01-sixty-cases-blind/ in the Ulysses repository. HEADER CORRECTED BY MERIDIAN 2026-08-01: the criteria pointer was moved from v2 to v3 because v2's lock instant was wrong (a wall-clock reading stamped as UTC). The labels are Ulysses' own and unmodified; the correction is Meridian's and is attributed to Meridian.
- Blind to the measured labels: True

## The order gate

- Criteria version: `mb-cls-criteria-v3`
- Criteria locked at: `2026-08-01T19:30:00Z`
- Criteria lock hash: `sha256:46b2dfef753f9ba44b4da7567b2b3ffb7ad8c2a591456d814eced12680bd5e1c`
- Labelled at: `2026-08-01T20:52:00Z` (strictly after the lock)

## The measurement

- System under test: `gemini-3.5-flash-lite@mb-cls-criteria-v3`
- n = 57

| | |
|---|---|
| Accuracy (observed agreement) | 0.5439 |
| Majority-class baseline | 0.4211 |

Accuracy is printed beside the majority-class baseline deliberately: a classifier that only predicts the gold standard's most frequent category reaches the baseline while having learned nothing. An accuracy above zero is not a result; an accuracy meaningfully above this floor is.

| Statistic | Value |
|---|---|
| Cohen's kappa | 0.3084 |
| Weighted kappa (linear) | 0.3426 |
| Weighted kappa (quadratic) | 0.3964 |
| Krippendorff's alpha (nominal) | 0.2465 |
| False-support rate | 0.0179 (1/56) |

### Confusion matrix (rows = gold, columns = system)

| gold \ system | supports | contradicts | qualifies | contextualizes |
|---|---|---|---|---|
| supports | 1 | 0 | 0 | 0 |
| contradicts | 0 | 3 | 2 | 7 |
| qualifies | 1 | 1 | 7 | 15 |
| contextualizes | 0 | 0 | 0 | 20 |

### Per category, against gold

| Category | Gold n | TP | FP | FN | Precision | Recall | F1 | Below power |
|---|---|---|---|---|---|---|---|---|
| supports | 1 | 1 | 1 | 0 | 0.5000 | 1.0000 | 0.6667 | True |
| contradicts | 12 | 3 | 1 | 9 | 0.7500 | 0.2500 | 0.3750 | True |
| qualifies | 24 | 7 | 2 | 17 | 0.7778 | 0.2917 | 0.4242 | False |
| contextualizes | 20 | 20 | 22 | 0 | 0.4762 | 1.0000 | 0.6452 | False |

**below_power: True** — below_power is set when a category has fewer than 20 gold labels. Below that, a headline figure must not be read as publication-grade evidence (docs/design/2026-07-24-capability-roadmap-entwurf.md, N1: 20-30 labels per category).

### Coverage and ties — not scores

Two counts below are not scores and must not be read as ones. UNDECIDABLE cases are cases the criteria failed to settle; they are excluded from the matrix because there is no correct answer to score against, and their count measures the criteria's coverage rather than any classifier's skill. TIE-BROKEN cases are cases where the conservative rule, not the definitions, produced the label — every one of them could have gone the other way, so the corroboration ceiling derived from this set is a point estimate with a width, and the tie count is that width. Both counts exist because an outside practice objected that the first version of these criteria produced a clean-looking output by destroying the evidence of its own uncertainty. It was right.

| | Count | Cases |
|---|---|---|
| Undecidable under the criteria | 3 | mbcls-2410.01440, mbcls-2505.12501, mbcls-2607.03863 |
| Decided by the conservative tie-break | 5 | mbcls-2409.05258, mbcls-2508.11860, mbcls-2508.15126, mbcls-2603.11515, mbcls-2606.10402 |

### Case by case

| Case | Gold | System | Correct |
|---|---|---|---|
| mbcls-2205.08794 | qualifies | qualifies | True |
| mbcls-2402.00559 | contextualizes | contextualizes | True |
| mbcls-2407.00466 | contextualizes | contextualizes | True |
| mbcls-2407.18367 | contextualizes | contextualizes | True |
| mbcls-2408.06292 | contradicts | contextualizes | False |
| mbcls-2409.04109 | contradicts | contextualizes | False |
| mbcls-2409.05258 | qualifies | qualifies | True |
| mbcls-2501.16961 | qualifies | supports | False |
| mbcls-2502.19613 | contradicts | contradicts | True |
| mbcls-2503.22444 | contextualizes | contextualizes | True |
| mbcls-2505.19897 | contextualizes | contextualizes | True |
| mbcls-2505.21034 | qualifies | contextualizes | False |
| mbcls-2505.21935 | contextualizes | contextualizes | True |
| mbcls-2506.02918 | contradicts | contextualizes | False |
| mbcls-2506.08968 | qualifies | qualifies | True |
| mbcls-2506.11237 | qualifies | qualifies | True |
| mbcls-2506.11442 | contradicts | qualifies | False |
| mbcls-2507.10590 | qualifies | contradicts | False |
| mbcls-2507.17209 | qualifies | contextualizes | False |
| mbcls-2508.11860 | qualifies | qualifies | True |
| mbcls-2508.15126 | qualifies | contextualizes | False |
| mbcls-2509.03036 | qualifies | contextualizes | False |
| mbcls-2510.02190 | contextualizes | contextualizes | True |
| mbcls-2510.19949 | contradicts | qualifies | False |
| mbcls-2511.04583 | qualifies | contextualizes | False |
| mbcls-2511.13825 | contradicts | contextualizes | False |
| mbcls-2511.23436 | qualifies | qualifies | True |
| mbcls-2512.11509 | contextualizes | contextualizes | True |
| mbcls-2512.18292 | contextualizes | contextualizes | True |
| mbcls-2512.22145 | qualifies | contextualizes | False |
| mbcls-2601.00828 | contradicts | contextualizes | False |
| mbcls-2601.03315 | contradicts | contextualizes | False |
| mbcls-2601.12346 | contextualizes | contextualizes | True |
| mbcls-2602.00521 | contextualizes | contextualizes | True |
| mbcls-2602.04288 | contradicts | contextualizes | False |
| mbcls-2602.12639 | qualifies | contextualizes | False |
| mbcls-2602.18920 | qualifies | contextualizes | False |
| mbcls-2603.11515 | qualifies | contextualizes | False |
| mbcls-2603.18516 | contextualizes | contextualizes | True |
| mbcls-2603.20262 | contextualizes | contextualizes | True |
| mbcls-2604.00149 | supports | supports | True |
| mbcls-2604.00248 | contextualizes | contextualizes | True |
| mbcls-2604.03553 | contextualizes | contextualizes | True |
| mbcls-2604.17406 | contradicts | contradicts | True |
| mbcls-2604.18418 | contextualizes | contextualizes | True |
| mbcls-2604.20441 | qualifies | contextualizes | False |
| mbcls-2605.06177 | contextualizes | contextualizes | True |
| mbcls-2605.23273 | qualifies | contextualizes | False |
| mbcls-2605.26730 | qualifies | contextualizes | False |
| mbcls-2606.04228 | contextualizes | contextualizes | True |
| mbcls-2606.07591 | contextualizes | contextualizes | True |
| mbcls-2606.10402 | qualifies | qualifies | True |
| mbcls-2606.28756 | contextualizes | contextualizes | True |
| mbcls-2607.09195 | qualifies | contextualizes | False |
| mbcls-2607.10127 | qualifies | contextualizes | False |
| mbcls-2607.22553 | qualifies | contextualizes | False |
| mbcls-2607.26064 | contradicts | contradicts | True |
