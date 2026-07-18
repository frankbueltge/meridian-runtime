"""The SourceFamily application service (task-packets/E3-T03.yaml):
``SourceFamilyService`` — persist ``SourceFamily`` objects that represent
evidence dependence between sources (MRR-FR-065). Third task of Epic E3
(claim, evidence, correction kernel).

Same ``services/`` root and layering contract as
``mrr.services.evidence``/``mrr.services.claim`` (E3-T01/E3-T02) — see
``mrr.services.source_family.service`` for the shared wiring pattern. This
task REPRESENTS families only; the independence CALCULATION that consumes
them (adjusting effective evidence weight, never deleting sources) is
E3-T05.
"""

from __future__ import annotations
