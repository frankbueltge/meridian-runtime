"""``RelationClassificationService`` (task-packets/N1-T04.yaml R3/R4): load a
blind commission and its criteria, drive an injected ``ModelAdapter`` over
every case, and return a ``RelationProposalSet``.

Read-only apart from nothing at all — this service writes no file; the CLI
that calls it does. It opens no database, constructs no adapter, and reads no
environment variable.

--- The one decision being measured ----------------------------------------

    Does this source support the claim, or contradict it?

That is the decision an automatic literature channel (step 3 of the owner's
ordering) would have to make, and the one no model in this repository makes
today. The gold standard built in N1-T02 measures exactly it, and until this
module existed there was nothing to measure: the first real run against that
standard had to use a constant majority-class baseline, which scored an
accuracy identical to its own floor and a Cohen's kappa of exactly zero.

--- Same commission, same criteria, same blindness -------------------------

The system under test receives the byte-identical commission the labelling
practice received and the byte-identical criteria its labels were made under
— generality fence, tie-break rule and all.

That fence is known to be uneven. The labelling practice's finding 4.1 is
that ``supports`` carries a generality requirement and ``contradicts`` does
not, so a system that self-verifies inside one narrow domain *contradicts*
the claim while a system checked externally inside the same narrow domain
only *qualifies* it — the single largest reason its counts came out 12:1. It
stands unrepaired by owner decision, because changing a definition
retroactively devalues sixty finished blind labels.

Passing the criteria with the fence intact is therefore not an oversight but
the only measurable arrangement: a system classifying under different
criteria than its standard measures nothing at all. What this run adds is
evidence about the fence rather than an opinion on it — where a second,
independent reader hits the same fences, the fence is doing the work; where
it does not, the reading was.

--- Blindness is enforced, not requested -----------------------------------

:meth:`RelationClassificationService.load_cases` REFUSES a cases file
carrying any of ``mrr.domain.relation_proposal.FORBIDDEN_CASE_KEYS`` instead
of ignoring them, and the type it builds
(``mrr.domain.relation_proposal.RelationCase``) has no field able to hold an
expected relation. A label therefore cannot reach a prompt by mistake, and a
file that contains one stops the run rather than quietly producing a
"blind" measurement that was not blind.

--- Every failure keeps its own name ---------------------------------------

``generate_structured`` distinguishes five failure kinds — ``schema_invalid``
(bounded repair exhausted) and the four non-completed terminal statuses
``refused``, ``content_filtered``, ``error``, ``timed_out``. Each is recorded
verbatim on the proposal for its case. Collapsing them into one generic
failure is on AGENTS.md's list of prohibited shortcuts, and for a run whose
whole point is measurement it would be the difference between "the model
would not answer" and "the network broke".

A case that produced no schema-valid verdict yields no prediction. The
service reports that rather than deciding what to do about it; the CLI
refuses on it (see :mod:`mrr.services.cli.classification_main`).

--- Why the model profile id is derived, not minted ------------------------

``ModelInvocationRequest`` pins a ``model_profile_id`` (an MRR urn) and a
``model_profile_hash``. There is no ``ModelProfile`` object here — this path
touches no database — so one has to come from somewhere, and a freshly minted
ULID would make two identical runs incomparable and put a random value in an
artefact that is supposed to be byte-deterministic. That is the same mistake
in a different costume as the hand-typed timestamp that blocked its own order
gate on 2026-08-01.

So both are DERIVED, deterministically, from the configuration that actually
decides what the model is asked: the provider model name, the prompt
template, and the criteria. Identical configuration yields an identical urn;
a changed prompt yields a different one. The identifier then means something
rather than merely being unique.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

from mrr.adapters.llm.structured_generation import generate_structured
from mrr.domain.exceptions import DomainError
from mrr.domain.model_adapter import ModelAdapter, ModelInvocationRequest
from mrr.domain.relation_proposal import (
    DOWNGRADED_TO_PROPOSAL,
    FORBIDDEN_CASE_KEYS,
    RELATION_CATEGORIES,
    AttemptRecord,
    RelationCase,
    RelationProposal,
    RelationProposalSet,
)
from pydantic import BaseModel, ConfigDict

#: Crockford base32 minus I, L, O and U — the exact alphabet
#: ``mrr.domain.identity.URN_PATTERN`` accepts in a ULID position.
_ULID_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH: Final = 26

#: The prompt. Its sha256 goes into the artefact, so a disagreement between
#: two runs is attributable to a specific text rather than to "the prompt".
#:
#: It carries the claim, the four definitions and the rules verbatim from the
#: criteria file, then one case. It carries nothing else — no examples, no
#: hints about the expected distribution, and above all no label.
PROMPT_TEMPLATE: Final = """\
You are classifying one source excerpt against one fixed claim, under a frozen \
set of criteria. Apply the criteria as written. Do not smooth them, and do not \
substitute your own judgement for a definition.

