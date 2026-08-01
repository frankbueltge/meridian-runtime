"""The evidence-relation proposal set (task-packets/N1-T04.yaml R1/R2/R3): a
Pydantic-validated projection of what a model proposed for each case of a
blind classification commission, and — in the same bytes — a
``mrr validate gold --predictions`` input.

--- Why "verified" cannot be produced here ---------------------------------

MRR-MTH-016 requires every model-assisted step to record a verification
disposition from a closed three-value vocabulary: ``verified`` /
``downgraded-to-proposal`` / ``rejected``. The same specification line, in
docs/spec/08_RESEARCH_METHOD_KERNEL.md section 5, says what earns the first
of those words: model-assisted steps produce proposals "**verified against
the anchored source** or downgraded to marked proposals".

This module's path performs no verification against an anchored source. It
sends an excerpt and a criteria set to a model and records what came back.
So every proposal it carries is :data:`DOWNGRADED_TO_PROPOSAL`, and
:data:`VERIFIED` and :data:`REJECTED` are unreachable — not by a rule bolted
on afterwards, but because neither describes what happened.

That ordering matters, because the alternative was proposed twice and is
wrong twice. Both the 2026-08-02 handoff and the N1-T02 derivation suggested
naming this disposition ``"schema-valid"``, on the reasoning that the
disposition should say what it measures. It should — but a fourth value
extends a closed MUST vocabulary (AGENTS.md rule 4), and it is not needed:
``downgraded-to-proposal`` is already the exact name for a proposal nobody
verified.

The live counter-example is in this repository.
``mrr.services.node_runtime.synthesis_executor``'s extraction arm sets
``verification_disposition="verified"`` as soon as the model returns
schema-valid JSON. Nothing was verified there either. That is the MTH-016
violation; this module is its deliberate opposite, and the repair of the
other arm is a separate packet (see the N1-T04 derivation, section 8).

--- Label isolation is a property of the type ------------------------------

:class:`RelationCase` is what a prompt gets to see, and it has no field able
to hold an expected relation. This mirrors ``benchmarks.meridianbench.
harness``'s ``BenchmarkCase``/``SystemUnderTest`` split (E4-T07), where the
label is typewise unreachable rather than merely unused, and N1-T02's own
AT5. A convention that the caller "must not pass the label" is a different
and weaker object than a type that cannot carry one.

The loader that builds these from a commission file refuses a case document
carrying any of :data:`FORBIDDEN_CASE_KEYS` rather than dropping them
quietly — see ``mrr.services.classification.relation_service``. Silently
ignoring a label in the input is how a blind run stops being blind while
still reporting that it was one.

--- Undecidable is an outcome, not a failure -------------------------------

The criteria's ``R-undecidable-is-a-finding`` says a case that cannot be
decided is marked and counted, never distributed into the four labels,
because a criteria set's coverage is a measurable property of it. A system
classifying under those criteria needs the same move available, or its
coverage cannot be compared with the standard's own — the gold set holds
three of sixty out of its matrix for exactly this reason.

So :attr:`RelationProposal.undecidable` is a decided outcome: it carries a
rationale and a disposition, and it yields no prediction. It is a different
object from a case where generation failed, which carries neither.

--- Determinism ------------------------------------------------------------

No wall clock anywhere. Every rendered byte is a function of the inputs and
of what the adapter returned, so two runs over identical inputs and identical
adapter responses produce byte-identical output.

The reason is on the record rather than a preference: a hand-typed timestamp
inside an apparatus that gates on time was mistyped within hours on
2026-08-01 and blocked its own order gate, and the refusal read as a finding
about the labelling practice when it was a defect in the reference clock.
When a run happened is a property of its commit and its workflow run, not of
a field that can come loose from the act it describes.
"""

from __future__ import annotations

import json
from typing import Final, Literal

from mrr.contracts.common import MRRModel
from pydantic import Field, model_validator

