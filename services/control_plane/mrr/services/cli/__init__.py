"""The ``mrr`` CLI (task-packets/E2-T07.yaml): a thin composition layer over
the merged E2 services — no new domain behavior, no HTTP, no model/LLM
invocation.

Two modules:

- ``mrr.services.cli.orchestration`` — ``run_local_evidence_loop``, the
  importable function that wires real dependencies (a PostgreSQL
  ``sqlalchemy.Engine``, an ``mrr.domain.artifacts.ArtifactStore``,
  caller-supplied Ed25519 keys) and drives one complete local run: approve a
  Research Score, register a node capability, negotiate and execute a
  deterministic Task Bundle, record the Run Manifest, and seal the Evidence
  Crate. Both the ``mrr`` console script and ``tests/e2e/`` call this same
  function, so the CLI and the acceptance test can never silently diverge in
  what "a complete local run" means.
- ``mrr.services.cli.main`` — the argparse-based console-script entry point
  (``mrr``, registered in ``pyproject.toml`` ``[project.scripts]``).

This root is ``services/control_plane`` (the same namespace root
``mrr.services.research_score``/``capability_registry``/``task_bundle``
already live under) — a new leaf package, not a new namespace root, per
task-packets/E2-T07.yaml's derived_decisions ("a cli leaf ... to avoid a new
namespace root"). The existing "Nothing inward imports the services layer"
import-linter contract in pyproject.toml already forbids every core package
from importing ``mrr.services`` (the whole root, by prefix), so it already
covers this new ``mrr.services.cli`` leaf too — no new contract was needed.
"""

from __future__ import annotations
