# Start here with Codex or Claude Code — specification v0.1.1

> **This file describes the START of the project (2026-07-17), not its current
> state.** The system is built: 80 task packets, six test tiers, two real
> research runs in the archive. Do not follow the first-invocation sequence
> below against the existing repository — it seeds a greenfield.
>
> To work on MRR as it stands: read `AGENTS.md`, then
> `docs/design/2026-07-31-mrr-review-und-integrationsrichtung.md` for where
> things actually are, then the newest handoff in `docs/design/`. Use
> `docs/spec/` for domain semantics only — its architecture is historical, see
> the status note in `docs/spec/README.md`.

Do not ask the coding agent to implement the whole system.

## Governance note

The immutable Meridian Classic baseline is not a shutdown instruction. The live Classic system may continue in parallel. Any task concerning migration, comparison, adoption, or retirement MUST read `docs/00_VISION_AND_GOVERNANCE.md`, `docs/05_EVALUATION_AND_ACCEPTANCE.md`, `docs/06_IMPLEMENTATION_PLAN.md`, and `docs/adr/ADR-0002-PARALLEL-OPERATION.md`.

## First invocation

Give the agent these files:

- `AGENTS.md`
- `task-packets/E1-T01.yaml`
- the task packet's cited source-of-truth files

Use this prompt:

```text
Implement task E1-T01 for Meridian Research Runtime.

Read AGENTS.md and task-packets/E1-T01.yaml first. Then read only the cited
source-of-truth sections. Treat normative requirements and schemas as binding.
Do not implement adjacent tasks or invent domain behavior.

Add all acceptance tests, run every required command, and return the exact
results. Stop and report a precise specification conflict if safe completion
requires broader changes.
```

After E1-T01 is independently reviewed and merged, run E1-T02 and then E1-T03 in separate sessions or branches.

## Context rule

The planner may inspect the entire specification. The implementation agent receives only the active task, directly relevant code, schemas, and cited requirements. This prevents broad-context improvisation while retaining architectural consistency.

## Review rule

Use a separate session or model lineage for review. Give the reviewer the patch, task packet, `AGENTS.md`, and cited specification sections. The reviewer must identify violated requirements and missing negative tests, not merely comment on style.
