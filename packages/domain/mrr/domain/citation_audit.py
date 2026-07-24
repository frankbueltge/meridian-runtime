"""Pure, dependency-free citation existence + title audit core (task-packets/
N2-T01.yaml R1). No network, no filesystem, no database — every function here
takes already-loaded values and returns a plain, typed result. Hand-rolled
identifier-pattern checks and title comparison (task-packets/N2-T01.yaml
derived_decisions (d)): both are short enough that pulling in a fuzzy-matching
or URL-validation dependency would widen this repo's dependency footprint and
``make security-check`` surface for no benefit, mirroring
``mrr.domain.agreement``'s own "hand-rolled, no new dependency" precedent
(task-packets/N1-T01.yaml derived_decisions (d)).

--- Existence is not support (the honesty boundary this module exists for) ---

This module answers exactly two questions about a cited reference: does the
declared identifier resolve to a real registered work (EXISTENCE), and, where
a label is claimed as the work's title, does that label match the resolved
title (TITLE correctness). It never asks whether the resolved source SUPPORTS
the claim it was cited for (task-packets/N2-T01.yaml specification_gaps:
that is N2-T02, a separate, LLM/human-touching use case) or whether a number
attributed to the source is consistent (N2-T03). See
``mrr.domain.citation_audit_report`` for the honesty header every report
built from this module's results carries — the N2 analogue of
``mrr.domain.agreement``'s "measures reliability, not validity" stance.

--- Five distinct statuses, never collapsed (AGENTS.md prohibited shortcut) --

:func:`classify_citation` returns one of exactly five ``CitationStatus``
values — ``"resolved"``, ``"not_found"`` (potential fabrication),
``"title_mismatch"`` (potential misattribution), ``"unverifiable"``
(metadata-only / paywalled / could-not-determine — never counted as either a
pass or a fail), and ``"malformed"`` (an ill-formed declared identifier). No
caller of this module may fold two of these into one generic outcome; the
Literal type itself is the only closed set in scope.

--- Determinism (task-packets/N2-T01.yaml invariant) -------------------------

No wall clock anywhere in this module. :func:`classify_citations` sorts its
output by ``citation_id`` explicitly (never relying on the caller's own
manifest order, and never a ``dict``/``set`` iteration order), so calling it
twice over equal inputs yields an identical sequence of
:class:`CitationVerdict` values.

--- Typed errors, never a silent substitute (AGENTS.md rule 12) -------------

A manifest citation with no matching resolution in the snapshot raises
:class:`MissingResolutionError`, naming the offending ``citation_id`` — never
silently treated as ``"not_found"`` (task-packets/N2-T01.yaml R1/invariant:
"A manifest citation with no matching resolution is a typed refusal, never a
silent not_found"). The malformed-identifier check happens BEFORE this
lookup is even attempted (task-packets/N2-T01.yaml R1: "decided BEFORE
consulting the snapshot") — an entry with no well-formed declared identifier
is reported ``"malformed"`` even when the snapshot has no resolution for it
at all.
"""

from __future__ import annotations

import re
import string
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

#: The closed set of five citation-audit statuses (task-packets/N2-T01.yaml
#: R1/derived_decisions (c)). Never collapsed into a generic pass/fail.
CitationStatus = Literal["resolved", "not_found", "title_mismatch", "unverifiable", "malformed"]

#: Every declared status, in the fixed order the summary counts are reported
#: in (task-packets/N2-T01.yaml R2) — re-exported here as the single source
#: of truth for that ordering.
CITATION_STATUSES: tuple[CitationStatus, ...] = (
    "resolved",
    "not_found",
    "title_mismatch",
    "unverifiable",
    "malformed",
)


class CitationAuditError(Exception):
    """Base class for every typed error this module raises."""


class MissingResolutionError(CitationAuditError):
    """Raised when a manifest citation has no matching resolution in the
    snapshot — a structural gap between the manifest and the snapshot, never
    silently treated as ``"not_found"`` (task-packets/N2-T01.yaml R1/
    invariant). Carries ``citation_id`` so a caller can report exactly which
    citation is missing without parsing the message string.
    """

    def __init__(self, citation_id: str) -> None:
        self.citation_id = citation_id
        super().__init__(f"citation {citation_id!r} has no matching resolution in the snapshot")