#: The four categories of the MB-CLS label space, in the fixed order the
#: criteria and the gold set both declare them.
RELATION_CATEGORIES: Final[tuple[str, ...]] = (
    "supports",
    "contradicts",
    "qualifies",
    "contextualizes",
)

#: MRR-MTH-016's closed vocabulary, re-declared here rather than imported.
#:
#: The identical Literal lives on ``mrr.services.node_runtime.
#: synthesis_executor.ExtractionOutcome``, but ``mrr.domain`` may not import
#: ``mrr.services`` — the import-linter contract "Nothing inward imports the
#: services layer" (pyproject.toml) forbids it, and rightly. The duplication
#: is therefore structural, not carelessness, and the specification is the
#: shared source both copies answer to.
VerificationDisposition = Literal["verified", "downgraded-to-proposal", "rejected"]

#: The only disposition this module can produce. See the module docstring.
DOWNGRADED_TO_PROPOSAL: Final = "downgraded-to-proposal"

#: Unreachable here, named so a reader can grep for the absence rather than
#: infer it: nothing on this path verifies a proposal against its anchored
#: source, and nothing on this path runs the caller-side verification step
#: that would justify a rejection.
VERIFIED: Final = "verified"
REJECTED: Final = "rejected"

#: Keys whose presence in a cases file means the input is not blind. The
#: loader refuses rather than dropping them; see the module docstring.
FORBIDDEN_CASE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "expected_relation",
        "expected_rationale",
        "decided_by",
        "tie_with",
        "undecidable",
        "undecidable_reason",
    }
)

#: Said in the artefact itself rather than in a note somewhere else, because
#: it binds every reader of a number computed from this file.
TRAINING_DATA_LIMITATION: Final = (
    "The excerpts classified here are published arXiv abstracts and may have been in "
    "this model's training data. Nothing in this run distinguishes a criteria-following "
    "reading from a recollection, and no attempt was made to estimate the difference — "
    "measuring it would need held-out material this pool does not contain. Any figure "
    "computed from this file carries that limitation with it."
)

#: The disposition note carried into the artefact, so the word does not have
#: to be trusted on its own.
_DISPOSITION_NOTE: Final = (
    "Every proposal here is 'downgraded-to-proposal' (MRR-MTH-016). Nothing was verified "
    "against its anchored source, which is what that specification defines 'verified' to "
    "mean, so 'verified' is unreachable on this path by construction rather than by "
    "policy. These are proposals; the curated record remains authoritative."
)


class RelationCase(MRRModel):
    """One case as the classifier is allowed to see it.

    Deliberately has no field that could carry an expected relation — see the
    module docstring. Adding one would defeat the blind condition of the whole
    exercise, and ``extra="forbid"`` means a caller cannot smuggle one in
    either.
    """

    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    excerpt_sha256: str = Field(min_length=1)


class AttemptRecord(MRRModel):
    """One underlying ``adapter.invoke`` call, in the order it was made.

    ``status`` is ``mrr.domain.model_adapter.TerminalStatus`` verbatim. It is
    recorded per attempt rather than only in aggregate because the bounded
    schema repair E4-T02 performs makes "succeeded on the second try" a
    different observation from "succeeded at once", and a classifier that
    needs repairs is telling you something about the prompt.
    """

    status: str = Field(min_length=1)
    response_hash: str | None = None


