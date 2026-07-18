"""The Task Bundle application service (task-packets/E2-T03.yaml): create,
offer, and decide on signed ``TaskBundle`` objects up to but not including
execution (QUEUED/RUNNING/COMPLETED, E2-T04).

Same ``services/`` root and layering contract as
``mrr.services.research_score`` (E2-T01) and ``mrr.services.capability_registry``
(E2-T02) — see ``mrr.services.task_bundle.service`` for the shared wiring
pattern and, above all, for the two-class split this task's design requires:
origin operations (``TaskBundleService``) and the target node's authoritative
decision (``NodeTaskDecisionService``), MRR-FR-022's separation of authority.
"""

from __future__ import annotations
