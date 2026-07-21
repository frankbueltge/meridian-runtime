"""The EvidenceMatrix application service (task-packets/K1-T03.yaml):
``EvidenceMatrixService`` — persist ``EvidenceMatrix`` objects and drive them
through ``mrr.domain.lifecycles.EVIDENCE_MATRIX_LIFECYCLE`` (``draft ->
active -> frozen -> superseded``).

Same ``services/`` root and layering contract as
``mrr.services.method_profile``/``mrr.services.source_family`` — see
``mrr.services.evidence_matrix.service`` for the shared wiring pattern.
"""

from __future__ import annotations
