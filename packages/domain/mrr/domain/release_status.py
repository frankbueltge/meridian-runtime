"""Pure, framework-free release-status ("correction banner") computation
(task-packets/E8-T05.yaml R2, docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-
APPROVAL-EVENT.md, ``mrr.domain.lifecycles.RELEASE_RECORD_LIFECYCLE``'s own
reserved ``released -> superseded`` edge). Fifth and closing task of Epic E8;
the closest templates are ``mrr.domain.projection``/``mrr.domain
.public_correction_view`` (the identical "pure decision logic over
already-read bodies, no I/O, no repository/service import" split this module
follows) and ``mrr.domain.ro_crate`` (whose own ``object_relative_path``
naming rule this module's own :func:`exported_object_urns` inverts — see
below).

This module computes exactly ONE thing: the three-verdict banner
(``current`` / ``corrections_affect_this_release`` / ``superseded``) plus the
SEPARATE ``duplicate_unsuperseded_releases`` anomaly flag
(reviewer_resolution (2)'s own detection duty), from already-resolved
inputs — a ``ReleaseRecord``'s own latest-revision body, an already-read
sequence of ``CorrectionEvent`` bodies, and a caller-supplied boolean the
service layer alone can determine (a "does more than one unsuperseded
release exist for this crate_id" scan requires reading the object
repository/event log a service can access, which this module deliberately
cannot — see the module docstring's "No I/O, ever" section). The
I/O-performing half — resolving a ``ReleaseRecord`` by id, discovering every
``CorrectionEvent`` ever recorded (mirroring ``mrr.services.projection
.service.ProjectionService``'s own established "scan the event log for
genesis events" discovery pattern, task-packets/E3-T07.yaml — reused HERE by
disclosed, minimal duplication rather than composed via ``ProjectionService``
itself, since that class needs an ``EdgeRepository`` this service has no
other use for, and its own ``_read_correction_bodies`` is module-private),
and running the same duplicate-crate_id scan — is
``mrr.services.release.service.ReleaseService.status`` (task-packets/
E8-T05.yaml R2's own "a service method on ReleaseService (read-only path)
resolves those inputs and calls it").

--- No I/O, ever ------------------------------------------------------------

No repository/service/adapter import, no filesystem, no network, no
``datetime.now()`` anywhere in this file (mirrors ``mrr.domain.ro_crate``'s/
``mrr.domain.research_report``'s own identical "no wall-clock timestamps"
invariant, task-packets/E8-T05.yaml's own "no wall clock anywhere" — the
ONLY two timestamps this module ever reads are the release's and each
correction's own already-stored ``created_at`` strings, parsed and compared
as real ``datetime`` values, never read from the system clock). Calling
:func:`compute_release_banner` twice with equal arguments returns an equal
result both times.

--- R2's verdict precedence, verbatim ----------------------------------------

Exactly three mutually exclusive verdicts, checked in this fixed order (a
release cannot be BOTH superseded and "corrections affect it" — superseded
status is definitionally the more final fact and wins outright):

1. **``superseded``** — ``release_body["status"] == "superseded"``. The
   superseding release's own urn comes from ``release_body["labels"]
   ["superseded_by"]`` (derived_decisions (a): a plain, open ``labels``
   string-map slot — schemas/release-record.schema.json/``BaseObject``
   already declare ``labels: dict[str, str] | None`` with no key vocabulary
   restriction, confirmed by direct inspection at derivation time; no
   collision with any existing label convention exists, so
   stop_condition 1 does not fire). No correction check runs at all once a
   release is superseded — its own history is frozen (AT4's own point).
2. **``corrections_affect_this_release``** — else, if ANY correction in
   ``correction_bodies`` both (a) has ``created_at`` STRICTLY LATER than
   ``release_body["created_at"]`` (both already-stored fields, parsed via
   ``datetime.fromisoformat`` — never compared as raw strings, since ISO-8601
   text without a fixed-width fractional-second count does not sort
   lexicographically the same way it compares as real instants — see
   ``mrr.services.capability_registry.service._parse_window``'s own
   identical rationale, cited here as this codebase's own established
   precedent for parsing a stored timestamp string back into an aware
   ``datetime`` before comparing it) AND (b) names at least one object urn,
   in its own ``affected_objects``/``impact_objects`` (schemas/
   correction-event.schema.json), that is also a member of the release's own
   exported object set (:func:`exported_object_urns`) — derived_decisions
   (b): this is INTERSECTION with the release's own shipped manifest, never
   the full transitive impact machinery (``mrr.domain.correction_impact`` is
   not imported or consulted here at all) — "the release knows exactly what
   it shipped." Every qualifying correction is reported (not just the
   first), each carrying its own sorted intersection
   (:class:`AffectingCorrection`), and the returned tuple is itself sorted by
   ``correction_id`` for a deterministic, order-independent result.
3. **``current``** — else (no correction qualifies, and the release is not
   superseded).

--- The anomaly flag is ADDITIONAL, never a fourth verdict --------------------

``duplicate_unsuperseded_releases`` rides alongside whichever of the three
verdicts above was computed — task-packets/E8-T05.yaml R2's own explicit
"returned alongside (never instead)". A release can be, simultaneously,
``"current"`` (or ``"corrections_affect_this_release"``) AND flagged as one
of more-than-one unsuperseded release sharing the same ``crate_id`` — the two
facts are independent and both are reported, never collapsed into one.

--- Exported-object-set derivation: inverting ``mrr.domain.ro_crate``'s own naming rule ---

``mrr.domain.ro_crate.object_relative_path`` (FROZEN — task-packets/
E8-T05.yaml ``forbidden_changes``) maps an MRR urn ``U`` to
``objects/<U with every ':' replaced by '_'>.json``, that module's own R1
"File naming" section. A release bundle's own manifest
(``mrr.services.release.manifest.compute_bundle_manifest``, task-packets/
E8-T04.yaml) records every content file's path RELATIVE TO THE BUNDLE ROOT —
and ``mrr.services.release.bundle.assemble_and_release`` writes the RO-Crate
export into the bundle's own ``ro-crate/`` subdirectory
(``_RO_CRATE_SUBDIR``), so every exported OBJECT file's manifest path is
therefore ``ro-crate/objects/<urn with ':' -> '_'>.json`` — this module's own
:data:`_RO_CRATE_OBJECTS_PREFIX`/:data:`_JSON_SUFFIX`. :func:`exported_object_urns`
inverts this: strip the fixed prefix/suffix, then reverse the substitution
(``'_' -> ':'``) — well-defined and lossless because ``mrr.domain.identity
.URN_PATTERN``'s own entity segment (``[a-z0-9-]+``) and ULID segment
(``[0-9A-HJKMNP-TV-Z]{26}``) never themselves contain an underscore, so the
only underscores ``object_relative_path`` ever introduces are the three that
replaced the urn's own three colons — a straight ``str.replace("_", ":")``
recovers exactly the original urn, verified against ``URN_PATTERN`` itself
before being trusted (a path that happens to match the prefix/suffix but
whose recovered text is not itself a valid urn — structurally impossible for
any file this codebase's own bundle assembly ever writes, but checked anyway,
fail-closed, rather than trusted blindly). Every OTHER bundle path —
``report.md``, ``report.html``, ``ro-crate/ro-crate-metadata.json``,
``ro-crate/artifacts/<hash>`` (content-addressed, not urn-addressed) — does
not match the ``ro-crate/objects/*.json`` shape and is silently excluded,
exactly as it should be: none of those paths names an MRR object urn at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from mrr.domain.identity import URN_PATTERN

#: R2's own three mutually exclusive verdicts, checked in the fixed
#: precedence order the module docstring documents.
ReleaseVerdict = Literal["current", "corrections_affect_this_release", "superseded"]

#: See the module docstring's "Exported-object-set derivation" section — the
#: fixed, frozen shape ``mrr.services.release.bundle.assemble_and_release``/
#: ``mrr.domain.ro_crate.object_relative_path`` together always produce for
#: an exported MRR object's own manifest path.
_RO_CRATE_OBJECTS_PREFIX = "ro-crate/objects/"
_JSON_SUFFIX = ".json"

#: derived_decisions (a): the open ``labels`` string-map slot this task
#: carries ``superseded_by`` in, rather than a new schema field.
_SUPERSEDED_BY_LABEL = "superseded_by"

_SUPERSEDED_STATUS = "superseded"

#: ``ReleaseVerdict`` literals, transcribed as plain module constants so the
#: rest of this module (and ``mrr.domain.research_report``'s own reuse of
#: this type) never repeats the literal string by hand.
VERDICT_CURRENT: ReleaseVerdict = "current"
VERDICT_CORRECTIONS_AFFECT: ReleaseVerdict = "corrections_affect_this_release"
VERDICT_SUPERSEDED: ReleaseVerdict = "superseded"


@dataclass(frozen=True, slots=True)
class AffectingCorrection:
    """One correction whose own ``affected_objects``/``impact_objects`` urns
    intersect the release's exported object set, AND whose own ``created_at``
    is strictly later than the release's own ``created_at`` — R2's own
    "corrections_affect_this_release" qualifying test. ``intersecting_object_ids``
    is the sorted intersection itself (R2: "with the sorted intersection
    listed per correction") — never the correction's FULL
    ``affected_objects``/``impact_objects`` set, only the urns that actually
    are members of what this release shipped.
    """

    correction_id: str
    intersecting_object_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseBanner:
    """The full R2 result: the three-verdict banner plus the separate
    anomaly flag. Every field derives from stored data
    (``release_body``/``correction_bodies``/the caller-supplied
    ``duplicate_unsuperseded_releases`` scan result) — no wall clock, no
    invented severity aggregation (derived_decisions (b)/(c)).
    """

    verdict: ReleaseVerdict
    release_id: str
    crate_id: str
    superseded_by: str | None
    affecting_corrections: tuple[AffectingCorrection, ...]
    duplicate_unsuperseded_releases: bool


def exported_object_urns(bundle_file_paths: Iterable[str]) -> frozenset[str]:
    """The release's own exported MRR object urns, recovered from its
    bundle manifest's own file paths — see the module docstring's "Exported-
    object-set derivation" section for the full inversion rationale. Paths
    that do not name an exported object (``report.md``, the RO-Crate
    metadata document, artifact files) are silently excluded — they name no
    urn at all, not a fact worth reporting.
    """
    urns: set[str] = set()
    for path in bundle_file_paths:
        if not path.startswith(_RO_CRATE_OBJECTS_PREFIX) or not path.endswith(_JSON_SUFFIX):
            continue
        encoded = path[len(_RO_CRATE_OBJECTS_PREFIX) : -len(_JSON_SUFFIX)]
        candidate = encoded.replace("_", ":")
        if URN_PATTERN.match(candidate) is not None:
            urns.add(candidate)
    return frozenset(urns)


def _correction_object_ids(correction_body: Mapping[str, Any]) -> frozenset[str]:
    """Every object urn ``correction_body`` names in its own
    ``affected_objects`` (a list of ``{"id": ..., "content_hash": ...}``
    mappings, schemas/correction-event.schema.json) or ``impact_objects`` (a
    list of plain urn strings) — the identical two-field union
    ``mrr.domain.projection.unresolved_critical_correction_ids_for_claim``
    already reads, restated here rather than imported (that function answers
    a different question — "does this correction flag THIS claim" — over a
    single target id, not "what is the full set of object ids this
    correction names," which is what this module's own intersection test
    needs).
    """
    affected = {
        str(ref["id"]) for ref in correction_body.get("affected_objects", []) if "id" in ref
    }
    impact = {str(oid) for oid in correction_body.get("impact_objects", [])}
    return frozenset(affected | impact)


def _affecting_corrections(
    *,
    correction_bodies: Sequence[Mapping[str, Any]],
    exported_urns: frozenset[str],
    release_created_at: datetime,
) -> tuple[AffectingCorrection, ...]:
    rows: list[AffectingCorrection] = []
    for correction_body in correction_bodies:
        correction_created_at = datetime.fromisoformat(str(correction_body["created_at"]))
        if correction_created_at <= release_created_at:
            continue
        intersection = sorted(_correction_object_ids(correction_body) & exported_urns)
        if not intersection:
            continue
        rows.append(
            AffectingCorrection(
                correction_id=str(correction_body["id"]),
                intersecting_object_ids=tuple(intersection),
            )
        )
    rows.sort(key=lambda row: row.correction_id)
    return tuple(rows)


def compute_release_banner(
    *,
    release_body: Mapping[str, Any],
    correction_bodies: Sequence[Mapping[str, Any]],
    duplicate_unsuperseded_releases: bool,
) -> ReleaseBanner:
    """The pure R2 computation — see the module docstring for the full
    verdict-precedence and anomaly-flag rationale.

    Args:
        release_body: a ``ReleaseRecord``'s own LATEST-revision body (e.g.
            ``StoredObject.body`` — schemas/release-record.schema.json),
            carrying at minimum ``id``, ``crate_id``, ``status``,
            ``created_at``, ``bundle.files[].path``, and (when
            ``status == "superseded"``) ``labels.superseded_by``.
        correction_bodies: every already-read ``CorrectionEvent`` body to
            check against (e.g. every correction this practice has ever
            recorded — the caller decides the candidate population; this
            function itself applies no further filter beyond R2's own
            intersection/``created_at`` test). Consumed once.
        duplicate_unsuperseded_releases: whether more than one unsuperseded
            ``ReleaseRecord`` exists for this release's own ``crate_id`` —
            already determined by the caller (this module has no repository
            access to determine it itself; see the module docstring's "No
            I/O, ever" section).

    Returns:
        The full :class:`ReleaseBanner`. Calling this twice with equal
        arguments returns an equal result both times.
    """
    release_id = str(release_body["id"])
    crate_id = str(release_body["crate_id"])
    status = str(release_body["status"])

    if status == _SUPERSEDED_STATUS:
        labels = release_body.get("labels") or {}
        superseded_by = labels.get(_SUPERSEDED_BY_LABEL) if isinstance(labels, Mapping) else None
        return ReleaseBanner(
            verdict=VERDICT_SUPERSEDED,
            release_id=release_id,
            crate_id=crate_id,
            superseded_by=str(superseded_by) if superseded_by is not None else None,
            affecting_corrections=(),
            duplicate_unsuperseded_releases=duplicate_unsuperseded_releases,
        )

    exported_urns = exported_object_urns(
        str(entry["path"]) for entry in release_body["bundle"]["files"]
    )
    release_created_at = datetime.fromisoformat(str(release_body["created_at"]))
    affecting = _affecting_corrections(
        correction_bodies=correction_bodies,
        exported_urns=exported_urns,
        release_created_at=release_created_at,
    )

    if affecting:
        return ReleaseBanner(
            verdict=VERDICT_CORRECTIONS_AFFECT,
            release_id=release_id,
            crate_id=crate_id,
            superseded_by=None,
            affecting_corrections=affecting,
            duplicate_unsuperseded_releases=duplicate_unsuperseded_releases,
        )

    return ReleaseBanner(
        verdict=VERDICT_CURRENT,
        release_id=release_id,
        crate_id=crate_id,
        superseded_by=None,
        affecting_corrections=(),
        duplicate_unsuperseded_releases=duplicate_unsuperseded_releases,
    )
