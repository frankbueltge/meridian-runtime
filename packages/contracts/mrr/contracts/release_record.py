"""Mirrors schemas/release-record.schema.json
(docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md decision 1).
Fourth task of Epic E8; the thirtieth entity schema/model pair in this
repository. The service and CLI half — ``ReleaseService.create``'s atomic
revision-1 + ``release.approved`` event write, and the bundle-assembly
function that composes E8-T01/T02's RO-Crate export with E8-T03's report
renders — is ``mrr.services.release`` (task-packets/E8-T04.yaml); read that
package's own module docstrings for the object's full design rationale. This
module is the schema/contract half only.

--- approved_by: PERSON urn, pattern-enforced HERE too, not just in schema ---

task-packets/E8-T04.yaml R1: "approved_by person-URN (pattern
``^urn:mrr:person:...`` in schema AND contract validator)". ``PersonUrn``
below is a dedicated ``Annotated`` string type — like
``mrr.contracts.common.Urn``/``Sha256``, but LOCKED to the literal ``person``
entity segment rather than the generic ``[a-z0-9-]+`` — so a
``mrr.services.release.service.ReleaseService`` caller cannot construct an
already-"valid-looking" ``ReleaseRecord`` with a non-person approver even by
hand-building the Pydantic model directly, independent of whatever the
SERVICE layer separately checks. The exact ULID character class is
duplicated from ``mrr.domain.identity.URN_PATTERN`` (not imported and
re-parameterized — that pattern's own ``entity`` group is not
substitutable from outside its compiled form) — a disclosed, minor
duplication, mirroring ``mrr.domain.artifacts.Classification``'s own
documented precedent ("a small enum appearing in a third place is
consistent with a pattern that already appears in two").

Because this contract-level pattern is intentionally REDUNDANT with
``ReleaseService.create``'s own explicit ``mrr.domain.identity.URN_PATTERN``
check (task-packets/E8-T04.yaml R2's own "the service refuses ... an
approved_by that is not a person-segment URN"), a caller cannot bypass the
gate by going around either layer alone — see that service module's own
docstring, "Deliberate deviation: create() takes RAW approval inputs, not a
pre-built ReleaseRecord", for the full reasoning of why BOTH layers exist and
which one a unit test exercises via ``model_construct`` to reach the
service's own defensive branch directly.

--- approval_mode: a NEW two-value Literal, deliberately NOT
    mrr.contracts.common.ApprovalMode -------------------------------------

``mrr.contracts.common.ApprovalMode`` is a DIFFERENT, pre-existing three-value
vocabulary (``automatic``/``human``/``dual`` — NodeManifest/TaskBundle's own
EXECUTION approval requirement). ADR-0011 decision 1 names its own, narrower
two-value vocabulary for a release's own A4 human act
(``single_human``/``dual``) — reusing the wider ``ApprovalMode`` would let a
hand-constructed ``ReleaseRecord`` accept ``"automatic"`` as a release
approval mode, which is precisely the kind of "no default, no inference"
A4 gate this object exists to prevent. Kept as its OWN ``Literal``,
``ReleaseApprovalMode`` (never imported from ``mrr.contracts.common``),
mirroring ``mrr.contracts.common.BaseObjectClassification``'s own documented
precedent for exactly this "same-shaped but semantically distinct enum, kept
separate to avoid drift by construction" situation.

--- Bundle.files: sorted, non-duplicate, enforced by model_validator --------

The schema's own ``files`` array cannot cleanly express "sorted by path" as
a JSON Schema constraint over array element order; this module enforces it
in Python instead (mirrors ``mrr.contracts.verification_result
.NumericRecomputation``'s own precedent of a hand-written validator for an
invariant no plain per-field JSON Schema constraint can express). This
mirrors, but does not replace, ``mrr.services.release.manifest``'s own
independent recomputation of the SAME sortedness at the service/CLI layer —
this contract-level check catches a malformed ``ReleaseRecord`` the instant
Python code constructs one, exactly as domain 2.13's own
if/then-plus-model_validator double-enforcement precedent already
establishes for this codebase.

--- No signature field --------------------------------------------------------

ADR-0011 decision 1 names no ``signature`` field for ``ReleaseRecord`` (unlike
``TaskBundle``/``EvidenceCrate``'s own origin/node signatures) — the human
approval act itself (``approval.approved_by`` plus the atomically-written
``release.approved`` event, ``mrr.services.release.service``) IS the
recorded authorization; a release is a LOCAL practice act, not a
cross-practice transfer needing AGENTS.md rule 9's signature+hash
verification. Create-only, like ``mrr.contracts.source_family.SourceFamily``
and ``mrr.contracts.research_decision.ResearchDecision``: no update/mutate
method exists anywhere in ``mrr.services.release`` (immutability by
omission); ``status`` becomes ``superseded`` only via a future E8-T05 task's
own, separate revision-2 write, out of this task's scope.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Urn
from pydantic import Field, StringConstraints, model_validator

__all__ = [
    "Approval",
    "Bundle",
    "BundleFile",
    "Disclosure",
    "PersonUrn",
    "ReleaseApprovalMode",
    "ReleaseRecord",
    "ReleaseStatus",
]

#: ADR-0011 decision 1: ``approval.approved_by`` must be a PERSON urn. See
#: the module docstring's "approved_by: PERSON urn" section for why the ULID
#: character class is duplicated here rather than reused from
#: ``mrr.domain.identity.URN_PATTERN`` directly.
_PERSON_URN_PATTERN = r"^urn:mrr:person:[0-9A-HJKMNP-TV-Z]{26}$"

PersonUrn = Annotated[str, StringConstraints(pattern=_PERSON_URN_PATTERN)]

#: Mirrors `disclosure` — MRR-FR-095's two-value vocabulary
#: (``mrr.domain.research_report.Disclosure``'s own identical Literal,
#: re-declared here rather than imported: ``mrr.contracts`` does not import
#: ``mrr.domain.research_report``, a report-projection-specific module, for
#: one two-value string type shared by name only).
Disclosure = Literal["internal", "public"]

#: Mirrors `approval.approval_mode` — see the module docstring's
#: "approval_mode: a NEW two-value Literal" section for why this is not
#: ``mrr.contracts.common.ApprovalMode``.
ReleaseApprovalMode = Literal["single_human", "dual"]

#: Mirrors `status`. ``mrr.domain.lifecycles.RELEASE_RECORD_LIFECYCLE``'s
#: single legal edge is ``released -> superseded``.
ReleaseStatus = Literal["released", "superseded"]


class BundleFile(MRRModel):
    """Mirrors one `bundle.files[]` entry."""

    path: str = Field(min_length=1)
    sha256: Sha256


class Bundle(MRRModel):
    """Mirrors `bundle`. See the module docstring's "Bundle.files: sorted,
    non-duplicate" section for why `_files_sorted_and_unique` exists.
    """

    files: list[BundleFile]
    root_hash: Sha256

    @model_validator(mode="after")
    def _files_sorted_and_unique(self) -> Self:
        paths = [entry.path for entry in self.files]
        if paths != sorted(paths):
            raise ValueError(
                "bundle.files must be sorted by path (ADR-0011 decision 1: "
                "'files: sorted [{path, sha256}]')"
            )
        if len(set(paths)) != len(paths):
            raise ValueError("bundle.files must not contain duplicate paths")
        return self


class Approval(MRRModel):
    """Mirrors `approval` — the A4 human act itself. Every field is
    required; none carries a default anywhere on this model (MRR-FR-102:
    "no default, no inference" is a property of the CALLER never being able
    to omit these three values and still end up with a valid ``Approval``).
    """

    approved_by: PersonUrn
    approval_statement: str = Field(min_length=1)
    approval_mode: ReleaseApprovalMode


class ReleaseRecord(BaseObject):
    """Mirrors schemas/release-record.schema.json. See the module docstring
    for the full design rationale.
    """

    kind: Literal["ReleaseRecord"]
    crate_id: Urn
    disclosure: Disclosure
    bundle: Bundle
    approval: Approval
    status: ReleaseStatus
