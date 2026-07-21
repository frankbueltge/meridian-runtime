"""The Obligation application service (task-packets/E6-T02.yaml):
``ObligationService`` — materialize the persisted ``Obligation`` aggregate
from an accepted/adapted ``TransferContract``'s structural obligation stubs
and non-empty ``caveats`` field (MRR-FR-083), bind it to the transferred
object(s) via ``subject_to_obligation`` edges, propagate that binding onto
every object later found to be built on them (reusing
``mrr.domain.obligation_propagation.compute_obligation_binding``), and
record the two verified response actions, ``resolve``/``defer``.

Same ``services/`` root and layering contract as ``mrr.services.correction``
(E3-T06)/``mrr.services.transfer`` (E6-T01) — see
``mrr.services.obligation.service`` for the shared wiring pattern and, above
all, for the record()/propagate() split this module mirrors from
``CorrectionImpactService`` and the event-derived transfer-decision gating
it reads (without modifying) from ``TransferService``.
"""

from __future__ import annotations
