# Meridian Research Runtime

Meridian Research Runtime (MRR) is the successor architecture to Meridian Classic: a
research-orchestration system built around explicit provenance, policy-gated execution,
and verifiable claims rather than trusted model output. It is implemented as vertical
slices — identity, policy, execution, evidence, verification, and correction together,
not a large agent framework with provenance added later. See `docs/spec/` for the
governing specification, in particular `docs/spec/06_IMPLEMENTATION_PLAN.md` for the
delivery strategy and epic sequence, and `AGENTS.md` for the rules binding any change in
this repository.

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
