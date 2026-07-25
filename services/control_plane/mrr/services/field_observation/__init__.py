"""The Field Observation application service (task-packets/R2-T01.yaml):
``FieldObservationService`` — a read-only, no-network, no-database service
that loads a committed, hash-anchored observation-batch descriptor,
integrity-verifies each declared input fail-closed, and — only once the
gate is clean — reuses the FROZEN N2 evaluator
(``mrr.services.citation_audit.service.CitationAuditService``) to build a
``mrr.domain.field_observation_report.FieldObservationReport``.

Like ``mrr.services.citation_audit``/``mrr.services.validation``, this
service opens NO database connection and constructs NO repository anywhere
— see ``mrr.services.field_observation.service`` for the full design
rationale, above all why the integrity gate runs strictly BEFORE the N2
evaluator is ever constructed (fail-closed).
"""

from __future__ import annotations
