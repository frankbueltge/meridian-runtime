"""Pure, framework-free W3C PROV mapping for the RO-Crate export (task-
packets/E8-T02.yaml, MRR-FR-055's second half). Second task of Epic E8; the
direct precedent is ``mrr.domain.ro_crate`` (E8-T01, this module's own only
consumer) — read that module's docstring first for the RO-Crate document
shape this one supplements. The binding source of truth for every mapping
row below is docs/spec/02_DOMAIN_MODEL.md section 6 ("RO-Crate and PROV
mapping"), transcribed here verbatim plus the packet's own exhaustively-
fixed derived rows — nothing in this module maps a kind, a urn segment, or a
relation this exact source list does not name (AGENTS.md rule 3: "do not
invent domain behavior that is absent from the specification").

Like ``mrr.domain.ro_crate``, everything here is a pure function of its
arguments: no I/O, no network, no filesystem, no repository/service/adapter
import (task-packets/E8-T02.yaml R1: "same discipline as ro_crate.py, same
AT" — enforced independently by
tests/unit/architecture/test_prov_mapping_boundary.py, mirroring that
module's own ``test_ro_crate_boundary.py``). The one non-stdlib import
beyond ``mrr.crypto.canonical.JSONValue`` is ``mrr.domain.identity
.URN_PATTERN`` (a plain compiled regex, not I/O) and
``mrr.domain.projection.ProvenanceEdge`` — E3-T07's own plain, frozen,
already-I/O-free dataclass for one BFS hop (``source_id``/``target_id``/
``relation``/``via``/``target_kind``/``edge_id``). Reusing it here, rather
than inventing a parallel "edge" shape, avoids a second definition of
exactly the four fields R2(a) below needs (``source_id``/``target_id``/
``relation``/``via``) that could silently drift from the first; it is not a
repository type (no persistence import, no I/O), so importing it does not
weaken this module's own AT5 boundary.

--- The kind -> PROV-type table (R1) ------------------------------------------

``KIND_TO_PROV_TYPE`` is exactly docs/spec/02_DOMAIN_MODEL.md section 6's own
three bullet rows —

    Artifact, SourceRecord, Claim          -> prov:Entity
    RunManifest, Review, CorrectionEvent   -> prov:Activity
    Practice, Node, Person, AgentRole      -> prov:Agent

— transcribed character-for-character, PLUS task-packets/E8-T02.yaml
``derived_decisions`` (a), exhaustive:

    EvidenceCrate       -> prov:Entity   ("a sealed bundle of results is a
                                           thing, not a happening")
    EvidenceAnchor      -> prov:Entity
    VerificationResult  -> prov:Activity (docs/spec/02_DOMAIN_MODEL.md
                                           section 2.13's own words, "a
                                           verification records checks" —
                                           the same reading that puts Review
                                           in the spec's own Activity row)

Every kind outside these thirteen gets ``None`` from ``prov_type_for_kind``
— never guessed, never defaulted to the "closest" row. ``Review`` and
``CorrectionEvent`` are transcribed even though no closure this exporter can
currently build ever reaches one (task-packets/E8-T02.yaml derived_decisions
(b): "their table rows are still transcribed into the mapping module NOW,
verbatim, so the later packets inherit them rather than re-deriving them").
task-packets/E8-T02.yaml's own stop_conditions: if a closure kind ever
appears that neither the spec table nor derived_decisions (a) names, this
module gives it ``None`` and the gap is recorded in the packet report — this
module itself never raises or guesses for an unrecognized kind.

--- The URN-entity-segment fallback for stub entities (R1, R3) ---------------

A prov relation can reference a urn the R2 closure (task-packets/
E8-T01.yaml) never resolves into an exported object — e.g. a
``RunManifest.executor_id`` when the run itself is not part of this
particular export's closure. ``mrr.domain.ro_crate`` still needs SOME
``@type`` to put on that urn's stub contextual entity (R3), without ever
having read the object behind it. ``URN_ENTITY_SEGMENT_TO_PROV_TYPE`` is
task-packets/E8-T02.yaml R1's own exhaustive fallback, keyed on the urn's
OWN entity segment (``mrr.domain.identity.URN_PATTERN``'s ``(?P<entity>...)``
group — the same segment ``mrr.domain.identity.new_urn`` mints from and
``mrr.domain.ro_crate.object_relative_path`` substitutes ``:`` for ``_`` in),
never on any resolved body:

    person, agent-role, practice,
      node, executor                    -> prov:Agent
    run                                 -> prov:Activity
    artifact, source-record, claim,
      evidence-anchor                   -> prov:Entity

Every other segment -> ``None``. The ``"executor"`` row was NOT in the
packet's original R1 list: this codebase consistently mints ``RunManifest
.executor_id`` fixture urns with entity segment ``"executor"`` (every
fixture across tests/integration/services/node_runtime/test_evidence_crate
.py, tests/property/test_canonical_signed_form_properties.py, tests/unit/
domain/test_crate_trust*.py, and this packet's own extended tests/
integration/services/test_export_cli_ro_crate.py — never ``"agent-role"``
or ``"person"``; production archival runs use ``"node"``, already mapped).
The implementing session honored the packet's stop_conditions ("never guess
a vocabulary row mid-implementation") by first shipping NO type for it and
flagging the gap; the reviewing instance then AMENDED task-packets/
E8-T02.yaml (reviewer_resolution, AMENDMENT 2026-07-22) to add
``"executor" -> prov:Agent`` — spec-derived, not invented: section 6's own
"executor/reviewer relation -> prov:wasAssociatedWith" row names the
executor as the associated AGENT of an activity. The vocabulary decision
stayed a governance act; this table follows the amended packet text.

--- R2: relation-deriving pure functions, exactly rules (a)-(e) --------------

The spec's fourth and fifth mapping-table rows (``derived_from ->
prov:wasDerivedFrom``, "producer relation -> prov:wasGeneratedBy",
"executor/reviewer relation -> prov:wasAssociatedWith", "input relation ->
prov:used") name relation KINDS, not the exact stored fields that carry
them for each MRR object kind — task-packets/E8-T02.yaml R2 grounds every
one of the four in a SPECIFIC, already-verified stored field or typed edge,
and this module implements exactly those five grounded rules, nothing wider:

  (a) ``group_derived_from_targets`` / ``derived_from_relation`` — a typed
      edge (``ProvenanceEdge.via == "edge"``) whose ``relation ==
      "derived_from"`` becomes ``prov:wasDerivedFrom`` on the edge's OWN
      source entity, pointing at its target. Deliberately excludes a
      ``via == "field"`` hop even if some future field were ever also named
      ``"derived_from"`` (none is, today — ``mrr.services.projection
      .service._ANCHOR_FIELD_REFERENCES`` is exactly
      ``("source_record_id", "run_id")`` — but the spec row's own wording is
      "typed edge derived_from", not "any hop named derived_from").
  (b) ``run_manifest_relations`` — ``RunManifest.executor_id`` (schema-
      required) becomes ``prov:wasAssociatedWith``; every artifact urn named
      anywhere inside ``RunManifest.parameters`` (schemas/run-manifest
      .schema.json: ``{"type": "object"}``, genuinely open-ended —
      ``mrr.services.node_runtime.run_manifest.RunManifestRecorder.record``
      sets it verbatim from ``task_bundle.instructions``, itself an
      unconstrained JSON object) becomes ``prov:used``. **Disclosed judgment
      call**: since neither the schema nor the packet names a specific
      sub-field for "the run's own declared inputs", this module reads that
      phrase as "every string value reachable anywhere inside ``parameters``
      that is itself a syntactically valid MRR urn with entity segment
      ``artifact``" — a recursive scan of the whole JSON value tree
      (``_artifact_urns_in`` / ``_collect_artifact_urns``: dicts by value,
      lists by element, strings tested against ``URN_PATTERN``), sorted and
      deduplicated. Nothing is invented: every urn this reports is a string
      that was already, verbatim, sitting inside the stored body; a
      ``parameters`` object naming no artifact urn at all (this packet's own
      integration fixture, whose ``parameters`` is
      ``{"operation": "percentage", "numerator": 42, "denominator": 100}``)
      yields no ``prov:used`` property at all — never a fabricated one.
  (c) ``verification_result_relations`` — ``VerificationResult.reviewer_id``
      (schema-required) becomes ``prov:wasAssociatedWith``;
      ``VerificationResult.target_id`` (schema-required) UNION every
      ``evidence_inspected`` urn (schema-required array, may be empty)
      becomes ``prov:used``, deduplicated (task-packets/E8-T02.yaml
      derived_decisions (c): "target_id AND evidence_inspected — both are
      stored, first-class fields ... nothing else" — ``checks_performed``
      prose is deliberately never read as a relation).
  (d) artifact File entities' ``prov:wasGeneratedBy`` (``mrr.domain
      .ro_crate``'s own concern, since artifacts get File entities only,
      never a contextual entity — see that module's docstring) and
  (e) ``evidence_crate_relations`` — ``EvidenceCrate.run_id`` (schema-
      required) becomes ``prov:wasGeneratedBy`` on the crate's OWN
      contextual entity, and, via ``artifact_generated_by_relation``, on
      EVERY artifact File entity too (MRR-FR-050/051: "a crate's artifacts
      are its run's products" — the same ``run_id``, not a per-artifact
      producer field this schema does not carry).

``prov_relations_for_object`` is the one dispatcher ``mrr.domain.ro_crate``
calls per contextual entity — ``RunManifest``/``VerificationResult``/
``EvidenceCrate`` route to (b)/(c)/(e) above; every other kind, INCLUDING
``Claim``, returns ``{}``. This is deliberate, not an oversight: R2's own
preamble states the spec's "executor/reviewer relation ->
prov:wasAssociatedWith" row applies to ACTIVITIES only, and a ``Claim`` is a
PROV Entity (per the table above) — so ``Claim.proposer_id`` gets NO prov
relation in this packet, remaining visible only as the existing ``mrr:``
extension field ``mrr.domain.ro_crate`` already emits verbatim. A PROV
attribution row the spec table does not itself name is not invented here
merely because a plausible-looking field exists.

Every one of these functions reads its target field(s) with a defensive
``isinstance`` check, never a bare index — a synthetic test body missing an
"optional" field (AT2), or one whose value is not the expected shape,
yields an absent property rather than a ``KeyError``/``TypeError``: "absent
field -> absent property", never a fabricated or defaulted value
(task-packets/E8-T02.yaml invariant).

--- Relation-value representation: a disclosed, deterministic choice --------

Every returned relation property value is EITHER a single ``{"@id": ...}``
reference (``prov:wasAssociatedWith``, ``prov:wasGeneratedBy`` — each MRR
record this packet grounds has exactly one executor/reviewer/producing-run)
OR a list of ``{"@id": ...}`` references, ALWAYS a list even when it holds
exactly one element (``prov:wasDerivedFrom``, ``prov:used`` — both can
genuinely hold more than one target, e.g. a ``VerificationResult`` inspecting
several evidence anchors), sorted by urn for determinism. This mirrors
``mrr.domain.ro_crate``'s own instruction to "pick ONE deterministic
representation and document it" for its ``@type`` list-vs-string choice — a
consumer can rely on cardinality being predictable per PROPERTY NAME without
inspecting the value's shape at read time.

--- Stub-urn discovery: one generic extractor, not per-relation logic -------

``relation_target_urns`` reads any already-built relations ``dict`` (as
returned by any function above) and returns every ``@id`` value reachable
inside any of its ``"prov:"``-prefixed keys, regardless of whether that
key's value is a single reference or a list of them. ``mrr.domain.ro_crate``
calls this once per relations dict it builds and accumulates the union
across the whole export — the single place "which urns does this graph
reference via prov:" is computed, so a future R2 rule addition needs no
matching addition to the stub-discovery logic.

--- Determinism -------------------------------------------------------------

Every function in this module is a pure computation over its arguments,
performs no I/O, and depends on no wall clock, global state, or iteration
order it does not itself sort — calling any of them twice with equal
arguments returns an equal result both times (task-packets/E8-T02.yaml R5:
"the metadata document remains deterministic").
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from mrr.crypto.canonical import JSONValue
from mrr.domain.identity import URN_PATTERN
from mrr.domain.projection import ProvenanceEdge

#: The PROV-O namespace RO-Crate's own ``@context`` gains a second prefix
#: for (task-packets/E8-T02.yaml derived_decisions (d)) — PROV terms are
#: always written prefixed (``prov:wasDerivedFrom``, never a bare IRI or an
#: unprefixed term), matching this codebase's own ``mrr:`` convention in
#: ``mrr.domain.ro_crate``.
PROV_VOCAB_URI = "http://www.w3.org/ns/prov#"
PROV_VOCAB_PREFIX = "prov"

#: The exact four spec-named relations (docs/spec/02_DOMAIN_MODEL.md section
#: 6) this module ever emits — see the module docstring's "R2" section for
#: which stored field grounds each one.
PROV_WAS_DERIVED_FROM = f"{PROV_VOCAB_PREFIX}:wasDerivedFrom"
PROV_WAS_ASSOCIATED_WITH = f"{PROV_VOCAB_PREFIX}:wasAssociatedWith"
PROV_USED = f"{PROV_VOCAB_PREFIX}:used"
PROV_WAS_GENERATED_BY = f"{PROV_VOCAB_PREFIX}:wasGeneratedBy"

_PROV_ENTITY = "prov:Entity"
_PROV_ACTIVITY = "prov:Activity"
_PROV_AGENT = "prov:Agent"

#: See the module docstring's "The kind -> PROV-type table" section — spec
#: rows verbatim, plus task-packets/E8-T02.yaml derived_decisions (a),
#: exhaustive. Every kind not listed here maps to ``None``
#: (``prov_type_for_kind``) — never guessed.
KIND_TO_PROV_TYPE: Mapping[str, str] = {
    # docs/spec/02_DOMAIN_MODEL.md section 6, verbatim.
    "Artifact": _PROV_ENTITY,
    "SourceRecord": _PROV_ENTITY,
    "Claim": _PROV_ENTITY,
    "RunManifest": _PROV_ACTIVITY,
    "Review": _PROV_ACTIVITY,
    "CorrectionEvent": _PROV_ACTIVITY,
    "Practice": _PROV_AGENT,
    "Node": _PROV_AGENT,
    "Person": _PROV_AGENT,
    "AgentRole": _PROV_AGENT,
    # task-packets/E8-T02.yaml derived_decisions (a), exhaustive.
    "EvidenceCrate": _PROV_ENTITY,
    "EvidenceAnchor": _PROV_ENTITY,
    "VerificationResult": _PROV_ACTIVITY,
}

#: See the module docstring's "The URN-entity-segment fallback" section —
#: task-packets/E8-T02.yaml R1 as amended 2026-07-22 (the ``"executor"``
#: row's history lives there), exhaustive. Every segment not listed here
#: maps to ``None`` (``prov_type_for_urn``).
URN_ENTITY_SEGMENT_TO_PROV_TYPE: Mapping[str, str] = {
    "person": _PROV_AGENT,
    "agent-role": _PROV_AGENT,
    "practice": _PROV_AGENT,
    "node": _PROV_AGENT,
    # task-packets/E8-T02.yaml reviewer_resolution AMENDMENT 2026-07-22 —
    # see the module docstring's fallback section for the full history.
    "executor": _PROV_AGENT,
    "run": _PROV_ACTIVITY,
    "artifact": _PROV_ENTITY,
    "source-record": _PROV_ENTITY,
    "claim": _PROV_ENTITY,
    "evidence-anchor": _PROV_ENTITY,
}

#: The three object kinds R2(b)/(c)/(e) grounds a relation-deriving function
#: for — every other kind routes to ``{}`` in ``prov_relations_for_object``.
_RUN_MANIFEST_KIND = "RunManifest"
_VERIFICATION_RESULT_KIND = "VerificationResult"
_EVIDENCE_CRATE_KIND = "EvidenceCrate"

#: R2(a): the one typed-edge type this module ever maps to a prov relation.
_DERIVED_FROM_EDGE_TYPE = "derived_from"

#: R2(b): the urn entity segment ``_artifact_urns_in`` looks for inside a
#: ``RunManifest``'s ``parameters`` object.
_ARTIFACT_ENTITY_SEGMENT = "artifact"

#: The one ``ProvenanceEdge.via`` value R2(a) accepts (see the module
#: docstring's "(a)" bullet for why a field-reference hop never qualifies).
_EDGE_HOP_KIND = "edge"


# ---------------------------------------------------------------------------
# Kind / urn -> PROV type.
# ---------------------------------------------------------------------------


def prov_type_for_kind(kind: str) -> str | None:
    """The PROV type for an MRR object ``kind`` (``KIND_TO_PROV_TYPE``), or
    ``None`` if ``kind`` names no row in that table — never guessed.
    """
    return KIND_TO_PROV_TYPE.get(kind)


def prov_type_for_urn(urn: str) -> str | None:
    """The PROV type for a urn's OWN entity segment
    (``URN_ENTITY_SEGMENT_TO_PROV_TYPE``), read straight from the urn string
    itself — no resolved body is ever consulted (there may not be one; this
    is exactly the R3 stub case). ``None`` if ``urn`` does not match
    ``mrr.domain.identity.URN_PATTERN`` at all, or its entity segment names
    no fallback row.
    """
    match = URN_PATTERN.match(urn)
    if match is None:
        return None
    return URN_ENTITY_SEGMENT_TO_PROV_TYPE.get(match.group("entity"))


# ---------------------------------------------------------------------------
# ``{"@id": ...}`` reference helpers.
# ---------------------------------------------------------------------------


def _id_ref(urn: str) -> dict[str, JSONValue]:
    return {"@id": urn}


def _id_refs(urns: Iterable[str]) -> list[JSONValue]:
    """A sorted, deduplicated list of ``{"@id": ...}`` references — see the
    module docstring's "Relation-value representation" section for why this
    is ALWAYS a list, even for a single urn.
    """
    return [_id_ref(urn) for urn in sorted(set(urns))]


# ---------------------------------------------------------------------------
# R2(b)/(c)/(e): per-kind relation-deriving functions.
# ---------------------------------------------------------------------------


def run_manifest_relations(body: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """R2(b): ``prov:wasAssociatedWith`` from ``executor_id``,
    ``prov:used`` from every artifact urn ``_artifact_urns_in`` finds inside
    ``parameters`` — see the module docstring's "(b)" bullet. Either
    property is entirely absent if its underlying field is absent, empty,
    or (for ``executor_id``) not a non-empty string.
    """
    relations: dict[str, JSONValue] = {}

    executor_id = body.get("executor_id")
    if isinstance(executor_id, str) and executor_id:
        relations[PROV_WAS_ASSOCIATED_WITH] = _id_ref(executor_id)

    used_urns = _artifact_urns_in(body.get("parameters"))
    if used_urns:
        relations[PROV_USED] = _id_refs(used_urns)

    return relations


def verification_result_relations(body: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """R2(c): ``prov:wasAssociatedWith`` from ``reviewer_id``, ``prov:used``
    from ``target_id`` UNION ``evidence_inspected`` (task-packets/
    E8-T02.yaml derived_decisions (c)) — see the module docstring's "(c)"
    bullet.
    """
    relations: dict[str, JSONValue] = {}

    reviewer_id = body.get("reviewer_id")
    if isinstance(reviewer_id, str) and reviewer_id:
        relations[PROV_WAS_ASSOCIATED_WITH] = _id_ref(reviewer_id)

    used_urns: set[str] = set()
    target_id = body.get("target_id")
    if isinstance(target_id, str) and target_id:
        used_urns.add(target_id)
    used_urns.update(_string_sequence(body.get("evidence_inspected")))
    if used_urns:
        relations[PROV_USED] = _id_refs(used_urns)

    return relations


def evidence_crate_relations(body: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """R2(e): ``prov:wasGeneratedBy`` from ``run_id`` — see the module
    docstring's "(e)" bullet. This is the crate's OWN contextual entity's
    relation; ``artifact_generated_by_relation`` below derives the SAME
    ``run_id`` for the crate's artifact File entities.
    """
    relations: dict[str, JSONValue] = {}
    run_id = body.get("run_id")
    if isinstance(run_id, str) and run_id:
        relations[PROV_WAS_GENERATED_BY] = _id_ref(run_id)
    return relations


def prov_relations_for_object(kind: str, body: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """The single dispatcher ``mrr.domain.ro_crate`` calls per contextual
    entity: routes ``RunManifest``/``VerificationResult``/``EvidenceCrate``
    to the three functions above; every other kind — INCLUDING ``Claim`` —
    returns ``{}`` (see the module docstring's own paragraph on why
    ``Claim.proposer_id`` deliberately gets no relation in this packet).
    """
    if kind == _RUN_MANIFEST_KIND:
        return run_manifest_relations(body)
    if kind == _VERIFICATION_RESULT_KIND:
        return verification_result_relations(body)
    if kind == _EVIDENCE_CRATE_KIND:
        return evidence_crate_relations(body)
    return {}


# ---------------------------------------------------------------------------
# R2(a): typed-edge derived_from -> prov:wasDerivedFrom.
# ---------------------------------------------------------------------------


def group_derived_from_targets(edges: Iterable[ProvenanceEdge]) -> dict[str, tuple[str, ...]]:
    """Every ``derived_from`` TYPED EDGE (``via == "edge"``; a field
    reference never qualifies — see the module docstring's "(a)" bullet)
    among ``edges``, grouped by its own ``source_id`` into a sorted,
    deduplicated tuple of target urns. ``mrr.domain.ro_crate`` looks up one
    object's own urn in the returned mapping (``.get(obj.urn, ())``) to
    build that object's ``prov:wasDerivedFrom`` property via
    ``derived_from_relation`` below.
    """
    by_source: dict[str, set[str]] = {}
    for edge in edges:
        if edge.via != _EDGE_HOP_KIND or edge.relation != _DERIVED_FROM_EDGE_TYPE:
            continue
        by_source.setdefault(edge.source_id, set()).add(edge.target_id)
    return {source_id: tuple(sorted(targets)) for source_id, targets in by_source.items()}


def derived_from_relation(target_urns: Sequence[str]) -> dict[str, JSONValue]:
    """``{"prov:wasDerivedFrom": [...]}`` for a non-empty ``target_urns``
    (typically one entry of ``group_derived_from_targets``'s own result),
    or ``{}`` if ``target_urns`` is empty — "absent field -> absent
    property" applies here too: an object with no outgoing ``derived_from``
    edge gets no ``prov:wasDerivedFrom`` property at all.
    """
    if not target_urns:
        return {}
    return {PROV_WAS_DERIVED_FROM: _id_refs(target_urns)}


# ---------------------------------------------------------------------------
# R2(d): artifact File entities -> prov:wasGeneratedBy the crate's run_id.
# ---------------------------------------------------------------------------


def artifact_generated_by_relation(run_id: str | None) -> dict[str, JSONValue]:
    """``{"prov:wasGeneratedBy": {"@id": run_id}}`` for every artifact File
    entity of a crate carrying ``run_id``, or ``{}`` if ``run_id`` is
    ``None``/empty (task-packets/E8-T02.yaml R2(d): "artifact File
    entities: prov:wasGeneratedBy the crate's own run_id").
    """
    if not run_id:
        return {}
    return {PROV_WAS_GENERATED_BY: _id_ref(run_id)}


# ---------------------------------------------------------------------------
# R3: which urns does a built relations dict reference?
# ---------------------------------------------------------------------------


def relation_target_urns(relations: Mapping[str, JSONValue]) -> frozenset[str]:
    """Every ``@id`` value reachable inside any ``"prov:"``-prefixed key of
    ``relations`` (as returned by any function above), regardless of
    whether that key's value is a single ``{"@id": ...}`` reference or a
    list of them — see the module docstring's "Stub-urn discovery" section.
    ``mrr.domain.ro_crate`` accumulates the union of this across the whole
    export to know which referenced urns need an R3 stub contextual entity
    (every urn NOT already an exported object's own urn).
    """
    urns: set[str] = set()
    for key, value in relations.items():
        if key.startswith(f"{PROV_VOCAB_PREFIX}:"):
            urns.update(_ids_in(value))
    return frozenset(urns)


def _ids_in(value: JSONValue) -> set[str]:
    if isinstance(value, Mapping):
        urn = value.get("@id")
        return {urn} if isinstance(urn, str) else set()
    if isinstance(value, Sequence) and not isinstance(value, str):
        found: set[str] = set()
        for item in value:
            found.update(_ids_in(item))
        return found
    return set()


# ---------------------------------------------------------------------------
# Reading urn strings out of an already-stored, schema-open JSON value.
# ---------------------------------------------------------------------------


def _string_sequence(value: JSONValue | None) -> tuple[str, ...]:
    """Every STRING element of ``value`` if it is a list/tuple, else an
    empty tuple — a defensive reading of a body field that SHOULD be a
    schema array of urn strings (e.g. ``VerificationResult
    .evidence_inspected``): a non-list value or a non-string element is
    skipped, never raised on (this module re-validates nothing a caller's
    own persistence layer already enforces at write time).
    """
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _artifact_urns_in(value: JSONValue | None) -> tuple[str, ...]:
    """Every distinct string, reachable anywhere inside ``value`` (dict
    values, list elements, recursively), that is itself a syntactically
    valid MRR urn with entity segment ``"artifact"`` — sorted. See the
    module docstring's "(b)" bullet for why this recursive scan, rather than
    a single named field, is how R2(b)'s "every artifact URN its parameters
    name" is computed against ``RunManifest.parameters``'s genuinely
    schema-open ``{"type": "object"}`` shape.
    """
    found: set[str] = set()
    _collect_artifact_urns(value, found)
    return tuple(sorted(found))


def _collect_artifact_urns(value: JSONValue | None, found: set[str]) -> None:
    if isinstance(value, str):
        match = URN_PATTERN.match(value)
        if match is not None and match.group("entity") == _ARTIFACT_ENTITY_SEGMENT:
            found.add(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            _collect_artifact_urns(item, found)
    elif isinstance(value, Sequence):
        for item in value:
            _collect_artifact_urns(item, found)
