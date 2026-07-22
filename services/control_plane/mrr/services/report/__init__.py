"""The Report application service (task-packets/E8-T03.yaml, MRR-FR-100/-101/
-104, MRR-FR-095): ``ReportService`` — a read-only service that turns one
sealed ``EvidenceCrate`` into a deterministic ``mrr.domain.research_report
.ResearchReport`` model, ready for ``render_markdown``/``render_html``.
Third task of Epic E8.

Same ``services/`` root and layering contract as ``mrr.services.export``/
``mrr.services.projection`` — see ``mrr.services.report.service`` for the
shared read-only wiring pattern (this service composes BOTH of those, never
re-reading the object repository/edge repository/event log a third,
divergent way) and the full "one closure, two consumers" design rationale.
"""

from __future__ import annotations
