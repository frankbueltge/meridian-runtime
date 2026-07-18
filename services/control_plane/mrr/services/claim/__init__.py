"""The Claim application service (task-packets/E3-T02.yaml): ``ClaimService``
— create atomic ``Claim`` objects, drive them through ``CLAIM_LIFECYCLE``
(E1-T04), and connect them to evidence, counterevidence, dependencies, and
other claims as typed graph edges (E1-T05) — the claim/evidence graph
docs/spec/01_SYSTEM_SPEC.md section 7.6 describes ("Stores typed nodes and
edges in PostgreSQL. A graph database is not required for v1."). Second task
of Epic E3 (claim, evidence, correction kernel).

Same ``services/`` root and layering contract as
``mrr.services.research_score``/``mrr.services.evidence`` (E2-T01/E3-T01) —
see ``mrr.services.claim.service`` for the shared wiring pattern and, above
all, for why a Claim's lifecycle transitions are new object revisions (like
ResearchScore, unlike TaskBundle) rather than ADR-0007 event-only
transitions: the Claim schema has no ``signature`` field to protect.
"""

from __future__ import annotations