Answer with a single JSON object and nothing else — no prose before or after \
it, and no markdown code fence around it. It must validate against this JSON \
schema exactly:

{schema}

THE CLAIM
{claim_text}

THE CATEGORIES, AS DEFINED
{definitions}

THE RULES
{rules}

THE CASE
Title: {title}

Excerpt:
{excerpt}

Return the category this excerpt takes toward the claim. Name in `decided_by` \
the id of the rule or the name of the definition that actually produced your \
answer, and give a one-sentence `rationale` for it. If the decision was close \
enough that a rule, a fence or a judgement call rather than the definitions \
produced it, name the runner-up in `tie_with`. If the criteria cannot decide \
this case at all, answer `undecidable` and say in the rationale which word or \
distinction the excerpt fails on.\
"""


class _RelationVerdict(BaseModel):
    """The target model one case's answer must validate against.

    ``extra="forbid"`` so a model that volunteers extra fields fails schema
    validation and gets a bounded repair attempt rather than having its
    surplus silently dropped.

    ``undecidable`` is a fifth value of ``relation`` rather than a separate
    boolean on purpose: two fields would allow the inconsistent state
    ("undecidable, and also `supports`"), and one field cannot.
    """

    model_config = ConfigDict(extra="forbid")

    relation: Literal[
        "supports",
        "contradicts",
        "qualifies",
        "contextualizes",
        "undecidable",
    ]
    rationale: str
    decided_by: str
    tie_with: Literal["supports", "contradicts", "qualifies", "contextualizes"] | None = None


def _target_schema_json() -> str:
    """The target model's JSON schema, canonically serialised.

    Derived from :class:`_RelationVerdict` so the prompt's instruction and
    the validation that judges the answer are the same object.
    """
    return json.dumps(_RelationVerdict.model_json_schema(), sort_keys=True)


def prompt_contract_sha256() -> str:
    """The sha256 of everything that decides what is asked: the template AND
    the target schema interpolated into it.

    Hashing the template alone would miss a changed schema, which changes the
    ask as surely as a changed sentence does.
    """
    return _sha256((PROMPT_TEMPLATE + _target_schema_json()).encode("utf-8"))


class ClassificationInputError(DomainError):
    """An input file cannot be read or does not have the expected shape.

    A DEPENDENCY problem (CLI exit 2), never a research result — mirrors
    ``mrr.services.validation.gold_service.GoldSetFileError``'s role exactly.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class CasesNotBlindError(DomainError):
    """The cases file carries labelling output. A REFUSAL (CLI exit 3).

    The whole exercise depends on the classifier not seeing the answers. A
    file that carries them is not a commission, and dropping the offending
    keys would produce a measurement that reports itself as blind without
    having been. Refusing names the key instead.
    """

    def __init__(self, path: Path, case_id: str, keys: Sequence[str]) -> None:
        self.path = path
        self.case_id = case_id
        self.keys = tuple(keys)
        super().__init__(
            f"{path}: case {case_id!r} carries {list(self.keys)!r}, which is labelling "
            "output. A blind commission carries the excerpt and the claim and nothing "
            "that answers them. Refusing rather than ignoring the keys."
        )


