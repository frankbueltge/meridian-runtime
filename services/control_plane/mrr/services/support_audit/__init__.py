"""The Support Audit application service (task-packets/N2-T03b.yaml):
``SupportAuditService`` — a read-only, NO-NETWORK, NO-DATABASE, MODEL-FREE
service that parses the committed support-batch descriptor, integrity-
verifies its two declared inputs (the claim manifest and the content
snapshot) fail-closed, and — only once the gate is clean — parses both and
evaluates every claim via ``mrr.domain.support_audit`` to build a
``mrr.domain.support_audit_report.SupportAuditReport``.

Like ``mrr.services.citation_audit``/``mrr.services.field_observation``/
``mrr.services.anchoring_integrity``, this service opens no database
connection and constructs no repository anywhere, and never imports
``sqlalchemy`` — see ``mrr.services.support_audit.service`` for the full
design rationale, above all why the integrity gate runs strictly BEFORE any
claim is ever evaluated (fail-closed).
"""

from __future__ import annotations
