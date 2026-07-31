# Meridian Research Runtime (MRR) — Implementation Specification v0.1.1

> **Status as of 2026-07-31: historical for architecture, normative for
> semantics.** Read this package as the record of what was intended, not as a
> build order.
>
> Still binding: the domain semantics — normative MUST/SHOULD/MAY requirements,
> domain contracts, state machines, JSON Schemas, and the safety and provenance
> invariants. Those are implemented and tested; changing them needs an ADR.
>
> No longer binding: the reference architecture and the delivery plan. The
> implementation deliberately did not build the FastAPI control-plane services,
> Temporal workflow coordination, S3 object storage, or the `workers/`/`infra/`
> layout described here — and `fastapi`, `starlette`, `temporalio`, `boto3` and
> `botocore` are now **forbidden modules** in the import-linter contract in
> `pyproject.toml`, checked by `make lint`. What exists instead is a library
> with a local `mrr` command line and a PostgreSQL database. Where this package
> and the repository disagree about infrastructure, **the repository is right.**
>
> Active product direction:
> `docs/design/2026-07-24-capability-roadmap-entwurf.md`. The reasoning behind
> this status: `docs/design/2026-07-31-mrr-review-und-integrationsrichtung.md`.

This repository-ready specification defines a new practice and software system for federated, auditable, evidence-first research automation.

## Strategic decision

The existing Meridian implementation is not rewritten in place or decommissioned by default. Its current state is sealed as an immutable baseline, while the operational system may continue to run and evolve as `meridian-classic`. The new system is provisionally named **Meridian Research Runtime (MRR)** and begins in shadow, challenger, and selected dual-run modes. Capability adoption is evidence-based, reversible, and does not imply a whole-system sunset.

The governing rule is:

> History is immutable. Policies, constitutions, workflows, and architecture are deliberately mutable through versioned amendments.

## What this package is

This package is the source material from which Codex, Claude Code, or human developers can implement MRR through bounded, testable work packets. It is intentionally more precise than a product brief and less implementation-specific than a finished codebase.

A specification cannot guarantee a perfect implementation. MRR therefore replaces ambiguous expectations with:

- normative requirements using MUST, SHOULD, and MAY;
- explicit domain contracts and state machines;
- machine-validatable JSON Schemas;
- hard safety and provenance invariants;
- benchmark fixtures and release gates;
- one-task-per-change instructions for coding agents;
- architecture decision records and migration rules.

## Reading order for coding agents

1. `AGENTS.md`
2. `docs/00_VISION_AND_GOVERNANCE.md`
3. `docs/01_SYSTEM_SPEC.md`
4. `docs/02_DOMAIN_MODEL.md`
5. `docs/03_API_AND_EVENTS.md`
6. `docs/04_SECURITY_AND_POLICY.md`
7. `docs/05_EVALUATION_AND_ACCEPTANCE.md`
8. `docs/06_IMPLEMENTATION_PLAN.md`
9. `docs/07_AGENT_TASK_TEMPLATE.md`
10. all accepted files in `docs/adr/`, especially `ADR-0002-PARALLEL-OPERATION.md`
11. `schemas/` and `examples/`

If documents conflict, the following precedence applies:

1. accepted ADR;
2. normative requirement with an `MRR-*` identifier;
3. JSON Schema;
4. examples;
5. explanatory prose.

No coding agent may silently resolve a conflict. It must stop the affected task, cite the conflict, and propose an ADR or specification amendment.

## Recommended first implementation slice

The first vertical slice is single-node and intentionally small:

1. create and approve a `ResearchScore`;
2. offer and accept a `TaskBundle` locally;
3. run a deterministic sandbox task;
4. seal an `EvidenceCrate`;
5. create one supported and one contested `Claim`;
6. run an independent verification;
7. open a correction and propagate `review_required` to dependants;
8. export a minimal RO-Crate-compatible bundle.

Federation, LLM-driven hypothesis generation, qualitative field mode, and publication projections are added only after this slice passes all hard gates.

## Working names

- Practice: `Meridian Research Runtime`
- Repository: `meridian-runtime`
- Parallel operating system: `meridian-classic`
- Immutable comparison baseline: `meridian-classic-baseline-2026-07`
- CLI: `mrr`
- API namespace: `/v1`
- Object namespace: `urn:mrr:<entity>:<ulid>`

Names are provisional and may change through an ADR without changing the conceptual contracts.
