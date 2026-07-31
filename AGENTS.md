# Instructions for Codex, Claude Code, and other coding agents

## Mission

Implement Meridian Research Runtime strictly from the specification. Prefer correctness, auditability, explicit failure, and small reversible changes over speed or apparent completeness.

**Read `docs/spec/` as historical, not as a build order.** It remains the
normative source for domain semantics — requirements, contracts, state
machines, invariants — and those still bind. Its *reference architecture* and
*delivery plan* do not: parts were deliberately not built, and some are now
forbidden outright (see Engineering defaults). Where the specification and this
repository disagree about infrastructure, the repository is right and the
specification is a record of what was once intended. Active product direction
lives in `docs/design/2026-07-24-capability-roadmap-entwurf.md`.

## Two kinds of task packet

Most packets build a capability: a module, a contract, a service method, derived
from a specification section. Rules 2 and 3 below govern them unchanged.

An **integration packet** builds no capability. It connects components that
already exist and are already tested, so that an operator can actually reach
them. Its acceptance criterion is a **named operator path that works end to
end**, never a module that passes its own tests.

This second type exists because its absence was measurable. Four times the same
gap appeared — a complete layer whose outer edge nobody owned:

| Layer | Missing outer edge | Closed by |
|---|---|---|
| `ModelAdapter` | no concrete provider adapter | E4-T08 |
| Federation objects | no transport | E5-T08 |
| Bundle transport | no way in (envelope construction) | E5-T10 |
| `EnvelopeTransport` | no implementation at all | I1-T01 |

None of those gaps was carelessness. Each fell between two correct exclusions:
composition work is not "domain behavior absent from the specification", so
rule 3 forbade inventing it, and it was not a specification section, so rule 2
gave it no packet to live in. **The same rules that produced the per-packet
quality structurally excluded the last mile.** More diligence does not fix
that; a packet type does.

An integration packet MAY add one concrete implementation of a port the
specification already declares — as E4-T08 did for `ModelAdapter` and I1-T01
for `EnvelopeTransport`. It MUST declare that addition explicitly in its
`derived_decisions`, never fold it into a passthrough. It MUST NOT add a
schema, a domain object, or a new general capability; those remain ordinary
packets.

Analysis: `docs/design/2026-07-31-mrr-review-und-integrationsrichtung.md`.

## Non-negotiable rules

1. Read the relevant specification sections before changing code.
2. Implement only one approved task packet at a time.
3. Do not invent domain behavior that is absent from the specification. (An
   integration packet composes existing behavior; see above. Composing is not
   inventing.)
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

In force, and enforced:

- Python 3.12+
- Pydantic v2 for runtime contracts
- PostgreSQL for authoritative metadata and graph edges
- a local command line (`mrr`) as the only operator interface
- content-addressed artifacts in a local store behind the
  `mrr.adapters.object_store` port
- OpenTelemetry for traces
- pytest, hypothesis, and contract tests
- Ruff and mypy in strict mode
- Alembic for database migrations

**Superseded — do NOT reach for these.** Earlier revisions of this file listed
FastAPI for HTTP interfaces, S3-compatible object storage, Temporal for durable
workflows, and OCI containers for sandbox tasks. None was built, and
`fastapi`, `starlette`, `temporalio`, `boto3` and `botocore` are now
**forbidden modules** in the import-linter contract in `pyproject.toml`,
checked on every run of `make lint`.

This was a live contradiction, not a stale preference: an agent following the
old list would have written code the linter rejects. What exists instead is a
library with a CLI and a database — no server, no broker, no workflow engine.
That is a deliberate architecture, and the enforcement is where it belongs, in
the linter rather than in a document nobody can run.

Adopting any of the superseded four again requires an accepted ADR that also
amends the import-linter contract in the same change.

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
