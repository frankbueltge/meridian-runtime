"""The source verifier tool (task-packets/E4-T05.yaml, MRR-FR-072: "Source
verification MUST retrieve or locally inspect the cited source and validate
the evidence anchor"). This module does the LOCAL-INSPECTION half only —
"retrieve" (actually fetching bytes over a network) belongs to a separate,
out-of-scope source-retrieval adapter; this tool opens no network connection
of its own, anywhere in it (see the module docstring of
``mrr.services.verifier`` and the AST architecture test that checks it).

--- The caller has already inspected; this tool VALIDATES what was found ----

``mrr.contracts.evidence_anchor.EvidenceAnchor`` already carries every field
a caller needs to state a citation precisely (``source_record_id``,
``snapshot_hash``, ``locator``, ``quoted_fragment_hash`` for a text anchor;
``run_id``, ``output_artifact``, ``selector`` for a computational one). This
module's job is deciding whether that ALREADY-CONSTRUCTED anchor resolves
against artifact content the CALLER supplies as locally available —
:class:`LocalTextArtifact`/:class:`LocalComputationalArtifact` are that
content, handed in directly (a full snapshot string, or a parsed structured
document); this tool fetches neither one itself. Two of the four locator
shapes domain 2.9 names for a text anchor — character and line offsets — are
UNAMBIGUOUS enough for this tool to resolve MECHANICALLY, itself, by direct
slicing; likewise a computational anchor's ``json_pointer`` selector (RFC
6901, resolved by :func:`_resolve_json_pointer`, a small hand-written
walker — no external dependency). The remaining locator/selector shapes
(page/section/paragraph, or a bare non-offset text ``selector``; and
table/column/row, ``query``, or ``notebook_cell`` for a computational anchor)
have no single, format-independent mechanical algorithm this tool could
apply to an arbitrary source without effectively parsing that source's
native format itself (PDF page numbers, spreadsheet tables, SQL query
results, notebook cell structures) — squarely a RETRIEVAL/extraction concern,
not a verification-DECISION concern, and out of this task's scope. For those
shapes, the caller supplies the fragment/value their own local inspection
already found (``resolved_fragment``/``resolved_value``), and this tool
still performs a REAL, deterministic check on it — that it is genuinely
CONTAINED within the supplied artifact content, never trusted blindly — via
:func:`_document_contains` (computational) or plain substring containment
(text). Flagged as an open specification question in this task's PR body:
a future, format-aware retrieval adapter could replace this fallback with
its own mechanical resolution for a specific source format.

--- The three-way status, and the source-access outcome the acceptance test
    names literally ------------------------------------------------------

docs/spec/01_SYSTEM_SPEC.md section 4.8 acceptance, quoted verbatim: "A
citation verifier that cannot open the source returns
``unverified_source_access``, not ``verified``." Neither ``"verified"`` nor
``"unverified_source_access"`` is one of ``mrr.contracts.evidence_anchor.
AnchorValidationStatus``'s own three values (``validated``/``unvalidated``/
``invalid``) — they name a DIFFERENT, narrower question this module answers
alongside the anchor status: was the cited source/run reachable and openable
AT ALL (``source_access_outcome``), independent of whether the specific
citation, once reached, actually holds. :data:`SourceAccessOutcome` is this
task's own small, minimal, two-value vocabulary (mirroring
``mrr.contracts.evidence_anchor.RecomputationStatus``'s own "not a
specification-given vocabulary, this task's own minimal proposal" precedent)
— not spec-derived beyond the two literal terms the acceptance criterion
itself names, flagged as an open specification question in this task's PR
body. The mapping from ``AnchorValidationStatus`` to it:

- ``"unvalidated"`` (the source/run could not be locally opened at all, or
  the anchor itself already declares ``anchor_unavailable_reason``) ->
  ``"unverified_source_access"`` — the literal acceptance-test case.
- ``"invalid"`` (the source/run WAS opened, but the anchor's specific
  locator/selector does not resolve within it, or a declared fragment hash
  does not match) -> ``"verified"`` — access itself succeeded; the failure
  is in the citation's OWN precision, not in reaching the material. This is
  what distinguishes "cannot open the source" from "opened the source and
  found the citation wrong" — two different failure modes docs/spec/
  05_EVALUATION_AND_ACCEPTANCE.md's MB-CIT cases name separately ("source is
  cited but inaccessible" vs. "citation points to wrong page or version").
- ``"validated"`` -> ``"verified"``.

This module does NOT attempt MB-CIT's deeper support/contradiction
classification ("source supports exact claim" vs. "supports only narrower
scope" vs. "contradicts claim" vs. "quote is accurate but context reverses
meaning") — that is a semantic judgment about MEANING, not mere anchor
access, and is explicitly out of this task's scope (see task-packets/
E4-T05.yaml forbidden_changes and required_output's own "open specification
questions" line). This tool answers only: is the anchor's cited location
genuinely present in locally available content, or not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from mrr.contracts.evidence_anchor import AnchorValidationStatus, EvidenceAnchor
from mrr.crypto.canonical import JSONValue
from mrr.crypto.hashing import content_hash

#: Mirrors the literal two terms docs/spec/01_SYSTEM_SPEC.md section 4.8's
#: acceptance criterion names ("returns `unverified_source_access`, not
#: `verified`") — see the module docstring's "source-access outcome" section
#: for why this is a separate, narrower vocabulary from
#: ``AnchorValidationStatus`` itself.
SourceAccessOutcome = Literal["verified", "unverified_source_access"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceVerificationOutcome:
    """The full result of validating one ``EvidenceAnchor`` by local
    inspection: the ``AnchorValidationStatus`` (feeds
    ``mrr.services.verifier.orchestrator.recommendation_for_anchor_status``),
    the narrower ``SourceAccessOutcome`` the section 4.8 acceptance test
    names literally, and a plain, factual, human-readable ``reason`` (never
    model-authored — built entirely from f-strings over this module's own
    deterministic findings).
    """

    anchor_validation_status: AnchorValidationStatus
    source_access_outcome: SourceAccessOutcome
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalTextArtifact:
    """The locally available content of a text anchor's cited source,
    entirely caller-supplied — this tool fetches none of it itself (MRR-FR-072
    "LOCAL inspection only"; see the module docstring).

    ``full_text`` is the complete locally available snapshot content, used
    both for the top-level ``snapshot_hash`` availability check and, when the
    anchor's locator carries character or line offsets, for this tool's own
    direct slicing. ``resolved_fragment`` is the exact fragment text the
    caller's own local inspection found at the anchor's locator when that
    locator instead carries only symbolic fields (page, section, paragraph,
    or a bare ``selector``) this tool has no mechanical way to resolve
    against a bare string — see the module docstring's "caller has already
    inspected" section. ``None`` there means the caller's inspection found
    no such location in the available content. Ignored (and safe to leave
    ``None``) whenever the anchor's locator carries char or line offsets,
    since those are resolved directly from ``full_text`` instead.
    """

    full_text: str
    resolved_fragment: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalComputationalArtifact:
    """Mirrors :class:`LocalTextArtifact` for a computational anchor's cited
    run output. ``document`` is the complete locally available structured
    output (e.g. a parsed JSON document or table), used both for the
    top-level availability check and, when the anchor's selector carries a
    ``json_pointer``, for this tool's own RFC 6901 resolution.
    ``resolved_value`` is the caller's own local-inspection result for any
    other selector shape (table/column/row, ``query``, ``notebook_cell``)
    this tool has no universal mechanical way to resolve against an
    arbitrary document structure. ``None`` there means the caller's
    inspection found nothing.
    """

    document: JSONValue
    resolved_value: JSONValue | None = None


class _JsonPointerError(Exception):
    """Internal-only signal: an RFC 6901 JSON Pointer did not resolve against
    the supplied document. Always caught by :func:`_resolve_json_pointer`'s
    caller and turned into an ``"invalid"`` anchor status — never leaks out
    of this module.
    """


def _resolve_json_pointer(document: JSONValue, pointer: str) -> JSONValue:
    """A minimal RFC 6901 JSON Pointer resolver — no external dependency, no
    ``eval``. ``""`` refers to the whole document; every other pointer must
    start with ``"/"``. Raises :class:`_JsonPointerError` if any segment does
    not resolve (an unknown object key, an out-of-range or non-numeric array
    index, or an attempt to descend into a scalar).
    """
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise _JsonPointerError(
            f"not a valid RFC 6901 JSON pointer (must start with '/'): {pointer!r}"
        )
    current: JSONValue = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise _JsonPointerError(f"JSON pointer segment {token!r} not found in object")
            current = current[token]
        elif isinstance(current, Sequence) and not isinstance(current, str):
            try:
                index = int(token)
            except ValueError as exc:
                raise _JsonPointerError(
                    f"JSON pointer segment {token!r} is not a valid array index"
                ) from exc
            if index < 0 or index >= len(current):
                raise _JsonPointerError(f"JSON pointer index {index} out of range")
            current = current[index]
        else:
            raise _JsonPointerError(
                f"cannot descend into a scalar value with pointer segment {token!r}"
            )
    return current


def _document_contains(document: JSONValue, value: JSONValue) -> bool:
    """``True`` iff ``value`` occurs, by equality, anywhere within
    ``document`` — itself, one of a mapping's values, or one of a sequence's
    items, walked recursively. The real, deterministic containment check
    behind a caller-supplied ``resolved_value``/``resolved_fragment`` for a
    locator/selector shape this module cannot mechanically resolve itself
    (see the module docstring) — never a blind trust of the caller's claim.
    """
    if document == value:
        return True
    if isinstance(document, Mapping):
        return any(_document_contains(item, value) for item in document.values())
    if isinstance(document, Sequence) and not isinstance(document, str):
        return any(_document_contains(item, value) for item in document)
    return False


def _unvalidated(reason: str) -> SourceVerificationOutcome:
    return SourceVerificationOutcome(
        anchor_validation_status="unvalidated",
        source_access_outcome="unverified_source_access",
        reason=reason,
    )


def _slice_by_char_offset(text: str, start: int, end: int) -> str | None:
    if start < 0 or end < start or end > len(text):
        return None
    return text[start:end]


def _slice_by_line_offset(text: str, start: int, end: int) -> str | None:
    """1-indexed, inclusive line range (``line_start``/``line_end`` per
    ``mrr.contracts.evidence_anchor.TextLocator``), matching how a human
    would cite "lines 3-5" of a document.
    """
    lines = text.split("\n")
    if start < 1 or end < start or end > len(lines):
        return None
    return "\n".join(lines[start - 1 : end])


def validate_text_anchor(
    anchor: EvidenceAnchor, *, local_artifact: LocalTextArtifact | None
) -> SourceVerificationOutcome:
    """Validate a text-kind ``EvidenceAnchor`` by local inspection only.

    ``local_artifact`` is ``None`` exactly when the cited source could not
    be locally opened at all — the literal section 4.8 acceptance case,
    yielding ``"unvalidated"`` / ``"unverified_source_access"``, never
    ``"validated"``.

    Raises:
        ValueError: ``anchor.anchor_kind != "text"`` — a caller/programmer
            error (use :func:`validate_computational_anchor`, or the
            dispatching :func:`validate_evidence_anchor`, instead).
    """
    if anchor.anchor_kind != "text":
        raise ValueError(
            f"validate_text_anchor requires anchor_kind == 'text', got {anchor.anchor_kind!r}"
        )
    if anchor.anchor_unavailable_reason is not None:
        return _unvalidated(
            f"the anchor itself declares anchor_unavailable_reason: "
            f"{anchor.anchor_unavailable_reason}"
        )
    if local_artifact is None:
        return _unvalidated("the cited source's content is not locally available")

    if anchor.snapshot_hash is not None:
        actual_hash = content_hash(local_artifact.full_text.encode("utf-8"))
        if actual_hash != anchor.snapshot_hash:
            return _unvalidated(
                "the locally available content does not match the anchor's snapshot_hash "
                f"(expected {anchor.snapshot_hash!r}, got {actual_hash!r}) — the exact anchored "
                "version is not the one locally available"
            )

    locator = anchor.locator
    fragment: str | None
    if locator is not None and locator.char_start is not None and locator.char_end is not None:
        fragment = _slice_by_char_offset(
            local_artifact.full_text, locator.char_start, locator.char_end
        )
    elif locator is not None and locator.line_start is not None and locator.line_end is not None:
        fragment = _slice_by_line_offset(
            local_artifact.full_text, locator.line_start, locator.line_end
        )
    elif locator is not None:
        # A purely symbolic locator (page/section/paragraph/selector, no
        # offsets) — this tool has no mechanical way to resolve it itself;
        # trust only a caller-supplied fragment that is genuinely present in
        # the available content (see the module docstring).
        candidate = local_artifact.resolved_fragment
        fragment = (
            candidate if candidate is not None and candidate in local_artifact.full_text else None
        )
    else:
        # No locator at all: the anchor's own exact-resolution invariant
        # (mrr.contracts.evidence_anchor.EvidenceAnchor) rests on
        # snapshot_hash/quoted_fragment_hash over the WHOLE document, not a
        # sub-location — the whole available text is the candidate fragment.
        fragment = local_artifact.full_text

    if fragment is None:
        return SourceVerificationOutcome(
            anchor_validation_status="invalid",
            source_access_outcome="verified",
            reason=(
                "the source was locally available but the anchor's locator does not resolve "
                "to any content within it"
            ),
        )

    if anchor.quoted_fragment_hash is not None:
        fragment_hash = content_hash(fragment.encode("utf-8"))
        if fragment_hash != anchor.quoted_fragment_hash:
            return SourceVerificationOutcome(
                anchor_validation_status="invalid",
                source_access_outcome="verified",
                reason=(
                    "the resolved fragment's content hash does not match the anchor's "
                    "quoted_fragment_hash"
                ),
            )

    return SourceVerificationOutcome(
        anchor_validation_status="validated",
        source_access_outcome="verified",
        reason=(
            "the cited source is locally available and the anchor's locator resolves to "
            "matching content"
        ),
    )


def validate_computational_anchor(
    anchor: EvidenceAnchor, *, local_artifact: LocalComputationalArtifact | None
) -> SourceVerificationOutcome:
    """Validate a computational-kind ``EvidenceAnchor`` by local inspection
    only. Mirrors :func:`validate_text_anchor`'s structure exactly — see
    that function's and the module's docstrings.

    Raises:
        ValueError: ``anchor.anchor_kind != "computational"`` — a
            caller/programmer error.
    """
    if anchor.anchor_kind != "computational":
        raise ValueError(
            "validate_computational_anchor requires anchor_kind == 'computational', got "
            f"{anchor.anchor_kind!r}"
        )
    if anchor.anchor_unavailable_reason is not None:
        return _unvalidated(
            f"the anchor itself declares anchor_unavailable_reason: "
            f"{anchor.anchor_unavailable_reason}"
        )
    if local_artifact is None:
        return _unvalidated("the cited run's output is not locally available")

    selector = anchor.selector
    resolved: JSONValue | None
    if selector is not None and selector.json_pointer is not None:
        try:
            resolved = _resolve_json_pointer(local_artifact.document, selector.json_pointer)
        except _JsonPointerError:
            resolved = None
    else:
        candidate = local_artifact.resolved_value
        resolved = (
            candidate
            if candidate is not None and _document_contains(local_artifact.document, candidate)
            else None
        )

    if resolved is None:
        return SourceVerificationOutcome(
            anchor_validation_status="invalid",
            source_access_outcome="verified",
            reason=(
                "the run output was locally available but the anchor's selector does not "
                "resolve to any value within it"
            ),
        )

    return SourceVerificationOutcome(
        anchor_validation_status="validated",
        source_access_outcome="verified",
        reason=("the cited run's output is locally available and the anchor's selector resolves"),
    )


def validate_evidence_anchor(
    anchor: EvidenceAnchor,
    *,
    local_text_artifact: LocalTextArtifact | None = None,
    local_computational_artifact: LocalComputationalArtifact | None = None,
) -> SourceVerificationOutcome:
    """Dispatch to :func:`validate_text_anchor` or
    :func:`validate_computational_anchor` by ``anchor.anchor_kind`` — the
    single entry point ``mrr.services.verifier.orchestrator`` calls. The
    artifact argument for the OTHER kind is simply ignored (a caller need
    only ever supply the one matching ``anchor.anchor_kind``).
    """
    if anchor.anchor_kind == "text":
        return validate_text_anchor(anchor, local_artifact=local_text_artifact)
    return validate_computational_anchor(anchor, local_artifact=local_computational_artifact)


__all__ = [
    "LocalComputationalArtifact",
    "LocalTextArtifact",
    "SourceAccessOutcome",
    "SourceVerificationOutcome",
    "validate_computational_anchor",
    "validate_evidence_anchor",
    "validate_text_anchor",
]
