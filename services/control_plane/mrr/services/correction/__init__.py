"""The Correction Impact application service (task-packets/E3-T06.yaml):
``CorrectionImpactService`` — record a ``CorrectionEvent``, traverse the typed
edge graph via ``mrr.domain.correction_impact.compute_impact`` to find every
downstream dependent, and mark affected ``Claim`` objects ``review_required``
via the E3-T02 ``ClaimService``, without ever deleting a claim's prior
decision. Sixth task of Epic E3 (claim, evidence, correction kernel).

Same ``services/`` root and layering contract as ``mrr.services.claim``/
``mrr.services.evidence`` (E3-T01/T02) — see ``mrr.services.correction.service``
for the shared wiring pattern and, above all, for the impact edge-type
mapping and traversal-direction rationale (documented in full in
``mrr.domain.correction_impact``, this service's pure-domain dependency).
"""

from __future__ import annotations
