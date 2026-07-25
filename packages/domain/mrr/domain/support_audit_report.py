"""Pure, framework-free support-audit report projection (task-packets/
N2-T03b.yaml). Mirrors ``mrr.domain.anchoring_integrity_report``'s/``mrr
.domain.field_observation_report``'s own split precedent (pure model-
building/rendering here; ``mrr.services.support_audit.service
.SupportAuditService`` performs every I/O step — reading the batch
descriptor, hashing its declared inputs, running the fail-closed gate,
evaluating every claim via ``mrr.domain.support_audit`` — and hands this
module already-computed verdicts) and, for the Pydantic base,
``mrr.contracts.common.MRRModel`` (``extra="forbid"``), the same
established, non-circular pattern those two modules already use.

--- NOT a BaseObject, NOT a persisted object (AGENTS.md rule 7) -------------

Every model here is a pure Pydantic projection: no ``id``/``api_version``/
``revision``/``content_hash``/audit fields, no ``schemas/*.schema.json``
mirror, and this report is NEVER passed to any persistence call anywhere in
this packet (``SupportAuditService`` opens no database connection and no
network connection at all). Matches ``mrr.domain.anchoring_integrity_report
.AnchoringIntegrityReport``'s/``mrr.domain.citation_audit_report
.CitationAuditReport``'s identical "a projection, not the primary research
record" stance.

--- The honesty header is structural, not advisory --------------------------

``presence_is_not_support`` and ``checked_excerpt_is_abstract_only`` are both
``Literal[True]`` — neither can be constructed as ``False`` even by a caller
mistake, because there is no other value the type permits. ``note`` is a
non-empty, fixed-content string from the SAME module-level constant every
report uses (:data:`_NOTE`) — never freehand text a caller could omit or
drift. This is the N2-T03 analogue of N1's "measures reliability, not
validity", N2-T01's "verifies existence, not support", N2-T02b's "anchoring,
not support", and R2-T01's "observation, not optimization" headers: a
``figure_supported_in_excerpt``/``quotation_verbatim`` verdict here means the
CHECKED EXCERPT — the ABSTRACT captured by N2-T03a, and NOTHING ELSE —
carries the claim. It says NOTHING about whether the source supports the
claim in substance (that is the deferred, blocked N2-T03c), and an ABSENT
verdict says NOTHING about whether the source contradicts the claim (not
decidable against an abstract at all — there is no ``figure_contradicted``
status anywhere in this module or ``mrr.domain.support_audit``).

--- Violations and observations are NEVER summed (the packet's single most
    important invariant) -----------------------------------------------------

:class:`SupportAuditCounts` carries every status's own count as a distinct
field, PLUS ``violations`` (== ``quotation_altered`` — the only violation
status this packet's vocabulary has) and ``observations`` (==
``figure_absent_from_checked_excerpt`` + ``quotation_absent_from_checked_
excerpt``) as two SEPARATE derived fields, computed here from the SAME
already-counted per-status values and never added to each other anywhere
in this module. Against the real committed inputs this counts 0 violations
and 18 observations (task-packets/N2-T03b.yaml acceptance_criteria) — were
they ever collapsed into one "problems" total, the very first run would
misreport 18 false violations (AGENTS.md's "collapsing distinct statuses
into one generic outcome" prohibited shortcut).

--- The used quotation-similarity threshold is part of the honesty header ---

``quotation_similarity_threshold`` is a required field on
:class:`SupportAuditReport` (``Field(gt=0.0, le=1.0)``, so it cannot even be
constructed as 0, negative, or above 1) and is rendered in BOTH
:func:`render_markdown` and :func:`render_json` alongside ``presence_is_not_
support``/``checked_excerpt_is_abstract_only``. A review of this packet
found that this value — the ONLY parameter in this packet's vocabulary able
to produce a VIOLATION (``quotation_altered``) — was originally a bare
module constant in ``mrr.domain.support_audit``: invisible to the fail-
closed hash gate and to any report reader, so a future code change could
shift every ``quotation_altered``/``quotation_absent_from_checked_excerpt``
verdict without any anchor noticing and without any reader being able to
tell which threshold was actually used. It now lives in the committed,
hashed claim manifest (``quotation_similarity_threshold``, gated exactly
like ``anchor_window_chars``) and is carried through to this exact field, so
a reader can always see which value decided every quotation verdict in the
report they are looking at.

--- The matched window is rendered for every resolved figure ----------------

Every :class:`FigureVerdictRow` with status ``"figure_supported_in_excerpt"``
carries a non-empty ``matched_windows`` (one entry per declared token,
computed by ``mrr.domain.support_audit.evaluate_figure_claim`` and passed
through unchanged) — rendered in BOTH :func:`render_markdown` and
:func:`render_json`, never omitted for a resolved figure and never rendered
for an absent one. No window size can distinguish a describing neighbour
from an accidental one (the committed claim manifest's own ``anchor_window_
note`` names ``agent-laboratory-stages`` as exactly such a case, resolving
through a coincidental enumeration marker rather than the abstract's real
"three stages" prose) — a human reader must be able to see the evidence
itself, not just a status word.

--- Determinism (task-packets/N2-T03b.yaml invariant) -------------------------

:func:`render_markdown`/:func:`render_json` are pure — no wall clock, no
network, no filesystem. :func:`build_support_audit_report` sorts its own
inputs (the one place ordering is decided — by ``claim_id`` for every one of
the three verdict kinds), so calling either renderer twice over an equal
report produces byte-identical output.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from mrr.contracts.common import MRRModel
from mrr.domain.support_audit import (
    ExclusionStatus,
    ExclusionVerdict,
    FigureStatus,
    FigureVerdict,
    QuotationStatus,
    QuotationVerdict,
)
from pydantic import Field

_NOTE = (
    "The CHECKED EXCERPT for every claim in this report is the ABSTRACT ONLY (captured by "
    "N2-T03a — the arXiv Atom 'summary' element, or the Crossref 'abstract' field for the one "
    "Nature DOI) — never the full paper, never a PDF. Presence in the abstract is NOT "
    "substantive support: a 'figure_supported_in_excerpt'/'quotation_verbatim' verdict proves "
    "only that the checked excerpt carries the claim, not that the source supports it in "
    "substance (that judgement is N2-T03c, a model/human-touching use case, deferred and "
    "currently blocked). Absence from the checked excerpt is NOT refutation: 'figure_absent_"
    "from_checked_excerpt' and 'quotation_absent_from_checked_excerpt' are OBSERVATIONS, never "
    "violations — an abstract covers only a fraction of a paper's own claims, measured at the "
    "N2-T03 derivation at roughly 28 percent numeric-token coverage across this corpus, so "
    "absence is the NORMAL case, not a defect. There is no 'figure_contradicted' status: "
    "contradiction is not decidable against an abstract and is not in this report's vocabulary "
    "at all. The only VIOLATION status this report can ever carry is 'quotation_altered' — the "
    "checked excerpt carries the quoted passage in demonstrably different words. This audit is "
    "entirely model-free: no LLM, no provider adapter, and no paraphrase-level judgement is made "
    "anywhere in producing it."
)


class MatchedWindowRow(MRRModel):
    """One figure token's rendered matched-excerpt window — mirrors
    ``mrr.domain.support_audit.MatchedWindow`` field for field.
    """

    token: str
    window_text: str


class FigureVerdictRow(MRRModel):
    """One figure claim's evaluated verdict — mirrors ``mrr.domain
    .support_audit.FigureVerdict`` field for field. ``matched_windows`` is
    non-empty iff ``status`` is ``"figure_supported_in_excerpt"``.
    """

    claim_id: str
    citation_id: str
    status: FigureStatus
    matched_windows: tuple[MatchedWindowRow, ...]


class QuotationVerdictRow(MRRModel):
    """One quotation claim's evaluated verdict — mirrors ``mrr.domain
    .support_audit.QuotationVerdict`` field for field.
    """

    claim_id: str
    citation_id: str
    status: QuotationStatus
    matched_text: str | None


class ExclusionVerdictRow(MRRModel):
    """One excluded claim, carrying the manifest's own named reason —
    mirrors ``mrr.domain.support_audit.ExclusionVerdict`` field for field.
    """

    claim_id: str
    citation_id: str
    status: ExclusionStatus
    exclusion_reason: str


class SupportAuditCounts(MRRModel):
    """Every status's own count as a distinct field, plus ``total``,
    ``violations`` (== ``quotation_altered`` only), and ``observations``
    (== ``figure_absent_from_checked_excerpt`` + ``quotation_absent_from_
    checked_excerpt``) — ``violations`` and ``observations`` are NEVER
    summed with each other anywhere (see the module docstring). Mirrors the
    exact shape of task-packets/N2-T03b.yaml's own ``acceptance_oracle.
    totals`` block, so a report's counts can be compared against it field
    for field.
    """

    figure_supported_in_excerpt: int = Field(ge=0)
    figure_absent_from_checked_excerpt: int = Field(ge=0)
    quotation_verbatim: int = Field(ge=0)
    quotation_absent_from_checked_excerpt: int = Field(ge=0)
    quotation_altered: int = Field(ge=0)
    claim_excluded: int = Field(ge=0)
    total: int = Field(ge=0)
    violations: int = Field(ge=0)
    observations: int = Field(ge=0)


class SupportAuditReport(MRRModel):
    """The full, top-level support-audit report (task-packets/N2-T03b.yaml):
    a fixed, structural honesty header, the batch's own identity, the three
    ORDERED verdict tuples (each sorted by ``claim_id``), and the counts
    block.
    """

    presence_is_not_support: Literal[True] = True
    checked_excerpt_is_abstract_only: Literal[True] = True
    note: str = _NOTE
    batch_id: str
    audit_target: str
    quotation_similarity_threshold: float = Field(gt=0.0, le=1.0)
    figures: tuple[FigureVerdictRow, ...]
    quotations: tuple[QuotationVerdictRow, ...]
    exclusions: tuple[ExclusionVerdictRow, ...]
    counts: SupportAuditCounts


def build_support_audit_report(
    *,
    batch_id: str,
    audit_target: str,
    quotation_similarity_threshold: float,
    figure_verdicts: Sequence[FigureVerdict],
    quotation_verdicts: Sequence[QuotationVerdict],
    exclusion_verdicts: Sequence[ExclusionVerdict],
) -> SupportAuditReport:
    """Assemble the :class:`SupportAuditReport` from already-computed
    ``mrr.domain.support_audit`` verdicts. Every verdict sequence is sorted
    here by ``claim_id`` (the one place this ordering is guaranteed
    regardless of what a caller hands in), and the counts block is computed
    from those same ordered sequences — ``violations``/``observations`` are
    DERIVED sums of already-distinct per-status counts, never independently
    supplied and never summed with each other (see the module docstring).

    ``quotation_similarity_threshold`` is carried through UNCHANGED onto the
    report header — the SAME value the caller already used to produce
    ``quotation_verdicts`` via ``mrr.domain.support_audit
    .evaluate_quotation_claim`` (this function does not re-derive or
    validate it; that happens once, at the manifest-parsing boundary in
    ``mrr.services.support_audit.service``). A reader of the rendered report
    can therefore always see exactly which threshold decided every
    ``quotation_altered``/``quotation_absent_from_checked_excerpt`` verdict
    in it, in both JSON and Markdown (task-packets/N2-T03b.yaml post-review
    correction).
    """
    ordered_figures = tuple(sorted(figure_verdicts, key=lambda verdict: verdict.claim_id))
    ordered_quotations = tuple(sorted(quotation_verdicts, key=lambda verdict: verdict.claim_id))
    ordered_exclusions = tuple(sorted(exclusion_verdicts, key=lambda verdict: verdict.claim_id))

    figure_supported_count = sum(
        1 for verdict in ordered_figures if verdict.status == "figure_supported_in_excerpt"
    )
    figure_absent_count = sum(
        1 for verdict in ordered_figures if verdict.status == "figure_absent_from_checked_excerpt"
    )
    quotation_verbatim_count = sum(
        1 for verdict in ordered_quotations if verdict.status == "quotation_verbatim"
    )
    quotation_altered_count = sum(
        1 for verdict in ordered_quotations if verdict.status == "quotation_altered"
    )
    quotation_absent_count = sum(
        1
        for verdict in ordered_quotations
        if verdict.status == "quotation_absent_from_checked_excerpt"
    )
    claim_excluded_count = len(ordered_exclusions)

    total = len(ordered_figures) + len(ordered_quotations) + len(ordered_exclusions)
    violations = quotation_altered_count
    observations = figure_absent_count + quotation_absent_count

    counts = SupportAuditCounts(
        figure_supported_in_excerpt=figure_supported_count,
        figure_absent_from_checked_excerpt=figure_absent_count,
        quotation_verbatim=quotation_verbatim_count,
        quotation_absent_from_checked_excerpt=quotation_absent_count,
        quotation_altered=quotation_altered_count,
        claim_excluded=claim_excluded_count,
        total=total,
        violations=violations,
        observations=observations,
    )

    return SupportAuditReport(
        batch_id=batch_id,
        audit_target=audit_target,
        quotation_similarity_threshold=quotation_similarity_threshold,
        figures=tuple(
            FigureVerdictRow(
                claim_id=verdict.claim_id,
                citation_id=verdict.citation_id,
                status=verdict.status,
                matched_windows=tuple(
                    MatchedWindowRow(token=window.token, window_text=window.window_text)
                    for window in verdict.matched_windows
                ),
            )
            for verdict in ordered_figures
        ),
        quotations=tuple(
            QuotationVerdictRow(
                claim_id=verdict.claim_id,
                citation_id=verdict.citation_id,
                status=verdict.status,
                matched_text=verdict.matched_text,
            )
            for verdict in ordered_quotations
        ),
        exclusions=tuple(
            ExclusionVerdictRow(
                claim_id=verdict.claim_id,
                citation_id=verdict.citation_id,
                status=verdict.status,
                exclusion_reason=verdict.exclusion_reason,
            )
            for verdict in ordered_exclusions
        ),
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic, no wall clock.
# ---------------------------------------------------------------------------


def _escape_table_cell(value: str) -> str:
    """Escape a value for embedding in a single Markdown table cell —
    mirrors ``mrr.domain.citation_audit_report._escape_table_cell``/``mrr
    .domain.anchoring_integrity_report._escape_table_cell`` exactly.
    """
    return value.replace("|", "\\|").replace("\n", " ")


def _render_counts_section(counts: SupportAuditCounts) -> list[str]:
    lines: list[str] = []
    lines.append("## Counts")
    lines.append("")
    lines.append(f"- **total claims:** {counts.total}")
    lines.append(f"- **violations (quotation_altered only):** {counts.violations}")
    lines.append(
        "- **observations (figure_absent_from_checked_excerpt + "
        f"quotation_absent_from_checked_excerpt):** {counts.observations}"
    )
    lines.append(f"- figure_supported_in_excerpt: {counts.figure_supported_in_excerpt}")
    lines.append(
        f"- figure_absent_from_checked_excerpt: {counts.figure_absent_from_checked_excerpt}"
    )
    lines.append(f"- quotation_verbatim: {counts.quotation_verbatim}")
    lines.append(
        f"- quotation_absent_from_checked_excerpt: {counts.quotation_absent_from_checked_excerpt}"
    )
    lines.append(f"- quotation_altered: {counts.quotation_altered}")
    lines.append(f"- claim_excluded: {counts.claim_excluded}")
    lines.append("")
    return lines


def _render_figures_section(figures: Sequence[FigureVerdictRow]) -> list[str]:
    lines: list[str] = []
    lines.append("## Figures")
    lines.append("")
    lines.append("| claim_id | citation_id | status | matched windows |")
    lines.append("|---|---|---|---|")
    for row in figures:
        windows_text = " ; ".join(
            f"[{window.token}] {window.window_text}" for window in row.matched_windows
        )
        cells = (row.claim_id, row.citation_id, row.status, windows_text)
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    return lines


def _render_quotations_section(quotations: Sequence[QuotationVerdictRow]) -> list[str]:
    lines: list[str] = []
    lines.append("## Quotations")
    lines.append("")
    lines.append("| claim_id | citation_id | status | matched text |")
    lines.append("|---|---|---|---|")
    for row in quotations:
        cells = (row.claim_id, row.citation_id, row.status, row.matched_text or "")
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    return lines


def _render_exclusions_section(exclusions: Sequence[ExclusionVerdictRow]) -> list[str]:
    lines: list[str] = []
    lines.append("## Exclusions — neither a hit nor a violation")
    lines.append("")
    lines.append("| claim_id | citation_id | exclusion_reason |")
    lines.append("|---|---|---|")
    for row in exclusions:
        cells = (row.claim_id, row.citation_id, row.exclusion_reason)
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    return lines


def render_markdown(report: SupportAuditReport) -> str:
    """Deterministic Markdown rendering: an honesty header, the counts
    block, then the three verdict tables — figures (with every resolved
    figure's matched window shown), quotations, and exclusions. Calling this
    twice with an equal ``report`` produces byte-identical output (no wall
    clock, no unordered iteration — every tuple here is already explicit
    and ordered).
    """
    lines: list[str] = []
    lines.append("# Support audit report (task-packets/N2-T03b.yaml)")
    lines.append("")
    lines.append("## Honesty header")
    lines.append("")
    lines.append(f"- **presence_is_not_support:** {report.presence_is_not_support}")
    lines.append(
        f"- **checked_excerpt_is_abstract_only:** {report.checked_excerpt_is_abstract_only}"
    )
    lines.append(f"- {report.note}")
    lines.append(f"- **batch id:** {report.batch_id}")
    lines.append(f"- **audit target:** {report.audit_target}")
    lines.append(
        f"- **quotation_similarity_threshold:** {report.quotation_similarity_threshold} "
        "(the exact value a quotation's best-window similarity is compared against to decide "
        "quotation_altered vs. quotation_absent_from_checked_excerpt — pinned in the committed "
        "claim manifest, not a bare code constant)"
    )
    lines.append("")
    lines.extend(_render_counts_section(report.counts))
    lines.extend(_render_figures_section(report.figures))
    lines.extend(_render_quotations_section(report.quotations))
    lines.extend(_render_exclusions_section(report.exclusions))
    return "\n".join(lines) + "\n"


def render_json(report: SupportAuditReport) -> str:
    """Deterministic JSON rendering: ``sort_keys=True`` so byte-identity
    across two renders never depends on Python dict/model-field insertion
    order, only on ``report``'s own already-fixed field values.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