class RelationProposal(MRRModel):
    """What came back for one case.

    Three shapes, kept distinguishable rather than flattened:

    * **decided** — a schema-valid verdict naming one of
      :data:`RELATION_CATEGORIES`. Carries a rationale, a ``decided_by``, and
      a disposition; yields a prediction.
    * **undecidable** — a schema-valid verdict declining to decide under
      ``R-undecidable-is-a-finding``. Carries a rationale and a disposition;
      yields NO prediction and is not a failure.
    * **failed** — no schema-valid verdict at all. Carries the distinct
      generation status and nothing else; yields no prediction and no
      disposition, because there is no proposal to dispose of.

    The biconditionals are enforced below rather than documented, mirroring
    ``mrr.domain.model_adapter.ModelInvocationOutcome``'s own style: a shape
    that cannot occur is better than a shape a reader has to check for.
    """

    case_id: str = Field(min_length=1)

    #: ``"proposal"`` when the model returned a schema-valid verdict, else the
    #: distinct failure kind E4-T02 surfaces (``schema_invalid``, ``refused``,
    #: ``content_filtered``, ``error``, ``timed_out``) — never collapsed into
    #: one generic error (AGENTS.md prohibited shortcuts).
    generation_status: str = Field(min_length=1)

    proposed_relation: str | None = None
    undecidable: bool = False
    rationale: str | None = None
    decided_by: str | None = None
    tie_with: str | None = None

    #: Present if and only if a proposal exists, and then always
    #: :data:`DOWNGRADED_TO_PROPOSAL`.
    verification_disposition: VerificationDisposition | None = None

    attempts: tuple[AttemptRecord, ...] = ()
    repair_attempts_used: int = Field(default=0, ge=0)

    @property
    def has_proposal(self) -> bool:
        """True when the model returned a schema-valid verdict, whether that
        verdict named a category or declined to decide.
        """
        return self.generation_status == "proposal"

    @model_validator(mode="after")
    def _check_shape(self) -> RelationProposal:
        if self.has_proposal:
            if self.verification_disposition != DOWNGRADED_TO_PROPOSAL:
                raise ValueError(
                    "a proposal must carry verification_disposition "
                    f"{DOWNGRADED_TO_PROPOSAL!r} — nothing on this path verifies against "
                    "an anchored source, so no other MTH-016 value can be earned"
                )
            if not self.rationale:
                raise ValueError(
                    "a proposal must carry a rationale (criteria rule R-rationale-required: "
                    "a label without a reason cannot be argued with)"
                )
            if self.undecidable and self.proposed_relation is not None:
                raise ValueError(
                    "an undecidable case must not also name a relation — "
                    "R-undecidable-is-a-finding keeps it out of the four labels entirely"
                )
            if not self.undecidable and self.proposed_relation not in RELATION_CATEGORIES:
                raise ValueError(
                    f"proposed_relation must be one of {RELATION_CATEGORIES} or the case "
                    f"must be marked undecidable, got {self.proposed_relation!r}"
                )
        else:
            if self.verification_disposition is not None:
                raise ValueError(
                    f"generation_status {self.generation_status!r} produced no proposal, so "
                    "there is nothing to carry a verification disposition"
                )
            if self.proposed_relation is not None or self.undecidable:
                raise ValueError(
                    f"generation_status {self.generation_status!r} produced no proposal, so "
                    "no relation and no undecidable finding can be recorded for it"
                )
        if self.tie_with is not None and self.tie_with not in RELATION_CATEGORIES:
            raise ValueError(
                f"tie_with must name one of {RELATION_CATEGORIES}, got {self.tie_with!r}"
            )
        if len(self.attempts) != 1 + self.repair_attempts_used and self.attempts:
            raise ValueError(
                f"attempts has {len(self.attempts)} entries but repair_attempts_used is "
                f"{self.repair_attempts_used}"
            )
        return self