# ---------------------------------------------------------------------------
# Identifier well-formedness (task-packets/N2-T01.yaml "Exact identifier
# patterns" — implemented verbatim, not improvised).
# ---------------------------------------------------------------------------

#: arXiv new scheme: YYMM.NNNNN, optional version suffix. YY is any two
#: digits (the year); MM is constrained to a plausible month, 01-12; the
#: sequence number is 4 or 5 digits; ``vN`` is optional.
_ARXIV_PATTERN = re.compile(r"\d{2}(0[1-9]|1[0-2])\.\d{4,5}(v\d+)?")

#: DOI: "10." then a 4-9 digit registrant code, "/", then a non-empty,
#: whitespace-free suffix.
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/\S+")


def is_wellformed_arxiv(value: str) -> bool:
    """``True`` iff ``value`` matches the arXiv new-scheme identifier pattern
    ``YYMM.NNNNN[vN]`` (task-packets/N2-T01.yaml: e.g. ``2511.02824`` valid,
    ``2513.02824`` invalid — month ``13`` does not exist, ``abcd.1234``
    invalid — not digits).
    """
    return _ARXIV_PATTERN.fullmatch(value) is not None


def is_wellformed_doi(value: str) -> bool:
    """``True`` iff ``value`` matches ``^10\\.\\d{4,9}/\\S+$`` (task-packets/
    N2-T01.yaml: e.g. ``10.1038/s41586-026-10265-5`` valid).
    """
    return _DOI_PATTERN.fullmatch(value) is not None


