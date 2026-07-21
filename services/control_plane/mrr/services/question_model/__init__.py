"""The QuestionModel registry service (task-packets/K1-T04.yaml): create and
drive versioned ``QuestionModel`` objects through ``QUESTION_MODEL_LIFECYCLE``
(``draft -> accepted -> superseded``, ``mrr.domain.lifecycles``).

Same ``services/`` root and layering contract as
``mrr.services.method_profile`` — see that package and
``mrr.services.question_model.service`` for the shared wiring pattern this
task reuses verbatim (K1-T01's own ``specification_gaps`` explicitly named
this service as K1-T04's own scope to add, since K1-T03 only reads
already-accepted instances via the generic ``ObjectRepository``).
"""

from __future__ import annotations
