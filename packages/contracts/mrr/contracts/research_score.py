"""Mirrors schemas/research-score.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.3, "ResearchScore": the authorized research envelope).

Field presence below follows the schema's top-level `required` list exactly:
`background`, `starting_assumptions`, and `ethics_references` are the only
three properties absent from it, so they are the only three with Pydantic
defaults (making them optional). Every other field is mandatory — including
ones whose array type has no `minItems` (e.g. `non_goals`, `approvals`):
the schema still requires the *key* to be present (an empty list is fine,
but a missing key is not), so those get no default either.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mrr.contracts.common import (
    AutonomyLevel,
    BaseObject,
    Budget,
    Classification,
    MRRModel,
    Scope,
    Urn,
)
from pydantic import Field, StringConstraints

#: Mirrors `publication_policy.max_disclosure`.
MaxDisclosure = Literal["INTERNAL", "PARTNER_RESTRICTED", "PUBLIC"]

#: Mirrors the top-level `status` enum.
ResearchScoreStatus = Literal[
    "DRAFT",
    "IN_REVIEW",
    "APPROVED",
    "ACTIVE",
    "REJECTED",
    "SUSPENDED",
    "SUPERSEDED",
    "ARCHIVED",
]


class MethodsPolicy(MRRModel):
    """Mirrors the `methods` object: both `allowed` and `prohibited` are
    required keys (each may be an empty list; the schema has no `minItems`
    on either — but the key itself must be present).
    """

    allowed: list[str]
    prohibited: list[str]


class PublicationPolicy(MRRModel):
    """Mirrors the `publication_policy` object. `external_publication_requires_approval`
    is a JSON Schema `const: true`, so it is a fixed `Literal[True]` rather than a plain
    bool.
    """

    max_disclosure: MaxDisclosure
    external_publication_requires_approval: Literal[True]


class ResearchScore(BaseObject):
    """Mirrors schemas/research-score.schema.json."""

    kind: Literal["ResearchScore"]
    question: str = Field(min_length=10)
    background: str | None = None
    # `items` in the schema additionally requires `minLength: 1` per string,
    # on top of the array's own `minItems: 1` — the one field in this schema
    # with both constraints stacked.
    objectives: list[Annotated[str, StringConstraints(min_length=1)]] = Field(min_length=1)
    non_goals: list[str]
    scope: Scope
    starting_assumptions: list[str] = Field(default_factory=list)
    methods: MethodsPolicy
    data_classes: list[Classification] = Field(min_length=1)
    ethics_references: list[str] = Field(default_factory=list)
    autonomy: dict[str, AutonomyLevel]
    budgets: Budget
    quality_gates: list[str] = Field(min_length=1)
    stop_conditions: list[str] = Field(min_length=1)
    publication_policy: PublicationPolicy
    status: ResearchScoreStatus
    approvals: list[Urn]
