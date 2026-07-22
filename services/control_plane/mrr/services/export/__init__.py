"""The Export application service (task-packets/E8-T01.yaml, MRR-FR-055's
first half): ``ExportService`` — a read-only service that turns one sealed
``EvidenceCrate`` and its provenance neighborhood into a self-contained,
offline-verifiable RO-Crate 1.1 export directory. First task of Epic E8.

Same ``services/`` root and layering contract as
``mrr.services.projection``/``mrr.services.verification`` — see
``mrr.services.export.service`` for the shared read-only wiring pattern
(``ObjectRepository``/``EdgeRepository``/the narrow event-journal Protocol,
plus ``mrr.domain.artifacts.ArtifactStore`` for payload bytes) and the full
closure/export design rationale.
"""

from __future__ import annotations
