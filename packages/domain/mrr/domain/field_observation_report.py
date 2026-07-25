"""Pure, framework-free field-observation report projection (task-packets/
R2-T01.yaml R2). Mirrors ``mrr.domain.citation_audit_report``'s own split
precedent (pure model-building/rendering here;
``mrr.services.field_observation.service.FieldObservationService`` performs
every I/O step — reading the descriptor, hashing its declared inputs,
running the gate, and reusing the frozen N2 evaluator — and hands this
module already-computed values) and, for the Pydantic base,
``mrr.contracts.common.MRRModel`` (``extra="forbid"``) — the same
established, non-circular pattern ``mrr.domain.citation_audit_report``/
``mrr.domain.agreement_report`` already use for the identical reason.

--- NOT a BaseObject, NOT a persisted object (task-packets/R2-T01.yaml R2) --

:class:`FieldObservationReport` and every nested model here are pure
Pydantic projections: no ``id``/``api_version``/``revision``/
``content_hash``/audit fields, no ``schemas/*.schema.json`` mirror, and this
report is NEVER passed to ``ObjectRepository.insert_revision`` or any other
persistence call anywhere in this packet
(``mrr.services.field_observation.service.FieldObservationService`` opens no
database connection and no network connection at all). Matches
``mrr.domain.citation_audit_report.CitationAuditReport``'s/``mrr.domain
.agreement_report.AgreementReport``'s identical "a projection, not the
primary research record" stance (AGENTS.md rule 7 combined with the
source-of-truth discipline's "narrative reports are projections").

--- The honesty header is structural, not advisory (R2) -----------------------

``observation_is_not_optimization`` is a ``Literal[True]`` — it cannot be
constructed as ``False`` even by a caller mistake, because there is no other
value the type permits. ``observation_note`` is a non-empty, fixed-content
string assembled from the SAME module-level constant every report uses
(:data:`_OBSERVATION_NOTE`) — never freehand text a caller could omit or
drift. This is the R2 analogue of N1's "measures reliability, not validity"
header and N2's "verifies existence, not support" header (task-packets/
R2-T01.yaml derived_decisions (a)): this report observes a hash-anchored
batch fail-closed and embeds the FROZEN N2 evaluator's own unchanged
report — it is NOT a self-modification proposal (R2-T02, deferred, behind
the human gate) and NOT an optimizer run against the evaluator (R2-T03,
deferred, gated on N1-T03 and a classification run with real per-item
spread). This packet contains no model/LLM step at all.

--- The embedded citation-audit report is unchanged, not re-scored ----------

:attr:`FieldObservationReport.citation_audit` holds the exact
``mrr.domain.citation_audit_report.CitationAuditReport`` the frozen N2
evaluator built — a domain-to-domain import/embed, never a re-implementation
(AGENTS.md rule 8: "No executor may approve or verify its own result" holds
here because this packet composes the unchanged evaluator rather than
re-evaluating its own output).

--- Determinism (task-packets/R2-T01.yaml invariant) --------------------------

:func:`render_markdown`/:func:`render_json` are pure — no wall clock, no
network, no filesystem. :func:`build_field_observation_report` sorts its
``anchor_results`` by ``role`` (the one place ordering is decided here — the
embedded ``citation_audit`` report is already ordered by its own builder),
so calling either renderer twice over an equal report produces
byte-identical output.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from mrr.contracts.common import MRRModel
from mrr.domain.citation_audit_report import CitationAuditReport
from mrr.domain.citation_audit_report import render_markdown as _render_citation_audit_markdown
from mrr.domain.field_observation import AnchorCheckResult, BatchRole
from pydantic import Field

_OBSERVATION_NOTE = (
    "This is a READ-ONLY field observation (task-packets/R2-T01.yaml): the declared "
    "inputs' integrity anchors (each input file's own sha256, pinned in the committed "
    "batch descriptor) were verified fail-closed BEFORE the embedded citation-audit "
    "report below was ever produced — on any mismatch the run halts with a typed "
    "IntegrityGateError and the frozen N2 evaluator is never invoked. This packet "
    "emits NO self-modification proposal (that is R2-T02, a separate, not-yet-built "
    "use case behind the human gate) and runs NO optimizer against the evaluator "
    "(that is R2-T03, also not-yet-built, gated on N1-T03 and a classification run "
    "with real per-item spread). This packet contains NO model/LLM step anywhere."
)


class AnchorRow(MRRModel):
    """One declared input's integrity-anchor comparison — mirrors
    ``mrr.domain.field_observation.AnchorCheckResult`` field for field
    (Pydantic projection of that plain dataclass), plus the boolean
    ``matched`` convenience field a rendered table reads directly rather
    than re-deriving from ``declared_sha256 == actual_sha256`` itself.
    """

    role: BatchRole
    path: str
    declared_sha256: str
    actual_sha256: str
    matched: bool


class FieldObservationReport(MRRModel):
    """The full, top-level field-observation report (task-packets/
    R2-T01.yaml R2): a fixed honesty header, the batch's own identity
    (``batch_id``, ``observation_kind``, ``audit_target``), an ORDERED tuple
    of anchor rows (sorted by ``role``), and the embedded, unchanged
    ``mrr.domain.citation_audit_report.CitationAuditReport`` the frozen N2
    evaluator built over this same batch's manifest+snapshot.
    """

    observation_is_not_optimization: Literal[True] = True
    observation_note: str = _OBSERVATION_NOTE
    batch_id: str
    observation_kind: str
    audit_target: str
    anchors: tuple[AnchorRow, ...] = Field(min_length=1)
    citation_audit: CitationAuditReport


def build_field_observation_report(
    *,
    batch_id: str,
    observation_kind: str,
    audit_target: str,
    anchor_results: Sequence[AnchorCheckResult],
    citation_audit: CitationAuditReport,
) -> FieldObservationReport:
    """Assemble the top-level :class:`FieldObservationReport` from
    already-computed R1 anchor results and the frozen N2 evaluator's own,
    already-built :class:`CitationAuditReport`. Pure — no I/O, no
    repository access, and no re-classification of anything the embedded
    report already decided. ``anchor_results`` is sorted by ``role`` here
    (defensively — the service already runs the gate over a role-sorted
    sequence, but this is the one place ordering is guaranteed regardless
    of what a caller hands in).
    """
    ordered = tuple(sorted(anchor_results, key=lambda result: result.role))
    rows = tuple(
        AnchorRow(
            role=result.role,
            path=result.path,
            declared_sha256=result.declared_sha256,
            actual_sha256=result.actual_sha256,
            matched=result.status == "anchor_ok",
        )
        for result in ordered
    )
    return FieldObservationReport(
        batch_id=batch_id,
        observation_kind=observation_kind,
        audit_target=audit_target,
        anchors=rows,
        citation_audit=citation_audit,
    )


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic, no wall clock.
# ---------------------------------------------------------------------------


def _escape_table_cell(value: str) -> str:
    """Escape a value for embedding in a single Markdown table cell — mirrors
    ``mrr.domain.citation_audit_report._escape_table_cell`` exactly (a
    literal ``|`` would otherwise be read as a column delimiter, and an
    embedded newline would break the row onto multiple lines).
    """
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: FieldObservationReport) -> str:
    """Deterministic Markdown rendering: an honesty header, the per-input
    anchor table, and the embedded citation-audit report's own Markdown
    rendering (task-packets/R2-T01.yaml R4: "Markdown shows the anchor
    table ..., the embedded citation-audit report, and the honesty header
    verbatim"). Calling this twice with an equal ``report`` produces
    byte-identical output (no wall clock, no unordered iteration —
    ``report.anchors`` is already an explicit, ordered tuple).
    """
    lines: list[str] = []
    lines.append("# Field observation report (task-packets/R2-T01.yaml)")
    lines.append("")
    lines.append("## Honesty header")
    lines.append("")
    lines.append(f"- **observation_is_not_optimization:** {report.observation_is_not_optimization}")
    lines.append(f"- {report.observation_note}")
    lines.append(f"- **batch id:** {report.batch_id}")
    lines.append(f"- **observation kind:** {report.observation_kind}")
    lines.append(f"- **audit target:** {report.audit_target}")
    lines.append("")
    lines.append("## Integrity anchors")
    lines.append("")
    lines.append("| role | matched | declared_sha256 | actual_sha256 | path |")
    lines.append("|---|---|---|---|---|")
    for row in report.anchors:
        cells = (row.role, str(row.matched), row.declared_sha256, row.actual_sha256, row.path)
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    lines.append("## Embedded citation-audit report")
    lines.append("")
    lines.append(_render_citation_audit_markdown(report.citation_audit))
    return "\n".join(lines) + "\n"


def render_json(report: FieldObservationReport) -> str:
    """Deterministic JSON rendering: ``sort_keys=True`` so byte-identity
    across two renders never depends on Python dict/model-field insertion
    order, only on ``report``'s own already-fixed field values.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
