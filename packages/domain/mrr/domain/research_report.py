"""Pure, framework-free research-report projection (task-packets/E8-T03.yaml,
docs/spec/01_SYSTEM_SPEC.md Stage 11 — MRR-FR-100/-101/-104 — plus MRR-FR-095
and section 7.9 "Projection Service"). Third task of Epic E8; the closest
templates are ``mrr.domain.ro_crate`` (E8-T01/T02 — the pure-shaping/
I/O-performing service split, the module-docstring density, and above all
"no wall-clock anywhere in the rendered bytes") and ``mrr.domain.
public_correction_view`` (E6-T05 — the fail-closed, caller-supplied-
attestation redaction rule THIS module reuses verbatim rather than forking).
Read both of those modules' docstrings first.

This module defines the frozen report model (a tree of ``dataclass(frozen=
True, slots=True)`` records rooted at :class:`ResearchReport`) and the two
pure renderers, :func:`render_markdown`/:func:`render_html`. Building the
model (:func:`build_report`) and rendering it are BOTH pure: no I/O, no
repository/service/adapter import (task-packets/E8-T03.yaml R1: "no
repository types"; enforced independently by tests/unit/architecture/
test_research_report_boundary.py, mirroring test_ro_crate_boundary.py), no
network, no filesystem, no model/LLM call anywhere (task-packets/E8-T03.yaml
derived_decisions (b): "both prohibitions of MRR-FR-104 are discharged
structurally"), and no wall-clock read — the ONLY date/time string either
renderer ever emits is the crate's own stored ``created_at``, read straight
out of an already-fetched object body, exactly as ``mrr.domain.ro_crate
.build_ro_crate_metadata``'s own ``datePublished`` already does for the
export. Calling ``build_report``/``render_markdown``/``render_html`` twice
with equal arguments returns byte-identical results (task-packets/
E8-T03.yaml AT4) — every dict this module reads is iterated in a SORTED
order it establishes itself, never trusting a caller's dict/set iteration
order.

The companion I/O-performing half — resolving the crate's export closure
(``mrr.services.export.service.ExportService.resolve_closure``, R2's own
extraction), discovering corrections (``mrr.services.projection.service
.ProjectionService.build_public_correction_view``) and per-claim provenance
(``.build_provenance_map``), and calling :func:`build_report` once with the
results already in hand — is ``mrr.services.report.service.ReportService``.
That split mirrors ``mrr.domain.ro_crate``/``mrr.services.export.service``
exactly: this module never resolves a urn, walks an edge, or reads a
database; the service performs every one of those I/O-bound steps and hands
this module already-loaded ``Mapping[str, JSONValue]`` bodies.

--- Why this module owns the report's OWN redaction too, not just corrections ---

R2's own text says ``ReportService`` composes ``ProjectionService`` "for
corrections discovery" — and, per this module's design, ``ReportService``
calls ``ProjectionService.build_public_correction_view`` (task-packets/
E6-T05.yaml, itself already fail-closed) to get the crate's unresolved
critical corrections, PRE-REDACTED for whichever disclosure is being
rendered, and hands the resulting ``PublicCorrectionRow`` sequence straight
into :func:`build_report` as a plain argument — see :data:`ResearchReport
.corrections`, which stores that reused type DIRECTLY rather than
re-declaring an identical-shaped dataclass a second time. That covers
CorrectionEvent free text (``reason``/``requested_action``) completely,
reusing ``mrr.domain.public_correction_view``'s functions verbatim, per
task-packets/E8-T03.yaml R3 and stop_condition 1 ("never fork a second
redaction rule").

But R3's own free-text enumeration is WIDER than what ``public_correction_
view`` was ever scoped to cover (that module's own docstring names exactly
two categories: "the two CorrectionEvent free-text fields ... and the one
Claim free-text field"): R3 additionally names finding statements
(``VerificationResult.findings[].statement``), failure messages
(``EvidenceCrate.failures[].message``), and known-unknown strings
(``EvidenceCrate.known_unknowns``/``Claim.known_unknowns``). No function in
``public_correction_view`` redacts any of those three, and that module is
FORBIDDEN to edit (task-packets/E8-T03.yaml forbidden_changes) — adding
functions there is not available either way. This module therefore restates
the IDENTICAL fail-closed formula locally (:func:`_all_attested_public`
below: "every object id a piece of text depends on must equal the literal
string 'PUBLIC' in the caller-supplied attestation map, or the text is
withheld" — byte-for-byte the same rule ``public_correction_view
._all_ids_attested_public`` implements, cited here rather than imported
since that name is module-private) for these three additional categories,
and for the ONE category ``public_correction_view`` DOES cover that still
needs a fresh call site here — the Claim assertion — this module calls that
module's own exported ``build_public_claim_row`` directly (see
:func:`_build_claim_row` below), constructing the ``ClaimTableRow`` it
expects from the already-loaded claim body plus this claim's own discovered
``unresolved_correction_ids`` (derived from the ``corrections`` argument,
never re-derived by a second, competing correction-discovery pass). This is
NOT "forking a second redaction rule" in the sense stop_condition 1
prohibits (a competing definition of "unresolved critical correction" or of
"is this text public") — the formula is one formula, cited at every use
site, and the ONE function ``public_correction_view`` already exports for
exactly this Claim-assertion case is called, not reimplemented. It is
extending the SAME formula to field categories that module never claimed to
own, because R3's own text directs exactly that extension for THIS report.
Flagged here for reviewer scrutiny, and again in the packet report.

--- One code path for both disclosures: the "always PUBLIC" attestation ----

R3: "internal renders every stored string." Rather than branching every
redaction call site on ``disclosure`` (one branch that redacts, one that
does not — two things that could silently drift apart), this module drives
the SAME fail-closed check through its own non-redacting branch for internal
disclosure, by attesting every object id "PUBLIC" — :class:`
AlwaysPublicAttestation`/:data:`ALWAYS_PUBLIC_ATTESTATION`. ``build_report``
uses this attestation internally whenever ``disclosure == "internal"``,
ignoring whatever ``classification_by_object_id`` the caller passed (task-
packets/E8-T03.yaml R4: "``--classification-file`` ... FORBIDDEN when
internal" — the CLI never even lets a caller supply one for this path).
``mrr.services.report.service.ReportService`` reuses this SAME exported
attestation for its own ``build_public_correction_view`` call when
rendering internal disclosure, for the identical reason — ONE "always
public" stand-in, not two independently-written ones that could diverge.

--- What is never redacted -------------------------------------------------

Identifiers, content hashes, statuses, and counts are never subject to any
of the above (task-packets/E8-T03.yaml R3: "identifiers and hashes are never
redacted, they are the record's spine"; also true of every ``uncertainty[]
.statement``/``Claim.scope`` field and every ``evidence_relations``/
``counterevidence_relations``/``dependencies`` urn list — none of those five
appear in R3's own free-text enumeration, so none of them is gated here;
inventing a sixth gated category the packet's own text does not name would
be inventing unstated scope, AGENTS.md rule 3). An unresolved critical
correction's existence and status are ALSO never redacted, in either
disclosure (MRR-FR-095, R3: "outranks redaction for their existence and
status") — this holds by construction here, since ``PublicCorrectionRow``'s
own structural fields (``correction_id``/``correction_type``/``severity``/
``status``/``affected_object_ids``/``impact_object_ids``/``unresolved``) are
ALWAYS populated regardless of ``redacted`` (see that module's own "What
redaction never touches" docstring section) and this module renders every
one of them unconditionally.

--- Sections: fixed order, always present (R1) -----------------------------

Exactly the eight R1 sections, in the fixed order R1 lists them, every
render (:func:`render_markdown`/:func:`render_html`): (1) header, (2)
methods, (3) claim table, (4) evidence map, (5) corrections, (6) known
unknowns, (7) failures, (8) provenance summary. An empty section/subsection
never disappears — it renders the literal line ``"(none recorded)"``
(:data:`_NONE_RECORDED`) instead (MRR-FR-104's "MUST NOT omit" made
structural, task-packets/E8-T03.yaml R1/R5). R5's own two structural tests
— "the corrections section cannot be omitted" and "rendered_urns ⊆
closure_urns" — are asserted in tests/unit/domain/test_research_report.py
directly against this module's own output.

--- No invented citations: ``source_urns`` (R5, AT3) -----------------------

:data:`ResearchReport.source_urns` is the exact set of every urn this module
ever READ FROM: every ``object_bodies`` key (a closure member's own
identity) PLUS every urn-shaped STRING VALUE reachable anywhere inside any
``object_bodies`` entry's OWN stored body (dict values, list elements,
recursively — :func:`_collect_urns`, the identical recursive-scan technique
``mrr.domain.prov_mapping._collect_artifact_urns`` already uses for the same
"is this string, verbatim, a urn this stored body already named" question)
UNION every id carried by the supplied ``corrections`` (``correction_id``/
``affected_object_ids``/``impact_object_ids``). This is deliberately WIDER
than "closure object_bodies keys alone": a crate's own ``run_id``/
``practice_id`` header fields, or a claim's own declared
``evidence_relations``/``counterevidence_relations``/``dependencies`` urn
lists, are honest, verbatim transcriptions of what an ALREADY-INCLUDED
stored body itself says — not invented, even when the urn they name is not
SEPARATELY resolved as its own object in this closure (a real, disclosed
limitation of what this export can independently verify, not a fabrication
by this renderer). What this set can NEVER contain is a urn that appears
nowhere in any actually-read stored value — R5's own mechanical test
extracts every urn-shaped token from BOTH rendered documents (a plain regex
scan, independent of this module's own bookkeeping) and asserts it is a
subset of ``source_urns``, with a control assertion proving the check can
fail (a foreign urn spliced into a COPY of the rendered text, matching
nothing any closure body or correction row ever stated).

--- Markdown avoids pipe tables; HTML uses them (a disclosed choice) -------

The Markdown renderer never emits a ``|``-delimited table: a stored free-text
string containing a literal ``|`` or a leading ``#``/``-`` would otherwise be
able to corrupt the document's OWN structure (Markdown has no analogue of
HTML's escaping — there is no ``&pipe;``). Every row is instead a labeled,
indented bullet block ("``- **Field:** value``"), which no stored string can
break out of. The HTML renderer DOES use ``<table>`` for the claim/
verification/correction/evidence-map rows: HTML's escaping
(:func:`_escape_html`, ``&``/``<``/``>``/``"``/``'``, applied to EVERY stored
string before it is ever concatenated into a tag) fully neutralizes
structural injection risk that Markdown has no comparable defense against, so
the more readable tabular form is used there instead. ``_escape_html`` is a
five-character substitution table, not "a markdown/html library" (task-
packets/E8-T03.yaml R1(b)'s prohibition) — it parses nothing and renders
nothing, it only makes a string safe to place inside markup this module
itself, by hand, already decided to emit.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from mrr.crypto.canonical import JSONValue, canonicalize
from mrr.domain.artifacts import Classification
from mrr.domain.identity import URN_PATTERN
from mrr.domain.projection import ClaimTableRow, ProvenanceEdge
from mrr.domain.public_correction_view import PublicCorrectionRow, build_public_claim_row

#: task-packets/E8-T03.yaml R3/R4: the report ships exactly these two
#: disclosure projections; ``partner-restricted`` (MRR-FR-103's third) is
#: this packet's own named gap (derived_decisions (c)) — not implemented.
Disclosure = Literal["internal", "public"]

#: The literal line every empty section/subsection renders instead of
#: disappearing (MRR-FR-104's "MUST NOT omit", made structural).
_NONE_RECORDED = "(none recorded)"

#: The literal marker text substituted for a piece of gated free text
#: whenever it is not attested PUBLIC. ``mrr.domain.public_correction_view``
#: itself defines no marker STRING (it only exposes a ``redacted: bool`` for
#: its own two row types) — this module is the first caller that renders
#: human-facing text at all, so the exact wording is this module's own
#: disclosed choice (task-packets/E8-T03.yaml specification_gaps' own
#: precedent of "no spec-given layout — this packet's own choice, noted").
_REDACTED_MARKER = "[redacted: not attested PUBLIC]"

#: The one literal value that unlocks disclosure — mirrors
#: ``mrr.domain.public_correction_view``'s own ``_PUBLIC`` constant exactly
#: (that name is module-private, so it is not imported; the value is the
#: same schema-declared literal in both places, never re-typed by accident
#: since both are checked against ``mrr.domain.artifacts.Classification``'s
#: own ``Literal`` membership by mypy).
_PUBLIC: Classification = "PUBLIC"


class AlwaysPublicAttestation(Mapping[str, Classification]):
    """A ``classification_by_object_id`` stand-in that reports EVERY object
    id as attested ``"PUBLIC"`` — see the module docstring's "One code path
    for both disclosures" section. Used only to drive the fail-closed
    redaction formula (both this module's own :func:`_all_attested_public`
    and ``mrr.domain.public_correction_view``'s reused functions) through
    their non-redacting branch for ``disclosure == "internal"``, so internal
    and public disclosure share exactly one code path per redacted field
    rather than a second, separately-written "show everything" branch.

    ``__len__``/``__iter__`` report an empty domain rather than pretending
    to enumerate an infinite one — only ``Mapping.get``/``__getitem__`` (the
    ``collections.abc.Mapping`` mixin's own ``get`` calls ``__getitem__``)
    are ever actually invoked by any caller of this class.
    """

    def __getitem__(self, key: str) -> Classification:
        return _PUBLIC

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


#: The ready-to-use singleton every internal-disclosure code path in this
#: module (and ``mrr.services.report.service.ReportService``, for its own
#: ``build_public_correction_view`` call) shares — see
#: :class:`AlwaysPublicAttestation`'s own docstring.
ALWAYS_PUBLIC_ATTESTATION: Mapping[str, Classification] = AlwaysPublicAttestation()


def _all_attested_public(
    object_ids: Iterable[str], classification_by_object_id: Mapping[str, Classification]
) -> bool:
    """``True`` iff every id in ``object_ids`` is attested, in
    ``classification_by_object_id``, as exactly the literal string
    ``"PUBLIC"`` — the identical fail-closed formula ``mrr.domain
    .public_correction_view._all_ids_attested_public`` implements (that name
    is module-private, hence restated rather than imported; see the module
    docstring's "Why this module owns the report's own redaction too"
    section). A missing key or any other value — one of the four other
    declared ``Classification`` levels, or an unrecognized string — fails
    this check identically; there is no special case for "unrecognized".
    """
    return all(classification_by_object_id.get(object_id) == _PUBLIC for object_id in object_ids)


def _redact(
    text: str,
    *,
    dependency_ids: tuple[str, ...],
    classification_by_object_id: Mapping[str, Classification],
) -> tuple[str, bool]:
    """``(text, False)`` if every id in ``dependency_ids`` is attested
    PUBLIC, else ``(_REDACTED_MARKER, True)``. The one call site every
    gated-free-text field in this module (findings, failures, known
    unknowns) routes through, so the marker text and the fail-closed check
    can never drift apart between fields.
    """
    if _all_attested_public(dependency_ids, classification_by_object_id):
        return text, False
    return _REDACTED_MARKER, True


# ---------------------------------------------------------------------------
# Small, defensive readers over an already-schema-validated ``JSONValue`` —
# every ``object_bodies`` entry this module reads is a stored body that
# already passed its own schema at write time (task-packets/E8-T03.yaml's
# own closure guarantee, inherited from ``ExportService.resolve_closure``),
# so these never silently substitute a default for a genuinely malformed
# value (AGENTS.md rule 12: "no silent exception handling") — they narrow
# ``JSONValue``'s wide union for mypy strict, and raise a plain, explicit
# ``ValueError`` on a shape violation that should be structurally
# impossible given that guarantee, exactly like ``mrr.domain.ro_crate
# .build_ro_crate_metadata``'s own "unreachable in practice" guard.
# ---------------------------------------------------------------------------


def _as_mapping(value: JSONValue | None) -> Mapping[str, JSONValue]:
    """``value`` narrowed to a ``Mapping``, or ``{}`` if ``value`` is
    ``None`` or an empty-equivalent JSON value — used only for genuinely
    OPTIONAL object-valued fields (e.g. ``Claim.scope``, ``EvidenceCrate
    .environment``), never to paper over a present-but-wrong-shaped value.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"expected a JSON object, got {value!r}")
    return value


def _as_object_sequence(value: JSONValue | None) -> Sequence[Mapping[str, JSONValue]]:
    """``value`` narrowed to a sequence of JSON objects — used for every
    schema array-of-object field this module reads (``uncertainty``,
    ``findings``, ``failures``, ``artifacts``). ``None`` (an absent optional
    array) reads as empty; any element that is not itself a ``Mapping``
    raises, since a schema-valid array-of-object field never contains one.
    """
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(f"expected a JSON array, got {value!r}")
    result: list[Mapping[str, JSONValue]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"expected a JSON object array element, got {item!r}")
        result.append(item)
    return result


def _as_string_sequence(value: JSONValue | None) -> Sequence[str]:
    """``value`` narrowed to a sequence of strings (``evidence_relations``,
    ``dependencies``, ``known_unknowns``, ``input_hashes``, ...). ``None``
    reads as empty.
    """
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError(f"expected a JSON array, got {value!r}")
    return tuple(str(item) for item in value)


def _as_float(value: JSONValue) -> float:
    """``value`` narrowed to ``float`` — used only for ``VerificationResult
    .confidence`` (schema type ``number``). ``bool`` is explicitly rejected
    even though Python's ``bool`` is an ``int`` subtype (and would otherwise
    silently coerce to ``0.0``/``1.0``), matching this codebase's own
    "``True``/``False`` is never a number" stance elsewhere.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)


def _collect_urns(value: JSONValue, found: set[str]) -> None:
    """Recursively accumulate every string in ``value`` that is itself,
    wholly, a valid MRR urn (``mrr.domain.identity.URN_PATTERN``) into
    ``found`` — the identical recursive-scan shape ``mrr.domain
    .prov_mapping._collect_artifact_urns`` already uses for the same "is
    this stored string a urn" question, applied here to every string
    reachable inside a JSON value rather than only ones matching one
    specific entity segment. See :func:`build_report`'s own ``source_urns``
    computation and the module docstring's "No invented citations" section.
    """
    if isinstance(value, str):
        if URN_PATTERN.match(value) is not None:
            found.add(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            _collect_urns(item, found)
    elif isinstance(value, Sequence):
        for item in value:
            _collect_urns(item, found)


# ---------------------------------------------------------------------------
# The report model — a tree of frozen, slotted dataclasses.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeaderSection:
    """R1(1). Every field is read verbatim from the crate's own stored
    body — none of it is ever redacted (identifiers/hashes/statuses/counts,
    per the module docstring's "What is never redacted" section).
    ``created_at`` is the ONLY date/time string either renderer ever emits
    anywhere in the document (task-packets/E8-T03.yaml invariant).
    """

    crate_urn: str
    run_urn: str
    run_state: str
    practice_id: str
    created_at: str
    content_hash: str
    object_count: int
    artifact_count: int


@dataclass(frozen=True, slots=True)
class ArtifactRefRow:
    """One entry of the crate's own ``artifacts`` (``ArtifactRef``) array —
    "artifact urns with hashes" (R1(2)). Always available: ``EvidenceCrate
    .artifacts`` is schema-required, so this needs no ``RunManifest``
    resolution at all (unlike :data:`MethodsSection.declared_parameters`
    below).
    """

    artifact_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class MethodsSection:
    """R1(2): "the run's declared protocol/parameters artifact urns with
    hashes and the environment block ... verbatim". Read as three parts
    (disclosed reading, flagged for reviewer scrutiny — R1's own prose
    admits more than one parse; see the module's packet report for the
    alternative considered): (a) ``RunManifest.parameters``, rendered
    verbatim when the ``RunManifest`` object itself is part of THIS crate's
    export closure (it is reached only via the R2(c) field-reference BFS
    from an included ``EvidenceAnchor.run_id`` — not guaranteed for every
    crate) — ``declared_parameters``/``run_manifest_included`` report this
    honestly rather than assuming resolution; (b) the crate's own
    ``artifacts`` array, always available regardless of (a) — see
    :class:`ArtifactRefRow`; (c) the crate's own ``environment`` block
    (``image_digest``/``code_revision``/``input_hashes``, plus
    ``model_profiles`` if the body carries it), schema-required and
    therefore always available too. Nothing here is ever redacted — none of
    it is free text R3 names.
    """

    run_urn: str
    run_manifest_included: bool
    declared_parameters: Mapping[str, JSONValue] | None
    artifact_refs: tuple[ArtifactRefRow, ...]
    environment_image_digest: str
    environment_code_revision: str
    environment_input_hashes: tuple[str, ...]
    environment_model_profiles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UncertaintyRow:
    """One ``Claim.uncertainty[]`` entry. Never redacted: ``statement`` is
    not among R3's own enumerated free-text categories (the module
    docstring's "What is never redacted" section) — a disclosed, literal
    reading of R3's own exhaustive list, not an oversight.
    """

    kind: str
    statement: str
    method: str | None


@dataclass(frozen=True, slots=True)
class FindingRow:
    """One ``VerificationResult.findings[]`` entry. ``severity`` is never
    redacted (a structural fact); ``statement`` is gated (R3: "finding
    statements"), fail-closed on this row's own owning verification id AND
    the claim it targets both being attested PUBLIC (a disclosed dependency
    choice — see the module docstring's "Why this module owns the report's
    own redaction too" section).
    """

    severity: str
    statement: str
    redacted: bool


@dataclass(frozen=True, slots=True)
class VerificationRow:
    """One ``VerificationResult`` targeting a claim in the claim table
    (R1(3)): "EVERY VerificationResult targeting it ... two disagreeing
    verifications appear as two rows under the same claim ... never merged
    or averaged". ``confidence`` is rendered under the literal column label
    "reviewer confidence (self-declared)" (derived_decisions (e)) by both
    renderers — never presented as if it were epistemic confidence
    (AGENTS.md prohibited-shortcuts). ``disagreement_on_record`` is ``True``
    on EVERY row for a claim whose verifications do not all share the same
    ``recommendation`` — not merely on the two rows that literally conflict,
    since a reader comparing any one row in isolation still needs the same
    "there is disagreement here" signal.
    """

    verification_id: str
    reviewer_role: str
    recommendation: str
    confidence: float
    finding_count: int
    findings: tuple[FindingRow, ...]
    disagreement_on_record: bool


@dataclass(frozen=True, slots=True)
class ClaimRow:
    """One row of the claim table (R1(3)). ``assertion``/``assertion_
    redacted`` come from ``mrr.domain.public_correction_view
    .build_public_claim_row`` (reused verbatim — see the module docstring);
    every other field is read directly from the claim's own stored body
    (none of ``claim_type``/``scope``/``uncertainty``/``evidence_relations``/
    ``counterevidence_relations``/``dependencies`` is gated — see the module
    docstring's "What is never redacted" section).
    """

    claim_id: str
    claim_type: str
    scope: Mapping[str, JSONValue]
    status: str
    uncertainty: tuple[UncertaintyRow, ...]
    assertion: str | None
    assertion_redacted: bool
    evidence_relations: tuple[str, ...]
    counterevidence_relations: tuple[str, ...]
    dependencies: tuple[str, ...]
    unresolved_correction_ids: tuple[str, ...]
    flagged: bool
    verifications: tuple[VerificationRow, ...]


@dataclass(frozen=True, slots=True)
class SourceRecordRow:
    """One ``SourceRecord`` in the crate's own closure (R1(4), "evidence
    map"). Never redacted — none of these fields is in R3's own free-text
    enumeration (the module docstring's "Evidence map" layout is this
    packet's own disclosed choice, per task-packets/E8-T03.yaml
    specification_gaps).
    """

    source_record_id: str
    title: str
    source_type: str
    primary_secondary_derived: str


@dataclass(frozen=True, slots=True)
class EvidenceAnchorRow:
    """One ``EvidenceAnchor`` in the crate's own closure (R1(4)). ``relation``/
    ``anchor_kind``/``anchor_validation_status`` are the "resolution status"
    R1(4) names; ``anchor_unavailable_reason`` (non-null exactly when exact
    anchoring failed, per schemas/evidence-anchor.schema.json) is shown
    alongside it, unredacted for the same reason as every other evidence-map
    field.
    """

    evidence_anchor_id: str
    relation: str
    anchor_kind: str
    anchor_validation_status: str
    anchor_unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class EvidenceMapSection:
    """R1(4). Both tuples are sorted by id — see :func:`build_report`."""

    source_records: tuple[SourceRecordRow, ...]
    evidence_anchors: tuple[EvidenceAnchorRow, ...]


@dataclass(frozen=True, slots=True)
class KnownUnknownRow:
    """One known-unknown string (crate-level or per-claim), gated per R3
    ("known-unknown strings"). ``text`` is ``_REDACTED_MARKER`` whenever
    ``redacted`` is ``True``.
    """

    text: str
    redacted: bool


@dataclass(frozen=True, slots=True)
class ClaimKnownUnknowns:
    """One claim's own ``known_unknowns`` (R1(6): "plus per-claim known
    unknowns"), gated on that claim's own id.
    """

    claim_id: str
    known_unknowns: tuple[KnownUnknownRow, ...]


@dataclass(frozen=True, slots=True)
class KnownUnknownsSection:
    """R1(6). ``crate_known_unknowns`` is gated on the crate's own id;
    ``per_claim`` lists every claim in claim-table order, EVEN a claim whose
    own ``known_unknowns`` is empty (so the section's own internal structure
    never silently omits a claim the claim table already shows).
    """

    crate_known_unknowns: tuple[KnownUnknownRow, ...]
    per_claim: tuple[ClaimKnownUnknowns, ...]


@dataclass(frozen=True, slots=True)
class FailureRow:
    """One ``EvidenceCrate.failures[]`` entry (R1(7), MRR-FR-054).
    ``code``/``category`` are structural, never redacted; ``message`` is
    gated per R3 ("failure messages"), fail-closed on the CRATE's own id
    (a failure carries no object-id reference of its own to gate on
    instead — a disclosed dependency choice, flagged in the packet report).
    """

    code: str
    category: str
    message: str
    redacted: bool


@dataclass(frozen=True, slots=True)
class ProvenanceEdgeRow:
    """One hop of a claim's own provenance map (R1(8)). Mirrors
    ``mrr.domain.projection.ProvenanceEdge`` exactly, minus ``source_id``
    (redundant here — every row in one :class:`ClaimProvenanceRow` shares
    the same claim as an implicit source-adjacent root) and ``edge_id``
    (an internal edge-table identifier, not itself a urn worth rendering).
    """

    target_id: str
    target_kind: str
    relation: str
    via: str


@dataclass(frozen=True, slots=True)
class ClaimProvenanceRow:
    """R1(8): "per claim, the count and a sorted, compact rendering of its
    provenance edges". ``edges`` is exactly ``ProjectionService
    .build_provenance_map(claim_id).edges`` (already sorted by that method's
    own ``(source_id, relation, target_id, via)`` key — reused, not
    re-sorted a second, possibly-divergent way), reshaped one-for-one into
    :class:`ProvenanceEdgeRow`.
    """

    claim_id: str
    edge_count: int
    edges: tuple[ProvenanceEdgeRow, ...]


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """The full report model (task-packets/E8-T03.yaml R1) for one crate,
    already disclosure-shaped by :func:`build_report` — ``render_markdown``/
    ``render_html`` render whatever is already here, with no further
    disclosure branching of their own (both take exactly one argument, this
    type — task-packets/E8-T03.yaml R1's own signature sketch).
    """

    header: HeaderSection
    methods: MethodsSection
    claims: tuple[ClaimRow, ...]
    evidence_map: EvidenceMapSection
    corrections: tuple[PublicCorrectionRow, ...]
    known_unknowns: KnownUnknownsSection
    failures: tuple[FailureRow, ...]
    provenance_summary: tuple[ClaimProvenanceRow, ...]
    disclosure: Disclosure
    source_urns: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Building the model — pure, from already-loaded closure bodies.
# ---------------------------------------------------------------------------

_CLAIM_KIND = "Claim"
_SOURCE_RECORD_KIND = "SourceRecord"
_EVIDENCE_ANCHOR_KIND = "EvidenceAnchor"
_VERIFICATION_RESULT_KIND = "VerificationResult"
_RUN_MANIFEST_KIND = "RunManifest"


def _attestation_for(
    disclosure: Disclosure, classification_by_object_id: Mapping[str, Classification]
) -> Mapping[str, Classification]:
    """The classification map every redaction call site in :func:`build_report`
    actually uses — the caller-supplied map for ``"public"``, or
    :data:`ALWAYS_PUBLIC_ATTESTATION` for ``"internal"`` (the module
    docstring's "One code path for both disclosures" section). The
    caller-supplied map is deliberately ignored for ``"internal"``: task-
    packets/E8-T03.yaml R4 makes ``--classification-file`` FORBIDDEN with
    ``--disclosure internal`` at the CLI, so this mirrors that refusal
    structurally, one layer down, rather than trusting every future caller
    to also enforce it.
    """
    if disclosure == "internal":
        return ALWAYS_PUBLIC_ATTESTATION
    return classification_by_object_id


def _build_claim_row(
    claim_body: Mapping[str, JSONValue],
    *,
    verifications: tuple[VerificationRow, ...],
    unresolved_correction_ids: tuple[str, ...],
    attestation: Mapping[str, Classification],
) -> ClaimRow:
    claim_id = str(claim_body["id"])
    evidence_relations = tuple(_as_string_sequence(claim_body.get("evidence_relations")))
    claim_table_row = ClaimTableRow(
        claim_id=claim_id,
        assertion=str(claim_body["assertion"]),
        status=str(claim_body["status"]),
        evidence_relations=evidence_relations,
        verification_ids=tuple(_as_string_sequence(claim_body.get("verification_ids"))),
        unresolved_correction_ids=unresolved_correction_ids,
        flagged=bool(unresolved_correction_ids),
        ceiling_checked=False,
        ceiling_violation=None,
    )
    public_claim_row = build_public_claim_row(
        claim_table_row, classification_by_object_id=attestation
    )

    scope = _as_mapping(claim_body.get("scope"))

    uncertainty = tuple(
        UncertaintyRow(
            kind=str(entry["kind"]),
            statement=str(entry["statement"]),
            method=None if entry.get("method") is None else str(entry["method"]),
        )
        for entry in _as_object_sequence(claim_body.get("uncertainty"))
    )

    return ClaimRow(
        claim_id=claim_id,
        claim_type=str(claim_body["claim_type"]),
        scope=scope,
        status=str(claim_body["status"]),
        uncertainty=uncertainty,
        assertion=public_claim_row.assertion,
        assertion_redacted=public_claim_row.redacted,
        evidence_relations=evidence_relations,
        counterevidence_relations=tuple(
            _as_string_sequence(claim_body.get("counterevidence_relations"))
        ),
        dependencies=tuple(_as_string_sequence(claim_body.get("dependencies"))),
        unresolved_correction_ids=unresolved_correction_ids,
        flagged=bool(unresolved_correction_ids),
        verifications=verifications,
    )


def _build_verifications_for_claim(
    claim_id: str,
    object_bodies: Mapping[str, Mapping[str, JSONValue]],
    *,
    attestation: Mapping[str, Classification],
) -> tuple[VerificationRow, ...]:
    matching = sorted(
        (
            body
            for body in object_bodies.values()
            if body.get("kind") == _VERIFICATION_RESULT_KIND and body.get("target_id") == claim_id
        ),
        key=lambda body: str(body["id"]),
    )
    recommendations = {str(body["recommendation"]) for body in matching}
    disagreement = len(recommendations) > 1

    rows = []
    for body in matching:
        verification_id = str(body["id"])
        dependency_ids = (verification_id, claim_id)
        findings = []
        for finding in _as_object_sequence(body.get("findings")):
            statement, redacted = _redact(
                str(finding["statement"]),
                dependency_ids=dependency_ids,
                classification_by_object_id=attestation,
            )
            findings.append(
                FindingRow(
                    severity=str(finding["severity"]), statement=statement, redacted=redacted
                )
            )
        rows.append(
            VerificationRow(
                verification_id=verification_id,
                reviewer_role=str(body["reviewer_role"]),
                recommendation=str(body["recommendation"]),
                confidence=_as_float(body["confidence"]),
                finding_count=len(findings),
                findings=tuple(findings),
                disagreement_on_record=disagreement,
            )
        )
    return tuple(rows)


def _build_known_unknown_rows(
    strings: Iterable[str], *, dependency_id: str, attestation: Mapping[str, Classification]
) -> tuple[KnownUnknownRow, ...]:
    rows = []
    for value in strings:
        text, redacted = _redact(
            value, dependency_ids=(dependency_id,), classification_by_object_id=attestation
        )
        rows.append(KnownUnknownRow(text=text, redacted=redacted))
    return tuple(rows)


def build_report(
    *,
    object_bodies: Mapping[str, Mapping[str, JSONValue]],
    crate_id: str,
    corrections: Sequence[PublicCorrectionRow],
    provenance_by_claim: Mapping[str, Sequence[ProvenanceEdge]],
    disclosure: Disclosure,
    classification_by_object_id: Mapping[str, Classification],
) -> ResearchReport:
    """Build the full :class:`ResearchReport` model for ``crate_id`` from
    already-loaded closure bodies. Pure — see the module docstring.

    Args:
        object_bodies: ``mrr.services.export.service.ExportService
            .resolve_closure(crate_id).object_bodies`` — every MRR object
            body this crate's export closure includes, keyed by urn. MUST
            contain ``crate_id`` itself, mapping to kind ``"EvidenceCrate"``.
        crate_id: the crate this report is rooted at — the SAME anchor
            ``ExportService.resolve_closure`` used to build ``object_bodies``
            (task-packets/E8-T03.yaml reviewer_resolution (2): "one
            definition of 'what belongs to this run's record'").
        corrections: every correction this report's corrections section
            renders — ALREADY discovered and shaped by the caller (typically
            ``mrr.services.projection.service.ProjectionService
            .build_public_correction_view``, called with whichever
            attestation matches ``disclosure`` — see :data:
            `ALWAYS_PUBLIC_ATTESTATION` for the internal case), and already
            filtered to this crate's own closure (this function does not
            re-filter). Order is not assumed; :func:`build_report` sorts
            (unresolved-critical-first is trivial today since every row this
            composition can discover already is one — see the module
            docstring — then by ``correction_id``).
        provenance_by_claim: one ``ProjectionService.build_provenance_map
            (claim_id).edges`` result per claim in ``object_bodies["proposed
            _claims"]`` (R1(8)) — a claim missing from this mapping renders
            an empty provenance row (count 0, "(none recorded)"), never an
            error, since a claim with no reachable provenance is a
            legitimate, honest fact about the graph.
        disclosure: which of the two shipped projections to build
            (task-packets/E8-T03.yaml R3/R4) — see :func:`_attestation_for`.
        classification_by_object_id: the caller-supplied attestation map
            (task-packets/E6-T05.yaml's bridge, reused verbatim) — used only
            when ``disclosure == "public"``; ignored for ``"internal"`` (see
            :func:`_attestation_for`).

    Raises:
        ValueError: ``crate_id`` is not a key of ``object_bodies``, or the
            body it names is not of kind ``"EvidenceCrate"`` — a plain
            ``if``/``raise`` (matching ``mrr.domain.ro_crate
            .build_ro_crate_metadata``'s identical "the crate must always be
            part of its own export plan" guard), since every real caller
            (``ReportService``) only ever calls this with an
            ``ExportClosure`` that already guarantees both.
    """
    crate_body = object_bodies.get(crate_id)
    if crate_body is None or crate_body.get("kind") != "EvidenceCrate":
        raise ValueError(
            f"crate_id {crate_id!r} does not name an EvidenceCrate object in object_bodies — "
            "build_report requires the exact closure ExportService.resolve_closure produced "
            "for this same crate_id"
        )

    attestation = _attestation_for(disclosure, classification_by_object_id)

    crate_artifacts = _as_object_sequence(crate_body.get("artifacts"))
    header = HeaderSection(
        crate_urn=crate_id,
        run_urn=str(crate_body["run_id"]),
        run_state=str(crate_body["run_state"]),
        practice_id=str(crate_body["practice_id"]),
        created_at=str(crate_body["created_at"]),
        content_hash=str(crate_body["content_hash"]),
        object_count=len(object_bodies),
        artifact_count=len(crate_artifacts),
    )

    run_id = str(crate_body["run_id"])
    run_manifest_body = object_bodies.get(run_id)
    run_manifest_included = (
        run_manifest_body is not None and run_manifest_body.get("kind") == _RUN_MANIFEST_KIND
    )
    declared_parameters = (
        _as_mapping(run_manifest_body.get("parameters"))
        if run_manifest_included and run_manifest_body is not None
        else None
    )
    environment = _as_mapping(crate_body.get("environment"))
    methods = MethodsSection(
        run_urn=run_id,
        run_manifest_included=run_manifest_included,
        declared_parameters=declared_parameters or None,
        artifact_refs=tuple(
            sorted(
                (
                    ArtifactRefRow(
                        artifact_id=str(ref["artifact_id"]), content_hash=str(ref["content_hash"])
                    )
                    for ref in crate_artifacts
                ),
                key=lambda row: row.artifact_id,
            )
        ),
        environment_image_digest=str(environment.get("image_digest", "")),
        environment_code_revision=str(environment.get("code_revision", "")),
        environment_input_hashes=tuple(_as_string_sequence(environment.get("input_hashes"))),
        environment_model_profiles=tuple(_as_string_sequence(environment.get("model_profiles"))),
    )

    corrections_sorted = tuple(sorted(corrections, key=lambda row: row.correction_id))
    corrections_by_claim: dict[str, list[str]] = {}
    for row in corrections_sorted:
        for object_id in (*row.affected_object_ids, *row.impact_object_ids):
            corrections_by_claim.setdefault(object_id, []).append(row.correction_id)

    proposed_claim_ids = sorted(_as_string_sequence(crate_body.get("proposed_claims")))

    claim_rows = []
    provenance_rows = []
    per_claim_known_unknowns = []
    for claim_id in proposed_claim_ids:
        claim_body = object_bodies.get(claim_id)
        if claim_body is None or claim_body.get("kind") != _CLAIM_KIND:
            raise ValueError(
                f"proposed claim {claim_id!r} is not present in object_bodies as a Claim — "
                "build_report requires the exact closure ExportService.resolve_closure "
                "produced for this crate (every proposed claim resolves there fail-fast)"
            )
        unresolved_correction_ids = tuple(sorted(set(corrections_by_claim.get(claim_id, []))))
        verifications = _build_verifications_for_claim(
            claim_id, object_bodies, attestation=attestation
        )
        claim_rows.append(
            _build_claim_row(
                claim_body,
                verifications=verifications,
                unresolved_correction_ids=unresolved_correction_ids,
                attestation=attestation,
            )
        )

        edges = tuple(provenance_by_claim.get(claim_id, ()))
        provenance_rows.append(
            ClaimProvenanceRow(
                claim_id=claim_id,
                edge_count=len(edges),
                edges=tuple(
                    ProvenanceEdgeRow(
                        target_id=edge.target_id,
                        target_kind=edge.target_kind,
                        relation=edge.relation,
                        via=edge.via,
                    )
                    for edge in edges
                ),
            )
        )

        per_claim_known_unknowns.append(
            ClaimKnownUnknowns(
                claim_id=claim_id,
                known_unknowns=_build_known_unknown_rows(
                    _as_string_sequence(claim_body.get("known_unknowns")),
                    dependency_id=claim_id,
                    attestation=attestation,
                ),
            )
        )

    source_records = tuple(
        sorted(
            (
                SourceRecordRow(
                    source_record_id=str(body["id"]),
                    title=str(body["title"]),
                    source_type=str(body["source_type"]),
                    primary_secondary_derived=str(body["primary_secondary_derived"]),
                )
                for body in object_bodies.values()
                if body.get("kind") == _SOURCE_RECORD_KIND
            ),
            key=lambda row: row.source_record_id,
        )
    )
    evidence_anchors = tuple(
        sorted(
            (
                EvidenceAnchorRow(
                    evidence_anchor_id=str(body["id"]),
                    relation=str(body["relation"]),
                    anchor_kind=str(body["anchor_kind"]),
                    anchor_validation_status=str(body["anchor_validation_status"]),
                    anchor_unavailable_reason=(
                        None
                        if body.get("anchor_unavailable_reason") is None
                        else str(body["anchor_unavailable_reason"])
                    ),
                )
                for body in object_bodies.values()
                if body.get("kind") == _EVIDENCE_ANCHOR_KIND
            ),
            key=lambda row: row.evidence_anchor_id,
        )
    )
    evidence_map = EvidenceMapSection(
        source_records=source_records, evidence_anchors=evidence_anchors
    )

    known_unknowns = KnownUnknownsSection(
        crate_known_unknowns=_build_known_unknown_rows(
            _as_string_sequence(crate_body.get("known_unknowns")),
            dependency_id=crate_id,
            attestation=attestation,
        ),
        per_claim=tuple(per_claim_known_unknowns),
    )

    failure_rows = []
    for entry in _as_object_sequence(crate_body.get("failures")):
        message, redacted = _redact(
            str(entry["message"]),
            dependency_ids=(crate_id,),
            classification_by_object_id=attestation,
        )
        failure_rows.append(
            FailureRow(
                code=str(entry["code"]),
                category=str(entry["category"]),
                message=message,
                redacted=redacted,
            )
        )
    failures = tuple(failure_rows)

    source_urn_accumulator: set[str] = set(object_bodies)
    for body in object_bodies.values():
        _collect_urns(body, source_urn_accumulator)
    for row in corrections_sorted:
        source_urn_accumulator.add(row.correction_id)
        source_urn_accumulator.update(row.affected_object_ids)
        source_urn_accumulator.update(row.impact_object_ids)
    source_urns = frozenset(source_urn_accumulator)

    return ResearchReport(
        header=header,
        methods=methods,
        claims=tuple(claim_rows),
        evidence_map=evidence_map,
        corrections=corrections_sorted,
        known_unknowns=known_unknowns,
        failures=failures,
        provenance_summary=tuple(provenance_rows),
        disclosure=disclosure,
        source_urns=source_urns,
    )


# ---------------------------------------------------------------------------
# Rendering — Markdown.
# ---------------------------------------------------------------------------


def _md_bullet(label: str, value: object) -> str:
    return f"- **{label}:** {value}"


def _scope_text(scope: Mapping[str, JSONValue]) -> str:
    """``Claim.scope`` rendered as RFC 8785 canonical JSON text (the
    archive's own canonical convention, ``mrr.crypto.canonical``) — never
    a Python ``repr`` and never an ad-hoc ``k=v`` join, so both renderers
    print the identical, machine-faithful text (reviewer-added fix, E8-T03
    review 2026-07-22: the first render leaked ``repr`` formatting).
    """
    return canonicalize(dict(scope)).decode("utf-8")


def _md_list_or_none(items: Sequence[str]) -> str:
    return ", ".join(items) if items else _NONE_RECORDED


def render_markdown(model: ResearchReport) -> str:
    """Render ``model`` to a deterministic, self-contained Markdown
    document. See the module docstring's "Markdown avoids pipe tables"
    section for why no ``|``-delimited table is ever used.
    """
    lines: list[str] = []
    h = model.header
    lines.append(f"# Research report — {h.crate_urn}")
    lines.append("")
    lines.append(f"_Disclosure: {model.disclosure}_")
    lines.append("")

    lines.append("## 1. Header")
    lines.append(_md_bullet("Crate", h.crate_urn))
    lines.append(_md_bullet("Run", h.run_urn))
    lines.append(_md_bullet("Run state", h.run_state))
    lines.append(_md_bullet("Practice", h.practice_id))
    lines.append(_md_bullet("Created at", h.created_at))
    lines.append(_md_bullet("Content hash", h.content_hash))
    lines.append(_md_bullet("Objects in closure", h.object_count))
    lines.append(_md_bullet("Artifacts in closure", h.artifact_count))
    lines.append("")

    lines.append("## 2. Methods")
    m = model.methods
    lines.append(_md_bullet("Run", m.run_urn))
    lines.append(_md_bullet("Run manifest included in closure", m.run_manifest_included))
    if m.declared_parameters:
        lines.append("- **Declared parameters:**")
        for key in sorted(m.declared_parameters):
            lines.append(f"    - `{key}`: {m.declared_parameters[key]!r}")
    else:
        lines.append(_md_bullet("Declared parameters", _NONE_RECORDED))
    if m.artifact_refs:
        lines.append("- **Artifact urns with hashes:**")
        for artifact_ref in m.artifact_refs:
            lines.append(f"    - `{artifact_ref.artifact_id}` — `{artifact_ref.content_hash}`")
    else:
        lines.append(_md_bullet("Artifact urns with hashes", _NONE_RECORDED))
    lines.append("- **Environment:**")
    lines.append(f"    - Image digest: `{m.environment_image_digest}`")
    lines.append(f"    - Code revision: `{m.environment_code_revision}`")
    lines.append(f"    - Input hashes: {_md_list_or_none(m.environment_input_hashes)}")
    lines.append(f"    - Model profiles: {_md_list_or_none(m.environment_model_profiles)}")
    lines.append("")

    lines.append("## 3. Claim table")
    if not model.claims:
        lines.append(_NONE_RECORDED)
    for claim in model.claims:
        lines.append(f"### {claim.claim_id}")
        lines.append(_md_bullet("Type", claim.claim_type))
        lines.append(_md_bullet("Status", claim.status))
        lines.append(_md_bullet("Assertion", claim.assertion))
        lines.append(
            _md_bullet("Scope", _scope_text(claim.scope) if claim.scope else _NONE_RECORDED)
        )
        if claim.uncertainty:
            lines.append("- **Uncertainty:**")
            for u in claim.uncertainty:
                method_suffix = f" (method: {u.method})" if u.method else ""
                lines.append(f"    - [{u.kind}] {u.statement}{method_suffix}")
        else:
            lines.append(_md_bullet("Uncertainty", _NONE_RECORDED))
        lines.append(_md_bullet("Evidence relations", _md_list_or_none(claim.evidence_relations)))
        lines.append(
            _md_bullet(
                "Counterevidence relations", _md_list_or_none(claim.counterevidence_relations)
            )
        )
        lines.append(_md_bullet("Dependencies", _md_list_or_none(claim.dependencies)))
        lines.append(
            _md_bullet(
                "Unresolved critical corrections", _md_list_or_none(claim.unresolved_correction_ids)
            )
        )
        if claim.verifications:
            lines.append("- **Verifications:**")
            for v in claim.verifications:
                disagreement = " — DISAGREEMENT ON RECORD" if v.disagreement_on_record else ""
                lines.append(f"    - `{v.verification_id}`{disagreement}")
                lines.append(f"        - Reviewer role: {v.reviewer_role}")
                lines.append(f"        - Recommendation: {v.recommendation}")
                lines.append(f"        - Reviewer confidence (self-declared): {v.confidence}")
                lines.append(f"        - Finding count: {v.finding_count}")
                if v.findings:
                    for finding in v.findings:
                        lines.append(f"            - [{finding.severity}] {finding.statement}")
                else:
                    lines.append(f"            - {_NONE_RECORDED}")
        else:
            lines.append(_md_bullet("Verifications", _NONE_RECORDED))
        lines.append("")

    lines.append("## 4. Evidence map")
    em = model.evidence_map
    if em.source_records:
        lines.append("- **Source records:**")
        for sr in em.source_records:
            lines.append(
                f"    - `{sr.source_record_id}` — {sr.title} "
                f"({sr.source_type}, {sr.primary_secondary_derived})"
            )
    else:
        lines.append(_md_bullet("Source records", _NONE_RECORDED))
    if em.evidence_anchors:
        lines.append("- **Evidence anchors:**")
        for ea in em.evidence_anchors:
            reason = (
                f" — unavailable: {ea.anchor_unavailable_reason}"
                if ea.anchor_unavailable_reason
                else ""
            )
            lines.append(
                f"    - `{ea.evidence_anchor_id}` — {ea.relation}/{ea.anchor_kind}, "
                f"validation: {ea.anchor_validation_status}{reason}"
            )
    else:
        lines.append(_md_bullet("Evidence anchors", _NONE_RECORDED))
    lines.append("")

    lines.append("## 5. Corrections")
    if model.corrections:
        for c in model.corrections:
            prefix = "UNRESOLVED CRITICAL — " if c.unresolved else ""
            lines.append(f"- **{prefix}`{c.correction_id}`**")
            lines.append(f"    - Type: {c.correction_type}")
            lines.append(f"    - Severity: {c.severity}")
            lines.append(f"    - Status: {c.status}")
            lines.append(f"    - Affected objects: {_md_list_or_none(c.affected_object_ids)}")
            lines.append(f"    - Impact objects: {_md_list_or_none(c.impact_object_ids)}")
            lines.append(f"    - Reason: {c.reason if c.reason is not None else _REDACTED_MARKER}")
            lines.append(
                f"    - Requested action: "
                f"{c.requested_action if c.requested_action is not None else _REDACTED_MARKER}"
            )
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    lines.append("## 6. Known unknowns")
    ku = model.known_unknowns
    if ku.crate_known_unknowns:
        lines.append("- **Crate-level:**")
        for row in ku.crate_known_unknowns:
            lines.append(f"    - {row.text}")
    else:
        lines.append(_md_bullet("Crate-level", _NONE_RECORDED))
    if ku.per_claim:
        lines.append("- **Per claim:**")
        for claim_ku in ku.per_claim:
            if claim_ku.known_unknowns:
                lines.append(f"    - `{claim_ku.claim_id}`:")
                for row in claim_ku.known_unknowns:
                    lines.append(f"        - {row.text}")
            else:
                lines.append(f"    - `{claim_ku.claim_id}`: {_NONE_RECORDED}")
    else:
        lines.append(_md_bullet("Per claim", _NONE_RECORDED))
    lines.append("")

    lines.append("## 7. Failures")
    if model.failures:
        for f_row in model.failures:
            lines.append(f"- **{f_row.code}** ({f_row.category}): {f_row.message}")
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    lines.append("## 8. Provenance summary")
    if model.provenance_summary:
        for pr in model.provenance_summary:
            lines.append(f"- **`{pr.claim_id}`** — {pr.edge_count} edge(s)")
            if pr.edges:
                for edge in pr.edges:
                    lines.append(
                        f"    - {edge.relation} ({edge.via}) -> "
                        f"`{edge.target_id}` ({edge.target_kind})"
                    )
            else:
                lines.append(f"    - {_NONE_RECORDED}")
    else:
        lines.append(_NONE_RECORDED)
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Rendering — HTML.
# ---------------------------------------------------------------------------

_HTML_ESCAPE_TABLE = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#x27;"),
)


def _escape_html(value: object) -> str:
    """Escape ``str(value)`` for safe placement inside HTML markup this
    module itself emits — a stored ``"<script>"`` becomes the literal text
    ``&lt;script&gt;``, never live markup. See the module docstring's
    "Markdown avoids pipe tables; HTML uses them" section for why this
    five-substitution table (not a library) is sufficient and is the ONLY
    escaping mechanism either renderer uses.
    """
    text = str(value)
    for needle, replacement in _HTML_ESCAPE_TABLE:
        text = text.replace(needle, replacement)
    return text


def _html_list_or_none(items: Sequence[str]) -> str:
    if not items:
        return _escape_html(_NONE_RECORDED)
    return ", ".join(f"<code>{_escape_html(item)}</code>" for item in items)


def render_html(model: ResearchReport) -> str:
    """Render ``model`` to a deterministic, self-contained, strictly-escaped
    HTML document — no external stylesheet/script reference, no inline
    style attribute, minimal semantic tags only (task-packets/E8-T03.yaml
    R1). See the module docstring's "Markdown avoids pipe tables; HTML uses
    them" section for the escaping discipline.
    """
    parts: list[str] = []
    h = model.header
    parts.append(f"<h1>Research report — {_escape_html(h.crate_urn)}</h1>")
    parts.append(f"<p><em>Disclosure: {_escape_html(model.disclosure)}</em></p>")

    parts.append("<h2>1. Header</h2>")
    parts.append("<dl>")
    for label, value in (
        ("Crate", h.crate_urn),
        ("Run", h.run_urn),
        ("Run state", h.run_state),
        ("Practice", h.practice_id),
        ("Created at", h.created_at),
        ("Content hash", h.content_hash),
        ("Objects in closure", h.object_count),
        ("Artifacts in closure", h.artifact_count),
    ):
        parts.append(f"<dt>{_escape_html(label)}</dt><dd>{_escape_html(value)}</dd>")
    parts.append("</dl>")

    parts.append("<h2>2. Methods</h2>")
    m = model.methods
    parts.append("<dl>")
    parts.append(f"<dt>Run</dt><dd>{_escape_html(m.run_urn)}</dd>")
    parts.append(
        f"<dt>Run manifest included in closure</dt><dd>{_escape_html(m.run_manifest_included)}</dd>"
    )
    if m.declared_parameters:
        rendered_params = "; ".join(
            f"{_escape_html(key)}={_escape_html(m.declared_parameters[key])}"
            for key in sorted(m.declared_parameters)
        )
        parts.append(f"<dt>Declared parameters</dt><dd>{rendered_params}</dd>")
    else:
        parts.append(f"<dt>Declared parameters</dt><dd>{_escape_html(_NONE_RECORDED)}</dd>")
    parts.append("</dl>")
    if m.artifact_refs:
        parts.append("<table><caption>Artifact urns with hashes</caption>")
        parts.append("<tr><th>Artifact</th><th>Content hash</th></tr>")
        for artifact_ref in m.artifact_refs:
            parts.append(
                f"<tr><td>{_escape_html(artifact_ref.artifact_id)}</td>"
                f"<td>{_escape_html(artifact_ref.content_hash)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append(f"<p>Artifact urns with hashes: {_escape_html(_NONE_RECORDED)}</p>")
    parts.append("<dl>")
    parts.append(
        f"<dt>Environment image digest</dt><dd>{_escape_html(m.environment_image_digest)}</dd>"
    )
    parts.append(
        f"<dt>Environment code revision</dt><dd>{_escape_html(m.environment_code_revision)}</dd>"
    )
    parts.append(
        "<dt>Environment input hashes</dt>"
        f"<dd>{_html_list_or_none(m.environment_input_hashes)}</dd>"
    )
    parts.append(
        "<dt>Environment model profiles</dt>"
        f"<dd>{_html_list_or_none(m.environment_model_profiles)}</dd>"
    )
    parts.append("</dl>")

    parts.append("<h2>3. Claim table</h2>")
    if not model.claims:
        parts.append(f"<p>{_escape_html(_NONE_RECORDED)}</p>")
    for claim in model.claims:
        parts.append(f"<h3>{_escape_html(claim.claim_id)}</h3>")
        parts.append("<dl>")
        parts.append(f"<dt>Type</dt><dd>{_escape_html(claim.claim_type)}</dd>")
        parts.append(f"<dt>Status</dt><dd>{_escape_html(claim.status)}</dd>")
        parts.append(f"<dt>Assertion</dt><dd>{_escape_html(claim.assertion)}</dd>")
        scope_text = _scope_text(claim.scope) if claim.scope else _NONE_RECORDED
        parts.append(f"<dt>Scope</dt><dd>{_escape_html(scope_text)}</dd>")
        parts.append(
            f"<dt>Evidence relations</dt><dd>{_html_list_or_none(claim.evidence_relations)}</dd>"
        )
        parts.append(
            f"<dt>Counterevidence relations</dt>"
            f"<dd>{_html_list_or_none(claim.counterevidence_relations)}</dd>"
        )
        parts.append(f"<dt>Dependencies</dt><dd>{_html_list_or_none(claim.dependencies)}</dd>")
        parts.append(
            f"<dt>Unresolved critical corrections</dt>"
            f"<dd>{_html_list_or_none(claim.unresolved_correction_ids)}</dd>"
        )
        parts.append("</dl>")
        if claim.uncertainty:
            parts.append("<table><caption>Uncertainty</caption>")
            parts.append("<tr><th>Kind</th><th>Statement</th><th>Method</th></tr>")
            for u in claim.uncertainty:
                parts.append(
                    f"<tr><td>{_escape_html(u.kind)}</td><td>{_escape_html(u.statement)}</td>"
                    f"<td>{_escape_html(u.method) if u.method else ''}</td></tr>"
                )
            parts.append("</table>")
        else:
            parts.append(f"<p>Uncertainty: {_escape_html(_NONE_RECORDED)}</p>")
        if claim.verifications:
            parts.append(
                "<table><caption>Verifications</caption><tr>"
                "<th>Verification</th><th>Reviewer role</th><th>Recommendation</th>"
                "<th>Reviewer confidence (self-declared)</th><th>Finding count</th>"
                "<th>Disagreement</th></tr>"
            )
            for v in claim.verifications:
                parts.append(
                    f"<tr><td>{_escape_html(v.verification_id)}</td>"
                    f"<td>{_escape_html(v.reviewer_role)}</td>"
                    f"<td>{_escape_html(v.recommendation)}</td>"
                    f"<td>{_escape_html(v.confidence)}</td>"
                    f"<td>{_escape_html(v.finding_count)}</td>"
                    f"<td>{'disagreement on record' if v.disagreement_on_record else ''}</td></tr>"
                )
                if v.findings:
                    for finding in v.findings:
                        parts.append(
                            f'<tr><td colspan="2"></td><td colspan="2">'
                            f"[{_escape_html(finding.severity)}] {_escape_html(finding.statement)}"
                            f'</td><td colspan="2"></td></tr>'
                        )
            parts.append("</table>")
        else:
            parts.append(f"<p>Verifications: {_escape_html(_NONE_RECORDED)}</p>")

    parts.append("<h2>4. Evidence map</h2>")
    em = model.evidence_map
    if em.source_records:
        parts.append(
            "<table><caption>Source records</caption><tr><th>Source record</th><th>Title</th>"
            "<th>Source type</th><th>Primary/secondary/derived</th></tr>"
        )
        for sr in em.source_records:
            parts.append(
                f"<tr><td>{_escape_html(sr.source_record_id)}</td><td>{_escape_html(sr.title)}</td>"
                f"<td>{_escape_html(sr.source_type)}</td>"
                f"<td>{_escape_html(sr.primary_secondary_derived)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append(f"<p>Source records: {_escape_html(_NONE_RECORDED)}</p>")
    if em.evidence_anchors:
        parts.append(
            "<table><caption>Evidence anchors</caption><tr><th>Evidence anchor</th>"
            "<th>Relation</th><th>Anchor kind</th><th>Validation status</th>"
            "<th>Unavailable reason</th></tr>"
        )
        for ea in em.evidence_anchors:
            unavailable_reason = (
                _escape_html(ea.anchor_unavailable_reason) if ea.anchor_unavailable_reason else ""
            )
            parts.append(
                f"<tr><td>{_escape_html(ea.evidence_anchor_id)}</td>"
                f"<td>{_escape_html(ea.relation)}</td>"
                f"<td>{_escape_html(ea.anchor_kind)}</td>"
                f"<td>{_escape_html(ea.anchor_validation_status)}</td>"
                f"<td>{unavailable_reason}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append(f"<p>Evidence anchors: {_escape_html(_NONE_RECORDED)}</p>")

    parts.append("<h2>5. Corrections</h2>")
    if model.corrections:
        parts.append(
            "<table><caption>Corrections</caption><tr><th>Correction</th><th>Type</th>"
            "<th>Severity</th><th>Status</th><th>Affected objects</th><th>Impact objects</th>"
            "<th>Reason</th><th>Requested action</th></tr>"
        )
        for c in model.corrections:
            prefix = "UNRESOLVED CRITICAL — " if c.unresolved else ""
            reason = c.reason if c.reason is not None else _REDACTED_MARKER
            requested_action = (
                c.requested_action if c.requested_action is not None else _REDACTED_MARKER
            )
            parts.append(
                f"<tr><td>{_escape_html(prefix)}{_escape_html(c.correction_id)}</td>"
                f"<td>{_escape_html(c.correction_type)}</td><td>{_escape_html(c.severity)}</td>"
                f"<td>{_escape_html(c.status)}</td>"
                f"<td>{_html_list_or_none(c.affected_object_ids)}</td>"
                f"<td>{_html_list_or_none(c.impact_object_ids)}</td>"
                f"<td>{_escape_html(reason)}</td><td>{_escape_html(requested_action)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append(f"<p>{_escape_html(_NONE_RECORDED)}</p>")

    parts.append("<h2>6. Known unknowns</h2>")
    ku = model.known_unknowns
    if ku.crate_known_unknowns:
        parts.append("<ul>")
        for row in ku.crate_known_unknowns:
            parts.append(f"<li>{_escape_html(row.text)}</li>")
        parts.append("</ul>")
    else:
        parts.append(f"<p>Crate-level: {_escape_html(_NONE_RECORDED)}</p>")
    if ku.per_claim:
        parts.append("<dl>")
        for claim_ku in ku.per_claim:
            parts.append(f"<dt>{_escape_html(claim_ku.claim_id)}</dt>")
            if claim_ku.known_unknowns:
                items = "".join(
                    f"<li>{_escape_html(row.text)}</li>" for row in claim_ku.known_unknowns
                )
                parts.append(f"<dd><ul>{items}</ul></dd>")
            else:
                parts.append(f"<dd>{_escape_html(_NONE_RECORDED)}</dd>")
        parts.append("</dl>")
    else:
        parts.append(f"<p>Per claim: {_escape_html(_NONE_RECORDED)}</p>")

    parts.append("<h2>7. Failures</h2>")
    if model.failures:
        parts.append(
            "<table><caption>Failures</caption><tr><th>Code</th><th>Category</th><th>Message</th></tr>"
        )
        for f_row in model.failures:
            parts.append(
                f"<tr><td>{_escape_html(f_row.code)}</td><td>{_escape_html(f_row.category)}</td>"
                f"<td>{_escape_html(f_row.message)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append(f"<p>{_escape_html(_NONE_RECORDED)}</p>")

    parts.append("<h2>8. Provenance summary</h2>")
    if model.provenance_summary:
        for pr in model.provenance_summary:
            parts.append(f"<h3>{_escape_html(pr.claim_id)} ({pr.edge_count} edge(s))</h3>")
            if pr.edges:
                parts.append(
                    "<table><tr><th>Relation</th><th>Via</th><th>Target</th>"
                    "<th>Target kind</th></tr>"
                )
                for edge in pr.edges:
                    parts.append(
                        f"<tr><td>{_escape_html(edge.relation)}</td>"
                        f"<td>{_escape_html(edge.via)}</td>"
                        f"<td>{_escape_html(edge.target_id)}</td>"
                        f"<td>{_escape_html(edge.target_kind)}</td></tr>"
                    )
                parts.append("</table>")
            else:
                parts.append(f"<p>{_escape_html(_NONE_RECORDED)}</p>")
    else:
        parts.append(f"<p>{_escape_html(_NONE_RECORDED)}</p>")

    body = "\n".join(parts)
    title = _escape_html(f"Research report — {model.header.crate_urn}")
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>{title}</title>\n</head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )
