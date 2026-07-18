"""The evidence substrate application services (task-packets/E3-T01.yaml):
``SourceRecordService`` and ``EvidenceAnchorService``. First task of Epic E3
(claim, evidence, correction kernel).

Same ``services/`` root and layering contract as
``mrr.services.research_score``/``mrr.services.capability_registry``/
``mrr.services.task_bundle`` (E2-T01/T02/T03) — see
``mrr.services.evidence.service`` for the shared wiring pattern and why both
services live in one module.
"""

from __future__ import annotations
