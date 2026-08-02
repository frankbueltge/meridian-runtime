"""``LiteratureCorpusBuilder`` (task-packets/N1-T05.yaml R3/R4/R5): join a
batch manifest, an anchored content snapshot and a proposal artefact into a
complete corpus directory — the five files
``.github/workflows/research-run.yml`` requires before it will call a
directory a stated question.

DETERMINISTIC AND OFFLINE. No network, no clock, no model, no database. Two
builds over identical inputs produce byte-identical output, and no written
byte is a wall-clock timestamp — N1-T02's invariant, kept for the reason
that is on the record: a hand-typed timestamp inside something that gates on
time blocked its own gate within hours.

--- Two products, one join --------------------------------------------------

:meth:`~LiteratureCorpusBuilder.build_commission` produces the BLIND
commission the classifier reads. It is built from the manifest and the
snapshot alone and carries no relation field of any kind, so the blindness is
structural rather than promised — the same guarantee
``mrr.domain.relation_proposal.RelationCase`` gives one level down.

:meth:`~LiteratureCorpusBuilder.build_corpus` produces the corpus directory
once the proposals exist.

--- Why an undecidable case leaves the corpus -------------------------------

``CorpusEntry.evidence_relation`` has four values and none of them means
"undecidable". Mapping an undecidable proposal onto ``contextualizes`` would
manufacture a reading the model expressly refused to give — the single worst
thing a channel like this can do, because the fabrication would be
indistinguishable from a judgement afterwards.

So the case is counted in the batch report and excluded from the corpus. The
gold standard does the same with its own three of sixty (n=57).

--- Why source_family_id is the arXiv base id -------------------------------

MRR-MTH-015 forbids counting copied or derivative sources as independent
evidence, and the independence calculation downstream trusts this field
without re-deriving it. Two versions of one paper share a base id and are
therefore one family, so a revision can never double as corroboration.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Final

from mrr.domain.exceptions import DomainError
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BatchReport",
    "CorpusBuildError",
    "CorpusBuildRefusedError",
    "LiteratureCorpusBuilder",
]

#: The five files research-run.yml:63-70 requires before a directory counts as
#: a stated question. Named here so a change to that workflow's list fails a
#: test in this package rather than producing a corpus it silently skips.
REQUIRED_CORPUS_FILES: Final[tuple[str, ...]] = (
    "question-model.proposal.json",
    "concept-charter.proposal.json",
    "method-protocol.proposal.json",
    "corpus-entries.json",
    "protocol-parameters.sidecar.json",
)

#: A versioned arXiv identifier ends in ``vN``; the base id is the family.
_ARXIV_VERSION_SUFFIX: Final = re.compile(r"v\d+$")

_ARXIV_ABS: Final = "https://arxiv.org/abs/"


class CorpusBuildError(DomainError):
    """An input is missing, unreadable, or the wrong shape.

    A DEPENDENCY problem (CLI exit 2), never a research result — the same role
    ``mrr.services.classification.relation_service.ClassificationInputError``
    plays for its own inputs.
    """


class CorpusBuildRefusedError(DomainError):
    """The inputs are well-formed but the corpus must not be written.

    A REFUSAL that is a RESULT (CLI exit 3): a batch too small to clear the
    protocol's own kill condition, or proposals that do not cover the anchored
    sources.
    """


class BatchReport(BaseModel):
    """What the build did, for the pull request and the packet report.

    Every count here is a number somebody will ask about later: how many
    sources were drawn, how many could actually be anchored, how many the
    model declined to place, and what it proposed.
    """

    model_config = ConfigDict(extra="forbid")

    batch: str
    drawn: int
    anchored: int
    unverifiable: tuple[str, ...] = ()
    undecidable: tuple[str, ...] = ()
    entries: int
    relation_counts: dict[str, int] = Field(default_factory=dict)


def _sha256_of_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_of_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def arxiv_family_id(arxiv_id: str) -> str:
    """The version-stripped identifier — see the module docstring."""
    return _ARXIV_VERSION_SUFFIX.sub("", arxiv_id)


def _read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CorpusBuildError(f"{path}: cannot read file ({exc})") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorpusBuildError(f"{path}: not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorpusBuildError(f"{path}: not valid JSON ({exc})") from exc


def _require_mapping(document: Any, path: Path) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise CorpusBuildError(f"{path}: top-level document must be a JSON object")
    return document


def _render(document: Any) -> str:
    """One rendering function for every written file, so determinism is a
    property of the module rather than of each call site.
    """
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


class _Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    arxiv: str
    title: str


class _Excerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    excerpt_text: str
    excerpt_sha256: str


class LiteratureCorpusBuilder:
    """Deterministic, offline joins over committed files.

    Stateless: every method takes its inputs and returns a document. Nothing
    here writes; the CLI that calls it does, atomically.
    """

    # ---- loading ---------------------------------------------------------

    def load_manifest(self, path: Path) -> tuple[_Citation, ...]:
        document = _require_mapping(_read_json(path), path)
        raw = document.get("citations")
        if not isinstance(raw, list) or not raw:
            raise CorpusBuildError(f"{path}: 'citations' must be a non-empty JSON array")

        citations: list[_Citation] = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise CorpusBuildError(f"{path}: a citations[] element is not a JSON object")
            citation_id = entry.get("citation_id")
            identifiers = entry.get("identifiers")
            if not isinstance(citation_id, str) or not citation_id:
                raise CorpusBuildError(f"{path}: a citations[] element has no 'citation_id'")
            if not isinstance(identifiers, dict):
                raise CorpusBuildError(
                    f"{path}: citations[{citation_id!r}].identifiers must be a JSON object"
                )
            arxiv = identifiers.get("arxiv")
            if not isinstance(arxiv, str) or not arxiv:
                raise CorpusBuildError(
                    f"{path}: citations[{citation_id!r}] has no arXiv identifier. This "
                    "channel anchors against arXiv only."
                )
            title = entry.get("claimed_title") or entry.get("cited_as") or ""
            citations.append(_Citation(citation_id=citation_id, arxiv=arxiv, title=str(title)))
        return tuple(citations)

    def load_snapshot(self, path: Path) -> tuple[dict[str, _Excerpt], tuple[str, ...], str]:
        """Anchored excerpts by citation id, the ids that could NOT be
        anchored, and the snapshot's own ``fetched_on`` date.

        The unanchored are returned rather than dropped: MRR-MTH-015 says
        unverifiable rows are marked, never dropped, and a source that
        silently vanished between the draw and the corpus is exactly the kind
        of gap that makes a later count unexplainable.

        ``fetched_on`` is read from the snapshot rather than from a clock. It
        is a committed fact about when the archive was captured, so the corpus
        stays deterministic AND its ``retrieval_timestamp`` stays true — the
        two properties that a wall-clock read would trade against each other.
        """
        document = _require_mapping(_read_json(path), path)
        raw = document.get("excerpts")
        if not isinstance(raw, list):
            raise CorpusBuildError(f"{path}: 'excerpts' must be a JSON array")
        fetched_on = document.get("fetched_on")
        if not isinstance(fetched_on, str) or not fetched_on:
            raise CorpusBuildError(
                f"{path}: 'fetched_on' must be a non-empty string. Without it the corpus "
                "would need a clock to state when its sources were retrieved, and a "
                "wall-clock byte in a derived file breaks determinism."
            )

        anchored: dict[str, _Excerpt] = {}
        unanchored: list[str] = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise CorpusBuildError(f"{path}: an excerpts[] element is not a JSON object")
            citation_id = entry.get("citation_id")
            if not isinstance(citation_id, str) or not citation_id:
                raise CorpusBuildError(f"{path}: an excerpts[] element has no 'citation_id'")
            text = entry.get("excerpt_text")
            digest = entry.get("excerpt_sha256")
            if entry.get("excerpt_available") is not True or not isinstance(text, str) or not text:
                unanchored.append(citation_id)
                continue
            if not isinstance(digest, str) or not digest:
                raise CorpusBuildError(
                    f"{path}: excerpts[{citation_id!r}] has text but no 'excerpt_sha256'"
                )
            # Recomputed, not trusted. The snapshot is committed archive and
            # the corpus is derived from it; if the two ever disagree, the
            # build must stop rather than publish a hash that does not
            # describe the bytes beside it.
            recomputed = _sha256_of_text(text)
            if recomputed != digest:
                raise CorpusBuildError(
                    f"{path}: excerpts[{citation_id!r}] carries {digest} but its text hashes "
                    f"to {recomputed}. The snapshot is inconsistent with itself; refusing to "
                    "derive a corpus from it."
                )
            anchored[citation_id] = _Excerpt(
                citation_id=citation_id, excerpt_text=text, excerpt_sha256=digest
            )
        return anchored, tuple(sorted(unanchored)), fetched_on

    def load_proposals(self, path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Proposals by case id, plus the artefact's run-level provenance.

        Reads ``mrr classify relations``' own output shape unchanged; this
        package adds no classification logic and no prompt.
        """
        document = _require_mapping(_read_json(path), path)
        raw = document.get("proposals")
        if not isinstance(raw, list):
            raise CorpusBuildError(f"{path}: 'proposals' must be a JSON array")

        by_case: dict[str, dict[str, Any]] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise CorpusBuildError(f"{path}: a proposals[] element is not a JSON object")
            case_id = entry.get("case_id")
            if not isinstance(case_id, str) or not case_id:
                raise CorpusBuildError(f"{path}: a proposals[] element has no 'case_id'")
            by_case[case_id] = entry

        provenance = {
            "model_name": document.get("model_name"),
            "model_profile_id": document.get("model_profile_id"),
            "prompt_template_sha256": document.get("prompt_template_sha256"),
            "criteria_version": document.get("criteria_version"),
            "criteria_sha256": document.get("criteria_sha256"),
        }
        return by_case, provenance

    # ---- the blind commission -------------------------------------------

    def build_commission(
        self,
        *,
        citations: tuple[_Citation, ...],
        anchored: dict[str, _Excerpt],
        criteria_path: Path,
        claim_text: str,
        batch: str,
    ) -> dict[str, Any]:
        """The classifier's input.

        Structurally blind: this document has no field able to carry a
        relation, so nothing downstream can be contaminated even by mistake.
        The union of case keys is exactly the union the gold commission
        carries — ``case_id``, ``claim_text``, ``excerpt``, ``excerpt_sha256``,
        ``source_identifiers``, ``source_url``, ``title``.
        """
        criteria_document = _require_mapping(_read_json(criteria_path), criteria_path)
        cases = [
            {
                "case_id": citation.citation_id,
                "claim_text": claim_text,
                "excerpt": anchored[citation.citation_id].excerpt_text,
                "excerpt_sha256": anchored[citation.citation_id].excerpt_sha256,
                "source_identifiers": {
                    "doi": None,
                    "repository_id": citation.arxiv,
                    "archive_id": None,
                    "local_asset_id": citation.citation_id,
                },
                "source_url": f"{_ARXIV_ABS}{citation.arxiv}",
                "title": citation.title,
            }
            for citation in citations
            if citation.citation_id in anchored
        ]
        if not cases:
            raise CorpusBuildRefusedError(
                f"batch {batch!r}: not one source could be anchored, so there is nothing to "
                "classify. A commission over zero excerpts is not a blind commission."
            )
        return {
            "schema_version": "gold-label-commission.v2",
            "commission_id": f"lit-{batch}-commission",
            "_note": (
                "A literature-channel commission (task-packets/N1-T05.yaml): hash-anchored "
                "excerpts with the question and the locked criteria, and no answers. It "
                "carries no relation field of any kind, so blindness is a property of the "
                "document rather than a promise about how it is used."
            ),
            "criteria_version": criteria_document.get("criteria_version"),
            "criteria_lock_content_hash": _sha256_of_file(criteria_path),
            "criteria": criteria_document,
            "categories": ["supports", "contradicts", "qualifies", "contextualizes"],
            "claim_text": claim_text,
            "case_provenance": (
                f"Drawn for literature batch {batch!r} by scripts/draw_backlog.py; abstracts "
                "fetched and hashed by scripts/fetch_source_content.py against arXiv only."
            ),
            "cases": cases,
        }

    # ---- the corpus ------------------------------------------------------

    def build_entries(
        self,
        *,
        citations: tuple[_Citation, ...],
        anchored: dict[str, _Excerpt],
        unanchored: tuple[str, ...],
        proposals: dict[str, dict[str, Any]],
        provenance: dict[str, Any],
        analysis: str,
        claim_type: str,
        snapshot_path: str,
        proposals_path: str,
        accuracy_note: str,
        fetched_on: str,
    ) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        """The corpus entries, plus the case ids the model declined to place.

        Every returned mapping is shaped to pass
        ``CorpusEntry.model_validate`` under ``extra="forbid"`` — asserted by
        the tests calling that method, never by this docstring.
        """
        entries: list[dict[str, Any]] = []
        undecidable: list[str] = []

        for citation in citations:
            case_id = citation.citation_id
            if case_id in unanchored:
                # An unanchored source is marked, never dropped (MTH-015). It
                # carries no relation because nothing was read: an excerpt
                # nobody could fetch cannot have been classified, and giving
                # it a relation would be the fabrication this channel exists
                # to avoid.
                continue
            if case_id not in anchored:
                continue

            proposal = proposals.get(case_id)
            if proposal is None:
                raise CorpusBuildRefusedError(
                    f"no proposal covers anchored source {case_id!r}. The proposal artefact "
                    "does not match the batch it claims to classify; refusing to build a "
                    "corpus in which some entries were read and others were guessed."
                )
            if proposal.get("undecidable") is True or not proposal.get("proposed_relation"):
                undecidable.append(case_id)
                continue

            relation = proposal["proposed_relation"]
            rationale = str(proposal.get("rationale") or "")
            if not rationale:
                raise CorpusBuildRefusedError(
                    f"proposal for {case_id!r} carries a relation but no rationale. A "
                    "classification whose reason travelled nowhere cannot be published as "
                    "one — the reason travels verbatim or not at all."
                )

            excerpt = anchored[case_id]
            entries.append(
                {
                    "entry_id": case_id,
                    "applies_to_analysis": analysis,
                    "claim_type": claim_type,
                    # The MODEL's proposal. Its standing is stated beside it in
                    # extraction.classification_provenance, never implied.
                    "evidence_relation": relation,
                    # The SOURCE axis (MTH-015): this excerpt was fetched and
                    # hashed. It says nothing about the relation above.
                    "verification_status": "verified",
                    "unverifiable_reason": None,
                    # The anchored excerpt VERBATIM, not a summary of it.
                    # A summary would be a second model output that nobody
                    # checked, sitting in the field a claim's prose is built
                    # from. This way every byte here has a committed hash.
                    "claim_relevant_finding": excerpt.excerpt_text,
                    "extraction": {
                        "classification_basis": rationale,
                        "classification_provenance": (
                            f"Model-PROPOSED relation, not a verified finding. Proposed by "
                            f"{provenance.get('model_name')} under "
                            f"{provenance.get('criteria_version')} "
                            f"(prompt {provenance.get('prompt_template_sha256')}), recorded in "
                            f"{proposals_path} with disposition "
                            f"'{proposal.get('verification_disposition')}'. "
                            f"Decided by rule: {proposal.get('decided_by')}; "
                            f"runner-up: {proposal.get('tie_with')}. {accuracy_note}"
                        ),
                    },
                    # Version-stripped: two versions of one paper are one
                    # family and can never corroborate each other (MTH-015).
                    "source_family_id": arxiv_family_id(citation.arxiv),
                    "identifiers": {
                        "doi": None,
                        "repository_id": citation.arxiv,
                        "archive_id": None,
                        "local_asset_id": case_id,
                    },
                    "title": citation.title,
                    "creators": [],
                    "publication_date": None,
                    "version": None,
                    # From the snapshot's own committed `fetched_on`, never a
                    # clock: true AND deterministic at once.
                    "retrieval_timestamp": f"{fetched_on}T00:00:00Z",
                    "retrieval_method": (
                        f"direct read of the pinned, hash-verified abstract snapshot at "
                        f"{snapshot_path} (excerpt {excerpt.excerpt_sha256}); fetched by "
                        "scripts/fetch_source_content.py against arXiv only"
                    ),
                    "source_type": "preprint",
                    "primary_secondary_derived": "primary",
                    "derivation_evidence": None,
                    "accessibility": None,
                    "licensing": None,
                }
            )

        return entries, tuple(undecidable)

    # ---- the four siblings that turn a folder into a question ------------

    def build_siblings(
        self,
        *,
        batch: str,
        claim_text: str,
        analysis: str,
        claim_type: str,
        manifest_path: str,
        manifest_sha256: str,
        snapshot_path: str,
        snapshot_sha256: str,
        proposals_path: str,
        provenance: dict[str, Any],
        accuracy_note: str,
        fetched_on: str,
        min_included_sources: int,
    ) -> dict[str, Any]:
        """The other four files ``research-run.yml:63-70`` requires.

        Without these the directory is a folder of sources; with them it is a
        stated, unanswered question and the nightly run will take it. Their
        text states, in plain language and in the place a reader of the RUN
        will look, that the classifications underneath are model proposals —
        not only in the entries, where only a reader of the corpus would see
        it.
        """
        pinned = (
            f"manifest {manifest_path} ({manifest_sha256}), abstracts pinned at "
            f"{snapshot_path} ({snapshot_sha256}), fetched {fetched_on}"
        )
        model_note = (
            f"Every evidence_relation in this corpus is a PROPOSAL made by "
            f"{provenance.get('model_name')} under {provenance.get('criteria_version')}, "
            f"recorded in {proposals_path}. None was verified against its anchored source. "
            f"{accuracy_note}"
        )
        return {
            "question-model.proposal.json": {
                "raw_question": (
                    f"Of the sources drawn for literature batch {batch!r}, which support the "
                    f"claim under examination, and which contradict, qualify or merely "
                    f"contextualize it?"
                ),
                "claim_type_sought": claim_type,
                "scope": {
                    "population": (
                        f"every source in the pinned batch manifest ({pinned}); classified "
                        f"against the pinned abstract snapshot and nothing else"
                    ),
                    "time": f"batch drawn for {batch}; abstracts fetched {fetched_on}",
                    "geography": None,
                    "conditions": [
                        "a source must be listed in the pinned batch manifest",
                        (
                            "classification is made against the source's own anchored "
                            "abstract, never against any characterisation of it"
                        ),
                        (
                            "the classification is a model proposal and is recorded as one; "
                            "no entry claims a verified relation"
                        ),
                    ],
                },
                "load_bearing_terms": [claim_text],
            },
            "concept-charter.proposal.json": {
                "entries": [
                    {
                        "entry_id": f"{analysis}-v1",
                        "term": "evidence relation",
                        "definition": (
                            "How one source's own anchored excerpt stands to the claim: it "
                            "supports the claim generally, contradicts it, qualifies it by "
                            "narrowing it to a named subset or condition, or merely "
                            "contextualizes it by bearing on the subject without taking a "
                            "position."
                        ),
                        "scope_note": (
                            "Taken verbatim from the frozen criteria "
                            f"{provenance.get('criteria_version')} "
                            f"({provenance.get('criteria_sha256')}), so this corpus is "
                            "classified under the same definitions the gold standard was "
                            "labelled under — including their KNOWN asymmetry: 'supports' "
                            "carries a generality requirement and 'contradicts' does not. "
                            "The asymmetry is part of the task for as long as that standard "
                            "is the standard; re-deriving what it costs is "
                            "docs/design/2026-08-02-zaun-gegenprobe-re-ableitung.md."
                        ),
                    }
                ]
            },
            "method-protocol.proposal.json": {
                "extraction_fields": ["claim_relevant_finding", "classification_basis"],
                "inclusion_criteria": [
                    f"listed in the pinned batch manifest ({manifest_path}, {manifest_sha256})",
                    (
                        f"an abstract is available for it in the pinned content snapshot "
                        f"({snapshot_path}, {snapshot_sha256})"
                    ),
                    "a model proposal exists for it that is not marked undecidable",
                ],
                "exclusion_criteria": [
                    "no abstract available in the pinned snapshot (excerpt_available == false)",
                    (
                        "the model declined to place the source (undecidable) — excluded and "
                        "counted, never mapped onto a default relation"
                    ),
                ],
                "sensitivity_variations": [],
                "planned_analyses": [analysis],
                "kill_conditions": [
                    f"fewer than {min_included_sources} included sources -> "
                    "stop_insufficient_evidence"
                ],
                "_model_assisted_note": model_note,
            },
            "protocol-parameters.sidecar.json": {
                "protocol_id": (
                    "placeholder-overwritten-at-run-time-by-establish_and_run_synthesis"
                ),
                "protocol_lock_content_hash": (
                    "placeholder-overwritten-at-run-time-by-establish_and_run_synthesis"
                ),
                "inclusion_filter": {
                    "primary_secondary_derived": {"allowed_values": ["primary", "secondary"]}
                },
                "eligibility_rules": {
                    "supported": {"min_independent_source_families": 2},
                    "contested": {"min_independent_source_families": 1},
                },
                "kill_conditions": {
                    "stop_insufficient_evidence": {"min_included_sources": min_included_sources}
                },
                "non_applicability_conditions": [
                    (
                        f"Findings characterize only the sources in this batch ({pinned}). "
                        "They say nothing about the state of the field and nothing about "
                        "work this batch did not draw."
                    ),
                    (
                        "Classification rests on ABSTRACTS only. An abstract may understate "
                        "or overstate a paper's own contribution; a full-text reading could "
                        "move an individual row."
                    ),
                    model_note,
                    (
                        "The excerpts are published arXiv abstracts that may have been in "
                        "the classifying model's training data. Nothing here distinguishes a "
                        "criteria-following reading from a recollection."
                    ),
                ],
            },
        }
