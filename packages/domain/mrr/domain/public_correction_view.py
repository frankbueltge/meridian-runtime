"""Pure, framework-free shaping logic for the PUBLIC unresolved-correction
projection (task-packets/E6-T05.yaml), docs/spec/01_SYSTEM_SPEC.md line 210,
MRR-FR-095 ("Public projections MUST display unresolved critical
corrections"), and MRR-NFR-006 (no raw restricted or participant-identifiable
data leaves its owning node by default). Fifth of Epic E6's six tasks; the
closest template is ``mrr.domain.projection`` (E3-T07) itself, whose
``is_unresolved_critical_correction``/``RESOLVED_CORRECTION_STATUSES``/
``ClaimTableRow`` this module imports and reuses byte-for-byte unmodified —
this module never re-derives, and never forks a second, competing
definition of, "unresolved critical correction". Read that module's
docstring first.

The companion I/O-performing half — reading real claims/corrections via the
already-existing ``mrr.services.projection.service.ProjectionService`` reads
(``_read_correction_bodies``, ``build_claim_table``) and calling
``build_public_correction_row``/``build_public_claim_row`` below once per
discovered correction/claim — is that same class's two new additive methods,
``build_public_correction_view``/``build_public_claim_table``.

--- What this module adds: fail-closed classification redaction -----------

Neither ``schemas/claim.schema.json`` nor ``schemas/correction-event.schema.json``
carries a classification field of its own (confirmed by direct schema
inspection at task-derivation time — the only classification fields in
``schemas/`` are ``common.schema.json``'s optional ``$defs.artifactRef.
classification`` and ``task-bundle.schema.json``'s own required field,
neither reachable from a ``Claim`` or a ``CorrectionEvent``). MRR-FR-095's
word "Public" therefore cannot be answered today by reading a stored field
anywhere in the graph. This module's ``classification_by_object_id`` is a
caller-supplied ``Mapping[str, Classification]`` attestation — a deliberate,
explicit, non-schema-changing stand-in for the not-yet-built formal
disclosure-review mechanism named at docs/spec/04_SECURITY_AND_POLICY.md
line 62 ("Derived output receives a disclosure review before export") and
MRR-FR-103 (Epic E8) — ADR-0010's staged-adoption step 2 (an explicit bridge
pending a future stored-classification-field step). ``Classification`` is
imported from ``mrr.domain.artifacts`` rather than redeclared a third time or
imported from ``mrr.contracts.common``: that module's own ``artifacts.py``
docstring already documents why importing ``mrr.contracts`` back into
``mrr.domain`` would create a package cycle (domain -> contracts -> domain)
that no other module in this codebase creates.

The redaction rule is FAIL-CLOSED: a piece of free text is shown only when
EVERY object id it depends on is attested literally ``"PUBLIC"`` in
``classification_by_object_id``. A missing entry, an unrecognized string,
and every one of the other four declared classification levels (INTERNAL,
RESTRICTED, SENSITIVE, PARTICIPANT_IDENTIFIABLE) all redact identically —
absence of proof is never treated as proof of public safety
(docs/spec/02_DOMAIN_MODEL.md section 4: only PUBLIC is described as
"transferable"). This is enforced by plain equality against the literal
string ``"PUBLIC"`` (``_all_ids_attested_public`` below), which already
handles "unrecognized" values for free: any string that is not exactly
``"PUBLIC"`` fails the check, whether it is one of the four other declared
levels, a typo, or something no schema ever declared at all — no special
case is needed, and none is added.

--- What redaction never touches: the structural fact -----------------------

MRR-FR-095 reads as a MUST, not a conditional one. A correction's own id,
severity, status, correction_type, and every id it names in
``affected_objects``/``impact_objects`` (and a claim's own id, status, and
``unresolved_correction_ids``) remain visible UNCONDITIONALLY, regardless of
any classification data — only the two CorrectionEvent free-text fields
(``reason``, ``requested_action``) and the one Claim free-text field
(``assertion``) are ever withheld. A design that could make an unresolved
critical correction vanish entirely under an incomplete classification map
would silently trade FR-095's MUST for MRR-NFR-006's MUST instead of
honoring both — this module refuses that trade.

--- Determinism -------------------------------------------------------------

``build_public_correction_row``/``build_public_claim_row`` perform no I/O,
mutate nothing, and depend only on their arguments' values — calling either
one twice with the same inputs yields an equal result both times.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mrr.domain.artifacts import Classification
from mrr.domain.projection import ClaimTableRow, is_unresolved_critical_correction

#: The one literal value that unlocks disclosure. Every other value —
#: missing, unrecognized, or one of the four other declared Classification
#: levels — redacts identically (see the module docstring's "fail-closed
#: classification redaction" section).
_PUBLIC: Classification = "PUBLIC"


@dataclass(frozen=True, slots=True)
class PublicCorrectionRow:
    """One row of the public unresolved-correction view (MRR-FR-095). The
    structural fields (everything except ``reason``/``requested_action``)
    are ALWAYS populated, regardless of ``redacted`` — see the module
    docstring's "What redaction never touches" section. ``reason`` and
    ``requested_action`` are both ``None`` whenever ``redacted`` is
    ``True``, and both carry the correction's own stored text otherwise.
    """

    correction_id: str
    correction_type: str
    severity: str
    status: str
    affected_object_ids: tuple[str, ...]
    impact_object_ids: tuple[str, ...]
    unresolved: bool
    reason: str | None
    requested_action: str | None
    redacted: bool


@dataclass(frozen=True, slots=True)
class PublicClaimRow:
    """One row of the public claim table — a redacted mirror of
    ``mrr.domain.projection.ClaimTableRow``. ``claim_id``, ``status``,
    ``unresolved_correction_ids``, and ``flagged`` are always populated
    (structural facts, per MRR-FR-095); ``assertion`` is ``None`` whenever
    ``redacted`` is ``True``.
    """

    claim_id: str
    status: str
    unresolved_correction_ids: tuple[str, ...]
    flagged: bool
    assertion: str | None
    redacted: bool


def _all_ids_attested_public(
    object_ids: Iterable[str], classification_by_object_id: Mapping[str, Classification]
) -> bool:
    """``True`` iff every id in ``object_ids`` maps, in
    ``classification_by_object_id``, to exactly the literal string
    ``"PUBLIC"``. A missing key, or any other value (recognized non-public
    level or unrecognized string alike), makes this ``False`` — the one
    fail-closed check both row builders below share. Pure: no I/O, no
    mutation of either argument.
    """
    return all(classification_by_object_id.get(object_id) == _PUBLIC for object_id in object_ids)


def build_public_correction_row(
    correction_body: Mapping[str, Any],
    *,
    classification_by_object_id: Mapping[str, Classification],
) -> PublicCorrectionRow:
    """Shape one ``PublicCorrectionRow`` from an already-read
    ``CorrectionEvent`` latest-revision body.

    Args:
        correction_body: a ``CorrectionEvent``'s latest-revision body (e.g.
            ``StoredObject.body``) — must carry ``id``, ``severity``,
            ``status``, ``correction_type``, ``reason``,
            ``requested_action``, ``affected_objects`` (a list of
            ``{"id": ..., ...}`` mappings), and ``impact_objects`` (a list of
            plain id strings), per schemas/correction-event.schema.json.
        classification_by_object_id: the caller-supplied attestation map
            (see the module docstring). Not mutated.

    Returns:
        a ``PublicCorrectionRow`` whose ``unresolved`` field is exactly
        ``mrr.domain.projection.is_unresolved_critical_correction``'s own
        verdict for this correction's ``severity``/``status`` — never a
        second, divergent computation. ``redacted`` is ``True`` unless the
        correction's own id AND every one of its ``affected_objects``/
        ``impact_objects`` ids are attested literally ``PUBLIC``; when
        ``redacted`` is ``True``, both ``reason`` and ``requested_action``
        are ``None``.
    """
    correction_id = correction_body["id"]
    severity = correction_body["severity"]
    status = correction_body["status"]
    affected_object_ids = tuple(ref["id"] for ref in correction_body.get("affected_objects", []))
    impact_object_ids = tuple(correction_body.get("impact_objects", []))

    contributing_ids: tuple[str, ...] = (correction_id, *affected_object_ids, *impact_object_ids)
    is_public = _all_ids_attested_public(contributing_ids, classification_by_object_id)

    return PublicCorrectionRow(
        correction_id=correction_id,
        correction_type=correction_body["correction_type"],
        severity=severity,
        status=status,
        affected_object_ids=affected_object_ids,
        impact_object_ids=impact_object_ids,
        unresolved=is_unresolved_critical_correction(severity=severity, status=status),
        reason=correction_body["reason"] if is_public else None,
        requested_action=correction_body["requested_action"] if is_public else None,
        redacted=not is_public,
    )


def build_public_claim_row(
    claim_row: ClaimTableRow,
    *,
    classification_by_object_id: Mapping[str, Classification],
) -> PublicClaimRow:
    """Shape one ``PublicClaimRow`` from an already-built ``ClaimTableRow``
    (``mrr.domain.projection.build_claim_table_row``'s own output, reused
    verbatim rather than re-derived from a raw claim body — see the module
    docstring).

    Args:
        claim_row: a ``ClaimTableRow`` already computed by
            ``mrr.services.projection.service.ProjectionService.
            build_claim_table``.
        classification_by_object_id: the caller-supplied attestation map
            (see the module docstring). Not mutated.

    Returns:
        a ``PublicClaimRow`` whose ``redacted`` is ``True`` unless the
        claim's own id AND every one of its ``unresolved_correction_ids``
        are attested literally ``PUBLIC`` (for an unflagged claim,
        ``unresolved_correction_ids`` is empty, so only the claim's own id
        matters); when ``redacted`` is ``True``, ``assertion`` is ``None``.
    """
    contributing_ids: tuple[str, ...] = (
        claim_row.claim_id,
        *claim_row.unresolved_correction_ids,
    )
    is_public = _all_ids_attested_public(contributing_ids, classification_by_object_id)

    return PublicClaimRow(
        claim_id=claim_row.claim_id,
        status=claim_row.status,
        unresolved_correction_ids=claim_row.unresolved_correction_ids,
        flagged=claim_row.flagged,
        assertion=claim_row.assertion if is_public else None,
        redacted=not is_public,
    )
