"""Pure, hand-rolled, no-new-dependency archive-anchoring-integrity core
(task-packets/N2-T02b.yaml R2). No file I/O, no network, no database —
every function here takes already-parsed values (``mrr.domain.archive_dump``
's typed rows, an already-computed dump-file hash) and returns a plain,
typed result. Two independent concerns share this module because they share
one input shape (``mrr.domain.archive_dump``'s typed rows) but must never
share one OUTPUT shape:

--- Anchoring is not support (the honesty boundary this module exists for) --

This module answers exactly one question about each ``EvidenceAnchor``/
``Claim`` reference: does it resolve to a real, archived object (REFERENCE
RESOLUTION)? It never asks whether the resolved ``SourceRecord`` SUPPORTS
the claim it is cited for — that is N2-T03, a separate, not-yet-built,
LLM/human-touching use case (see ``mrr.domain.anchoring_integrity_report``
for the honesty header every report built from this module's results
carries). This is the N2-T02b analogue of N1's "measures reliability, not
validity", N2-T01's "verifies existence, not support", and R2-T01's
"observation, not optimization".

--- Four closed sets, kept apart by TYPE and never unified (AGENTS.md
    prohibited shortcut: "collapsing distinct statuses into one generic
    outcome") --------------------------------------------------------------

Two are REFERENCE-RESOLUTION statuses — :data:`AnchorLinkStatus`
(``"anchor_resolved"``/``"anchor_dangling"``) and
:data:`ClaimReferenceStatus` (``"claim_reference_resolved"``/
``"claim_reference_dangling"``) — where the dangling value is an INTEGRITY
VIOLATION: the machine form of AGENTS.md's "letting an agent cite a source
it did not retrieve and anchor". Two are COVERAGE statuses —
:data:`SourceCoverage` (``"source_anchored"``/``"source_unanchored"``) and
:data:`AnchorCoverage` (``"anchor_referenced"``/``"anchor_unreferenced"``)
— which are OBSERVATIONS, never violations: a corpus source that ended up
carrying no evidence, or an anchor no claim happened to cite, is not an
error. Folding a dangling reference and an unanchored source into one
"problems" outcome would immediately report 1 (``mrr_k1t04_real_run_v2``)
and 2 (``mrr_run2_corroboration_floor_v1``) FALSE violations against the
real, committed archive — docs/design/2026-07-25-n2-t02-derivation.md's own
verified fact-lock. No caller of this module may fold a reference-resolution
status and a coverage status into a shared type; the four ``Literal``\\ s
themselves are the only closed sets in scope, and each verdict dataclass
below carries exactly one of them.

--- The fail-closed dump-hash gate (mirrors, does NOT reuse, R2-T01's) ------

``mrr.domain.field_observation.check_anchor``/``check_and_gate`` compare an
already-computed hash against a pinned anchor identically to what this
packet needs — but that module's ``BatchRole`` is a CLOSED two-value set
(``"manifest"``, ``"snapshot"``); a dump is neither, and widening another
packet's closed ``Literal`` to make reuse convenient would soften an
invariant that packet deliberately established (task-packets/N2-T02b.yaml
derived_decisions (d)). :func:`check_dump_anchor`/:func:`check_and_gate`
below mirror that pattern exactly, over this packet's own OPEN set of
declared dumps (``schema_name: str`` — the number of dumps grows with every
future archive run, never a fixed two).

--- Determinism (task-packets/N2-T02b.yaml invariant) ------------------------

No wall clock anywhere in this module. Every ``check_*``/coverage function
sorts its own output explicitly by its primary id (never the caller's own
argument order, never a ``dict``/``set`` iteration order), so calling any of
them twice over equal inputs yields an identical sequence.
"""

from __future__ import annotations

from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Literal

from mrr.domain.archive_dump import ClaimRow, EvidenceAnchorRow, SourceRecordRow


class AnchoringIntegrityError(Exception):
    """Base class for every typed error this module raises."""


# ---------------------------------------------------------------------------
# Section 1: the fail-closed dump-hash gate — mirrors
# mrr.domain.field_observation's check_anchor/check_and_gate pattern over an
# OPEN dump set (see the module docstring). Pure comparison of already-
# computed hash strings; no file I/O, no hashlib import, no normalisation
# guessed.
# ---------------------------------------------------------------------------

#: The closed set of exactly two hash-comparison outcomes for one declared
#: dump — never collapsed into a bare ``bool`` a future caller could
#: silently reinterpret (mirrors ``mrr.domain.field_observation
#: .AnchorStatus``).
DumpAnchorStatus = Literal["dump_anchor_ok", "dump_anchor_mismatch"]


@dataclass(frozen=True, slots=True)
class DumpDeclaration:
    """One declared dump from the committed anchoring-batch descriptor
    (task-packets/N2-T02b.yaml R4, e.g. ``corpora/archive-integrity/
    anchoring-batch.v1.json``'s own ``dumps[]`` entries): its schema name,
    its declared path (exactly as written in the descriptor — resolving it
    relative to the descriptor's own directory is the SERVICE's job, never
    this dataclass's), and its pinned sha256 anchor (already in
    ``"sha256:<hex>"`` form). Unlike ``mrr.domain.field_observation
    .BatchInput``, ``schema_name`` is a plain ``str``, not a closed
    ``Literal`` role — this list is OPEN and grows with every future run.
    """

    schema_name: str
    path: str
    declared_sha256: str


