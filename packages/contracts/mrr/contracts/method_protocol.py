"""Mirrors schemas/method-protocol.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 3 "Method-governance objects (Layer 1)",
AS AMENDED by commit 1d453bf "Spec-08-Amendment
MethodProtocol-Re-Review-Zyklus"). Third and heaviest of the six
task-packets/K1-T01.yaml entities.

--- Lifecycle: draft -> reviewed -> locked -> amended | executed; amended -> reviewed ---

Spec 08 section 3's table, as amended, reads: "draft -> reviewed -> locked ->
amended | executed; amended -> reviewed (re-review cycle: an amendment is a
new revision that must be re-reviewed and re-locked before further
confirmatory work; each lock binds that revision's own content hash, so work
recorded under an earlier lock stays auditable against its own hash)". This
is realized by ``mrr.domain.lifecycles.METHOD_PROTOCOL_LIFECYCLE`` as five
edges — ``(draft, reviewed)``, ``(reviewed, locked)``, ``(locked, amended)``,
``(locked, executed)``, ``(amended, reviewed)`` — with ``executed`` the
machine's only terminal state (``amended`` is no longer a dead end under the
amendment: it re-enters review, and from there may be re-locked and
eventually executed, or amended again, without ever declaring a
self-transition). ``(amended, executed)`` and ``(amended, amended)`` remain
UNDRAWN and illegal — an amended protocol must pass back through a fresh
``reviewed``/``locked`` pair before further confirmatory work or a second
amendment, exactly as the amendment's own prose describes.

--- Lock hash IS baseObject.content_hash, not a new field (MRR-MTH-007) -------

"Locking binds the exact content hash, actor, and time" is NOT a new,
separately-computed field on this model. It IS the locked revision's own
already-existing ``BaseObject.content_hash`` (schemas/common.schema.json
`$defs.baseObject`, computed per ADR-0004 by
``mrr.domain.hashing_policy.compute_content_hash``, unchanged, not touched
by this task) — once a revision is inserted via the existing, unchanged,
append-only ``ObjectRepository.insert_revision``, that revision's own
``content_hash`` is permanently addressable by ``(id, revision)`` for as
long as the object exists. This module adds exactly two NEW body fields
recording the two facts ``content_hash`` alone does not carry — WHO locked
it and WHEN — ``locked_by: Urn`` and ``locked_at: AwareDatetime``, both
required non-null exactly when ``status`` is ``locked``/``amended``/
``executed`` (the lock, once made, is a permanent historical fact carried
forward on every later revision of the same object id, including a
post-amendment ``reviewed`` revision reverting them to null — see the
co-occurrence validator below) and both required null on
``draft``/``reviewed``.

--- The `amendment` block (MRR-MTH-008) ---------------------------------------

"Post-lock changes MUST be amendments: a new revision recording reason,
actor, and whether outcome information had been observed" is realized as one
nested object, ``ProtocolAmendment`` (``reason``, ``actor``, ``amended_at``,
``outcome_information_observed``, and ``amended_locked_content_hash`` — an
explicit back-reference to the EXACT locked revision's own ``content_hash``
being amended), required non-null exactly when ``status == "amended"`` and
required null otherwise. ``outcome_information_observed`` is a plain,
undecorated ``bool`` with no default — MTH-008's "demotes affected analyses
to exploratory" consequence is explicitly NOT implemented here (that
demotion acts on downstream Claims/analyses, a K1-T02/K1-T03 concern); this
module only makes the FACT machine-recordable, unconditionally, on every
amendment.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Urn
from pydantic import AwareDatetime, Field, model_validator

__all__ = ["MethodProtocol", "MethodProtocolStatus", "ProtocolAmendment"]

#: Mirrors schemas/method-protocol.schema.json's `status` enum, as amended
#: (commit 1d453bf): draft -> reviewed -> locked -> amended | executed;
#: amended -> reviewed. See mrr.domain.lifecycles.METHOD_PROTOCOL_LIFECYCLE
#: for the declared edge set.
MethodProtocolStatus = Literal["draft", "reviewed", "locked", "amended", "executed"]


class ProtocolAmendment(MRRModel):
    """Mirrors the `amendment` object (MRR-MTH-008). Every field is required
    unconditionally whenever this object is present at all — there is no
    partial amendment record.
    """

    reason: str = Field(min_length=1)
    actor: Urn
    amended_at: AwareDatetime
    outcome_information_observed: bool
    amended_locked_content_hash: Sha256


class MethodProtocol(BaseObject):
    """Mirrors schemas/method-protocol.schema.json.

    Every property in the schema's top-level `required` list is required
    here too. `locked_at`, `locked_by`, and `amendment` are all explicitly
    nullable and individually optional (default `None`), per this
    codebase's universal `model_dump_json(exclude_none=True)` round-trip
    convention — their co-occurrence with `status` is enforced by the two
    validators below, not by Python defaults or schema `required`.
    """

    kind: Literal["MethodProtocol"]
    profile_id: Urn
    extraction_fields: list[str]
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    sensitivity_variations: list[str]
    planned_analyses: list[str] = Field(min_length=1)
    kill_conditions: list[str] = Field(min_length=1)
    locked_at: AwareDatetime | None = None
    locked_by: Urn | None = None
    amendment: ProtocolAmendment | None = None
    status: MethodProtocolStatus

    @model_validator(mode="after")
    def _lock_fields_match_status(self) -> Self:
        """MRR-MTH-007: `locked_at`/`locked_by` are both non-null exactly
        when `status` is one of `locked`/`amended`/`executed`, and both null
        exactly when `status` is `draft`/`reviewed`.
        """
        lock_expected = self.status in ("locked", "amended", "executed")
        lock_present = self.locked_at is not None and self.locked_by is not None
        lock_absent = self.locked_at is None and self.locked_by is None

        if lock_expected and not lock_present:
            raise ValueError(
                f"MethodProtocol status {self.status!r} requires both locked_at and "
                "locked_by to be non-null (MRR-MTH-007)"
            )
        if not lock_expected and not lock_absent:
            raise ValueError(
                f"MethodProtocol status {self.status!r} requires both locked_at and "
                "locked_by to be null"
            )
        return self

    @model_validator(mode="after")
    def _amendment_matches_status(self) -> Self:
        """MRR-MTH-008: `amendment` is non-null exactly when
        `status == "amended"`, and null for every other status.
        """
        if self.status == "amended" and self.amendment is None:
            raise ValueError(
                "MethodProtocol status 'amended' requires a non-null amendment block (MRR-MTH-008)"
            )
        if self.status != "amended" and self.amendment is not None:
            raise ValueError(
                f"MethodProtocol status {self.status!r} must not carry a non-null "
                "amendment block (MRR-MTH-008)"
            )
        return self
