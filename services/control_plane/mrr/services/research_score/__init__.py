"""The Research Score application service (task-packets/E2-T01.yaml):
create and revise versioned ``ResearchScore`` objects, drive them through the
``RESEARCH_SCORE_LIFECYCLE`` (E1-T04) recording approvals, and expose the
authoritative "may this score start work" gate (MRR-FR-004).

``mrr.services`` is the first namespace root under ``services/`` — plain
application classes with no HTTP/workflow framework wiring (that is a later
task, per docs/spec/01_SYSTEM_SPEC.md section 7.1's "Research Score Service"
responsibility and this packet's ``forbidden_changes``). Unlike
``mrr.domain``/``mrr.crypto``/``mrr.contracts``/``mrr.policy``/
``mrr.provenance``/``mrr.observability``/``mrr.persistence``, this root is
not required to stay framework-free — it is simply framework-free *today*
because this task needs no framework. An import-linter "forbidden" contract
in pyproject.toml enforces the one direction that does matter: none of those
seven core/persistence packages may import ``mrr.services`` (services depend
inward on them, never the reverse).
"""

from __future__ import annotations
