"""Pure, framework-free citation-audit report projection (task-packets/
N2-T01.yaml R2). Mirrors ``mrr.domain.agreement_report``'s own split
precedent (pure model-building/rendering here; ``mrr.services.citation_audit
.service.CitationAuditService`` performs every I/O step and hands this module
already-loaded values) and, for the Pydantic base, ``mrr.contracts.common
.MRRModel`` (``extra="forbid"``) — the same established, non-circular pattern
``mrr.domain.agreement_report`` already uses for the identical reason.

--- NOT a BaseObject, NOT a persisted object (task-packets/N2-T01.yaml R2) ---

:class:`CitationAuditReport` and every nested model here are pure Pydantic
projections: no ``id``/``api_version``/``revision``/``content_hash``/audit
fields, no ``schemas/*.schema.json`` mirror, and this report is NEVER passed
to ``ObjectRepository.insert_revision`` or any other persistence call
anywhere in this packet (``mrr.services.citation_audit.service
.CitationAuditService`` opens no database connection at all). Matches
``mrr.domain.agreement_report.AgreementReport``'s identical "a projection,
not the primary research record" stance (AGENTS.md rule 7 combined with the
source-of-truth discipline's "narrative reports are projections").

--- The honesty header is structural, not advisory (R2) -----------------------

``verifies_existence_not_support`` is a ``Literal[True]`` — it cannot be
constructed as ``False`` even by a caller mistake, because there is no other
value the type permits. ``existence_note`` is a non-empty, fixed-content
string assembled from the SAME module-level constant template every report
uses (:data:`_EXISTENCE_NOT_SUPPORT_NOTE`) — never freehand text a caller
could omit or drift. This is the N2 analogue of N1's "measures reliability,
not validity" header (task-packets/N2-T01.yaml derived_decisions (a)):
"resolved" here means the reference EXISTS and is correctly TITLED — it is
NOT evidence the source SUPPORTS the claim it is cited for (N2-T02, deferred,
named) and NOT evidence any number attributed to it is consistent (N2-T03,
deferred, named).

--- Determinism (task-packets/N2-T01.yaml invariant) --------------------------

:func:`render_markdown`/:func:`render_json` are pure — no wall clock, no
network, no filesystem. :func:`build_citation_audit_report` sorts its
``citations`` by ``citation_id`` (the one place ordering is decided), so
calling either renderer twice over an equal report produces byte-identical
output (tests/unit/domain/test_citation_audit_report.py).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from mrr.contracts.common import MRRModel
from mrr.domain.citation_audit import CITATION_STATUSES, CitationStatus, CitationVerdict
from pydantic import Field

_EXISTENCE_NOT_SUPPORT_NOTE = (
    "This report verifies that each cited identifier EXISTS (it resolves to a real, "
    "registered work) and that its TITLE is correctly attributed. It does NOT verify that "
    "the resolved source SUPPORTS the claim it was cited for — support-checking is a "
    "separate, not-yet-built use case (N2-T02, LLM/human-touching) — and it does NOT verify "
    "that any number attributed to the source is consistent with it (number-consistency is "
    "N2-T03, also not-yet-built). A 'resolved' status below means the reference exists and "
    "is titled as claimed here, nothing more."
)


class SummaryCounts(MRRModel):
    """Counts per :data:`mrr.domain.citation_audit.CitationStatus`, plus
    ``total`` — the five statuses are ALWAYS reported distinctly (task-
    packets/N2-T01.yaml invariant), never collapsed into a pass/fail count.
    """

    resolved: int = Field(ge=0)
    not_found: int = Field(ge=0)
    title_mismatch: int = Field(ge=0)
    unverifiable: int = Field(ge=0)
    malformed: int = Field(ge=0)
    total: int = Field(ge=0)


class CitationVerdictRow(MRRModel):
    """One citation's audit verdict — mirrors ``mrr.domain.citation_audit
    .CitationVerdict`` field for field (Pydantic projection of that plain
    dataclass).
    """

    citation_id: str
    cited_as: str
    identifier: str
    status: CitationStatus
    resolved_title: str | None
    reason: str


class CitationAuditReport(MRRModel):
    """The full citation-audit report (task-packets/N2-T01.yaml R2): a fixed
    honesty header, the audit's own target/input identity (``audit_target``,
    ``manifest_path``, ``snapshot_path``, ``snapshot_sha256``), the summary
    counts, and an ORDERED list of per-citation verdicts (sorted by
    ``citation_id``).
    """

    verifies_existence_not_support: Literal[True] = True
    existence_note: str = _EXISTENCE_NOT_SUPPORT_NOTE
    audit_target: str
    manifest_path: str
    snapshot_path: str
    snapshot_sha256: str
    summary: SummaryCounts
    citations: tuple[CitationVerdictRow, ...]


def build_citation_audit_report(
    *,
    audit_target: str,
    manifest_path: str,
    snapshot_path: str,
    snapshot_sha256: str,
    verdicts: Sequence[CitationVerdict],
) -> CitationAuditReport:
    """Assemble the top-level :class:`CitationAuditReport` from
    already-computed R1 verdicts. ``verdicts`` is sorted by ``citation_id``
    here (defensively — ``mrr.domain.citation_audit.classify_citations``
    already returns them in that order, but this is the one place ordering
    is guaranteed regardless of what a caller hands in) and the per-status
    summary counts are computed from that same ordered sequence.
    """
    ordered = tuple(sorted(verdicts, key=lambda verdict: verdict.citation_id))

    counts: dict[CitationStatus, int] = dict.fromkeys(CITATION_STATUSES, 0)
    for verdict in ordered:
        counts[verdict.status] += 1

    summary = SummaryCounts(
        resolved=counts["resolved"],
        not_found=counts["not_found"],
        title_mismatch=counts["title_mismatch"],
        unverifiable=counts["unverifiable"],
        malformed=counts["malformed"],
        total=len(ordered),
    )

    rows = tuple(
        CitationVerdictRow(
            citation_id=verdict.citation_id,
            cited_as=verdict.cited_as,
            identifier=verdict.identifier,
            status=verdict.status,
            resolved_title=verdict.resolved_title,
            reason=verdict.reason,
        )
        for verdict in ordered
    )

    return CitationAuditReport(
        audit_target=audit_target,
        manifest_path=manifest_path,
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_sha256,
        summary=summary,
        citations=rows,
    )


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic, no wall clock.
# ---------------------------------------------------------------------------


def _escape_table_cell(value: str) -> str:
    """Escape a value for embedding in a single Markdown table cell: a
    literal ``|`` would otherwise be read as a column delimiter, and an
    embedded newline would break the row onto multiple lines — neither is
    expected in this report's own fields (``cited_as`` strings, titles),
    but both are handled explicitly rather than assumed absent.
    """
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: CitationAuditReport) -> str:
    """Deterministic Markdown rendering: an honesty header, the summary
    counts, and a per-citation table (task-packets/N2-T01.yaml R4: "Markdown
    shows the per-citation table (cited_as, identifier, status,
    resolved_title)"). Calling this twice with an equal ``report`` produces
    byte-identical output (no wall clock, no unordered iteration —
    ``report.citations`` is already an explicit, ordered tuple).
    """
    lines: list[str] = []
    lines.append("# Citation audit report (task-packets/N2-T01.yaml)")
    lines.append("")
    lines.append("## Honesty header")
    lines.append("")
    lines.append(f"- **verifies_existence_not_support:** {report.verifies_existence_not_support}")
    lines.append(f"- {report.existence_note}")
    lines.append(f"- **audit target:** {report.audit_target}")
    lines.append(f"- **manifest path:** {report.manifest_path}")
    lines.append(f"- **snapshot path:** {report.snapshot_path}")
    lines.append(f"- **snapshot sha256:** {report.snapshot_sha256}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **total:** {report.summary.total}")
    lines.append(f"- **resolved:** {report.summary.resolved}")
    lines.append(f"- **not_found:** {report.summary.not_found}")
    lines.append(f"- **title_mismatch:** {report.summary.title_mismatch}")
    lines.append(f"- **unverifiable:** {report.summary.unverifiable}")
    lines.append(f"- **malformed:** {report.summary.malformed}")
    lines.append("")
    lines.append("## Citations")
    lines.append("")
    lines.append("| citation_id | cited_as | identifier | status | resolved_title | reason |")
    lines.append("|---|---|---|---|---|---|")
    for row in report.citations:
        resolved_title = row.resolved_title if row.resolved_title is not None else "—"
        cells = (
            row.citation_id,
            row.cited_as,
            row.identifier,
            row.status,
            resolved_title,
            row.reason,
        )
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_json(report: CitationAuditReport) -> str:
    """Deterministic JSON rendering: ``sort_keys=True`` so byte-identity
    across two renders never depends on Python dict/model-field insertion
    order, only on ``report``'s own already-fixed field values.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
