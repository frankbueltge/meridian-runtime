"""Mirrors schemas/question-model.schema.json (docs/spec/08_RESEARCH_METHOD_KERNEL.md,
status ACCEPTED 2026-07-21, section 3 "Method-governance objects (Layer 1)").
First of the six task-packets/K1-T01.yaml entities.

MRR-MTH-001: a question addressed through the runtime MUST be represented as
an accepted ``QuestionModel`` before an executor task for it is negotiated; a
raw natural-language question MUST NOT directly create an executable
analysis task. This module makes a ``QuestionModel`` a first-class,
independently reviewable object with its own lifecycle
(``mrr.domain.lifecycles.QUESTION_MODEL_LIFECYCLE``), distinct from and
prior to any ``TaskBundle`` — it does not itself enforce that a
``TaskBundle`` actually references an accepted one (a repository lookup,
service-level, deferred to task-packets/K1-T02.yaml/K1-T03.yaml).

``claim_type_sought`` reuses ``mrr.contracts.claim.ClaimType`` verbatim
(singular — the one claim type this question seeks, unlike
``MethodProfile.claim_types``'s plural set a profile CAN produce).
``scope`` reuses ``mrr.contracts.common.Scope`` verbatim for spec 08's
"population/scope/time" rather than inventing a parallel shape.
``load_bearing_terms`` is schema-present but NOT required non-empty,
mirroring ``MethodProfile.inappropriate_uses``'s own identical judgment call
(task-packets/K0-T01.yaml specification_gaps; flagged again here in
task-packets/K1-T01.yaml specification_gaps).
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.claim import ClaimType
from mrr.contracts.common import BaseObject, Scope
from pydantic import Field

__all__ = ["QuestionModel", "QuestionModelStatus"]

#: Mirrors schemas/question-model.schema.json's `status` enum — spec 08
#: section 3's table: "QuestionModel | ... | draft -> accepted -> superseded".
QuestionModelStatus = Literal["draft", "accepted", "superseded"]


class QuestionModel(BaseObject):
    """Mirrors schemas/question-model.schema.json.

    Every property in the schema's top-level `required` list is required
    here too — including `load_bearing_terms`, which allows an empty list
    but not an absent key (see the module docstring).
    """

    kind: Literal["QuestionModel"]
    raw_question: str = Field(min_length=1)
    claim_type_sought: ClaimType
    scope: Scope
    load_bearing_terms: list[str]
    status: QuestionModelStatus
