"""Pure, framework-free anchoring-integrity report projection (task-packets/
N2-T02b.yaml R3). Mirrors ``mrr.domain.citation_audit_report``'s/``mrr.domain
.field_observation_report``'s own split precedent (pure model-building/
rendering here; ``mrr.services.anchoring_integrity.service
.AnchoringIntegrityService`` performs every I/O step and hands this module
already-computed values) and, for the Pydantic base, ``mrr.contracts.common
.MRRModel`` (``extra="forbid"``) — the same established, non-circular
pattern those two modules already use.

--- NOT a BaseObject, NOT a persisted object (task-packets/N2-T02b.yaml R3) -

Every model here is a pure Pydantic projection: no ``id``/``api_version``/
``revision``/``content_hash``/audit fields, no ``schemas/*.schema.json``
mirror, and this report is NEVER passed to ``ObjectRepository
.insert_revision`` or any other persistence call anywhere in this packet
(``AnchoringIntegrityService`` opens no database connection and no network
connection at all). Matches ``mrr.domain.citation_audit_report
.CitationAuditReport``'s identical "a projection, not the primary research
record" stance (AGENTS.md rule 7 combined with the source-of-truth
discipline's "narrative reports are projections").

--- The honesty header is structural, not advisory (R3) ----------------------

``anchoring_is_not_support`` is a ``Literal[True]`` — it cannot be
constructed as ``False`` even by a caller mistake, because there is no other
value the type permits. ``note`` is a non-empty, fixed-content string from
the SAME module-level constant every report uses (:data:`_ANCHORING_NOTE`)
— never freehand text a caller could omit or drift. This is the N2-T02b
analogue of N1's "measures reliability, not validity", N2-T01's "verifies
existence, not support", and R2-T01's "observation, not optimization"
headers: a resolvable anchor here means the anchor points at a REALLY
ARCHIVED SourceRecord — it says NOTHING about whether that source SUPPORTS
the claim it is cited for (N2-T03, deferred, named).

--- Violations and observations are NEVER summed (the packet's single most
    important invariant) -----------------------------------------------------

:class:`ViolationCounts` (``anchor_dangling``, ``claim_reference_dangling``)
and :class:`ObservationCounts` (``source_unanchored``, ``anchor_unreferenced``)
are separate, distinctly-named fields on :class:`DumpAnchoringReport` — this
module never introduces a combined "problems" field, and never sums a
violation count with an observation count anywhere (task-packets/
N2-T02b.yaml R3/invariant; AGENTS.md's "collapsing ... into one generic
error" prohibited shortcut). Against the real, committed archive, collapsing
them would immediately report 1 (``mrr_k1t04_real_run_v2``) and 2
(``mrr_run2_corroboration_floor_v1``) FALSE violations.

--- Determinism (task-packets/N2-T02b.yaml invariant) -------------------------

:func:`render_markdown`/:func:`render_json` are pure — no wall clock, no
network, no filesystem. :func:`build_dump_anchoring_report`/
:func:`build_anchoring_integrity_report` both sort their own inputs (the one
place ordering is decided), so calling either renderer twice over an equal
report produces byte-identical output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal

from mrr.contracts.common import MRRModel
from mrr.domain.anchoring_integrity import (
    AnchorCoverage,
    AnchorCoverageVerdict,
    AnchorLinkStatus,
    AnchorLinkVerdict,
    ClaimReferenceStatus,
    ClaimReferenceVerdict,
    ClaimRelationKind,
    DumpAnchorCheckResult,
    SourceCoverage,
    SourceCoverageVerdict,
)
from pydantic import Field

_ANCHORING_NOTE = (
    "This report verifies REFERENCE RESOLUTION over the committed archive dumps: (i) a "
    "resolvable EvidenceAnchor->SourceRecord link or Claim->EvidenceAnchor reference proves "
    "the anchor points at a REALLY ARCHIVED object, and (ii) it does NOT establish that the "
    "resolved source SUPPORTS the claim it was cited for — support-checking is a separate, "
    "not-yet-built use case (N2-T03, LLM/human-touching). (iii) 'source_unanchored' "
    "SourceRecords and 'anchor_unreferenced' EvidenceAnchors are OBSERVATIONS, not errors — a "
    "corpus source that ended up carrying no evidence, or an anchor no claim happened to cite, "
    "is not an integrity violation, and this report never sums an observation count into a "
    "violation count. (iv) No external resolution of the archive's own SourceRecords (arXiv, "
    "DOI, or otherwise) is performed here."
)


class DumpFileAnchor(MRRModel):
    """One declared dump's file-level integrity-anchor comparison — mirrors
    ``mrr.domain.field_observation_report.AnchorRow`` field for field.
    """

    schema_name: str
    path: str
    declared_sha256: str
    actual_sha256: str
    matched: bool


class ObjectCounts(MRRModel):
    """One dump's object row counts: ``total`` (every row of every kind,
    including every historical revision — see ``mrr.domain.archive_dump``'s
    module docstring), and ``by_kind`` (a ``kind`` -> row-count mapping,
    e.g. ``{"SourceRecord": 18, "EvidenceAnchor": 17, "Claim": 4, ...}``).
    """

    total: int = Field(ge=0)
    by_kind: dict[str, int]


class AnchorLinkVerdictRow(MRRModel):
    """One ``EvidenceAnchor``'s reference-resolution verdict — mirrors
    ``mrr.domain.anchoring_integrity.AnchorLinkVerdict`` field for field.
    """

    anchor_id: str
    source_record_id: str
    status: AnchorLinkStatus


class ClaimReferenceVerdictRow(MRRModel):
    """One Claim->EvidenceAnchor reference's resolution verdict — mirrors
    ``mrr.domain.anchoring_integrity.ClaimReferenceVerdict`` field for
    field.
    """

    claim_id: str
    anchor_id: str
    relation_kind: ClaimRelationKind
    status: ClaimReferenceStatus


class SourceCoverageVerdictRow(MRRModel):
    """One ``SourceRecord``'s coverage verdict — mirrors
    ``mrr.domain.anchoring_integrity.SourceCoverageVerdict`` field for
    field.
    """

    source_record_id: str
    title: str
    status: SourceCoverage


class AnchorCoverageVerdictRow(MRRModel):
    """One ``EvidenceAnchor``'s coverage verdict — mirrors
    ``mrr.domain.anchoring_integrity.AnchorCoverageVerdict`` field for
    field.
    """

    anchor_id: str
    status: AnchorCoverage


class ViolationCounts(MRRModel):
    """Integrity VIOLATION counts — ``anchor_dangling`` and
    ``claim_reference_dangling`` only. Never a combined "problems" field
    (see the module docstring).
    """

    anchor_dangling: int = Field(ge=0)
    claim_reference_dangling: int = Field(ge=0)


class ObservationCounts(MRRModel):
    """Coverage OBSERVATION counts — ``source_unanchored`` and
    ``anchor_unreferenced`` only. Never a violation, and never summed with
    :class:`ViolationCounts` (see the module docstring).
    """

    source_unanchored: int = Field(ge=0)
    anchor_unreferenced: int = Field(ge=0)


class DumpAnchoringReport(MRRModel):
    """The full anchoring-integrity report for ONE dump (task-packets/
    N2-T02b.yaml R3): its file-level integrity anchor, its object counts,
    the four ORDERED verdict tuples, and the two SEPARATE count blocks.
    """

    schema_name: str
    file_anchor: DumpFileAnchor
    object_counts: ObjectCounts
    anchor_links: tuple[AnchorLinkVerdictRow, ...]
    claim_references: tuple[ClaimReferenceVerdictRow, ...]
    source_coverage: tuple[SourceCoverageVerdictRow, ...]
    anchor_coverage: tuple[AnchorCoverageVerdictRow, ...]
    violations: ViolationCounts
    observations: ObservationCounts


class AnchoringIntegrityReport(MRRModel):
    """The full, top-level anchoring-integrity report (task-packets/
    N2-T02b.yaml R3): a fixed honesty header, the batch's own identity, and
    an ORDERED tuple of per-dump reports (sorted by ``schema_name``).
    """

    anchoring_is_not_support: Literal[True] = True
    note: str = _ANCHORING_NOTE
    batch_id: str
    observation_kind: str
    audit_target: str
    dumps: tuple[DumpAnchoringReport, ...] = Field(min_length=1)


def build_dump_anchoring_report(
    *,
    schema_name: str,
    file_anchor: DumpAnchorCheckResult,
    total_objects: int,
    object_counts_by_kind: Mapping[str, int],
    anchor_links: Sequence[AnchorLinkVerdict],
    claim_references: Sequence[ClaimReferenceVerdict],
    source_coverage: Sequence[SourceCoverageVerdict],
    anchor_coverage: Sequence[AnchorCoverageVerdict],
) -> DumpAnchoringReport:
    """Assemble one dump's :class:`DumpAnchoringReport` from already-computed
    R2 verdicts. Every verdict sequence is sorted here (defensively — R2's
    own functions already return sorted tuples, but this is the one place
    ordering is guaranteed regardless of what a caller hands in), and the
    two count blocks are computed from those same ordered sequences —
    NEVER summed together (see the module docstring).
    """
    ordered_links = tuple(sorted(anchor_links, key=lambda verdict: verdict.anchor_id))
    ordered_references = tuple(
        sorted(
            claim_references,
            key=lambda verdict: (verdict.claim_id, verdict.anchor_id, verdict.relation_kind),
        )
    )
    ordered_source_coverage = tuple(
        sorted(source_coverage, key=lambda verdict: verdict.source_record_id)
    )
    ordered_anchor_coverage = tuple(sorted(anchor_coverage, key=lambda verdict: verdict.anchor_id))

    return DumpAnchoringReport(
        schema_name=schema_name,
        file_anchor=DumpFileAnchor(
            schema_name=file_anchor.schema_name,
            path=file_anchor.path,
            declared_sha256=file_anchor.declared_sha256,
            actual_sha256=file_anchor.actual_sha256,
            matched=file_anchor.status == "dump_anchor_ok",
        ),
        object_counts=ObjectCounts(
            total=total_objects, by_kind=dict(sorted(object_counts_by_kind.items()))
        ),
        anchor_links=tuple(
            AnchorLinkVerdictRow(
                anchor_id=verdict.anchor_id,
                source_record_id=verdict.source_record_id,
                status=verdict.status,
            )
            for verdict in ordered_links
        ),
        claim_references=tuple(
            ClaimReferenceVerdictRow(
                claim_id=verdict.claim_id,
                anchor_id=verdict.anchor_id,
                relation_kind=verdict.relation_kind,
                status=verdict.status,
            )
            for verdict in ordered_references
        ),
        source_coverage=tuple(
            SourceCoverageVerdictRow(
                source_record_id=verdict.source_record_id,
                title=verdict.title,
                status=verdict.status,
            )
            for verdict in ordered_source_coverage
        ),
        anchor_coverage=tuple(
            AnchorCoverageVerdictRow(anchor_id=verdict.anchor_id, status=verdict.status)
            for verdict in ordered_anchor_coverage
        ),
        violations=ViolationCounts(
            anchor_dangling=sum(1 for v in ordered_links if v.status == "anchor_dangling"),
            claim_reference_dangling=sum(
                1 for v in ordered_references if v.status == "claim_reference_dangling"
            ),
        ),
        observations=ObservationCounts(
            source_unanchored=sum(
                1 for v in ordered_source_coverage if v.status == "source_unanchored"
            ),
            anchor_unreferenced=sum(
                1 for v in ordered_anchor_coverage if v.status == "anchor_unreferenced"
            ),
        ),
    )


def build_anchoring_integrity_report(
    *,
    batch_id: str,
    observation_kind: str,
    audit_target: str,
    dumps: Sequence[DumpAnchoringReport],
) -> AnchoringIntegrityReport:
    """Assemble the top-level :class:`AnchoringIntegrityReport` from
    already-built per-dump reports, sorted by ``schema_name`` (the one place
    this ordering is guaranteed regardless of what a caller hands in).
    """
    ordered_dumps = tuple(sorted(dumps, key=lambda report: report.schema_name))
    return AnchoringIntegrityReport(
        batch_id=batch_id,
        observation_kind=observation_kind,
        audit_target=audit_target,
        dumps=ordered_dumps,
    )


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic, no wall clock.
# ---------------------------------------------------------------------------


def _escape_table_cell(value: str) -> str:
    """Escape a value for embedding in a single Markdown table cell — mirrors
    ``mrr.domain.citation_audit_report._escape_table_cell`` exactly.
    """
    return value.replace("|", "\\|").replace("\n", " ")


def _render_dump_section(dump: DumpAnchoringReport) -> list[str]:
    lines: list[str] = []
    lines.append(f"## Dump: {dump.schema_name}")
    lines.append("")
    lines.append("### Integrity anchor")
    lines.append("")
    lines.append("| schema_name | matched | declared_sha256 | actual_sha256 | path |")
    lines.append("|---|---|---|---|---|")
    anchor = dump.file_anchor
    cells = (
        anchor.schema_name,
        str(anchor.matched),
        anchor.declared_sha256,
        anchor.actual_sha256,
        anchor.path,
    )
    lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    lines.append("### Object counts")
    lines.append("")
    lines.append(f"- **total objects:** {dump.object_counts.total}")
    for kind, count in dump.object_counts.by_kind.items():
        lines.append(f"  - {kind}: {count}")
    lines.append("")
    lines.append("### Violations — integrity, never observations")
    lines.append("")
    lines.append(
        f"- **anchor_dangling:** {dump.violations.anchor_dangling} (of "
        f"{len(dump.anchor_links)} anchor links checked)"
    )
    lines.append(
        f"- **claim_reference_dangling:** {dump.violations.claim_reference_dangling} (of "
        f"{len(dump.claim_references)} claim references checked)"
    )
    lines.append("")
    lines.append("### Observations — coverage, never violations")
    lines.append("")
    lines.append(f"- **source_unanchored:** {dump.observations.source_unanchored}")
    unanchored_sources = [
        source_row
        for source_row in dump.source_coverage
        if source_row.status == "source_unanchored"
    ]
    for source_row in unanchored_sources:
        lines.append(f"  - {source_row.title} ({source_row.source_record_id})")
    lines.append(f"- **anchor_unreferenced:** {dump.observations.anchor_unreferenced}")
    unreferenced_anchors = [
        anchor_row
        for anchor_row in dump.anchor_coverage
        if anchor_row.status == "anchor_unreferenced"
    ]
    for anchor_row in unreferenced_anchors:
        lines.append(f"  - {anchor_row.anchor_id}")
    lines.append("")
    return lines


def render_markdown(report: AnchoringIntegrityReport) -> str:
    """Deterministic Markdown rendering: an honesty header, then per dump an
    anchor row, object kind counts, the violation block, and — visually
    separate — the observation block with unanchored sources named
    (task-packets/N2-T02b.yaml R5). Calling this twice with an equal
    ``report`` produces byte-identical output (no wall clock, no unordered
    iteration — every tuple here is already explicit and ordered).
    """
    lines: list[str] = []
    lines.append("# Anchoring integrity report (task-packets/N2-T02b.yaml)")
    lines.append("")
    lines.append("## Honesty header")
    lines.append("")
    lines.append(f"- **anchoring_is_not_support:** {report.anchoring_is_not_support}")
    lines.append(f"- {report.note}")
    lines.append(f"- **batch id:** {report.batch_id}")
    lines.append(f"- **observation kind:** {report.observation_kind}")
    lines.append(f"- **audit target:** {report.audit_target}")
    lines.append("")
    for dump in report.dumps:
        lines.extend(_render_dump_section(dump))
    return "\n".join(lines) + "\n"


def render_json(report: AnchoringIntegrityReport) -> str:
    """Deterministic JSON rendering: ``sort_keys=True`` so byte-identity
    across two renders never depends on Python dict/model-field insertion
    order, only on ``report``'s own already-fixed field values.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
