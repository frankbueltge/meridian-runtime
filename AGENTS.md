# Instructions for Codex, Claude Code, and other coding agents

## Mission

Implement Meridian Research Runtime strictly from the specification. Prefer correctness, auditability, explicit failure, and small reversible changes over speed or apparent completeness.

## Non-negotiable rules

1. Read the relevant specification sections before changing code.
2. Implement only one approved task packet at a time.
3. Do not invent domain behavior that is absent from the specification.
4. Do not weaken a MUST requirement to make a test pass.
5. Every change MUST include tests at the appropriate level.
6. All externally visible data structures MUST be schema-validated.
7. No model output may directly become authoritative state.
8. No executor may approve or verify its own result.
9. No cross-practice object may be accepted without signature and hash verification.
10. No raw restricted or participant-identifiable data may leave its owning node by default.
11. No privileged containers, unpinned dependencies, unrestricted network egress, or secrets in prompts.
12. Do not leave placeholders, silent exception handling, fake implementations, or TODO-only branches in merged code.
13. Do not modify files outside the task packet's allowed paths without reporting a blocking dependency.
14. If a requirement is ambiguous, create a specification issue or ADR proposal; do not guess.
15. Sealing the Meridian Classic baseline is not permission to stop, mutate, or replace the live Classic system. Such actions require an explicit capability-specific task and accepted decision record.

## Required delivery format

Every implementation response or pull request MUST contain:

- task identifier;
- concise implementation summary;
- files changed;
- migrations added;
- tests added or changed;
- exact commands executed and their results;
- security or privacy implications;
- known limitations;
- any specification conflict discovered.

## Engineering defaults

Unless superseded by an ADR:

- Python 3.12+
- FastAPI for HTTP interfaces
- Pydantic v2 for runtime contracts
- PostgreSQL for authoritative metadata and graph edges
- S3-compatible object storage for content-addressed artifacts
- Temporal for durable workflows
- OCI containers for sandbox tasks
- OpenTelemetry for traces
- pytest, hypothesis, and contract tests
- Ruff and mypy in strict mode
- Alembic for database migrations

## Commands expected in the eventual repository

The implementation MUST converge on these stable commands:

```bash
make format
make lint
make typecheck
make test
make test-contract
make test-integration
make test-e2e
make benchmark
make security-check
```

A task is not complete if the relevant commands fail.

## Source-of-truth discipline

- Database state is authoritative for current materialized state.
- The append-only domain event log is authoritative for audit history.
- Object storage is authoritative for sealed artifact bytes.
- Git is authoritative for code, schemas, prompts, policies, and specification versions.
- Narrative reports are projections and are never the primary research record.

## Prohibited shortcuts

- storing mutable blobs without content hashes;
- using an LLM confidence number as epistemic confidence;
- counting copied sources as independent evidence;
- letting an agent cite a source it did not retrieve and anchor;
- automatic publication or participant contact;
- collapsing `unknown`, `not_found`, `contradicted`, and `failed` into one generic error;
- silently overwriting prior object revisions;
- using a graph database before PostgreSQL graph edges are proven insufficient.
