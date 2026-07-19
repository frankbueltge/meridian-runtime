"""The Projection application service (task-packets/E3-T07.yaml):
``ProjectionService`` — a read-only, re-derivable claim-table and
provenance-map projection over the already-persisted claim/evidence/
correction graph (E1-T05 ``ObjectRepository``/``EdgeRepository``, E1-T06
``EventLog``, E3-T01..T06). Seventh and final task of Epic E3 (claim,
evidence, correction kernel).

Same ``services/`` root and layering contract as ``mrr.services.claim``/
``mrr.services.correction`` (E3-T02/T06) — see ``mrr.services.projection.service``
for the shared wiring pattern and, above all, for how the claim table and
provenance map are built purely by READING existing state (never writing an
object revision, event, or edge) and why that makes both re-derivable
"projections" in the AGENTS.md sense ("Narrative reports are projections and
are never the primary research record").
"""

from __future__ import annotations
