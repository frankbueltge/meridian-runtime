# Blind instance — protocol note (K1-T06, R1)

- Executed 2026-07-22, immediately after the brief's freeze commit.
- The verifying instance was a fresh model instance in an isolated context: it received
  ONLY the brief's content inline, had all tool access disabled (no repository reads, no
  database access, no web search), and was instructed not to attempt reconstructing any
  prior classification. Tools are named generically per the house rule.
- Its complete return is frozen verbatim in `blind-returns.json` (this commit), BEFORE
  any comparison against the pipeline's classifications was performed (AT1).
- Honesty note (R4): the same responsible human (the owner) stands behind proposer,
  executor, and verifier instances; the model family is shared with the engineering
  tooling. The independence claimed is substantive — blind re-reading of the primary
  material with a fresh context and no pipeline execution — not institutional.