@dataclass(frozen=True, slots=True)
class DumpAnchorCheckResult:
    """The named result of one dump-file integrity-anchor comparison
    (mirrors ``mrr.domain.field_observation.AnchorCheckResult``). ``status``
    is the definitive verdict; both hash strings are carried so a caller (or
    a rendered report) can show the disagreement without recomputing
    anything.
    """

    schema_name: str
    path: str
    declared_sha256: str
    actual_sha256: str
    status: DumpAnchorStatus


def check_dump_anchor(
    schema_name: str, path: str, declared_sha256: str, actual_sha256: str
) -> DumpAnchorCheckResult:
    """Pure comparison of two already-computed hash strings — no file I/O,
    no normalisation guessed. ``"dump_anchor_ok"`` iff the two strings are
    exactly equal, else ``"dump_anchor_mismatch"``.
    """
    status: DumpAnchorStatus = (
        "dump_anchor_ok" if declared_sha256 == actual_sha256 else "dump_anchor_mismatch"
    )
    return DumpAnchorCheckResult(
        schema_name=schema_name,
        path=path,
        declared_sha256=declared_sha256,
        actual_sha256=actual_sha256,
        status=status,
    )


class IntegrityGateError(AnchoringIntegrityError):
    """Raised by :func:`check_and_gate` the moment ANY declared dump's
    actual sha256 does not match its pinned anchor — a fail-closed refusal,
    raised BEFORE any dump is ever parsed by ``mrr.domain.archive_dump``
    (task-packets/N2-T02b.yaml invariant: "the gate is strictly before the
    evaluation, and no parse result may exist when it fires"). Carries
    ``schema_name``, ``path``, ``declared_sha256``, and ``actual_sha256`` so
    a caller can report exactly which dump failed and why, without parsing
    the message string.
    """

    def __init__(
        self, schema_name: str, path: str, declared_sha256: str, actual_sha256: str
    ) -> None:
        self.schema_name = schema_name
        self.path = path
        self.declared_sha256 = declared_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"integrity gate failed for dump {schema_name!r} at {path!r}: declared sha256 "
            f"{declared_sha256!r} does not match actual sha256 {actual_sha256!r}"
        )


def check_and_gate(results: Sequence[DumpAnchorCheckResult]) -> None:
    """Raise :class:`IntegrityGateError` the moment ANY ``results`` entry is
    ``"dump_anchor_mismatch"`` — naming the FIRST mismatch in
    ``schema_name``-sorted order, never the caller's own argument order.
    Returns ``None`` (does nothing) when every result is
    ``"dump_anchor_ok"``, however many results are given, including zero.
    """
    ordered = sorted(results, key=lambda result: result.schema_name)
    for result in ordered:
        if result.status == "dump_anchor_mismatch":
            raise IntegrityGateError(
                result.schema_name, result.path, result.declared_sha256, result.actual_sha256
            )


# ---------------------------------------------------------------------------
# Section 2: reference resolution (VIOLATIONS when dangling) and coverage
# (OBSERVATIONS, never violations) — the packet's actual integrity core. See
# the module docstring's "four closed sets" section for why these never
# share a type.
# ---------------------------------------------------------------------------

#: REFERENCE-RESOLUTION status for one EvidenceAnchor -> SourceRecord link.
#: ``"anchor_dangling"`` is an integrity VIOLATION.
AnchorLinkStatus = Literal["anchor_resolved", "anchor_dangling"]

#: REFERENCE-RESOLUTION status for one Claim -> EvidenceAnchor reference.
#: ``"claim_reference_dangling"`` is an integrity VIOLATION.
ClaimReferenceStatus = Literal["claim_reference_resolved", "claim_reference_dangling"]

#: COVERAGE status for one SourceRecord: does at least one EvidenceAnchor
#: point at it? Never a violation — a corpus source carrying no evidence is
#: an observation, not an error.
SourceCoverage = Literal["source_anchored", "source_unanchored"]

#: COVERAGE status for one EvidenceAnchor: does at least one Claim reference
#: it? Never a violation, for the identical reason as :data:`SourceCoverage`.
AnchorCoverage = Literal["anchor_referenced", "anchor_unreferenced"]

#: Which of a Claim's two relation lists one reference came from — carried
#: on :class:`ClaimReferenceVerdict` purely for report readability; the
#: closed status set above is what decides violation-vs-not, never this.
ClaimRelationKind = Literal["evidence", "counterevidence"]


@dataclass(frozen=True, slots=True)
class AnchorLinkVerdict:
    """One ``EvidenceAnchor``'s reference-resolution verdict."""

    anchor_id: str
    source_record_id: str
    status: AnchorLinkStatus


