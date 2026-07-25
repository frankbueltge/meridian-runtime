"""Pure, hand-rolled, no-new-dependency artifact-presence core (task-packets/
A2-T01.yaml, "Teil 2 — Nachsehen"). No file I/O, no network, no database —
every function here takes already-computed values (``mrr.domain.archive_dump``
's typed ``ArchivedObject`` rows; already-computed presence/hash booleans a
caller obtained by touching the filesystem) and returns a plain, typed
result. Mirrors ``mrr.domain.anchoring_integrity``'s own split precedent
(this module is R2's analogue for A2) and, for the extraction half,
``mrr.domain.archive_dump``'s own "derived, typed views" section — WITHOUT
modifying that module (task-packets/A2-T01.yaml forbidden_changes): the two
extraction functions below read ``mrr.domain.archive_dump.ArchivedObject
.body`` directly, exactly the way that module's own ``extract_evidence_
anchors``/``extract_source_records``/``extract_claims`` do, for the two
extra fields (``RunManifest.artifact_store_reference``, ``EvidenceAnchor
.snapshot_hash``) this packet needs that ``archive_dump``'s own typed views
do not carry.

--- Content addressing: root + hash fully determines the path (A2's own
    "one field suffices, not fifty-one" insight) --------------------------

The store is content-addressed (``adapters/object_store/.../local.py``):
``<root>/<hex[0:2]>/<hex[2:4]>/<hex>``. :func:`derive_blob_path`
reimplements exactly that formula — it does NOT import ``adapters
.object_store.local.LocalFilesystemArtifactStore``, whose constructor
``mkdir``\\s its root as a side effect the moment it is instantiated; a
read-only audit must never create a directory that was never there,
especially when the whole point of running it is to find out whether the
bytes ever existed at all.

--- One dump, possibly several runs — resolving "the" recorded root -------

A committed dump can carry more than one ``RunManifest`` (e.g.
``mrr_run2_corroboration_floor_v1.sql``: a run and a later corroborating run
over the same question — see docs/design/2026-07-26-a2-derivation-artifact-
store-reference.md). A text-kind ``EvidenceAnchor`` carries no ``run_id``
(task-packets/A2-T01.yaml explicitly forbids adding one — "the store is
content-addressed, so root plus the anchor's own hash already determines
the path"), so this module cannot attribute an individual anchor to a
specific run's manifest. :func:`resolve_dump_store_root` collects the
DISTINCT set of recorded roots across every ``RunManifest`` in the dump:
zero roots means every anchor in the dump is ``store_reference_not_recorded``
(both real committed dumps — this packet's own acceptance oracle); exactly
one distinct root is applied to every anchor in the dump (the common,
single/consistent-root case); MORE than one distinct root is a genuine
ambiguity this closed design cannot silently resolve — it is refused
(:class:`AmbiguousArtifactStoreRootError`), never guessed (AGENTS.md rule
14). Flagged explicitly in this packet's own delivery report as a derived
design decision the task packet itself does not spell out, not a
specification-given rule.

--- Four closed statuses, one per EvidenceAnchor, kept apart by TYPE -------

:data:`ArtifactPresenceStatus` is the SAME closed four-value set task-
packets/A2-T01.yaml's ``status_vocabulary`` names. Two are VIOLATIONS
(``artifact_missing``, ``artifact_hash_mismatch``); one is an OBSERVATION
(``store_reference_not_recorded`` — never a violation: nothing is broken,
nothing is known); one is a hit (``artifact_present``). No caller of this
module may sum the violation count with the observation count — collapsing
them is the single most important mistake this packet exists to avoid: the
first real run of this tool produces 51 observations and ZERO violations,
and folding them together would report 51 false errors where there are
none (AGENTS.md's "collapsing distinct statuses into one generic outcome"
prohibited shortcut).

--- Anchors without a snapshot_hash: reported, never silently dropped -----

Neither real committed dump has an ``EvidenceAnchor`` without a
``snapshot_hash`` (every one of the 51 is a text anchor resolved via
``snapshot_hash``, never ``quoted_fragment_hash`` — that field hashes an
in-document EXCERPT computed on the fly, not a separately stored blob; see
``services/control_plane/mrr/services/verifier/source.py``'s own
``validate_text_anchor``). A future dump could still have one (a
computational anchor, or a text anchor resolved only via
``quoted_fragment_hash``/``anchor_unavailable_reason``) — for such an
anchor there is no hash this module could derive a blob path from at all,
so :func:`extract_artifact_anchors` reports its id SEPARATELY rather than
silently skipping it (AGENTS.md rule 12) or forcing it into one of the four
closed statuses, which all presuppose a checkable hash.

--- Determinism (task-packets/A2-T01.yaml invariant) ------------------------

No wall clock anywhere in this module. :func:`check_artifact_presence`
takes ALREADY-COMPUTED bool/hash-string inputs (the SERVICE performs the
actual filesystem stat/read/hash) — mirrors ``mrr.domain.anchoring_integrity
.check_dump_anchor``'s identical "pure comparison of already-computed
values" shape. Every function returning a sequence sorts it explicitly by
its own primary id, never a ``dict``/``set`` iteration order, so calling any
of them twice over equal inputs yields an identical sequence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mrr.domain.archive_dump import ArchivedObject, ArchiveDumpParseError

#: Length of the "sha256:" prefix stripped before deriving on-disk shard
#: paths — mirrors ``adapters/object_store/.../local.py``'s own
#: ``_SHA256_PREFIX_LEN``; reimplemented here rather than imported so this
#: pure domain module never depends on that filesystem adapter (see the
#: module docstring's "content addressing" section).
_SHA256_PREFIX_LEN = len("sha256:")

#: How many leading hex characters form each of the two shard directory
#: levels — mirrors ``adapters/object_store/.../local.py``'s own
#: ``_SHARD_LEVEL_CHARS``.
_SHARD_LEVEL_CHARS = 2

#: The closed set of four per-anchor statuses (task-packets/A2-T01.yaml
#: status_vocabulary). ``artifact_missing``/``artifact_hash_mismatch`` are
#: VIOLATIONS; ``store_reference_not_recorded`` is an OBSERVATION;
#: ``artifact_present`` is a hit. Never collapsed (see the module
#: docstring).
ArtifactPresenceStatus = Literal[
    "artifact_present",
    "artifact_missing",
    "artifact_hash_mismatch",
    "store_reference_not_recorded",
]


class ArtifactPresenceError(Exception):
    """Base class for every typed error this module raises."""


class AmbiguousArtifactStoreRootError(ArtifactPresenceError):
    """Raised by :func:`resolve_dump_store_root` when a dump's RunManifest
    objects declare MORE THAN ONE distinct recorded root — see the module
    docstring's "one dump, possibly several runs" section for why this is
    refused rather than guessed. Carries the sorted tuple of the
    conflicting roots so a caller can report exactly what disagreed.
    """

    def __init__(self, roots: Sequence[str]) -> None:
        self.roots = tuple(sorted(roots))
        joined = ", ".join(repr(root) for root in self.roots)
        super().__init__(
            "this dump's RunManifest objects declare more than one distinct recorded "
            f"artifact-store root, and no anchor carries a run reference to disambiguate "
            f"them (roots: {joined})"
        )


@dataclass(frozen=True, slots=True)
class RunManifestStoreReferenceRow:
    """The derived, typed view of one ``RunManifest`` object body's
    ``artifact_store_reference`` (task-packets/A2-T01.yaml): its own run id,
    and the two-way ``status``/``root`` pair exactly as the archived body
    declares it — ``status="not_recorded"``/``root=None`` when the body
    carries no ``artifact_store_reference`` key at all (the true statement
    for every ``RunManifest`` committed before this packet).
    """

    run_id: str
    status: Literal["recorded", "not_recorded"]
    root: str | None


@dataclass(frozen=True, slots=True)
class ArtifactAnchorRow:
    """The derived, typed view of one ``EvidenceAnchor`` object body's own
    content-addressed hash (task-packets/A2-T01.yaml): its own id and its
    ``snapshot_hash`` — the field the two real committed dumps populate on
    every single anchor (never ``quoted_fragment_hash``; see the module
    docstring). Anchors with no ``snapshot_hash`` carry nothing this module
    can derive a blob path from; :func:`extract_artifact_anchors` reports
    them separately, never silently.
    """

    anchor_id: str
    snapshot_hash: str


def extract_run_manifest_store_references(
    objects: Sequence[ArchivedObject],
) -> tuple[RunManifestStoreReferenceRow, ...]:
    """Every ``RunManifest`` row's derived ``artifact_store_reference`` view,
    in ``objects`` order (a caller wanting a deterministic order sorts
    separately — mirrors ``mrr.domain.archive_dump``'s own "no ordering
    opinion baked into extraction" stance).

    Raises:
        mrr.domain.archive_dump.ArchiveDumpParseError: a RunManifest body's
            ``artifact_store_reference`` key is present but not a JSON
            object, its ``status`` sub-field is neither ``"recorded"`` nor
            ``"not_recorded"``, its ``root`` sub-field is present but not a
            string, or the two together violate the ``status``/``root``
            biconditional ``mrr.contracts.run_manifest
            .ArtifactStoreReference`` itself enforces — never silently
            defaulted to ``not_recorded``; a malformed reference is a
            data-shape problem, reported as such.
    """
    rows: list[RunManifestStoreReferenceRow] = []
    for obj in objects:
        if obj.kind != "RunManifest":
            continue
        raw_reference = obj.body.get("artifact_store_reference")
        if raw_reference is None:
            rows.append(
                RunManifestStoreReferenceRow(run_id=obj.object_id, status="not_recorded", root=None)
            )
            continue
        if not isinstance(raw_reference, dict):
            raise ArchiveDumpParseError(
                f"RunManifest {obj.object_id!r}: 'artifact_store_reference' must be a JSON "
                f"object, got {type(raw_reference).__name__}"
            )
        status = raw_reference.get("status")
        if status not in ("recorded", "not_recorded"):
            raise ArchiveDumpParseError(
                f"RunManifest {obj.object_id!r}: 'artifact_store_reference.status' must be "
                f"'recorded' or 'not_recorded', got {status!r}"
            )
        root = raw_reference.get("root")
        if root is not None and not isinstance(root, str):
            raise ArchiveDumpParseError(
                f"RunManifest {obj.object_id!r}: 'artifact_store_reference.root' must be a "
                f"string or null, got {type(root).__name__}"
            )
        has_root = root is not None
        if (status == "recorded") != has_root:
            raise ArchiveDumpParseError(
                f"RunManifest {obj.object_id!r}: 'artifact_store_reference' violates its own "
                f"biconditional (status={status!r}, root={root!r})"
            )
        rows.append(RunManifestStoreReferenceRow(run_id=obj.object_id, status=status, root=root))
    return tuple(rows)


def extract_artifact_anchors(
    objects: Sequence[ArchivedObject],
) -> tuple[tuple[ArtifactAnchorRow, ...], tuple[str, ...]]:
    """Every ``EvidenceAnchor`` row's derived :class:`ArtifactAnchorRow`,
    split from the ids of anchors that carry NO ``snapshot_hash`` at all
    (see the module docstring's "anchors without a snapshot_hash" section).
    Returns ``(anchors_with_snapshot_hash, anchor_ids_without_snapshot_hash)``,
    both in ``objects`` order.

    Raises:
        mrr.domain.archive_dump.ArchiveDumpParseError: an EvidenceAnchor
            body's ``snapshot_hash`` key is present but not a string.
    """
    with_hash: list[ArtifactAnchorRow] = []
    without_hash: list[str] = []
    for obj in objects:
        if obj.kind != "EvidenceAnchor":
            continue
        snapshot_hash = obj.body.get("snapshot_hash")
        if snapshot_hash is None:
            without_hash.append(obj.object_id)
            continue
        if not isinstance(snapshot_hash, str):
            raise ArchiveDumpParseError(
                f"EvidenceAnchor {obj.object_id!r}: 'snapshot_hash' must be a string or null, "
                f"got {type(snapshot_hash).__name__}"
            )
        with_hash.append(ArtifactAnchorRow(anchor_id=obj.object_id, snapshot_hash=snapshot_hash))
    return tuple(with_hash), tuple(without_hash)


def resolve_dump_store_root(manifests: Sequence[RunManifestStoreReferenceRow]) -> str | None:
    """The single recorded root that applies to every anchor in this dump —
    see the module docstring's "one dump, possibly several runs" section.
    ``None`` means every anchor in the dump is
    ``store_reference_not_recorded``.

    Raises:
        AmbiguousArtifactStoreRootError: more than one distinct recorded
            root is declared across ``manifests``.
    """
    recorded_roots = sorted(
        {
            manifest.root
            for manifest in manifests
            if manifest.status == "recorded" and manifest.root is not None
        }
    )
    if not recorded_roots:
        return None
    if len(recorded_roots) == 1:
        return recorded_roots[0]
    raise AmbiguousArtifactStoreRootError(recorded_roots)


def derive_blob_path(root: str, content_hash: str) -> Path:
    """The expected on-disk blob path for ``content_hash`` under ``root``:
    ``<root>/<hex[0:2]>/<hex[2:4]>/<hex>`` — reimplements exactly
    ``adapters/object_store/.../local.py``'s own layout formula (see the
    module docstring for why this is a REIMPLEMENTATION, not an import of
    that adapter). Pure path algebra — no filesystem access happens here.
    """
    hex_digest = content_hash[_SHA256_PREFIX_LEN:]
    return (
        Path(root)
        / hex_digest[:_SHARD_LEVEL_CHARS]
        / hex_digest[_SHARD_LEVEL_CHARS : 2 * _SHARD_LEVEL_CHARS]
        / hex_digest
    )


@dataclass(frozen=True, slots=True)
class ArtifactPresenceVerdict:
    """One ``EvidenceAnchor``'s artifact-presence verdict — the row
    ``mrr.domain.artifact_presence_report`` projects into its Pydantic
    report. ``blob_path`` is ``None`` iff ``status ==
    "store_reference_not_recorded"`` (no root, so no path could be
    derived).
    """

    anchor_id: str
    expected_hash: str
    blob_path: str | None
    status: ArtifactPresenceStatus


def check_artifact_presence(
    anchor: ArtifactAnchorRow,
    *,
    store_root: str | None,
    blob_path: str | None,
    blob_exists: bool,
    actual_hash: str | None,
) -> ArtifactPresenceVerdict:
    """Assign exactly one of the four :data:`ArtifactPresenceStatus` values
    to ``anchor``, from ALREADY-COMPUTED inputs (the SERVICE performs the
    actual filesystem stat/read/hash; see the module docstring's
    "Determinism" section) — mirrors ``mrr.domain.anchoring_integrity
    .check_dump_anchor``'s identical "pure comparison" shape.

    ``store_root is None`` means :func:`resolve_dump_store_root` found no
    recorded root for this dump at all — in that case the verdict is
    ``store_reference_not_recorded`` regardless of ``blob_path``/
    ``blob_exists``/``actual_hash`` (which a well-behaved caller never
    computed in the first place: no root means there is nothing to derive a
    path from, so ``mrr.services.artifact_presence.service`` never touches
    the filesystem for such an anchor).
    """
    if store_root is None:
        return ArtifactPresenceVerdict(
            anchor_id=anchor.anchor_id,
            expected_hash=anchor.snapshot_hash,
            blob_path=None,
            status="store_reference_not_recorded",
        )
    if not blob_exists:
        return ArtifactPresenceVerdict(
            anchor_id=anchor.anchor_id,
            expected_hash=anchor.snapshot_hash,
            blob_path=blob_path,
            status="artifact_missing",
        )
    if actual_hash != anchor.snapshot_hash:
        return ArtifactPresenceVerdict(
            anchor_id=anchor.anchor_id,
            expected_hash=anchor.snapshot_hash,
            blob_path=blob_path,
            status="artifact_hash_mismatch",
        )
    return ArtifactPresenceVerdict(
        anchor_id=anchor.anchor_id,
        expected_hash=anchor.snapshot_hash,
        blob_path=blob_path,
        status="artifact_present",
    )
