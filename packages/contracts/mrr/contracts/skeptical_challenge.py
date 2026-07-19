"""Mirrors schemas/skeptical-challenge.schema.json (docs/spec/01_SYSTEM_SPEC.md
section 4.8, "Stage 8 — Skepticism and independent verification", MRR-FR-074:
"The skeptic MUST search for counterevidence, alternative explanations, scope
leakage, and hidden assumptions."). Fourth task of Epic E4 (task-packets/
E4-T04.yaml); the fifteenth entity schema/model pair in this repository.

--- challenge_type: a closed, four-value enum, no fifth value --------------

MRR-FR-074 names exactly four challenge kinds. ``ChallengeType`` is a closed
``Literal`` of exactly those four strings, in the exact order MRR-FR-074
states them. ``CHALLENGE_TYPES`` names the same four values, in the same
order, as a plain tuple — mirroring ``mrr.contracts.hypothesis.BranchRole``/
``BRANCH_ROLES``'s own "one Literal, one tuple, same order everywhere"
precedent, so this task's own skeptic role (``mrr.services.skeptic.
service``) shares ONE source of truth for the vocabulary with this contract
and cannot silently drift from it.

--- Pins its target and its producer by reference, never by embedding -----

Mirrors ``mrr.contracts.model_invocation.ModelInvocation``'s own
"references a ModelProfile by id and pinned content hash rather than
embedding one" precedent, applied TWICE here:

- ``target_claim_id``/``target_claim_hash`` pin the ``mrr.contracts.claim.
  Claim`` this challenge targets — the caller-supplied id and content hash
  of that claim, never the claim object itself (this module does not import
  ``mrr.contracts.claim``; the skeptic role that builds a
  ``SkepticalChallenge`` does not fetch or persist the claim it challenges
  either, per task-packets/E4-T04.yaml forbidden_changes).
- ``producing_model_profile_id``/``producing_model_profile_hash`` pin the
  ``mrr.contracts.model_profile.ModelProfile`` that produced this challenge,
  so independence LINEAGE is RECORDED here — never COMPUTED. Independence
  computation stays ``mrr.domain.independence`` (E3-T05), wired by the
  verifier (E4-T05, out of this task's scope); this model only carries the
  two reference fields a later independence calculation would consume.

--- A SkepticalChallenge is a PROPOSAL, never a verdict (MRR-FR-070) -------

This model declares no ``verdict``/``decision``/``verified``/``resolved``/
``claim_status`` field of any kind, anywhere — mirroring
``mrr.contracts.hypothesis.Hypothesis``'s own "not a claim of result"
precedent and ``mrr.contracts.model_invocation.ModelInvocation``'s own
"there is structurally no field anywhere on this object a caller could set
to mark a response accepted" precedent. The skeptic role
(``mrr.services.skeptic.service``) issues no verification decision and
changes no claim's status; MRR-FR-070/075 belong to the verifier (E4-T05,
out of this task's scope).

--- supporting_source_ids: optional, plain URNs, not a spec-given structure ---

Not a docs/spec/02_DOMAIN_MODEL.md-given field — this task's own minimal,
necessary addition (task-packets/E4-T04.yaml derived_decisions: the
skeptic's LLM-facing content includes "optional supporting source
references"), the same kind of documented, flagged addition
``mrr.contracts.evidence_anchor.EvidenceAnchor.anchor_unavailable_reason``
already is for its entity — flagged as an open specification question in
this task's PR body. Modeled as a plain list of URNs (e.g. of
``mrr.contracts.source_record.SourceRecord``/``mrr.contracts.evidence_anchor.
EvidenceAnchor`` objects the model cited), mirroring
``mrr.contracts.hypothesis.Hypothesis.dependencies``'s own "URNs of other
objects" precedent — not required by the schema (a challenge may cite no
source), defaulting to an empty list.
"""

from __future__ import annotations

from typing import Literal

from mrr.contracts.common import BaseObject, Sha256, Urn
from pydantic import Field

#: MRR-FR-074: exactly these four challenge kinds, closed — no fifth value.
ChallengeType = Literal[
    "counterevidence",
    "alternative_explanation",
    "scope_leakage",
    "hidden_assumption",
]

#: The four kinds above, in the exact same order everywhere the vocabulary is
#: enumerated (this tuple, the schema `enum`, and the skeptic's own coverage
#: check) — single-sourced so the "exactly four, this order" fact cannot
#: silently drift between the contract and the skeptic role.
CHALLENGE_TYPES: tuple[ChallengeType, ...] = (
    "counterevidence",
    "alternative_explanation",
    "scope_leakage",
    "hidden_assumption",
)


class SkepticalChallenge(BaseObject):
    """Mirrors schemas/skeptical-challenge.schema.json.

    Every property is in the schema's top-level `required` list except
    `supporting_source_ids` — the one field with a Python default of an
    empty list here (a plain, non-nullable schema type omitted from
    `required`, matching `mrr.contracts.model_profile.ModelProfile.
    tool_permissions`'s own precedent).
    """

    kind: Literal["SkepticalChallenge"]
    challenge_type: ChallengeType
    target_claim_id: Urn
    target_claim_hash: Sha256
    statement: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    supporting_source_ids: list[Urn] = Field(default_factory=list)
    producing_model_profile_id: Urn
    producing_model_profile_hash: Sha256


__all__ = [
    "CHALLENGE_TYPES",
    "ChallengeType",
    "SkepticalChallenge",
]
