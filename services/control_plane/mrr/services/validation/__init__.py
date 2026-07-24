"""The Validation application service (task-packets/N1-T01.yaml):
``ValidationService`` — a read-only, DB-free service that loads the declared
agreement crosswalk fixture plus the two committed model-collapse
classification files it names, validates the alignment is total, and builds
a stratified ``mrr.domain.agreement_report.AgreementReport`` via
``mrr.domain.agreement``'s pure metric core.

Unlike every other ``services/control_plane/mrr/services/*`` package, this
one opens NO database connection and constructs NO repository anywhere —
see ``mrr.services.validation.service`` for the full design rationale.
"""

from __future__ import annotations
