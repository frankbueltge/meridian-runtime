"""Pure, framework-free artifact-presence report projection (task-packets/
A2-T01.yaml, "Teil 2 — Nachsehen"). Mirrors ``mrr.domain
.anchoring_integrity_report``'s/``mrr.domain.citation_audit_report``'s own
split precedent (pure model-building/rendering here;
``mrr.services.artifact_presence.service.ArtifactPresenceService`` performs
every I/O step and hands this module already-computed values) and, for the
Pydantic base, ``mrr.contracts.common.MRRModel`` (``extra="forbid"``) — the
same established, non-circular pattern those two modules already use.

--- NOT a BaseObject, NOT a persisted object (mirrors N2-T01/N2-T02b) --------

Every model here is a pure Pydantic projection: no ``id``/``api_version``/
``revision``/``content_hash``/audit fields, no ``schemas/*.schema.json``
mirror, and this report is NEVER passed to ``ObjectRepository
.insert_revision`` or any other persistence call anywhere in this packet
(``ArtifactPresenceService`` opens no database connection and no network
connection at all). Matches ``mrr.domain.anchoring_integrity_report
.AnchoringIntegrityReport``'s identical "a projection, not the primary
research record" stance (AGENTS.md rule 7 combined with the source-of-truth
discipline's "narrative reports are projections").

--- The honesty header is structural, not advisory ---------------------------

``recorded_root_is_not_evidence_soundness`` is a ``Literal[True]`` — it
cannot be constructed as ``False`` even by a caller mistake, because there
is no other value the type permits. ``note`` is a non-empty, fixed-content
string from the SAME module-level constant every report uses
(:data:`_ARTIFACT_PRESENCE_NOTE`) — never freehand text a caller could omit
or drift. task-packets/A2-T01.yaml's own objective names this explicitly: "a
header stating in plain type that a recorded root proves where the bytes
were meant to go, never that the evidence is sound." A hit
(``artifact_present``) means the blob sits at the derived path with a
matching hash — nothing about whether that blob's CONTENT still supports
the claim it was cited for (that is a separate, already-built concern —
``mrr audit support``, N2-T03b).

--- Violations and observations are NEVER summed (the packet's hardest rule) -

:class:`ArtifactPresenceViolationCounts` (``artifact_missing``,
``artifact_hash_mismatch``) and :class:`ArtifactPresenceObservationCounts`
(``store_reference_not_recorded``) are separate, distinctly-named fields on
:class:`ArtifactPresenceReport` — this module never introduces a combined
"problems" field, and never sums a violation count with an observation
count anywhere (task-packets/A2-T01.yaml: "store_reference_not_recorded is
an OBSERVATION, never a violation ... collapsing them is the hardest
mistake"). Against the two real committed dumps, collapsing them would
immediately report 17 and 34 FALSE violations where there are none —
docs/design/2026-07-26-a2-derivation-artifact-store-reference.md's own
acceptance oracle.

--- Determinism (task-packets/A2-T01.yaml invariant) -------------------------

:func:`render_markdown`/:func:`render_json` are pure — no wall clock, no
network, no filesystem. :func:`build_artifact_presence_report` sorts every
sequence it is handed (the one place ordering is guaranteed regardless of
what a caller hands in), so calling either renderer twice over an equal
report produces byte-identical output.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from mrr.contracts.common import MRRModel
from mrr.domain.artifact_presence import ArtifactPresenceStatus, ArtifactPresenceVerdict
from pydantic import Field

_ARTIFACT_PRESENCE_NOTE = (
    "A recorded artifact_store_reference proves ONLY where this run's bytes were MEANT to be "
    "written — it is NOT evidence the bytes are sound. 'artifact_present' means the blob "
    "exists at the derived path and its hash matches the anchor's own snapshot_hash, nothing "
    "more; it does not re-verify that the snapshot content still supports the claim it was "
    "cited for (a separate, already-built concern — mrr audit support, N2-T03b). "
    "'store_reference_not_recorded' is an OBSERVATION, never a violation: the run wrote no "
    "root down anywhere, so nothing is known about where its bytes went — it is NOT evidence "
    "the bytes are missing, and it is NOT evidence all is well. This report never sums "
    "store_reference_not_recorded into its violation counts."
)


class ArtifactPresenceViolationCounts(MRRModel):
    """Artifact-presence VIOLATION counts — ``artifact_missing`` and
    ``artifact_hash_mismatch`` only. Never a combined "problems" field (see
    the module docstring).
    """

    artifact_missing: int = Field(ge=0)
    artifact_hash_mismatch: int = Field(ge=0)


class ArtifactPresenceObservationCounts(MRRModel):
    """Artifact-presence OBSERVATION count — ``store_reference_not_recorded``
    only. Never a violation, and never summed with
    :class:`ArtifactPresenceViolationCounts` (see the module docstring).
    """

    store_reference_not_recorded: int = Field(ge=0)


class ArtifactAnchorVerdictRow(MRRModel):
    """One ``EvidenceAnchor``'s artifact-presence verdict — mirrors
    ``mrr.domain.artifact_presence.ArtifactPresenceVerdict`` field for
    field.
    """

    anchor_id: str
    expected_hash: str
    blob_path: str | None
    status: ArtifactPresenceStatus


class ArtifactPresenceReport(MRRModel):
    """The full artifact-presence report for ONE committed dump
    (task-packets/A2-T01.yaml): a fixed honesty header, the audited dump's
    own identity, the dump-level recorded-root resolution, the ORDERED
    per-anchor verdicts, the anchors this report could not check at all
    (no ``snapshot_hash``), and the two SEPARATE count blocks.
    """

    recorded_root_is_not_evidence_soundness: Literal[True] = True
    note: str = _ARTIFACT_PRESENCE_NOTE
    dump_path: str
    store_reference_status: Literal["recorded", "not_recorded"]
    store_root: str | None
    run_manifest_ids: tuple[str, ...]
    anchors: tuple[ArtifactAnchorVerdictRow, ...]
    anchors_without_snapshot_hash: tuple[str, ...]
    violations: ArtifactPresenceViolationCounts
    observations: ArtifactPresenceObservationCounts


def build_artifact_presence_report(
    *,
    dump_path: str,
    store_root: str | None,
    run_manifest_ids: Sequence[str],
    verdicts: Sequence[ArtifactPresenceVerdict],
    anchors_without_snapshot_hash: Sequence[str],
) -> ArtifactPresenceReport:
    """Assemble the :class:`ArtifactPresenceReport` from already-computed
    verdicts. Every sequence is sorted here (defensively — the SERVICE
    already builds them in a deterministic order, but this is the one place
    ordering is guaranteed regardless of what a caller hands in), and the
    two count blocks are computed from that same ordered sequence — NEVER
    summed together (see the module docstring).
    """
    ordered_verdicts = tuple(sorted(verdicts, key=lambda verdict: verdict.anchor_id))
    ordered_manifest_ids = tuple(sorted(run_manifest_ids))
    ordered_missing_hash_ids = tuple(sorted(anchors_without_snapshot_hash))

    rows = tuple(
        ArtifactAnchorVerdictRow(
            anchor_id=verdict.anchor_id,
            expected_hash=verdict.expected_hash,
            blob_path=verdict.blob_path,
            status=verdict.status,
        )
        for verdict in ordered_verdicts
    )
    violations = ArtifactPresenceViolationCounts(
        artifact_missing=sum(1 for v in ordered_verdicts if v.status == "artifact_missing"),
        artifact_hash_mismatch=sum(
            1 for v in ordered_verdicts if v.status == "artifact_hash_mismatch"
        ),
    )
    observations = ArtifactPresenceObservationCounts(
        store_reference_not_recorded=sum(
            1 for v in ordered_verdicts if v.status == "store_reference_not_recorded"
        ),
    )

    return ArtifactPresenceReport(
        dump_path=dump_path,
        store_reference_status="recorded" if store_root is not None else "not_recorded",
        store_root=store_root,
        run_manifest_ids=ordered_manifest_ids,
        anchors=rows,
        anchors_without_snapshot_hash=ordered_missing_hash_ids,
        violations=violations,
        observations=observations,
    )


# ---------------------------------------------------------------------------
# Rendering — pure, deterministic, no wall clock.
# ---------------------------------------------------------------------------


def _escape_table_cell(value: str) -> str:
    """Escape a value for embedding in a single Markdown table cell — mirrors
    ``mrr.domain.citation_audit_report._escape_table_cell``/``mrr.domain
    .anchoring_integrity_report._escape_table_cell`` exactly.
    """
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: ArtifactPresenceReport) -> str:
    """Deterministic Markdown rendering: an honesty header, the dump's
    recorded-root resolution, a per-anchor table, and — visually separate —
    the violation and observation counts, plus anchors this report could
    not check at all. Calling this twice with an equal ``report`` produces
    byte-identical output (no wall clock, no unordered iteration — every
    tuple here is already explicit and ordered).
    """
    lines: list[str] = []
    lines.append("# Artifact presence report (task-packets/A2-T01.yaml)")
    lines.append("")
    lines.append("## Honesty header")
    lines.append("")
    lines.append(
        f"- **recorded_root_is_not_evidence_soundness:** "
        f"{report.recorded_root_is_not_evidence_soundness}"
    )
    lines.append(f"- {report.note}")
    lines.append("")
    lines.append("## Dump")
    lines.append("")
    lines.append(f"- **dump path:** {report.dump_path}")
    lines.append(f"- **store reference status:** {report.store_reference_status}")
    lines.append(f"- **store root:** {report.store_root if report.store_root is not None else '—'}")
    lines.append(f"- **run manifest ids:** {', '.join(report.run_manifest_ids) or '—'}")
    lines.append("")
    lines.append("## Violations — integrity, never observations")
    lines.append("")
    lines.append(
        f"- **artifact_missing:** {report.violations.artifact_missing} (of "
        f"{len(report.anchors)} anchors checked)"
    )
    lines.append(
        f"- **artifact_hash_mismatch:** {report.violations.artifact_hash_mismatch} (of "
        f"{len(report.anchors)} anchors checked)"
    )
    lines.append("")
    lines.append("## Observations — coverage, never violations")
    lines.append("")
    lines.append(
        f"- **store_reference_not_recorded:** {report.observations.store_reference_not_recorded} "
        f"(of {len(report.anchors)} anchors checked)"
    )
    lines.append("")
    lines.append("## Anchors checked")
    lines.append("")
    lines.append("| anchor_id | status | expected_hash | blob_path |")
    lines.append("|---|---|---|---|")
    for row in report.anchors:
        cells = (
            row.anchor_id,
            row.status,
            row.expected_hash,
            row.blob_path if row.blob_path is not None else "—",
        )
        lines.append("| " + " | ".join(_escape_table_cell(cell) for cell in cells) + " |")
    lines.append("")
    lines.append("## Anchors without a snapshot_hash — not checked at all")
    lines.append("")
    if report.anchors_without_snapshot_hash:
        for anchor_id in report.anchors_without_snapshot_hash:
            lines.append(f"- {anchor_id}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_json(report: ArtifactPresenceReport) -> str:
    """Deterministic JSON rendering: ``sort_keys=True`` so byte-identity
    across two renders never depends on Python dict/model-field insertion
    order, only on ``report``'s own already-fixed field values.
    """
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
