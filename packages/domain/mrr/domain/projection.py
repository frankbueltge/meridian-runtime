"""Pure, framework-free shaping logic and return-shape dataclasses for the
read-only claim-table / provenance-map projection (task-packets/E3-T07.yaml,
docs/spec/01_SYSTEM_SPEC.md section 7.9 "Projection Service", MRR-FR-095,
MRR-FR-100). Seventh and final task of Epic E3 (claim, evidence, correction
kernel); the closest templates are ``mrr.domain.independence`` (E3-T05, "pure
decision logic, no persistence, no I/O") and ``mrr.domain.correction_impact``
(E3-T06, a pure function consumed by an application-layer service that does
the actual repository reads). This module carries no SQLAlchemy, driver, or
framework import (MRR-NFR-010).

The companion I/O-performing half — reading real claims, corrections, and
typed edges via the E1-T05 ``ObjectRepository``/``EdgeRepository`` and the
E1-T06 event log, then calling ``build_claim_table_row`` below once per
discovered claim — is ``mrr.services.projection.service.ProjectionService``,
which imports and calls this module's functions rather than re-implementing
any of this shaping logic.

--- What a "projection" is here, and what it is NOT ---------------------------

AGENTS.md's source-of-truth discipline: "Narrative reports are projections
and are never the primary research record." Nothing in this module or its
companion service ever writes an object revision, a domain event, or a typed
edge — every function here is a pure computation over already-read data
(``Mapping[str, Any]`` bodies and plain values), and the service's own two
public methods (``build_claim_table``/``build_provenance_map``) are reads
only. Building the same projection twice from an unchanged graph MUST — and,
being pure functions of their inputs with no caching, DOES — yield an
identical result (task-packets/E3-T07.yaml invariant: "byte-identical on
repeated builds from the same graph state").

--- MRR-FR-095: precisely what counts as "resolved" ---------------------------

MRR-FR-095: "Public projections MUST display unresolved critical
corrections." task-packets/E3-T07.yaml's own instruction: "check the
correction-event status enum and define 'unresolved' precisely, document
it" — this is a genuine judgment call, not read off any single spec
sentence, so it is spelled out here rather than left implicit.

``mrr.domain.lifecycles.CORRECTION_LIFECYCLE`` declares eight states:
``OPEN``, ``IMPACT_ANALYSIS``, ``NOTIFYING``, ``AWAITING_RESPONSES``,
``DELIVERY_PENDING``, ``RESOLVED``, ``PARTIALLY_RESOLVED``,
``REJECTED_BY_RECIPIENT``. Exactly two of the eight spell the word
"resolved" in their own name: ``RESOLVED`` and ``PARTIALLY_RESOLVED``. This
module reads "resolved", for MRR-FR-095 display purposes, as exactly those
two — ``RESOLVED_CORRECTION_STATUSES`` below. Every other state —
``OPEN``/``IMPACT_ANALYSIS``/``NOTIFYING``/``AWAITING_RESPONSES``/
``DELIVERY_PENDING`` (still in flight; nothing has been accepted yet) AND
``REJECTED_BY_RECIPIENT`` (a recipient exercised its own autonomy to decline
the requested action; the correction was not applied — nothing about the
underlying problem was actually fixed) — is "unresolved" and stays visible
if ``severity == "critical"``.

This directly follows docs/spec/05_EVALUATION_AND_ACCEPTANCE.md's own
E2E-003 pass criteria, read side by side with its scenario steps:
"recipient autonomy is preserved; unresolved public correction is visible"
describes exactly the ``REJECTED_BY_RECIPIENT`` case — a recipient MAY
decline (autonomy preserved), but that decline does not make the underlying
critical correction disappear from view; it remains an unresolved public
correction. **Flagged for reviewer scrutiny**: a stricter reading that also
treats ``REJECTED_BY_RECIPIENT`` as "resolved" (on the theory that the
correction's own lifecycle has reached one of its declared terminal states,
whatever a recipient decided) is not implemented here, precisely because it
would make E2E-003's own "unresolved public correction is visible" criterion
vacuous in exactly the scenario it seems written to cover (every recipient
rejects). ``DELIVERY_PENDING`` has no drawn outgoing edge at all in
``CORRECTION_LIFECYCLE`` (an open specification question flagged in that
module already) — it is treated here as still in flight/unresolved, the more
conservative of the two readings (an un-terminated state showing as resolved
would be the surprising direction to be wrong in).

--- What flags a claim ---------------------------------------------------------

A ``CorrectionEvent`` names two distinct id sets: ``affected_objects`` (what
it is directly ABOUT — the correction's own seeds) and ``impact_objects``
(the downstream dependents ``mrr.domain.correction_impact.compute_impact``
computed, per E3-T06). task-packets/E3-T07.yaml's own derived_decisions: "a
claim is flagged if a critical correction whose status is not resolved lists
it in affected_objects/impact_objects" — both sets, not just one. This means
a claim the correction is directly ABOUT is shown flagged in the projection
even though ``mrr.services.correction.service.CorrectionImpactService``
deliberately does NOT transition that seed claim's own ``status`` to
``review_required`` (see that module's own "What counts as affected"
docstring section) — ``status`` (the claim's own lifecycle state) and
``flagged``/``unresolved_correction_ids`` (a projection-only derived signal)
are deliberately two independent things. This is the correct reading of
MRR-FR-095's own text ("Public projections MUST display unresolved critical
corrections") applied literally: it is about what is DISPLAYED, not about
which claims separately get transitioned to ``review_required`` by the
correction-impact service, and a claim can be a correction's own directly
corrected subject without a status change having been separately triggered.

--- MRR-MTH-004: the ceiling-gate projection extension (task-packets/K1-T02.yaml) ---

``build_claim_table_row`` gains two NEW, fully OPTIONAL keyword parameters,
``ruled_ceiling``/``profile_max_ceiling`` (both default ``None``), and
``ClaimTableRow`` gains two new fields, ``ceiling_checked``/
``ceiling_violation``, additively — every EXISTING call site (both
parameters omitted) is byte-identical to its pre-K1-T02 behavior:
``ceiling_checked=False``, ``ceiling_violation=None``. This is the "at ...
projection rendering" half of MRR-MTH-004's "the claim service MUST reject
claim language above the ruled ceiling ... at submission and at projection
rendering" — re-deriving the SAME verdict
``mrr.domain.claim_ceiling.ceiling_violation_reason`` computes at
``ClaimService.attach_ruling``/``_transition`` time, rather than trusting the
claim's own stored status, consistent with this module's own "narrative
reports are projections, never authoritative" discipline (AGENTS.md): a claim
marked ``supported`` under a ruling that was LATER superseded to a stricter
ceiling surfaces ``ceiling_violation`` at render time even though its own
stored object was valid when it was written.

``ceiling_checked``/``ceiling_violation`` deliberately do NOT distinguish
"checked, no violation" from "not checked at all" via a single field — both
read ``ceiling_violation=None``, disambiguated only by the separate
``ceiling_checked`` boolean (flagged for reviewer scrutiny in
task-packets/K1-T02.yaml specification_gaps; a single three-value
``Literal["not_checked", "licensed", "violated"]`` field is a defensible
alternative a reviewer may prefer instead). The I/O-performing half that
resolves a claim's own ``ruled_by`` edge(s) and the
``MethodRuling``->``MethodProtocol``->``MethodProfile`` chain behind them is
``mrr.services.projection.service.ProjectionService.build_claim_table``,
which calls this function once per claim exactly as it always has, now
optionally supplying the two new keyword arguments.

--- Determinism ------------------------------------------------------------

``unresolved_critical_correction_ids_for_claim``/``build_claim_table_row``
never mutate their inputs, perform no I/O, and depend only on their
arguments' values, never on iteration order (the returned tuple of
correction ids is always sorted) — repeatable, order-independent, and safe
to call any number of times with the same inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from mrr.domain.claim_ceiling import ceiling_violation_reason

#: See the module docstring's "MRR-FR-095: precisely what counts as
#: 'resolved'" section for the full derivation. Exactly the two
#: CORRECTION_LIFECYCLE states that spell "resolved" in their own name.
RESOLVED_CORRECTION_STATUSES: frozenset[str] = frozenset({"RESOLVED", "PARTIALLY_RESOLVED"})

#: The one CorrectionEvent.severity value MRR-FR-095 cares about — a
#: "material" or "minor" correction, however unresolved, does not flag a
#: claim under this task's scope (task-packets/E3-T07.yaml derived_decisions:
#: "a non-resolved CRITICAL CorrectionEvent").
CRITICAL_CORRECTION_SEVERITY = "critical"

#: A provenance hop is either a real ``mrr.domain.repositories.TypedEdge``
#: read via ``EdgeRepository.edges_from``, or a declared object-field
#: reference (``EvidenceAnchor.source_record_id``/``run_id`` — see
#: ``mrr.services.projection.service``'s own module docstring, "Field
#: references are provenance too", for why the graph alone cannot reach a
#: SourceRecord or a run at all today).
ProvenanceHopKind = Literal["edge", "field"]


@dataclass(frozen=True, slots=True)
class ClaimTableRow:
    """One row of the claim table (task-packets/E3-T07.yaml derived_decisions,
    item 1): a claim's identity, assertion, latest-revision status, its own
    declared evidence/verification references, and the MRR-FR-095
    unresolved-critical-correction signal. Every field traces to an
    authoritative source object by id — ``claim_id``/``evidence_relations``/
    ``verification_ids`` come straight from the claim's own latest revision
    body; ``unresolved_correction_ids`` names the exact ``CorrectionEvent``
    ids responsible for ``flagged`` (never a bare boolean with no
    traceable source).
    """

    claim_id: str
    assertion: str
    status: str
    evidence_relations: tuple[str, ...]
    verification_ids: tuple[str, ...]
    unresolved_correction_ids: tuple[str, ...]
    flagged: bool
    ceiling_checked: bool
    ceiling_violation: str | None


@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    """One hop in a ``ProvenanceMap`` — either a real typed graph edge
    (``via="edge"``, ``edge_id`` set to that edge's own id) or a declared
    object-field reference (``via="field"``, ``edge_id`` is ``None`` since
    there is no edge row to name; ``relation`` instead names the field,
    e.g. ``"source_record_id"``). ``target_kind`` is the target object's own
    ``kind`` (e.g. ``"EvidenceAnchor"``, ``"SourceRecord"``, ``"RunManifest"``,
    ``"Claim"``) — read from that object's own stored revision, never
    inferred or guessed from the edge/field name alone.
    """

    source_id: str
    target_id: str
    target_kind: str
    relation: str
    via: ProvenanceHopKind
    edge_id: str | None


@dataclass(frozen=True, slots=True)
class ProvenanceMap:
    """The typed-edge/field paths from one claim to the evidence anchors,
    source records, and runs it actually traces back to (task-packets/
    E3-T07.yaml derived_decisions, item 2). ``edges`` lists only hops whose
    target object actually resolves in the object repository at build time —
    "invent nothing" (task-packets/E3-T07.yaml invariant): a dangling edge
    whose target no longer (or never did) resolve is silently excluded, not
    represented with a placeholder.
    """

    claim_id: str
    edges: tuple[ProvenanceEdge, ...]


def is_unresolved_critical_correction(*, severity: str, status: str) -> bool:
    """``True`` iff ``severity == "critical"`` and ``status`` is not one of
    ``RESOLVED_CORRECTION_STATUSES`` — see the module docstring's "MRR-FR-095"
    section for the full derivation of both thresholds. Pure and
    deterministic: depends only on the two arguments.
    """
    return severity == CRITICAL_CORRECTION_SEVERITY and status not in RESOLVED_CORRECTION_STATUSES


def unresolved_critical_correction_ids_for_claim(
    claim_id: str, corrections: Iterable[Mapping[str, Any]]
) -> tuple[str, ...]:
    """The sorted, deduplicated ids of every correction in ``corrections``
    that is critical, unresolved (``is_unresolved_critical_correction``), and
    names ``claim_id`` in its own ``affected_objects`` or ``impact_objects``
    (task-packets/E3-T07.yaml derived_decisions: "lists it in
    affected_objects/impact_objects" — either set flags the claim; see the
    module docstring's "What flags a claim" section for why both, not just
    one).

    Args:
        claim_id: the claim id to check.
        corrections: an iterable of already-read ``CorrectionEvent`` bodies
            (plain dict-like mappings, e.g. ``StoredObject.body`` — each
            expected to carry ``id``, ``severity``, ``status``,
            ``affected_objects`` (a list of ``{"id": ..., ...}`` mappings),
            and ``impact_objects`` (a list of plain id strings), matching
            schemas/correction-event.schema.json). Consumed in one pass, so
            a single-use iterator is safe to pass here. Not mutated.

    Returns:
        a sorted tuple of distinct correction ids — empty if none flag
        ``claim_id``. Never depends on the iteration order of
        ``corrections``.
    """
    matched_ids: set[str] = set()
    for correction in corrections:
        if not is_unresolved_critical_correction(
            severity=correction["severity"], status=correction["status"]
        ):
            continue
        affected_ids = {ref["id"] for ref in correction.get("affected_objects", [])}
        impact_ids = set(correction.get("impact_objects", []))
        if claim_id in affected_ids or claim_id in impact_ids:
            matched_ids.add(correction["id"])
    return tuple(sorted(matched_ids))


def build_claim_table_row(
    claim_body: Mapping[str, Any],
    corrections: Iterable[Mapping[str, Any]],
    *,
    ruled_ceiling: str | None = None,
    profile_max_ceiling: str | None = None,
) -> ClaimTableRow:
    """Shape one ``ClaimTableRow`` from an already-read ``Claim`` latest-
    revision body and an already-read iterable of ``CorrectionEvent`` bodies.

    Args:
        claim_body: a ``Claim``'s latest-revision body (e.g.
            ``StoredObject.body``) — must carry ``id``, ``assertion``,
            ``status``, ``evidence_relations``, ``verification_ids``, per
            schemas/claim.schema.json. ``claim_type`` is additionally read
            when both ``ruled_ceiling``/``profile_max_ceiling`` are supplied
            (see below).
        corrections: every candidate correction body to check ``claim_body``
            against (see ``unresolved_critical_correction_ids_for_claim``).
            Consumed once; a single-use iterator is safe if ``corrections``
            is only ever checked against one claim, but this function itself
            makes no assumption either way (it iterates ``corrections``
            exactly once).
        ruled_ceiling: the already-resolved ``MethodRuling.ruled_ceiling``
            governing this claim (task-packets/K1-T02.yaml, MRR-MTH-004's
            "at ... projection rendering" half), or ``None`` if the caller
            has not resolved a ``ruled_by`` chain for this claim (e.g. no
            ``ruled_by`` edge exists at all — see the module docstring's
            "ceiling-gate projection extension" section). Both this and
            ``profile_max_ceiling`` must be supplied together for the gate
            to be checked at all.
        profile_max_ceiling: the already-resolved governing
            ``MethodProfile.max_claim_ceiling``, or ``None`` — see
            ``ruled_ceiling`` above.

    Returns:
        a ``ClaimTableRow`` whose ``flagged`` is ``True`` iff
        ``unresolved_correction_ids`` is non-empty — never true "out of
        nowhere" with no id to point to. ``ceiling_checked`` is ``True`` iff
        BOTH ``ruled_ceiling``/``profile_max_ceiling`` were supplied
        (non-``None``); when ``True``, ``ceiling_violation`` is whatever
        ``mrr.domain.claim_ceiling.ceiling_violation_reason`` reports for
        this claim's own ``claim_type`` against the supplied pair — the
        SAME gate ``ClaimService.attach_ruling``/``_transition`` enforce,
        never re-implemented here. When either parameter is omitted (the
        legacy call shape, and every pre-K1-T02 call site), the result is
        byte-identical to pre-K1-T02 behavior: ``ceiling_checked=False``,
        ``ceiling_violation=None``.
    """
    unresolved_ids = unresolved_critical_correction_ids_for_claim(claim_body["id"], corrections)

    ceiling_checked = ruled_ceiling is not None and profile_max_ceiling is not None
    ceiling_violation: str | None = None
    if ceiling_checked:
        assert ruled_ceiling is not None
        assert profile_max_ceiling is not None
        ceiling_violation = ceiling_violation_reason(
            claim_type=claim_body["claim_type"],
            ruled_ceiling=ruled_ceiling,
            profile_max_ceiling=profile_max_ceiling,
        )

    return ClaimTableRow(
        claim_id=claim_body["id"],
        assertion=claim_body["assertion"],
        status=claim_body["status"],
        evidence_relations=tuple(claim_body.get("evidence_relations", [])),
        verification_ids=tuple(claim_body.get("verification_ids", [])),
        unresolved_correction_ids=unresolved_ids,
        flagged=bool(unresolved_ids),
        ceiling_checked=ceiling_checked,
        ceiling_violation=ceiling_violation,
    )
