"""The MethodRuling application service (task-packets/K1-T03.yaml):
``MethodRulingService`` — persist ``MethodRuling`` objects and drive them
through ``mrr.domain.lifecycles.METHOD_RULING_LIFECYCLE`` (``pending ->
issued -> superseded``).

Same ``services/`` root and layering contract as
``mrr.services.evidence_matrix``/``mrr.services.method_profile`` — see
``mrr.services.method_ruling.service`` for the shared wiring pattern.
"""

from __future__ import annotations
