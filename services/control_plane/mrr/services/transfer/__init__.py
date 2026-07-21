"""The Transfer application service (task-packets/E6-T01.yaml):
``TransferService`` — create, offer, and respond to a signed,
cross-practice ``TransferContract`` (MRR-FR-080/081/082), reusing the
generic ``ObjectRepository``/``EdgeRepository`` (no migration).

Same ``services/`` root and layering contract as ``mrr.services.task_bundle``
(E2-T03)/``mrr.services.correction`` (E3-T06) — see
``mrr.services.transfer.service`` for the shared wiring pattern and, above
all, for the ADR-0007 event-only ``offer``/``respond`` transitions and the
``adapted_from`` edge this task's own ``respond`` records.
"""

from __future__ import annotations
