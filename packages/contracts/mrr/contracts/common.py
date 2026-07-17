"""Shared Pydantic v2 building blocks mirroring the ``$defs`` in
schemas/common.schema.json: ``Signature``, ``ArtifactRef``, ``Scope``,
``Budget``, and the ``BaseObject`` model with the eight required base fields
from docs/spec/02_DOMAIN_MODEL.md section 1 ("Every first-class object MUST
contain ...").

The ``urn`` and ``sha256`` string patterns are **not** re-declared here. They
already live in E1-T02 (``mrr.domain.identity.URN_PATTERN``,
``mrr.crypto.hashing.SHA256_PATTERN``) as the single source of truth for
those formats; re-declaring the regex here would let the schema-facing
Pydantic copy and the domain-facing copy drift apart silently. ``Urn`` and
``Sha256`` below are thin ``Annotated`` aliases over those same compiled
patterns.

Every model in ``mrr.contracts`` derives from ``MRRModel``, which sets
``extra="forbid"``. Every entity schema in schemas/ combines
``common.schema.json#/$defs/baseObject`` with its own fragment via ``allOf``
and then closes the whole object with ``"unevaluatedProperties": false`` —
i.e. no field outside the ones enumerated across both fragments is
permitted anywhere. ``extra="forbid"`` on every model (including the nested
ones defined here) is the Pydantic-side mirror of that closure.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mrr.crypto.hashing import SHA256_PATTERN
from mrr.domain.identity import URN_PATTERN
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

#: Mirrors schemas/common.schema.json `$defs.urn`, via the exact compiled
#: pattern mrr.domain.identity already validates URNs with.
Urn = Annotated[str, StringConstraints(pattern=URN_PATTERN.pattern)]

#: Mirrors schemas/common.schema.json `$defs.sha256`, via the exact compiled
#: pattern mrr.crypto.hashing already validates content hashes with.
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN.pattern)]

#: Mirrors `$defs.baseObject.properties.api_version` (JSON Schema `const`).
ApiVersion = Literal["mrr/v1alpha1"]

#: Mirrors the repeated classification enum (docs/spec/02_DOMAIN_MODEL.md
#: section 4; used inline by several schemas, e.g. `$defs.artifactRef`,
#: research-score.schema.json `data_classes`, task-bundle.schema.json
#: `classification`).
Classification = Literal[
    "PUBLIC",
    "INTERNAL",
    "RESTRICTED",
    "SENSITIVE",
    "PARTICIPANT_IDENTIFIABLE",
]

#: Mirrors the repeated autonomy-ceiling enum (used inline by
#: research-score.schema.json `autonomy` and node-manifest.schema.json
#: `capabilities[].max_autonomy`).
AutonomyLevel = Literal["A0", "A1", "A2", "A3", "A4"]

#: Mirrors the repeated approval-mode enum (used inline by
#: node-manifest.schema.json `capabilities[].approval` and
#: task-bundle.schema.json `approval_requirement`).
ApprovalMode = Literal["automatic", "human", "dual"]


class MRRModel(BaseModel):
    """Base class for every MRR contract model.

    ``extra="forbid"`` rejects any field not declared on the model, matching
    the ``additionalProperties: false`` / ``unevaluatedProperties: false``
    closure every schema in schemas/ applies to itself.
    """

    model_config = ConfigDict(extra="forbid")


class Signature(MRRModel):
    """Mirrors schemas/common.schema.json `$defs.signature`
    (docs/spec/02_DOMAIN_MODEL.md section 1.3: signer, key, algorithm,
    signature, signed-at timestamp).
    """

    signer_practice_id: Urn
    key_id: str = Field(min_length=1)
    algorithm: Literal["Ed25519"]
    signed_at: AwareDatetime
    value: str = Field(min_length=40)


class ArtifactRef(MRRModel):
    """Mirrors schemas/common.schema.json `$defs.artifactRef`.

    ``classification`` is optional in the schema (absent from its
    ``required`` list), hence ``None`` here rather than a mandatory field.
    """

    artifact_id: Urn
    content_hash: Sha256
    classification: Classification | None = None


class Scope(MRRModel):
    """Mirrors schemas/common.schema.json `$defs.scope`.

    None of the four properties are required by the schema. ``population``,
    ``time``, and ``geography`` are explicitly nullable
    (``type: ["string", "null"]``); ``conditions`` is a plain (non-nullable)
    array, so it defaults to an empty list rather than ``None``.
    """

    population: str | None = None
    time: str | None = None
    geography: str | None = None
    conditions: list[str] = Field(default_factory=list)


class Budget(MRRModel):
    """Mirrors schemas/common.schema.json `$defs.budget`.

    None of the five properties are required, and none allow an explicit
    JSON ``null`` (each has a plain scalar type). They default to ``None``
    to mean "not stated"; callers serializing to schema-valid JSON must drop
    unset fields rather than emit them as ``null`` (see
    ``mrr.contracts`` round-trip helpers / scripts/check_contracts.py, which
    dump with ``exclude_none=True`` for exactly this reason).
    """

    currency: str | None = Field(default=None, min_length=3, max_length=3)
    money_limit: float | None = Field(default=None, ge=0)
    compute_seconds: int | None = Field(default=None, ge=0)
    model_tokens: int | None = Field(default=None, ge=0)
    human_review_minutes: int | None = Field(default=None, ge=0)


class BaseObject(MRRModel):
    """Mirrors schemas/common.schema.json `$defs.baseObject`
    (docs/spec/02_DOMAIN_MODEL.md section 1: the eight fields every
    first-class object MUST contain, plus the two optional ones).

    Entity modules subclass this and narrow ``kind`` to a ``Literal`` of the
    entity's own name (schemas do the equivalent with a JSON Schema
    ``const``).
    """

    id: Urn
    api_version: ApiVersion
    kind: str = Field(min_length=1)
    practice_id: Urn
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    created_by: Urn
    content_hash: Sha256
    supersedes: Urn | None = None
    labels: dict[str, str] | None = None
