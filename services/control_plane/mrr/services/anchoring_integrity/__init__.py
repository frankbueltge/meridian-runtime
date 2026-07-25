"""The Anchoring Integrity application service (task-packets/N2-T02b.yaml):
``AnchoringIntegrityService`` — a read-only, NO-NETWORK, NO-DATABASE service
that parses a committed anchoring-batch descriptor, integrity-verifies each
declared archive dump fail-closed, and — only once the gate is clean —
parses each dump and resolves every EvidenceAnchor/Claim reference to build
a ``mrr.domain.anchoring_integrity_report.AnchoringIntegrityReport``.

Like ``mrr.services.citation_audit``/``mrr.services.field_observation``,
this service opens no database connection and constructs no repository
anywhere, and never imports ``sqlalchemy`` — see ``mrr.services
.anchoring_integrity.service`` for the full design rationale, above all why
the integrity gate runs strictly BEFORE any dump is ever parsed
(fail-closed).
"""

from __future__ import annotations
