"""Mirrors schemas/concept-charter.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 3 "Method-governance objects (Layer 1)").
Second of the six task-packets/K1-T01.yaml entities.

``entries`` is required non-empty (``Field(min_length=1)``, mirroring
``mrr.contracts.source_family.SourceFamily.member_source_ids``'s own
identical non-degeneracy precedent: a charter with zero entries
operationalizes nothing). Each entry carries its own ``entry_id`` — required
unique within one ``ConceptCharter`` revision, enforced by the
``model_validator`` below (not expressible as a plain JSON Schema
per-field constraint) — because spec 08 section 3's ``operationalizes`` edge
description (ConceptCharter entry -> QuestionModel term) points at ONE
entry, not the whole charter, and MRR-MTH-018's "versioned charter entries"
(referenced by ``mrr.contracts.method_protocol.MethodProtocol.
sensitivity_variations``) needs a stable handle finer-grained than the
charter's own ``baseObject.id``. The ``operationalizes`` edge itself is
explicitly task-packets/K1-T02.yaml's addition (forbidden_changes) — these
entries exist as addressable data ahead of that edge.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel
from pydantic import Field, model_validator

__all__ = ["ConceptCharter", "ConceptCharterEntry", "ConceptCharterStatus"]

#: Mirrors schemas/concept-charter.schema.json's `status` enum — spec 08
#: section 3's table: "ConceptCharter | ... | draft -> accepted -> superseded".
ConceptCharterStatus = Literal["draft", "accepted", "superseded"]


class ConceptCharterEntry(MRRModel):
    """Mirrors one entry of `entries`. `scope_note` is the only field absent
    from the schema item's own `required` list — explicitly nullable, per
    this codebase's universal `model_dump_json(exclude_none=True)` round-trip
    convention.
    """

    entry_id: str = Field(min_length=1)
    term: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    scope_note: str | None = None


class ConceptCharter(BaseObject):
    """Mirrors schemas/concept-charter.schema.json.

    Every property in the schema's top-level `required` list is required
    here too.
    """

    kind: Literal["ConceptCharter"]
    entries: list[ConceptCharterEntry] = Field(min_length=1)
    status: ConceptCharterStatus

    @model_validator(mode="after")
    def _entry_ids_are_unique(self) -> Self:
        """Mirrors the schema's own docstring-equivalent invariant: no two
        entries within one ConceptCharter revision may share an `entry_id`.
        """
        seen: set[str] = set()
        for entry in self.entries:
            if entry.entry_id in seen:
                raise ValueError(
                    f"duplicate ConceptCharter entry_id {entry.entry_id!r} — entry_id "
                    "must be unique within one ConceptCharter revision"
                )
            seen.add(entry.entry_id)
        return self