def _sha256(data: bytes) -> str:
    """``sha256:<hex>``, the textual form
    ``schemas/common.schema.json#/$defs/sha256`` requires.
    """
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _read_json(path: Path) -> tuple[Any, bytes]:
    """Read once, return the parsed document and the exact bytes.

    Both are needed — the document to work with, the bytes to hash — and
    reading twice would leave a window in which the file could change between
    the hash and the parse.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClassificationInputError(path, f"cannot read file ({exc})") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClassificationInputError(path, f"not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text), raw
    except json.JSONDecodeError as exc:
        raise ClassificationInputError(path, f"not valid JSON ({exc})") from exc


def _require_mapping(value: Any, *, path: Path, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ClassificationInputError(
            path, f"{what} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_str(document: Mapping[str, Any], key: str, *, path: Path) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ClassificationInputError(path, f"{key!r} must be a non-empty string")
    return value


def derive_model_profile_urn(*, model_name: str, prompt_template: str, criteria_sha256: str) -> str:
    """A deterministic ``urn:mrr:model-profile:<ULID-shaped>`` for one exact
    configuration.

    Content-addressed rather than random: the same model asked the same way
    about the same criteria gets the same identifier, and a changed prompt
    gets a different one. See this module's docstring for why a minted ULID
    would have been the wrong choice here.
    """
    digest = hashlib.sha256(
        json.dumps(
            {
                "model_name": model_name,
                "prompt_template": prompt_template,
                "criteria_sha256": criteria_sha256,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).digest()
    value = int.from_bytes(digest, "big")
    chars = []
    for _ in range(_ULID_LENGTH):
        chars.append(_ULID_ALPHABET[value & 0x1F])
        value >>= 5
    return "urn:mrr:model-profile:" + "".join(reversed(chars))


class LoadedCriteria(BaseModel):
    """The frozen criteria, as much of them as a prompt needs, plus the hash
    of the exact bytes they came from.
    """

    model_config = ConfigDict(extra="forbid")

    criteria_version: str
    criteria_sha256: str
    claim_text: str
    definitions: str
    rules: str


class LoadedCases(BaseModel):
    """The blind commission's cases and the hash of the file they came from."""

    model_config = ConfigDict(extra="forbid")

    commission_sha256: str
    cases: tuple[RelationCase, ...]


