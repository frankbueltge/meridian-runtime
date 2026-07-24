# Meridian Research Runtime

Meridian Research Runtime (MRR) is the successor architecture to Meridian Classic: a
research-orchestration system built around explicit provenance, policy-gated execution,
and verifiable claims rather than trusted model output. It is implemented as vertical
slices — identity, policy, execution, evidence, verification, and correction together,
not a large agent framework with provenance added later. See `docs/spec/` for the
governing specification, in particular `docs/spec/06_IMPLEMENTATION_PLAN.md` for the
delivery strategy and epic sequence, and `AGENTS.md` for the rules binding any change in
this repository.

## Public-facing work & license

This runtime feeds **On Record** (<https://frankbueltge.de/on-record>) — a
claim-graph-rooted publication of the Hammond instantiation dispute with preserved
dissent. The capability roadmap and the research records behind it live in `docs/design/`
(start with `2026-07-24-capability-roadmap-entwurf.md`). The first real run — K1-T04,
schema `mrr_k1t04_real_run_v2` — established two model-collapse claims with recorded
verification disagreement: a deliberately small, honest scope, not a general-purpose
research agent.

Code is licensed under **PolyForm Noncommercial 1.0.0**; works, texts and data under
**CC BY-NC-SA 4.0** — see `LICENSE.md`. © Frank Bültge.

## Quality commands

All commands run through `uv` and are wired up in the `Makefile`:

| Command | What it does |
|---|---|
| `make format` | Formats and auto-fixes code with Ruff. |
| `make format-check` | Checks formatting without modifying files. |
| `make lint` | Runs Ruff lint checks and the import-boundary contract (import-linter). |
| `make typecheck` | Runs mypy in strict mode. |
| `make test` | Runs the `unit` and `property` test tiers. |
| `make test-contract` | Runs the `contract` test tier. |
| `make test-integration` | Runs the `integration` test tier. |
| `make test-e2e` | Runs the `e2e` test tier. |
| `make test-adversarial` | Runs the `adversarial` test tier. |
| `make benchmark` | Runs the `meridianbench` benchmark tier. |
| `make security-check` | Runs `pip-audit` against locked dependencies and `bandit` against `packages/`. |

Test tiers that have no tests yet are declared in `tests/EMPTY_TIERS.txt` and are
reported as explicitly expected-empty, never as passing feature coverage — see
`scripts/run_test_tier.py`.

## The `mrr` CLI

`mrr` (console script, `mrr.services.cli.main:main`) is a thin operator CLI over the
merged E2 single-node vertical slice — no new domain behavior, no HTTP, no model/LLM
dependency. `mrr run --database-url ... --artifact-root ...` drives one complete local
evidence loop end to end: approve a Research Score, register a node's signed capability
manifest, negotiate and execute a deterministic Task Bundle, record the Run Manifest, and
seal the Evidence Crate. The underlying composition function,
`mrr.services.cli.orchestration.run_local_evidence_loop`, is what both the CLI and
`tests/e2e/test_e2e_001_single_node_evidence_loop.py` (E2E-001, E2 scope) call — see
`task-packets/E2-T07.yaml`.

## Layout

```text
meridian-runtime/
├── pyproject.toml       # single distribution "mrr", hatchling build backend
├── Makefile             # quality commands
├── packages/            # mrr.domain, mrr.crypto, mrr.contracts, mrr.policy,
│                        # mrr.provenance, mrr.observability — one PEP 420
│                        # namespace package merged from six roots
├── tests/               # unit, property, contract, integration, e2e, adversarial
├── scripts/             # run_test_tier.py and other repo tooling
├── docs/spec/           # the governing specification and ADRs
├── schemas/, examples/  # JSON Schemas and example objects for external contracts
└── task-packets/        # approved task packets implemented one at a time
```

`packages/`, `services/`, `workers/`, and `adapters/` are the code roots the
specification anticipates; only `packages/` exists so far — later epics add the rest as
their tasks require it. Core packages (`mrr.domain`, `mrr.crypto`, `mrr.contracts`,
`mrr.policy`, `mrr.provenance`, `mrr.observability`) must not import FastAPI, Temporal,
a model-provider SDK, or an object-store client SDK; this is enforced by the
import-linter contract in `pyproject.toml` and checked in `make lint`.

## License

Code is licensed under **PolyForm Noncommercial License 1.0.0**. Non-code works, texts,
and data are licensed under **CC BY-NC-SA 4.0**. See `LICENSE.md` for the full text.