class RelationProposalSet(MRRModel):
    """A whole run: what was asked, of which model, over which cases, and what
    came back.

    This object IS the predictions file. ``mrr.services.validation.
    gold_service.GoldValidityService.load_predictions`` reads ``system_id``
    and ``predictions`` from the top level and tolerates every other key
    (the majority-baseline file already relies on that tolerance for its own
    note), so the measurement input and the audit trail are one artefact
    instead of two that have to be kept in agreement.
    """

    system_id: str = Field(min_length=1)

    #: Which model, pinned the way ``ModelInvocationRequest`` pins it, plus
    #: the concrete provider model name — because "Gemini" names no object
    #: that could be re-measured.
    model_profile_id: str = Field(min_length=1)
    model_profile_hash: str = Field(min_length=1)
    model_name: str = Field(min_length=1)

    #: The sha256 of the exact prompt template used, and of the two inputs
    #: that decide what the prompt says. Together these are what makes a
    #: disagreement between two runs attributable to something.
    prompt_template_sha256: str = Field(min_length=1)
    commission_sha256: str = Field(min_length=1)
    criteria_sha256: str = Field(min_length=1)
    criteria_version: str = Field(min_length=1)

    claim_text: str = Field(min_length=1)
    categories: tuple[str, ...] = RELATION_CATEGORIES

    disposition_note: str = _DISPOSITION_NOTE
    limitations: str = TRAINING_DATA_LIMITATION

    proposals: tuple[RelationProposal, ...]

    @model_validator(mode="after")
    def _check_cases_are_distinct(self) -> RelationProposalSet:
        if not self.proposals:
            raise ValueError("a proposal set must cover at least one case")
        seen = [proposal.case_id for proposal in self.proposals]
        if len(set(seen)) != len(seen):
            raise ValueError("case ids must be distinct within one proposal set")
        return self

    def predictions(self) -> dict[str, str]:
        """The case-to-relation mapping ``mrr validate gold`` measures.

        Derived on demand rather than stored, so it cannot drift away from
        the proposals it summarises. Undecidable and failed cases contribute
        nothing — the first because the criteria keep them out of the matrix,
        the second because there is no answer to score.
        """
        return {
            proposal.case_id: proposal.proposed_relation
            for proposal in self.proposals
            if proposal.proposed_relation is not None
        }

    def undecidable_case_ids(self) -> tuple[str, ...]:
        """Cases the model declined to decide, in input order — a coverage
        measurement of the criteria, not a score of the classifier.
        """
        return tuple(p.case_id for p in self.proposals if p.undecidable)

    def failed_case_ids(self) -> tuple[str, ...]:
        """Cases where generation produced no schema-valid verdict, in input
        order. A non-empty result is why the CLI refuses: a shorter
        predictions file measures as though it were whole.
        """
        return tuple(p.case_id for p in self.proposals if not p.has_proposal)

    def tie_broken_case_ids(self) -> tuple[str, ...]:
        """Cases whose verdict recorded a runner-up under ``R-record-any-tie``.

        Reported beside the standard's own tie count rather than folded into
        an accuracy: the labelling practice's finding 4.3 was that a tie the
        record cannot see is a decision that cannot be argued with.
        """
        return tuple(p.case_id for p in self.proposals if p.tie_with is not None)


def render_json(proposal_set: RelationProposalSet) -> str:
    """Render the set as deterministic, sorted JSON carrying ``predictions``
    at the top level, so the file is directly readable by
    ``mrr validate gold --predictions``.

    ``predictions`` is assembled here rather than declared as a field: it is
    derived data, and a stored copy of derived data is a second thing that can
    be wrong. Trailing newline so the file is POSIX-clean.
    """
    document = proposal_set.model_dump(mode="json")
    document["predictions"] = proposal_set.predictions()
    document["undecidable_case_ids"] = list(proposal_set.undecidable_case_ids())
    document["tie_broken_case_ids"] = list(proposal_set.tie_broken_case_ids())
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


__all__ = [
    "DOWNGRADED_TO_PROPOSAL",
    "FORBIDDEN_CASE_KEYS",
    "REJECTED",
    "RELATION_CATEGORIES",
    "TRAINING_DATA_LIMITATION",
    "VERIFIED",
    "AttemptRecord",
    "RelationCase",
    "RelationProposal",
    "RelationProposalSet",
    "VerificationDisposition",
    "render_json",
]