class RelationClassificationService:
    """Drives an injected ``ModelAdapter`` over a blind commission.

    Constructs no adapter and no database connection; see the module
    docstring.
    """

    def load_criteria(self, path: Path) -> LoadedCriteria:
        """Load the frozen criteria file and render the parts a prompt needs.

        The definitions and the rules go into the prompt as the criteria state
        them, not as a paraphrase: the standard's labels were produced under
        these exact words, and a system asked in other words is answering a
        different question.
        """
        document_raw, raw_bytes = _read_json(path)
        document = _require_mapping(document_raw, path=path, what="criteria")

        criteria_version = _require_str(document, "criteria_version", path=path)

        claim = _require_mapping(
            document.get("claim_under_classification"),
            path=path,
            what="claim_under_classification",
        )
        claim_text = _require_str(claim, "text", path=path)

        definitions_raw = _require_mapping(
            document.get("definitions"), path=path, what="definitions"
        )
        missing = [name for name in RELATION_CATEGORIES if name not in definitions_raw]
        if missing:
            raise ClassificationInputError(
                path, f"definitions are missing the categories {missing!r}"
            )
        definitions = "\n".join(
            f"- {name}: {definitions_raw[name]}" for name in RELATION_CATEGORIES
        )

        rules_raw = document.get("rules")
        if not isinstance(rules_raw, list) or not rules_raw:
            raise ClassificationInputError(path, "'rules' must be a non-empty array")
        rules_lines = []
        for entry in rules_raw:
            rule = _require_mapping(entry, path=path, what="a rules[] entry")
            rules_lines.append(
                f"- {_require_str(rule, 'id', path=path)}: {_require_str(rule, 'rule', path=path)}"
            )

        return LoadedCriteria(
            criteria_version=criteria_version,
            criteria_sha256=_sha256(raw_bytes),
            claim_text=claim_text,
            definitions=definitions,
            rules="\n".join(rules_lines),
        )

    def load_cases(self, path: Path) -> LoadedCases:
        """Load a blind commission's cases.

        Raises:
            CasesNotBlindError: a case carries labelling output. See that
                class, and this module's docstring, for why this refuses
                rather than dropping the key.
        """
        document_raw, raw_bytes = _read_json(path)
        document = _require_mapping(document_raw, path=path, what="commission")

        cases_raw = document.get("cases")
        if not isinstance(cases_raw, list) or not cases_raw:
            raise ClassificationInputError(path, "'cases' must be a non-empty array")

        cases: list[RelationCase] = []
        for entry in cases_raw:
            case = _require_mapping(entry, path=path, what="a cases[] entry")
            case_id = _require_str(case, "case_id", path=path)
            present = sorted(FORBIDDEN_CASE_KEYS & set(case))
            if present:
                raise CasesNotBlindError(path, case_id, present)
            cases.append(
                RelationCase(
                    case_id=case_id,
                    title=_require_str(case, "title", path=path),
                    claim_text=_require_str(case, "claim_text", path=path),
                    excerpt=_require_str(case, "excerpt", path=path),
                    excerpt_sha256=_require_str(case, "excerpt_sha256", path=path),
                )
            )

        return LoadedCases(commission_sha256=_sha256(raw_bytes), cases=tuple(cases))

    def build_prompt(self, case: RelationCase, criteria: LoadedCriteria) -> str:
        """Render the prompt for one case.

        Takes a :class:`~mrr.domain.relation_proposal.RelationCase`, whose
        type cannot carry an expected relation — that is where the blindness
        of this path actually lives.

        The target schema is interpolated from :class:`_RelationVerdict`
        itself rather than restated in prose, so the instruction and the model
        a response is validated against cannot drift apart. ``generate_
        structured`` puts the schema into its REPAIR prompt only; the first
        call gets whatever the caller wrote, so a caller that does not say
        "return this JSON" is asking a language model for prose and then
        failing it for not being JSON.
        """
        return PROMPT_TEMPLATE.format(
            schema=_target_schema_json(),
            claim_text=criteria.claim_text,
            definitions=criteria.definitions,
            rules=criteria.rules,
            title=case.title,
            excerpt=case.excerpt,
        )

    def classify(
        self,
        *,
        adapter: ModelAdapter,
        cases: LoadedCases,
        criteria: LoadedCriteria,
        model_name: str,
        system_id: str,
        max_repair_attempts: int = 1,
        pause: Callable[[], None] | None = None,
    ) -> RelationProposalSet:
        """Classify every case and return the proposal set.

        Args:
            adapter: the injected port. This service never builds one.
            pause: called between cases. Injected rather than read from a
                clock so tests run at full speed and this module stays free
                of a sleep it did not ask for; the CLI passes a real one
                because the free-tier provider is rate-limited.

        Never raises on a model failure: a case that produced no schema-valid
        verdict becomes a proposal carrying its distinct failure status, and
        what to do about that is the caller's decision.
        """
        prompt_template_sha256 = prompt_contract_sha256()
        model_profile_id = derive_model_profile_urn(
            model_name=model_name,
            prompt_template=PROMPT_TEMPLATE + _target_schema_json(),
            criteria_sha256=criteria.criteria_sha256,
        )
        model_profile_hash = _sha256(
            json.dumps(
                {
                    "model_name": model_name,
                    "prompt_template_sha256": prompt_template_sha256,
                    "criteria_sha256": criteria.criteria_sha256,
                },
                sort_keys=True,
            ).encode("utf-8")
        )

        proposals: list[RelationProposal] = []
        for index, case in enumerate(cases.cases):
            if pause is not None and index > 0:
                pause()
            proposals.append(
                self._classify_one(
                    adapter=adapter,
                    case=case,
                    criteria=criteria,
                    model_profile_id=model_profile_id,
                    model_profile_hash=model_profile_hash,
                    max_repair_attempts=max_repair_attempts,
                )
            )

        return RelationProposalSet(
            system_id=system_id,
            model_profile_id=model_profile_id,
            model_profile_hash=model_profile_hash,
            model_name=model_name,
            prompt_template_sha256=prompt_template_sha256,
            commission_sha256=cases.commission_sha256,
            criteria_sha256=criteria.criteria_sha256,
            criteria_version=criteria.criteria_version,
            claim_text=criteria.claim_text,
            proposals=tuple(proposals),
        )

    def _classify_one(
        self,
        *,
        adapter: ModelAdapter,
        case: RelationCase,
        criteria: LoadedCriteria,
        model_profile_id: str,
        model_profile_hash: str,
        max_repair_attempts: int,
    ) -> RelationProposal:
        request = ModelInvocationRequest(
            model_profile_id=model_profile_id,
            model_profile_hash=model_profile_hash,
            prompt_text=self.build_prompt(case, criteria),
            operation_kind="stochastic",
            # raw_permitted, and this is load-bearing rather than lax.
            #
            # `generate_structured` validates `outcome.raw_response_text`, and
            # `ModelInvocationOutcome` forbids that field under "hashes_only"
            # (MRR-FR-045, enforced in its __post_init__). So under
            # "hashes_only" there is never any text to validate, the else
            # branch at structured_generation.py:259-277 records "no response
            # text available", and `status == "proposal"` is UNREACHABLE. The
            # policy does not merely redact the evidence; it decides the
            # outcome.
            #
            # This was found by running it: the first online run returned
            # schema_invalid for every case that reached the provider at all.
            #
            # Nothing sensitive travels here — the prompt is a published
            # criteria set plus a published abstract, and no participant data
            # or secret can reach it (AGENTS.md rule 11 concerns secrets in
            # prompts; there are none). The raw text stays in memory for the
            # length of one validation and enters no artefact: the proposal
            # set records hashes, per its own module docstring.
            redaction_policy="raw_permitted",
        )
        result = generate_structured(
            adapter, request, _RelationVerdict, max_repair_attempts=max_repair_attempts
        )
        attempts = tuple(
            AttemptRecord(status=outcome.status, response_hash=outcome.response_hash)
            for outcome in result.attempts
        )

        if result.status != "proposal" or result.proposal is None:
            return RelationProposal(
                case_id=case.case_id,
                generation_status=result.status,
                attempts=attempts,
                repair_attempts_used=result.repair_attempts_used,
            )

        verdict = result.proposal
        undecidable = verdict.relation == "undecidable"
        return RelationProposal(
            case_id=case.case_id,
            generation_status="proposal",
            proposed_relation=None if undecidable else verdict.relation,
            undecidable=undecidable,
            rationale=verdict.rationale,
            decided_by=verdict.decided_by,
            tie_with=verdict.tie_with,
            # The one disposition this path can earn — see
            # mrr.domain.relation_proposal's module docstring.
            verification_disposition=DOWNGRADED_TO_PROPOSAL,
            attempts=attempts,
            repair_attempts_used=result.repair_attempts_used,
        )


__all__ = [
    "PROMPT_TEMPLATE",
    "prompt_contract_sha256",
    "CasesNotBlindError",
    "ClassificationInputError",
    "LoadedCases",
    "LoadedCriteria",
    "RelationClassificationService",
    "derive_model_profile_urn",
]
