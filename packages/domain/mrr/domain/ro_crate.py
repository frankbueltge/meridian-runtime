"""Pure, framework-free RO-Crate 1.1 export shaping (task-packets/E8-T01.yaml,
MRR-FR-055's first half). First task of Epic E8; the closest templates are
``mrr.domain.projection`` (a pure-domain module consumed by an application-
layer service that performs the actual repository/store reads —
``mrr.services.export.service.ExportService`` plays that role here) and
``mrr.domain.hashing_policy`` (this module's own dependency, for the exact
canonicalization discipline object bodies get before they are hashed,
signed, or — as of this packet — exported).

Everything in this module is a pure function of its arguments: no I/O, no
network, no filesystem, and — task-packets/E8-T01.yaml R1's own words — "no
repository types". The only imports beyond the standard library are
``mrr.crypto.canonical`` (RFC 8785 canonical-JSON bytes, the exact same
helper ``mrr.domain.hashing_policy`` already uses for hashing/signing) and
``dataclasses``/``collections.abc`` for the return shapes below. Calling
either public builder twice with the same arguments yields byte-identical
results (task-packets/E8-T01.yaml AT4, "determinism"): both are pure
functions over immutable inputs, with no caching, randomness, or wall-clock
read anywhere in this file.

--- What this module does NOT do -------------------------------------------

It does not resolve a URN to a stored object, walk a provenance graph, fetch
artifact bytes, or write anything to disk. ``mrr.services.export.service
.ExportService`` performs every one of those (I/O-bound) steps and then
calls the two builders below with the results already in hand — already-
loaded object bodies (``Mapping[str, JSONValue]``, e.g. a ``StoredObject
.body``) and already-known artifact byte counts. This mirrors
``mrr.domain.projection.build_claim_table_row``'s own "takes an already-read
``Claim`` body, not a claim id" shape exactly, for the same reason: a pure
function cannot read a database, and pretending otherwise by accepting a
repository Protocol here would smuggle an I/O dependency into a module this
task-packet, and the import-linter contract in pyproject.toml, both require
to stay free of one (task-packets/E8-T01.yaml AT5).

--- File naming (R1): fixed, collision-free, computed here ------------------

- one MRR object with urn ``U`` -> ``objects/<U with every ':' replaced by
  '_'>.json`` (``object_relative_path``);
- one artifact with content hash ``sha256:<hex>`` -> ``artifacts/<hex>``,
  the ``sha256:`` prefix dropped (``artifact_relative_path``);
- the crate's own RO-Crate metadata document -> ``ro-crate-metadata.json``
  at the export root (``METADATA_FILE_NAME``).

Both naming functions are collision-free because MRR urns and sha256 hex
digests are themselves collision-free identifiers (``mrr.domain.identity``,
``mrr.crypto.hashing``) and neither transform can make two distinct inputs
coincide: ``':' -> '_'`` is a fixed, deterministic character substitution
over an alphabet (``mrr.domain.identity.URN_PATTERN``'s entity/ULID
segments) that never itself contains an underscore-vs-colon ambiguity — no
valid urn produces the same string as another valid urn after the
substitution, since the substitution only ever touches the two literal
``':'`` separators, never a character that could originate from the
entity/ULID segments themselves. Object filenames and artifact filenames
also cannot collide with each other (different subdirectories) or with
``ro-crate-metadata.json`` (reserved, unprefixed, at the root — no MRR urn
or content hash ever produces that literal string).

--- Object bodies supply their own file size; artifacts do not (R1) ----------

An MRR object's exported bytes ARE its own canonical form
(``mrr.crypto.canonical.canonicalize`` over the stored body, task-packets/
E8-T01.yaml R3's "Object JSON files are written as RFC 8785 canonical
bytes") — a value this module can compute directly from the body it was
already handed, with no additional input. ``build_export_plan`` therefore
canonicalizes every object body itself and reports both the resulting bytes
(for ``mrr.services.export.service.ExportService`` to write verbatim — never
re-canonicalized a second time, so there is exactly one computation of "what
this object's exported bytes are" to ever drift) and their length (for the
metadata's ``contentSize``). An artifact's raw bytes, by contrast, are
opaque payload this module never sees (no filesystem/object-store import
here at all) — its size is supplied by the caller as part of
``artifact_sizes``, sourced from bytes ``ExportService`` already fetched via
``mrr.domain.artifacts.ArtifactStore.get`` before calling this module.

--- The RO-Crate 1.1 document (R1) -------------------------------------------

``build_ro_crate_metadata`` returns a plain ``dict`` — no RO-Crate/JSON-LD
library import anywhere in this codebase (task-packets/E8-T01.yaml derived
decision (a): "hand-authored, profile-conformant JSON-LD authored as plain
dicts"). Its ``@graph`` carries, in order:

1. the metadata file descriptor (``@id: "ro-crate-metadata.json"``,
   ``@type: "CreativeWork"``, ``conformsTo`` pointing at the RO-Crate 1.1
   profile, ``about`` pointing at the root data entity);
2. the root data entity (``@id: "./"``, ``@type: "Dataset"``,
   ``datePublished`` — the crate's OWN ``created_at``, never
   ``datetime.now()`` (see "No wall-clock timestamps" below) — and
   ``hasPart`` naming every exported object and artifact file, in the same
   sorted-urn/sorted-hash order ``build_export_plan`` already establishes);
3. one ``File`` entity per exported file (object or artifact), carrying
   ``contentSize`` and ``mrr:contentHash``. An object file's entity
   additionally carries ``about``, pointing back at its own contextual
   entity (item 4) — a disclosed addition beyond R1's literal minimum
   enumeration, flagged for reviewer scrutiny: R1 does not name this link
   explicitly, but it costs nothing (derivable in full from the already-
   documented, deterministic ``object_relative_path`` naming rule — a
   reader who knows that rule can already recover the same association from
   the filename alone) and it directly serves the packet's own objective
   ("a third party can inspect object IDs, hashes ... without the running
   MRR database") by making the object-file <-> contextual-entity
   correspondence explicit in the graph rather than only implicit in a
   naming convention;
4. one contextual entity per MRR object (``@id`` is the object's own urn,
   ``@type`` is the compact IRI ``mrr:<kind>``), carrying ``mrr:urn``,
   ``mrr:kind``, ``mrr:contentHash``, ``mrr:practiceId`` verbatim from the
   stored body, plus ``mrr:signature`` — verbatim, whatever the body's own
   ``signature`` field holds — WHENEVER the body actually carries one. R1's
   own text says "(for the crate) mrr:signature" because the crate is the
   only kind this packet's own closure (task-packets/E8-T01.yaml R2) ever
   includes that carries a top-level ``signature`` field at all (Claim,
   SourceRecord, EvidenceAnchor, VerificationResult do not); this module
   does not hardcode "only if kind == EvidenceCrate", since that would be
   inventing a kind-based rule the schemas do not state — it checks for the
   field's actual presence in the body, which happens to produce exactly
   R1's "for the crate" outcome against today's closure and stays correct
   without amendment if a future closure ever includes another signed kind.

Artifacts get File entities only (item 3) — never a contextual entity (item
4): task-packets/E8-T01.yaml R2(b) names artifacts as included "via their
ArtifactRef descriptors", distinct from the id-addressable
source_records/evidence_anchors/proposed_claims arrays, precisely because no
"Artifact" object is ever resolvable through ``ObjectRepository`` (see
``mrr.services.node_runtime.evidence_crate``'s own docstring, "``artifact_
refs`` are caller-supplied": "``ArtifactDescriptor`` has no ``artifact_id``
field at all"). A contextual entity's required ``mrr:kind``/``mrr:
practiceId`` therefore has nothing to read for an artifact; a reader who
wants the mapping from an ``artifact_id`` URN to its content hash already
has it, verbatim, inside the crate's own exported object file (its
``artifacts`` array, an ``ArtifactRef`` list) — this module does not
duplicate that mapping into the metadata document a second time.

--- No prov:* terms (R1, E8-T02's boundary) ----------------------------------

Nothing in this module emits any ``prov:`` term, or declares a ``prov``
``@context`` prefix. task-packets/E8-T01.yaml derived decision (c): PROV
mapping — including ``prov:wasDerivedFrom`` for exactly the edges the
provenance BFS already walks to build this closure — is E8-T02's entire
subject; emitting even a partial PROV vocabulary here would force that
future task to edit this packet's already-shipped output format instead of
purely adding to it. This is asserted directly by a unit test scanning the
built metadata document for any ``"prov:"``-prefixed key or ``@context``
entry.

--- No wall-clock timestamps (R1, an explicit invariant) ---------------------

The only timestamp this module ever emits is ``datePublished``, and its
value is always read from an already-loaded object body (the crate's own
``created_at`` field, itself set once, at seal time, by
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer`` — long
before an export ever runs) — never ``datetime.now()`` or any other clock
read. Calling ``build_ro_crate_metadata`` for the same crate body at two
different real-world moments produces byte-identical output.

--- The extension vocabulary URI: a disclosed placeholder (rule 14) ---------

``MRR_VOCAB_URI`` reuses the exact ``https://example.invalid`` authority
every schemas/*.schema.json ``$id`` already uses (e.g.
``https://example.invalid/mrr/schemas/evidence-crate.schema.json``),
narrowed to the schemas' own common ``/mrr/`` path segment and given a
sibling ``vocab#`` path — never a real, owned domain this project has not
actually registered. task-packets/E8-T01.yaml's own specification_gaps
names this exact choice as open ("until the owner's site-coupling decision
lands a real host") — flagged here again, at the point of use, rather than
guessed at as if it were a settled value (AGENTS.md rule 14).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from mrr.crypto.canonical import JSONValue, canonicalize

#: RO-Crate 1.1's own remote context document — pinned to an exact version so
#: a future 1.2 migration is an explicit, diffable change to this constant,
#: never a silent drift (task-packets/E8-T01.yaml specification_gaps).
RO_CRATE_CONTEXT_URI = "https://w3id.org/ro/crate/1.1/context"

#: The RO-Crate 1.1 profile URI the metadata file descriptor's ``conformsTo``
#: points at — distinct from ``RO_CRATE_CONTEXT_URI`` above (a JSON-LD
#: ``@context`` document vs. a profile identifier; RO-Crate 1.1 deliberately
#: uses two different URIs for the two purposes).
RO_CRATE_PROFILE_URI = "https://w3id.org/ro/crate/1.1"

#: See the module docstring's "The extension vocabulary URI" section.
MRR_VOCAB_URI = "https://example.invalid/mrr/vocab#"

#: The compact-IRI prefix declared for ``MRR_VOCAB_URI`` in every exported
#: metadata document's ``@context``.
MRR_VOCAB_PREFIX = "mrr"

#: Fixed root-level filename (task-packets/E8-T01.yaml R1) — never inside
#: ``objects/`` or ``artifacts/``, and never equal to any possible
#: ``object_relative_path``/``artifact_relative_path`` output (see the
#: module docstring's "File naming" section).
METADATA_FILE_NAME = "ro-crate-metadata.json"

#: The RO-Crate root data entity's own fixed ``@id`` (task-packets/
#: E8-T01.yaml R1: 'a root data entity "./"').
ROOT_DATA_ENTITY_ID = "./"

#: The ``sha256:`` prefix stripped when deriving an artifact's filename
#: (task-packets/E8-T01.yaml R1: ``artifacts/<64-hex>``, no scheme prefix).
_SHA256_PREFIX = "sha256:"

#: The one body field this module treats as "this object carries a
#: signature verbatim" (see the module docstring's "for the crate" note) —
#: read generically, not gated on any particular ``kind``.
_SIGNATURE_FIELD = "signature"


def object_relative_path(urn: str) -> str:
    """The fixed, deterministic export path for the MRR object identified by
    ``urn`` — see the module docstring's "File naming" section.
    """
    return f"objects/{urn.replace(':', '_')}.json"


def artifact_relative_path(content_hash: str) -> str:
    """The fixed, deterministic export path for the artifact whose content
    hash is ``content_hash`` (``sha256:<64 hex>``) — see the module
    docstring's "File naming" section. ``content_hash`` is assumed already
    schema-valid (every caller in this codebase sources it from an already-
    validated ``StoredObject.content_hash``/``ArtifactDescriptor
    .content_hash``/``ArtifactRef.content_hash`` field); this pure builder
    does not re-validate it.
    """
    return f"artifacts/{content_hash.removeprefix(_SHA256_PREFIX)}"


@dataclass(frozen=True, slots=True)
class ExportedObject:
    """One MRR object included in the export closure, already resolved to
    its own stored body and already reduced to the exact bytes
    ``mrr.services.export.service.ExportService`` will write for it.
    """

    urn: str
    body: Mapping[str, JSONValue]
    relative_path: str
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class ExportedArtifact:
    """One artifact included in the export closure. ``size_bytes`` is
    supplied by the caller (see the module docstring's "Object bodies
    supply their own file size; artifacts do not" section) — this module
    never sees the artifact's actual bytes.
    """

    content_hash: str
    relative_path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """The deterministic export plan (task-packets/E8-T01.yaml R1(a)):
    exactly which files an export produces, in sorted-urn / sorted-hash
    order (task-packets/E8-T01.yaml R2: "Inclusion is by URN set union,
    deduplicated, exported in sorted-URN order").
    """

    objects: tuple[ExportedObject, ...] = field(default_factory=tuple)
    artifacts: tuple[ExportedArtifact, ...] = field(default_factory=tuple)


def build_export_plan(
    object_bodies: Mapping[str, Mapping[str, JSONValue]],
    artifact_sizes: Mapping[str, int],
) -> ExportPlan:
    """Build the deterministic export plan from already-loaded object
    bodies and already-known artifact byte counts.

    Args:
        object_bodies: every MRR object to export, keyed by its own urn —
            e.g. ``{obj.id: obj.body for obj in closure}``, where each body
            is an already-persisted ``StoredObject.body`` (schema-conformant
            JSON per schemas/*.schema.json, carrying at least ``id``,
            ``kind``, ``content_hash``, ``practice_id``). Not mutated.
        artifact_sizes: every artifact to export, keyed by its own
            ``sha256:<hex>`` content hash, mapped to its raw byte count —
            e.g. ``{content_hash: len(data) for content_hash, data in
            fetched_bytes.items()}``. Not mutated.

    Returns:
        An ``ExportPlan`` whose ``objects``/``artifacts`` are sorted by urn
        / content hash respectively — calling this twice with equal
        arguments returns an equal plan both times (task-packets/
        E8-T01.yaml AT4).
    """
    objects = tuple(
        ExportedObject(
            urn=urn,
            body=object_bodies[urn],
            relative_path=object_relative_path(urn),
            canonical_bytes=canonicalize(object_bodies[urn]),
        )
        for urn in sorted(object_bodies)
    )
    artifacts = tuple(
        ExportedArtifact(
            content_hash=content_hash,
            relative_path=artifact_relative_path(content_hash),
            size_bytes=artifact_sizes[content_hash],
        )
        for content_hash in sorted(artifact_sizes)
    )
    return ExportPlan(objects=objects, artifacts=artifacts)


def _id_reference(relative_path: str) -> dict[str, JSONValue]:
    """A JSON-LD ``{"@id": ...}`` reference object — used both for
    ``hasPart`` entries and for ``about``/``conformsTo`` links below.
    """
    return {"@id": relative_path}


def _metadata_file_descriptor() -> dict[str, JSONValue]:
    return {
        "@id": METADATA_FILE_NAME,
        "@type": "CreativeWork",
        "conformsTo": {"@id": RO_CRATE_PROFILE_URI},
        "about": {"@id": ROOT_DATA_ENTITY_ID},
    }


def _root_data_entity(
    *, date_published: JSONValue, has_part: Sequence[JSONValue]
) -> dict[str, JSONValue]:
    return {
        "@id": ROOT_DATA_ENTITY_ID,
        "@type": "Dataset",
        "datePublished": date_published,
        "hasPart": list(has_part),
    }


def _object_file_entity(obj: ExportedObject) -> dict[str, JSONValue]:
    return {
        "@id": obj.relative_path,
        "@type": "File",
        "contentSize": len(obj.canonical_bytes),
        f"{MRR_VOCAB_PREFIX}:contentHash": obj.body["content_hash"],
        "about": {"@id": obj.urn},
    }


def _artifact_file_entity(artifact: ExportedArtifact) -> dict[str, JSONValue]:
    return {
        "@id": artifact.relative_path,
        "@type": "File",
        "contentSize": artifact.size_bytes,
        f"{MRR_VOCAB_PREFIX}:contentHash": artifact.content_hash,
    }


def _object_contextual_entity(obj: ExportedObject) -> dict[str, JSONValue]:
    kind = obj.body["kind"]
    entity: dict[str, JSONValue] = {
        "@id": obj.urn,
        "@type": f"{MRR_VOCAB_PREFIX}:{kind}",
        f"{MRR_VOCAB_PREFIX}:urn": obj.urn,
        f"{MRR_VOCAB_PREFIX}:kind": kind,
        f"{MRR_VOCAB_PREFIX}:contentHash": obj.body["content_hash"],
        f"{MRR_VOCAB_PREFIX}:practiceId": obj.body["practice_id"],
    }
    if _SIGNATURE_FIELD in obj.body:
        entity[f"{MRR_VOCAB_PREFIX}:signature"] = obj.body[_SIGNATURE_FIELD]
    return entity


def build_ro_crate_metadata(*, crate_urn: str, plan: ExportPlan) -> dict[str, JSONValue]:
    """Build the ``ro-crate-metadata.json`` document (task-packets/
    E8-T01.yaml R1(b)) for ``plan`` — see the module docstring's "The
    RO-Crate 1.1 document" section for the full entity-by-entity rationale.

    Args:
        crate_urn: the urn of the ``EvidenceCrate`` object among ``plan
            .objects`` — its ``created_at`` becomes ``datePublished`` (the
            module docstring's "No wall-clock timestamps" section).
        plan: the ``ExportPlan`` this metadata document describes — every
            file it names becomes a ``hasPart`` entry and a ``File``
            entity; every object in ``plan.objects`` becomes a contextual
            entity.

    Returns:
        A plain ``dict`` — no RO-Crate/JSON-LD library round trip anywhere.
        Calling this twice with equal arguments returns an equal dict both
        times (task-packets/E8-T01.yaml AT4).

    Raises:
        ValueError: ``crate_urn`` does not name any object in ``plan
            .objects`` — unreachable in practice, since
            ``mrr.services.export.service.ExportService`` always includes
            the crate itself in the closure it hands to this function; a
            plain ``if``/``raise`` (not a bare ``assert``) so this guard
            survives Python's optimized (``-O``) bytecode mode, matching
            ``mrr.domain.projection.build_claim_table_row``'s identical
            "unreachable" guard convention.
    """
    crate_candidates = [obj for obj in plan.objects if obj.urn == crate_urn]
    if not crate_candidates:
        raise ValueError(
            f"crate_urn {crate_urn!r} names no object in plan.objects — "
            "the crate itself must always be part of its own export plan"
        )
    crate = crate_candidates[0]

    has_part: list[JSONValue] = [_id_reference(obj.relative_path) for obj in plan.objects]
    has_part.extend(_id_reference(artifact.relative_path) for artifact in plan.artifacts)

    graph: list[JSONValue] = [
        _metadata_file_descriptor(),
        _root_data_entity(date_published=crate.body["created_at"], has_part=has_part),
    ]
    graph.extend(_object_file_entity(obj) for obj in plan.objects)
    graph.extend(_artifact_file_entity(artifact) for artifact in plan.artifacts)
    graph.extend(_object_contextual_entity(obj) for obj in plan.objects)

    return {
        "@context": [RO_CRATE_CONTEXT_URI, {MRR_VOCAB_PREFIX: MRR_VOCAB_URI}],
        "@graph": graph,
    }


def build_export(
    *,
    crate_urn: str,
    object_bodies: Mapping[str, Mapping[str, JSONValue]],
    artifact_sizes: Mapping[str, int],
) -> tuple[ExportPlan, dict[str, JSONValue]]:
    """Convenience composition of ``build_export_plan``/``build_ro_crate_
    metadata`` — the one call ``mrr.services.export.service.ExportService``
    makes once it has finished resolving the closure (task-packets/
    E8-T01.yaml R2) and fetching every artifact's bytes (R3).
    """
    plan = build_export_plan(object_bodies, artifact_sizes)
    metadata = build_ro_crate_metadata(crate_urn=crate_urn, plan=plan)
    return plan, metadata
