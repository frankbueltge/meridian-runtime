"""Mirrors schemas/source-record.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.8, "SourceRecord": describes an external or local source).

--- Metadata is distinct from content (domain 2.8's own invariant) ----------

"Invariant: source metadata and source content are distinct. A correct DOI
does not prove that a claim is supported." This module carries every field
domain 2.8 lists as SourceRecord's own descriptive metadata (identifiers,
title, creators, publication data, retrieval provenance, classification,
accessibility/licensing) and deliberately carries NOTHING that expresses
whether this source supports, contradicts, qualifies, or contextualizes any
proposition — no boolean "verified"/"supports_claim" field, no confidence
score. That relation lives exclusively on a separate ``EvidenceAnchor``
(``mrr.contracts.evidence_anchor``), which references a ``SourceRecord`` by
id (``source_record_id``) rather than this object carrying any evidentiary
judgment about itself.

--- source_type is a free string, not an enum -------------------------------

Domain 2.8 lists "source type" as a field without enumerating values
anywhere in docs/spec/ (confirmed by a full-repository search before writing
this module — unlike, say, ``claim_type`` or ``run_state``, which the
specification does enumerate explicitly). Inventing a fixed vocabulary here
would be exactly the kind of unspecified domain behavior AGENTS.md rule 3
forbids, so ``source_type`` stays a plain non-empty string.

--- identifiers is a required key, not a required identifier ----------------

The ``identifiers`` object is itself required (mirroring
``mrr.contracts.run_manifest.RunManifest.environment``'s "always present,
possibly all-absent-inside" pattern) but none of its four members (``doi``,
``repository_id``, ``archive_id``, ``local_asset_id``) are individually
required — domain 2.8 lists them as alternatives ("stable identifiers such
as DOI, repository ID, archive identifier, or local asset ID"), not a
mandatory set.
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Urn
from pydantic import AwareDatetime, Field

#: Mirrors `primary_secondary_derived`.
SourceClassification = Literal["primary", "secondary", "derived"]


class SourceIdentifiers(MRRModel):
    """Mirrors the `identifiers` object. See the module docstring's
    "identifiers is a required key, not a required identifier" section for
    why none of these four are individually required.
    """

    doi: str | None = None
    repository_id: str | None = None
    archive_id: str | None = None
    local_asset_id: str | None = None


class SourceRecord(BaseObject):
    """Mirrors schemas/source-record.schema.json.

    Every property is in the schema's top-level `required` list except
    `publication_date`, `version`, `snapshot_artifact_hash`,
    `source_family_id`, `derivation_evidence`, `accessibility`, and
    `licensing` — all explicitly nullable (`anyOf [<type>, {"type": "null"}]`
    in the schema) and, per this codebase's universal
    `model_dump_json(exclude_none=True)` round-trip convention (see
    `mrr.contracts.run_manifest`'s own docstring for the full reasoning),
    deliberately absent from `required` so a `None` value round-trips
    correctly instead of disappearing into a required-but-missing key.

    `source_family_id` links to a future `SourceFamily` (domain 2.10) — not
    itself implemented by this module (task-packets/E3-T01.yaml explicitly
    scopes source-family representation to E3-T03); it is carried here as a
    bare, structurally-validated but semantically-unresolved URN.
    """

    kind: Literal["SourceRecord"]
    identifiers: SourceIdentifiers
    title: str = Field(min_length=1)
    creators: list[str]
    publication_date: str | None = None
    version: str | None = None
    retrieval_timestamp: AwareDatetime
    retrieval_method: str = Field(min_length=1)
    snapshot_artifact_hash: Sha256 | None = None
    source_type: str = Field(min_length=1)
    primary_secondary_derived: SourceClassification
    source_family_id: Urn | None = None
    derivation_evidence: str | None = None
    accessibility: dict[str, str] | None = None
    licensing: dict[str, str] | None = None
