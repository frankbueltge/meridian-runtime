"""The Citation Audit application service (task-packets/N2-T01.yaml):
``CitationAuditService`` — a read-only, no-network, no-database service that
loads a committed citation manifest plus its committed resolution snapshot,
classifies every citation via ``mrr.domain.citation_audit``'s pure
classification core, and builds a ``mrr.domain.citation_audit_report
.CitationAuditReport``.

Like ``mrr.services.validation`` (task-packets/N1-T01.yaml), this service
opens NO database connection and constructs NO repository anywhere — see
``mrr.services.citation_audit.service`` for the full design rationale. This
service additionally opens NO network connection: the resolution snapshot is
a committed, point-in-time fetch result (task-packets/N2-T01.yaml derived_
decisions (b)), never re-fetched by this tool.
"""

from __future__ import annotations
