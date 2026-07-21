"""The ResearchDecision application service (task-packets/K1-T03.yaml):
``ResearchDecisionService`` — persist ``ResearchDecision`` objects (append-only,
``mrr.domain.lifecycles.RESEARCH_DECISION_LIFECYCLE`` has exactly one state
and zero transitions).

Same ``services/`` root and layering contract as
``mrr.services.source_family`` — see ``mrr.services.research_decision.service``
for the shared wiring pattern (create-only, mirrors ``SourceFamilyService``
exactly, since this kind has no lifecycle transition to drive at all).
"""

from __future__ import annotations