@dataclass(frozen=True, slots=True)
class ClaimReferenceVerdict:
    """One Claim->EvidenceAnchor reference's resolution verdict — one row
    per individual reference (never deduplicated: a Claim citing the same
    anchor twice, once as evidence and once as counterevidence, produces two
    rows, matching the real reference COUNT the acceptance oracle reports).
    """

    claim_id: str
    anchor_id: str
    relation_kind: ClaimRelationKind
    status: ClaimReferenceStatus


@dataclass(frozen=True, slots=True)
class SourceCoverageVerdict:
    """One ``SourceRecord``'s coverage verdict — carries ``title`` so a
    rendered report can name an unanchored source without a second lookup.
    """

    source_record_id: str
    title: str
    status: SourceCoverage


@dataclass(frozen=True, slots=True)
class AnchorCoverageVerdict:
    """One ``EvidenceAnchor``'s coverage verdict."""

    anchor_id: str
    status: AnchorCoverage


def check_anchor_links(
    anchors: Sequence[EvidenceAnchorRow], source_record_ids: Set[str]
) -> tuple[AnchorLinkVerdict, ...]:
    """Resolve every ``EvidenceAnchor.source_record_id`` against the set of
    real ``SourceRecord`` ids — ``"anchor_dangling"`` (a VIOLATION) iff no
    such id exists. Returned sorted by ``anchor_id``.
    """
    verdicts = [
        AnchorLinkVerdict(
            anchor_id=anchor.anchor_id,
            source_record_id=anchor.source_record_id,
            status=(
                "anchor_resolved"
                if anchor.source_record_id in source_record_ids
                else "anchor_dangling"
            ),
        )
        for anchor in anchors
    ]
    return tuple(sorted(verdicts, key=lambda verdict: verdict.anchor_id))


def check_claim_references(
    claims: Sequence[ClaimRow], anchor_ids: Set[str]
) -> tuple[ClaimReferenceVerdict, ...]:
    """Resolve every Claim reference — BOTH ``evidence_relations`` AND
    ``counterevidence_relations`` (task-packets/N2-T02b.yaml: "Claim->anchor
    references come from each Claim's evidence_relations AND
    counterevidence_relations") — against the set of real ``EvidenceAnchor``
    ids. ``"claim_reference_dangling"`` (a VIOLATION) iff no such id exists.
    Returned sorted by ``(claim_id, anchor_id, relation_kind)`` for a fully
    deterministic order even when a claim references the same anchor twice.
    """
    verdicts: list[ClaimReferenceVerdict] = []
    for claim in claims:
        for anchor_id in claim.evidence_relations:
            verdicts.append(
                ClaimReferenceVerdict(
                    claim_id=claim.claim_id,
                    anchor_id=anchor_id,
                    relation_kind="evidence",
                    status=(
                        "claim_reference_resolved"
                        if anchor_id in anchor_ids
                        else "claim_reference_dangling"
                    ),
                )
            )
        for anchor_id in claim.counterevidence_relations:
            verdicts.append(
                ClaimReferenceVerdict(
                    claim_id=claim.claim_id,
                    anchor_id=anchor_id,
                    relation_kind="counterevidence",
                    status=(
                        "claim_reference_resolved"
                        if anchor_id in anchor_ids
                        else "claim_reference_dangling"
                    ),
                )
            )
    return tuple(
        sorted(
            verdicts,
            key=lambda verdict: (verdict.claim_id, verdict.anchor_id, verdict.relation_kind),
        )
    )


def source_coverage(
    sources: Sequence[SourceRecordRow], anchors: Sequence[EvidenceAnchorRow]
) -> tuple[SourceCoverageVerdict, ...]:
    """For every ``SourceRecord``, is it pointed at by at least one
    ``EvidenceAnchor``? OBSERVATION only, never a violation — an unanchored
    corpus source is not an error. Returned sorted by ``source_record_id``.
    """
    anchored_source_ids = {anchor.source_record_id for anchor in anchors}
    verdicts = [
        SourceCoverageVerdict(
            source_record_id=source.source_record_id,
            title=source.title,
            status=(
                "source_anchored"
                if source.source_record_id in anchored_source_ids
                else "source_unanchored"
            ),
        )
        for source in sources
    ]
    return tuple(sorted(verdicts, key=lambda verdict: verdict.source_record_id))


def anchor_coverage(
    anchors: Sequence[EvidenceAnchorRow], claims: Sequence[ClaimRow]
) -> tuple[AnchorCoverageVerdict, ...]:
    """For every ``EvidenceAnchor``, is it referenced by at least one Claim
    (via either relation list)? OBSERVATION only, never a violation.
    Returned sorted by ``anchor_id``.
    """
    referenced_anchor_ids: set[str] = set()
    for claim in claims:
        referenced_anchor_ids.update(claim.evidence_relations)
        referenced_anchor_ids.update(claim.counterevidence_relations)
    verdicts = [
        AnchorCoverageVerdict(
            anchor_id=anchor.anchor_id,
            status=(
                "anchor_referenced"
                if anchor.anchor_id in referenced_anchor_ids
                else "anchor_unreferenced"
            ),
        )
        for anchor in anchors
    ]
    return tuple(sorted(verdicts, key=lambda verdict: verdict.anchor_id))
