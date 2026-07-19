"""MB-CIT — citation and evidence anchoring
(docs/spec/05_EVALUATION_AND_ACCEPTANCE.md section 3.1, "MB-CIT — Citation
and evidence anchoring"). Scored entirely by
``mrr.services.verifier.source.validate_evidence_anchor`` (E4-T05), reused
verbatim — this module never re-decides whether an anchor resolves itself.

--- What the label adds beyond what the verifier alone determines ----------

``validate_evidence_anchor`` already decides, from a case's own ``input``
(the anchor plus whatever local artifact content is available), whether that
anchor resolves (``"validated"``/``"invalid"``/``"unvalidated"``) —
``valid_anchor_resolution_rate`` is exactly that verdict, aggregated over the
cases the label says should genuinely resolve (never over a case this
benchmark deliberately made inaccessible/invalid: a source that cannot be
opened is never counted as a valid resolution, independent of what any
system reports — task-packets/E4-T07.yaml's own acceptance test, taken
literally). The label, ``CitationExpectation.must_not_report_pass``, answers
a DIFFERENT question the verifier's own three-way status cannot on its own:
whether a SYSTEM's own self-reported verdict ("pass"/"fail"/"unknown") was
honest. A system that reports "pass" on a case the label says must not pass
is a false support — this is precisely docs/spec/05 section 3.1's
"false-support rate" metric, and precisely the scoring role a label plays
that this benchmark's own reused, purely-mechanical verifier tool cannot
play by itself (deciding whether a system's CLAIM about a citation was
truthful is not the same question as deciding whether the citation
mechanically resolves).

--- Fixture set -------------------------------------------------------------

Six cases: two that should resolve as valid support (a text anchor and a
computational anchor, each ``must_not_report_pass=False``) and four that must
not be reported as passing (inaccessible source, wrong-locator/wrong-version,
stale snapshot, and an unresolvable computational selector — each
``must_not_report_pass=True``), spanning docs/spec/05's own MB-CIT case list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from mrr.contracts.evidence_anchor import (
    AnchorValidationStatus,
    ComputationalSelector,
    EvidenceAnchor,
    TextLocator,
)
from mrr.crypto.canonical import JSONValue
from mrr.crypto.hashing import content_hash
from mrr.domain.identity import new_urn
from mrr.services.verifier.source import (
    LocalComputationalArtifact,
    LocalTextArtifact,
    SourceAccessOutcome,
    validate_evidence_anchor,
)

from benchmarks.meridianbench.harness import BenchmarkCase

#: A system under test's self-reported verdict on one citation. Deliberately
#: a narrower, benchmark-local vocabulary (not
#: ``mrr.contracts.evidence_anchor.AnchorValidationStatus``, and not
#: ``mrr.contracts.verification_result.Recommendation``): this benchmark asks
#: a system to report only whether it would treat a citation as supporting
#: evidence, not to reproduce the verifier's own internal status vocabulary.
CitationVerdict = Literal["pass", "fail", "unknown"]

_FIXTURE_PRACTICE_ID = new_urn("practice")
_FIXTURE_AGENT_ID = new_urn("agent-role")


def _fixture_anchor(**overrides: object) -> EvidenceAnchor:
    """Build a schema-valid ``EvidenceAnchor`` for a fixture with sensible,
    fixed base-object fields — mirrors
    ``tests/unit/services/verifier/test_source.py``'s own ``_base_anchor``
    helper precedent exactly.
    """
    data: dict[str, object] = {
        "id": new_urn("evidence-anchor"),
        "api_version": "mrr/v1alpha1",
        "kind": "EvidenceAnchor",
        "practice_id": _FIXTURE_PRACTICE_ID,
        "revision": 1,
        "created_at": datetime.now(UTC),
        "created_by": _FIXTURE_AGENT_ID,
        "content_hash": "sha256:" + "a" * 64,
        "relation": "supports",
        "extraction_method": "meridianbench fixture",
        "extractor_id": _FIXTURE_AGENT_ID,
        "anchor_validation_status": "unvalidated",
        "transformation_chain": [],
    }
    data.update(overrides)
    return EvidenceAnchor.model_validate(data)


@dataclass(frozen=True, slots=True, kw_only=True)
class CitationCaseInput:
    """What a system under test receives for one MB-CIT case: the claim
    text being cited for, the ``EvidenceAnchor`` citing a source, and
    whatever locally available artifact content the case supplies — the
    SAME content ``validate_evidence_anchor`` itself inspects, so the system
    and the scorer look at identical material. Never includes
    ``must_not_report_pass``.
    """

    claim_text: str
    anchor: EvidenceAnchor
    local_text_artifact: LocalTextArtifact | None = None
    local_computational_artifact: LocalComputationalArtifact | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CitationExpectation:
    """The label: whether a correct system must NOT report ``"pass"`` on
    this case. Reachable only by the scorer — never by a system under test.
    """

    must_not_report_pass: bool


CitationCase = BenchmarkCase[CitationCaseInput, CitationExpectation]


@dataclass(frozen=True, slots=True, kw_only=True)
class CitationSystemOutput:
    """What a system under test reports for one MB-CIT case."""

    verdict: CitationVerdict


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoredCitationCase:
    """One case's full scoring detail — for a human-readable report. The
    pass/fail decisions the metrics aggregate are ``is_valid_resolution``
    and ``is_false_support``.
    """

    case_id: str
    system_verdict: CitationVerdict
    anchor_validation_status: AnchorValidationStatus
    source_access_outcome: SourceAccessOutcome
    must_not_report_pass: bool
    is_valid_resolution: bool
    is_false_support: bool


def score_citation_case(case: CitationCase, output: CitationSystemOutput) -> ScoredCitationCase:
    """Score one case: run ``validate_evidence_anchor`` (E4-T05, reused
    verbatim) on the case's own anchor/artifact for the ground-truth
    resolution, and compare the SYSTEM's reported verdict against the
    label's ``must_not_report_pass`` for false support.
    """
    outcome = validate_evidence_anchor(
        case.input.anchor,
        local_text_artifact=case.input.local_text_artifact,
        local_computational_artifact=case.input.local_computational_artifact,
    )
    is_valid_resolution = outcome.anchor_validation_status == "validated"
    is_false_support = case.expected.must_not_report_pass and output.verdict == "pass"
    return ScoredCitationCase(
        case_id=case.case_id,
        system_verdict=output.verdict,
        anchor_validation_status=outcome.anchor_validation_status,
        source_access_outcome=outcome.source_access_outcome,
        must_not_report_pass=case.expected.must_not_report_pass,
        is_valid_resolution=is_valid_resolution,
        is_false_support=is_false_support,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CitationMetrics:
    """The MB-CIT metrics report: docs/spec/05 section 3.1's "anchor
    resolution rate" and "false-support rate".

    ``valid_anchor_resolution_rate`` is computed over the cases the label
    says SHOULD resolve as support (``must_not_report_pass=False``) — of
    those, the fraction the verifier actually confirms as ``"validated"``.
    This denominator choice (rather than all cases) is this task's own
    interpretive reading of docs/spec/05 section 3.1's "anchor resolution
    rate" (which names the metric without specifying its denominator) —
    flagged as an open specification question in this task's PR body,
    mirroring ``mrr.contracts.evidence_anchor.RecomputationStatus``'s own
    "not spec-derived" precedent. ``false_support_rate`` is always over ALL
    cases (section 4's target reads as a whole-corpus rate).
    """

    valid_anchor_resolution_rate: float
    false_support_rate: float
    case_count: int
    scored_cases: tuple[ScoredCitationCase, ...]


def score_citation_suite(
    cases: tuple[CitationCase, ...], outputs: tuple[CitationSystemOutput, ...]
) -> CitationMetrics:
    """Score an entire MB-CIT suite run: one output per case, same order.
    Deterministic — ``validate_evidence_anchor`` performs no I/O and no
    model call of any kind.
    """
    if len(cases) != len(outputs):
        raise ValueError(
            f"expected one output per case, got {len(outputs)} outputs for {len(cases)} cases"
        )
    scored = tuple(
        score_citation_case(case, output) for case, output in zip(cases, outputs, strict=True)
    )
    case_count = len(scored)
    should_resolve = [s for s in scored if not s.must_not_report_pass]
    valid_resolution_rate = (
        sum(1 for s in should_resolve if s.is_valid_resolution) / len(should_resolve)
        if should_resolve
        else 0.0
    )
    false_support_rate = (
        sum(1 for s in scored if s.is_false_support) / case_count if case_count else 0.0
    )
    return CitationMetrics(
        valid_anchor_resolution_rate=valid_resolution_rate,
        false_support_rate=false_support_rate,
        case_count=case_count,
        scored_cases=scored,
    )


_SUPPORTING_TEXT = "Adoption reached sixty percent of the surveyed cohort by the third quarter."
_SUPPORTING_SNAPSHOT_HASH = content_hash(_SUPPORTING_TEXT.encode("utf-8"))
_SUPPORTING_FRAGMENT = _SUPPORTING_TEXT[:34]  # "Adoption reached sixty percent of"
_SUPPORTING_FRAGMENT_HASH = content_hash(_SUPPORTING_FRAGMENT.encode("utf-8"))

_STALE_TEXT = "This page has since been edited and no longer says what it said."

_COMPUTATIONAL_DOCUMENT: JSONValue = {"rows": [{"metric": "retention", "value": 0.81}]}


#: The MB-CIT fixture suite — two genuinely resolvable citations and four
#: that must never be reported as passing, spanning docs/spec/05's own
#: MB-CIT case list (see each case's ``metadata["category"]``).
MB_CIT_CASES: tuple[CitationCase, ...] = (
    BenchmarkCase(
        case_id="mb-cit-001-supports-exact-claim",
        input=CitationCaseInput(
            claim_text="Adoption reached sixty percent of the surveyed cohort.",
            anchor=_fixture_anchor(
                anchor_kind="text",
                source_record_id=new_urn("source-record"),
                snapshot_hash=_SUPPORTING_SNAPSHOT_HASH,
                locator=TextLocator(char_start=0, char_end=34),
                quoted_fragment_hash=_SUPPORTING_FRAGMENT_HASH,
            ),
            local_text_artifact=LocalTextArtifact(full_text=_SUPPORTING_TEXT),
        ),
        expected=CitationExpectation(must_not_report_pass=False),
        metadata={"category": "source supports exact claim"},
    ),
    BenchmarkCase(
        case_id="mb-cit-002-cited-but-inaccessible",
        input=CitationCaseInput(
            claim_text="The vendor's internal dashboard reports 60% adoption.",
            anchor=_fixture_anchor(
                anchor_kind="text",
                source_record_id=new_urn("source-record"),
                snapshot_hash=content_hash(b"content this run never retrieved"),
                locator=None,
                quoted_fragment_hash=None,
            ),
            local_text_artifact=None,
        ),
        expected=CitationExpectation(must_not_report_pass=True),
        metadata={"category": "source is cited but inaccessible"},
    ),
    BenchmarkCase(
        case_id="mb-cit-003-wrong-locator",
        input=CitationCaseInput(
            claim_text="Page 12 of the report states adoption reached sixty percent.",
            anchor=_fixture_anchor(
                anchor_kind="text",
                source_record_id=new_urn("source-record"),
                snapshot_hash=_SUPPORTING_SNAPSHOT_HASH,
                locator=TextLocator(char_start=0, char_end=10_000),
                quoted_fragment_hash=None,
            ),
            local_text_artifact=LocalTextArtifact(full_text=_SUPPORTING_TEXT),
        ),
        expected=CitationExpectation(must_not_report_pass=True),
        metadata={"category": "citation points to wrong page or version"},
    ),
    BenchmarkCase(
        case_id="mb-cit-004-url-content-changed",
        input=CitationCaseInput(
            claim_text="The live page still says adoption reached sixty percent.",
            anchor=_fixture_anchor(
                anchor_kind="text",
                source_record_id=new_urn("source-record"),
                snapshot_hash=_SUPPORTING_SNAPSHOT_HASH,
                locator=None,
                quoted_fragment_hash=None,
            ),
            local_text_artifact=LocalTextArtifact(full_text=_STALE_TEXT),
        ),
        expected=CitationExpectation(must_not_report_pass=True),
        metadata={"category": "URL content changed after retrieval"},
    ),
    BenchmarkCase(
        case_id="mb-cit-005-computational-resolves",
        input=CitationCaseInput(
            claim_text="Retention across the cohort's rows was 0.81.",
            anchor=_fixture_anchor(
                anchor_kind="computational",
                run_id=new_urn("run"),
                recomputation_status="reproduced",
                selector=ComputationalSelector(json_pointer="/rows/0/value"),
            ),
            local_computational_artifact=LocalComputationalArtifact(
                document=_COMPUTATIONAL_DOCUMENT
            ),
        ),
        expected=CitationExpectation(must_not_report_pass=False),
        metadata={"category": "recomputable analysis with known output"},
    ),
    BenchmarkCase(
        case_id="mb-cit-006-computational-selector-missing",
        input=CitationCaseInput(
            claim_text="Row five of the retention table reports 0.99.",
            anchor=_fixture_anchor(
                anchor_kind="computational",
                run_id=new_urn("run"),
                recomputation_status="reproduced",
                selector=ComputationalSelector(json_pointer="/rows/5/value"),
            ),
            local_computational_artifact=LocalComputationalArtifact(
                document=_COMPUTATIONAL_DOCUMENT
            ),
        ),
        expected=CitationExpectation(must_not_report_pass=True),
        metadata={"category": "claim is not found"},
    ),
)

__all__ = [
    "MB_CIT_CASES",
    "CitationCase",
    "CitationCaseInput",
    "CitationExpectation",
    "CitationMetrics",
    "CitationSystemOutput",
    "CitationVerdict",
    "ScoredCitationCase",
    "score_citation_case",
    "score_citation_suite",
]