def is_wellformed_url(value: str) -> bool:
    """``True`` iff ``value`` is a well-formed http(s) URL — a scheme of
    ``http``/``https`` and a non-empty host, with no embedded whitespace
    (task-packets/N2-T01.yaml: "a well-formed http(s) URL (scheme + host)").
    Hand-rolled over :func:`urllib.parse.urlparse` (stdlib, not a new
    dependency) rather than a regex — URL syntax has enough legitimate
    variation (ports, userinfo, IPv6 hosts) that re-deriving it as a regex
    would either reject valid URLs or accept malformed ones no scheme+host
    check would.
    """
    if not value or any(character.isspace() for character in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _is_wellformed(kind: Literal["arxiv", "doi", "url"], value: str) -> bool:
    if kind == "arxiv":
        return is_wellformed_arxiv(value)
    if kind == "doi":
        return is_wellformed_doi(value)
    return is_wellformed_url(value)


# ---------------------------------------------------------------------------
# Title comparison (task-packets/N2-T01.yaml "Exact title comparison").
# ---------------------------------------------------------------------------

#: Extra, non-ASCII punctuation stripped from a normalised title's ends,
#: alongside :data:`string.punctuation` — curly quotes, em/en dashes, an
#: ellipsis character, and guillemets/low quotes, none of which
#: :data:`string.punctuation` (ASCII-only) covers.
_EXTRA_PUNCTUATION = "“”‘’—–…«»„"
_STRIP_CHARS = string.punctuation + _EXTRA_PUNCTUATION


def normalise_title(title: str) -> str:
    """Casefold, collapse internal whitespace runs to a single space, and
    strip leading/trailing whitespace and surrounding punctuation
    (task-packets/N2-T01.yaml "Exact title comparison").

    ``" ".join(title.casefold().split())`` both strips leading/trailing
    whitespace and collapses every internal whitespace run to one space in a
    single pass (``str.split()`` with no argument already splits on any
    whitespace run and discards empty leading/trailing pieces); the
    surrounding-punctuation strip is a second, explicit pass so a title like
    ``"'Kosmos: An AI Scientist.'"`` normalises to ``"kosmos: an ai
    scientist"`` (the internal colon is not "surrounding" and stays).
    """
    collapsed = " ".join(title.casefold().split())
    return collapsed.strip(_STRIP_CHARS)


@dataclass(frozen=True, slots=True)
class TitleMatchResult:
    """The named result of a title comparison (task-packets/N2-T01.yaml R1:
    "returning a typed result, never a bare bool guessed from a fuzzy
    ratio"). ``matches`` is the definitive verdict; ``method`` names exactly
    which rule decided it — ``"exact"`` (the two normalised titles are
    identical), ``"prefix"`` (one normalised title is a prefix of the
    other — "a cited label that is the work's title prefix counts as a
    match"), or ``"no_match"``.
    """

    matches: bool
    method: Literal["exact", "prefix", "no_match"]


def title_matches(claimed: str, resolved: str) -> TitleMatchResult:
    """``True`` (with ``method="exact"``) iff the two titles are identical
    after :func:`normalise_title`; ``True`` (with ``method="prefix"``) iff
    one normalised title is a prefix of the other; else ``False`` (with
    ``method="no_match"``). Never a fuzzy similarity ratio.
    """
    normalised_claimed = normalise_title(claimed)
    normalised_resolved = normalise_title(resolved)

    if normalised_claimed == normalised_resolved:
        return TitleMatchResult(matches=True, method="exact")

    if (
        normalised_claimed
        and normalised_resolved
        and (
            normalised_resolved.startswith(normalised_claimed)
            or normalised_claimed.startswith(normalised_resolved)
        )
    ):
        return TitleMatchResult(matches=True, method="prefix")

    return TitleMatchResult(matches=False, method="no_match")


# ---------------------------------------------------------------------------
# Manifest / snapshot input shapes and classification.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CitationEntry:
    """One declared citation from the committed citation manifest
    (task-packets/N2-T01.yaml R3: e.g. ``corpora/e2e-survey/citations
    .manifest.json``). ``arxiv_id``/``doi`` are ``None`` when the manifest's
    own ``identifiers`` object does not declare that key — never an empty
    string standing in for "absent".
    """

    citation_id: str
    cited_as: str
    cited_url: str
    arxiv_id: str | None
    doi: str | None
    claimed_title: str | None

    def declared_identifiers(self) -> tuple[tuple[Literal["arxiv", "doi", "url"], str], ...]:
        """Every identifier this entry actually declares, tagged by kind —
        the URL is always included (task-packets/N2-T01.yaml R1: "declared
        identifiers (arxiv/doi/url)"), since ``cited_url`` is itself a
        declared, checkable identifier, not merely a convenience link.
        """
        declared: list[tuple[Literal["arxiv", "doi", "url"], str]] = []
        if self.arxiv_id is not None:
            declared.append(("arxiv", self.arxiv_id))
        if self.doi is not None:
            declared.append(("doi", self.doi))
        declared.append(("url", self.cited_url))
        return tuple(declared)

    def display_identifier(self) -> str:
        """The identifier shown in the report's per-citation row — the most
        specific declared identifier (arXiv, else DOI, else the raw cited
        URL), resolver-tagged the same way the resolution snapshot's own
        ``identifier`` field is (e.g. ``"arxiv:2511.02824"``).
        """
        if self.arxiv_id is not None:
            return f"arxiv:{self.arxiv_id}"
        if self.doi is not None:
            return f"doi:{self.doi}"
        return self.cited_url


@dataclass(frozen=True, slots=True)
class CitationResolution:
    """One resolution from the committed resolution snapshot
    (task-packets/N2-T01.yaml R3: e.g. ``corpora/e2e-survey/verification/
    resolution-snapshot.json``). ``unverifiable`` defaults to ``False``
    since the real snapshot's resolutions carry no such key at all — an
    absent flag means "not flagged unverifiable", never a guess.
    """

    citation_id: str
    resolved: bool
    resolved_title: str | None
    unverifiable: bool = False


@dataclass(frozen=True, slots=True)
class CitationVerdict:
    """The classification result for one citation — the row
    ``mrr.domain.citation_audit_report`` projects into its Pydantic report
    (task-packets/N2-T01.yaml R2).
    """

    citation_id: str
    cited_as: str
    identifier: str
    status: CitationStatus
    resolved_title: str | None
    reason: str


def classify_citation(
    entry: CitationEntry, resolution: CitationResolution | None
) -> CitationVerdict:
    """Classify one citation into exactly one of the five
    :data:`CITATION_STATUSES` (task-packets/N2-T01.yaml R1's exact order):

    1. If NONE of ``entry``'s declared identifiers is well-formed ->
       ``"malformed"`` — decided BEFORE ``resolution`` is even inspected (it
       may legitimately be ``None`` here without raising).
    2. Else, if ``resolution`` is ``None`` -> raise
       :class:`MissingResolutionError`.
    3. Else, if ``resolution.unverifiable`` -> ``"unverifiable"`` — never
       counted as resolved or not_found, regardless of ``resolution
       .resolved``.
    4. Else, if not ``resolution.resolved`` -> ``"not_found"``.
    5. Else (resolved and not unverifiable): if ``entry.claimed_title`` is
       declared and :func:`title_matches` says it does NOT match
       ``resolution.resolved_title`` -> ``"title_mismatch"``; otherwise ->
       ``"resolved"``.

    Raises:
        MissingResolutionError: ``entry`` has at least one well-formed
            declared identifier, but ``resolution`` is ``None``.
    """
    declared = entry.declared_identifiers()
    any_wellformed = any(_is_wellformed(kind, value) for kind, value in declared)

    if not any_wellformed:
        declared_repr = ", ".join(f"{kind}={value!r}" for kind, value in declared)
        return CitationVerdict(
            citation_id=entry.citation_id,
            cited_as=entry.cited_as,
            identifier=entry.display_identifier(),
            status="malformed",
            resolved_title=None,
            reason=(
                f"no declared identifier is well-formed ({declared_repr}) — decided before "
                "consulting the resolution snapshot"
            ),
        )

    if resolution is None:
        raise MissingResolutionError(entry.citation_id)

    if resolution.unverifiable:
        return CitationVerdict(
            citation_id=entry.citation_id,
            cited_as=entry.cited_as,
            identifier=entry.display_identifier(),
            status="unverifiable",
            resolved_title=resolution.resolved_title,
            reason=(
                "the resolution snapshot flags this citation unverifiable (metadata-only / "
                "paywalled / could-not-determine) — never counted as resolved or not_found"
            ),
        )

    if not resolution.resolved:
        return CitationVerdict(
            citation_id=entry.citation_id,
            cited_as=entry.cited_as,
            identifier=entry.display_identifier(),
            status="not_found",
            resolved_title=resolution.resolved_title,
            reason="the resolution snapshot reports resolved=false — potential fabrication",
        )

    if entry.claimed_title is not None:
        match = title_matches(entry.claimed_title, resolution.resolved_title or "")
        if not match.matches:
            return CitationVerdict(
                citation_id=entry.citation_id,
                cited_as=entry.cited_as,
                identifier=entry.display_identifier(),
                status="title_mismatch",
                resolved_title=resolution.resolved_title,
                reason=(
                    f"claimed title {entry.claimed_title!r} does not match resolved title "
                    f"{resolution.resolved_title!r} — potential misattribution"
                ),
            )
        return CitationVerdict(
            citation_id=entry.citation_id,
            cited_as=entry.cited_as,
            identifier=entry.display_identifier(),
            status="resolved",
            resolved_title=resolution.resolved_title,
            reason=f"identifier resolves and the claimed title matches ({match.method})",
        )

    return CitationVerdict(
        citation_id=entry.citation_id,
        cited_as=entry.cited_as,
        identifier=entry.display_identifier(),
        status="resolved",
        resolved_title=resolution.resolved_title,
        reason="identifier resolves; no claimed title was declared to compare against",
    )


def classify_citations(
    entries: Sequence[CitationEntry], resolutions: Sequence[CitationResolution]
) -> tuple[CitationVerdict, ...]:
    """Classify every entry in ``entries``, sorted by ``citation_id`` for a
    deterministic, caller-order-independent result (task-packets/N2-T01.yaml
    invariant: "output ordered by citation_id"). Builds the ``resolutions``
    lookup once, by ``citation_id``.

    Raises:
        MissingResolutionError: propagated from :func:`classify_citation` for
            the first (in ``citation_id`` order) entry with at least one
            well-formed declared identifier but no matching resolution.
    """
    resolutions_by_id = {resolution.citation_id: resolution for resolution in resolutions}
    ordered_entries = sorted(entries, key=lambda entry: entry.citation_id)
    return tuple(
        classify_citation(entry, resolutions_by_id.get(entry.citation_id))
        for entry in ordered_entries
    )
