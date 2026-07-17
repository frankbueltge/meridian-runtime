"""Mirrors schemas/correction-event.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.16, "CorrectionEvent").
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Urn
from pydantic import Field

#: Mirrors `correction_type`.
CorrectionType = Literal[
    "metadata",
    "source_invalidated",
    "numeric_error",
    "method_error",
    "scope_error",
    "consent_change",
    "data_withdrawal",
    "security",
    "other",
]

#: Mirrors `severity` (docs/spec/02_DOMAIN_MODEL.md section 2.16, "Severity
#: levels").
CorrectionSeverity = Literal["minor", "material", "critical"]

#: Mirrors the top-level `status` enum (the correction's own workflow
#: state, distinct from `Claim.status`).
CorrectionStatus = Literal[
    "OPEN",
    "IMPACT_ANALYSIS",
    "NOTIFYING",
    "AWAITING_RESPONSES",
    "DELIVERY_PENDING",
    "RESOLVED",
    "PARTIALLY_RESOLVED",
    "REJECTED_BY_RECIPIENT",
]


class AffectedObjectRef(MRRModel):
    """Mirrors an `affected_objects[]` entry; both properties are required."""

    id: Urn
    content_hash: Sha256


class CorrectionEvent(BaseObject):
    """Mirrors schemas/correction-event.schema.json.

    `replacement_object_id` is the only property absent from the schema's
    top-level `required` list, and is explicitly nullable
    (`anyOf: [urn, null]`) as well as optional.
    """

    kind: Literal["CorrectionEvent"]
    affected_objects: list[AffectedObjectRef] = Field(min_length=1)
    correction_type: CorrectionType
    severity: CorrectionSeverity
    reason: str = Field(min_length=1)
    evidence_refs: list[Urn]
    originator_id: Urn
    requested_action: str
    replacement_object_id: Urn | None = None
    status: CorrectionStatus
    impact_objects: list[Urn]
