#!/usr/bin/env python3
"""``scripts/fetch_citation_resolutions.py`` (task-packets/N2-T02a.yaml
R1-R4): a standalone, stdlib-only, gated fetch script OUTSIDE the runtime
that resolves every citation in a committed citation manifest against the
two open, keyless metadata APIs (arXiv, Crossref) and writes the result as a
deterministic ``citation-resolution-snapshot.v1`` document — the exact shape
``mrr.services.citation_audit.service.CitationAuditService`` (the FROZEN
N2-T01 evaluator) already parses.

--- Outside the runtime, by construction (R1) --------------------------------

This module lives under ``scripts/``, not ``packages/`` or ``services/``, and
imports NOTHING from ``mrr.*`` (checked directly by
tests/unit/architecture/test_fetch_citation_resolutions_boundary.py). No
runtime path can import this module and no runtime path gains network
capability from its existence — the audit tool
(``mrr.services.citation_audit.service.CitationAuditService``) stays
no-network, exactly as N2-T01 established. This script is the ONE place in
the whole repository network egress happens, and it is run ONCE, by hand, its
output committed as archive (R4) — never a scheduled or runtime capability.

--- The gate: https + a two-host allowlist, enforced before any socket opens (R1) ---

Every outbound request goes through :func:`_open_url`, which calls
:func:`_check_allowlisted` FIRST: the URL's scheme must be ``https`` and its
host must be exactly ``export.arxiv.org`` or ``api.crossref.org`` — anything
else raises :class:`EgressRefusedError`, a typed refusal, before
``urllib.request.urlopen`` is ever called. The host check uses
``urllib.parse.urlsplit(...).hostname`` (never a substring/prefix check on
the raw URL string), so a lookalike host that merely CONTAINS an allowlisted
name (e.g. a userinfo trick like
``https://export.arxiv.org@evil.example/...``, which parses to hostname
``evil.example``) is refused, not waved through.
tests/unit/scripts/test_fetch_citation_resolutions_egress.py proves this with
a monkeypatched ``urllib.request.urlopen`` that fails the test if it is ever
called for a refused URL.

--- Keyless, by design (R1) ---------------------------------------------------

Both APIs are open. This script reads no environment variable, accepts no
``--token``/``--api-key`` flag, and sends no ``Authorization`` header —
only a descriptive ``User-Agent``. Nothing that could leak into a log or a
committed snapshot is ever held here in the first place.

--- Batching (R2) --------------------------------------------------------------

Every arXiv id is resolved in ONE batched request
(:func:`fetch_arxiv_batch`, ``id_list=<comma-joined ids>``) — not one round
trip per id. Every DOI is resolved with its own Crossref GET
(:func:`fetch_crossref_work`) since Crossref's ``works`` endpoint has no
batch form.

--- Matching discipline: versioned ids stay versioned (R2) --------------------

A requested id ABSENT from the arXiv Atom response resolves to
``resolved=False`` (never dropped, never back-filled). A VERSIONED requested
id (e.g. ``2502.14297v3``) is matched ONLY against an entry with that EXACT
versioned id — never a different version of the same paper, and never
normalised to the base id first. An UNVERSIONED requested id matches an
entry by its base id regardless of which version arXiv's API currently
returns for it (arXiv always echoes back a concrete version even for an
unversioned request — see :func:`_match_arxiv_entry`).

--- Nothing is interpreted, scored, or repaired (R1) ---------------------------

This script transcribes what the two APIs return. It does not judge
correctness, does not compare titles to any claim, and does not retry a
different identifier when one fails — that classification is the frozen
N2-T01 evaluator's job (``mrr.domain.citation_audit``), run separately, over
this script's already-committed output.

--- Determinism (R3) ------------------------------------------------------------

:func:`build_snapshot_document` is pure (no network, no filesystem, no wall
clock — ``fetched_on`` is a given string, not computed inside it) and sorts
its ``resolutions`` by ``citation_id``. :func:`render_snapshot_json` renders
with ``sort_keys=True`` plus a trailing newline. Calling both twice over an
equal set of already-fetched records produces byte-identical output
(tests/unit/scripts/test_fetch_citation_resolutions_snapshot.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The gate: https + a hard, two-host allowlist (task-packets/N2-T02a.yaml R1).
# ---------------------------------------------------------------------------

#: The ONLY two hosts this script will ever open a connection to. Not
#: configurable via any flag or environment variable — widening this set is a
#: code change, reviewable like any other.
ALLOWED_HOSTS: frozenset[str] = frozenset({"export.arxiv.org", "api.crossref.org"})

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
CROSSREF_WORKS_URL = "https://api.crossref.org/works/"

#: Every request carries an explicit timeout (task-packets/N2-T02a.yaml R2).
REQUEST_TIMEOUT_SECONDS = 30

#: Descriptive, non-secret. No API key, token, or Authorization header is
#: ever constructed anywhere in this module.
USER_AGENT = (
    "meridian-research-runtime-citation-audit/1.0 "
    "(+scripts/fetch_citation_resolutions.py; gated N2-T02a fetch; no credentials sent)"
)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_ABS_PREFIX = "http://arxiv.org/abs/"
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$")

SCHEMA_VERSION = "citation-resolution-snapshot.v1"

#: The two resolver descriptions carried in every committed snapshot's
#: ``resolvers`` object (task-packets/N2-T02a.yaml R3) — the documented
#: not-found semantics, worded identically to
#: corpora/e2e-survey/verification/resolution-snapshot.json's own.
_ARXIV_RESOLVER_DESCRIPTION = (
    "arXiv API — https://export.arxiv.org/api/query?id_list=<id> (Atom; a requested id "
    "absent from the response = NOT FOUND)"
)
_DOI_RESOLVER_DESCRIPTION = (
    "Crossref REST — https://api.crossref.org/works/<doi> (JSON; 404 = NOT FOUND)"
)

#: task-packets/N2-T02a.yaml R3: "note (stating verbatim that this records
#: EXISTENCE and the canonical title, and NOT that a source supports the
#: claim it was cited for — N2-T03)". This packet IS N2-T02(a); the deferred
#: support-checking use case is N2-T03, named here accordingly (the
#: e2e-survey snapshot's own note names N2-T02 because it was written before
#: N2-T02 existed as a task — this one names its actual successor).
_EXISTENCE_NOTE = (
    "Point-in-time resolution of each cited identifier against the open metadata APIs "
    "(task-packets/N2-T02a.yaml). Re-runnable: the same batched id_list / per-DOI queries "
    "reproduce these results, so this snapshot is verifiable, not asserted. It records "
    "EXISTENCE and the canonical title each identifier resolves to. It does NOT establish "
    "that the resolved source SUPPORTS the claim it was cited for, nor that any number "
    "attributed to it is consistent with it — both are N2-T03, support-checking and "
    "number-consistency, not yet built. Existence is not support."
)


# ---------------------------------------------------------------------------
# Typed errors — never a silent substitute (AGENTS.md rule 12).
# ---------------------------------------------------------------------------


class FetchScriptError(Exception):
    """Base class for every typed error this script raises."""


class EgressRefusedError(FetchScriptError):
    """Raised by :func:`_check_allowlisted` BEFORE any socket is opened, when
    a request URL is not ``https`` or its host is not in
    :data:`ALLOWED_HOSTS`. Never a warning, never a silent proceed.
    """

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        super().__init__(f"refusing to open {url!r}: {reason}")


class ManifestInputError(FetchScriptError):
    """The manifest at ``--manifest`` cannot even be read as data — missing,
    unreadable, not valid UTF-8/JSON, or the wrong top-level shape.
    """


class UnresolvableManifestEntryError(FetchScriptError):
    """A manifest citation declares neither an ``arxiv`` nor a ``doi``
    identifier (task-packets/N2-T02a.yaml R2: "an entry with neither is
    written out as a typed refusal, never guessed at").
    """

    def __init__(self, citation_id: str) -> None:
        self.citation_id = citation_id
        super().__init__(
            f"citation {citation_id!r} declares neither an arxiv nor a doi identifier — "
            "refusing to guess a resolver"
        )


class UpstreamRefusedError(FetchScriptError):
    """A resolver responded with something other than one of the documented,
    handled outcomes (arXiv: any HTTP status other than 200; Crossref: any
    HTTP status other than 200 or 404) — named with the offending HTTP
    status, never silently folded into ``resolved=False``.
    """


# ---------------------------------------------------------------------------
# The gate itself.
# ---------------------------------------------------------------------------


def _check_allowlisted(url: str) -> None:
    """Typed refusal, called BEFORE any socket is opened. Uses
    ``urllib.parse.urlsplit(...).hostname`` — never a naive substring/prefix
    check on the raw URL string — so a URL that merely CONTAINS an
    allowlisted host name is refused, not waved through.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise EgressRefusedError(url, f"scheme {parsed.scheme!r} is not https")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise EgressRefusedError(
            url,
            f"host {parsed.hostname!r} is not in the allowlist {sorted(ALLOWED_HOSTS)!r}",
        )


def _open_url(url: str) -> bytes:
    """The ONLY function in this module that opens a socket. Enforces the
    https + host allowlist first (:func:`_check_allowlisted`); sends a
    descriptive ``User-Agent`` and no credential of any kind.
    """
    _check_allowlisted(url)
    # The allowlist check above is the honest guard against this being an
    # unrestricted, attacker-steerable fetch: the URL reaching this line is
    # already proven https and host-allowlisted.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# arXiv: one batched request, Atom parsing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArxivResolution:
    requested_id: str
    resolved: bool
    resolved_title: str | None
    resolved_detail: str | None


@dataclass(frozen=True, slots=True)
class _ArxivEntry:
    versioned_id: str
    title: str | None
    first_author: str | None


def _arxiv_base_id(versioned_id: str) -> str:
    return _ARXIV_VERSION_SUFFIX.sub("", versioned_id)


def _has_version_suffix(identifier: str) -> bool:
    return _ARXIV_VERSION_SUFFIX.search(identifier) is not None


def _match_arxiv_entry(requested_id: str, entries: Sequence[_ArxivEntry]) -> _ArxivEntry | None:
    """A versioned ``requested_id`` matches ONLY an entry with that exact
    versioned id. An unversioned ``requested_id`` matches an entry by base id
    regardless of the (always concrete) version arXiv returns for it.
    """
    if _has_version_suffix(requested_id):
        for entry in entries:
            if entry.versioned_id == requested_id:
                return entry
        return None
    for entry in entries:
        if _arxiv_base_id(entry.versioned_id) == requested_id:
            return entry
    return None


def parse_arxiv_atom(raw_xml: bytes, requested_ids: Sequence[str]) -> tuple[ArxivResolution, ...]:
    """Pure — no network. Parses an already-fetched arXiv Atom response and
    maps every id in ``requested_ids`` (in the given order) to a resolution.
    A requested id with no matching entry becomes ``resolved=False``
    (task-packets/N2-T02a.yaml R2) — never dropped from the output, never
    back-filled from another source.
    """
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        raise UpstreamRefusedError(f"arXiv response is not valid XML/Atom: {exc}") from exc

    entries: list[_ArxivEntry] = []
    for entry_el in root.findall(f"{_ATOM_NS}entry"):
        id_el = entry_el.find(f"{_ATOM_NS}id")
        if id_el is None or id_el.text is None:
            continue
        full_id = id_el.text.strip()
        if not full_id.startswith(_ARXIV_ABS_PREFIX):
            # Not a paper entry this script can match to a requested id
            # (e.g. arXiv's own "errors#..." id for a malformed id_list
            # element). Every requested id with no genuine matching entry
            # below ends up resolved=False on its own — nothing here is
            # special-cased, dropped, or back-filled.
            continue
        versioned_id = full_id[len(_ARXIV_ABS_PREFIX) :]

        title_el = entry_el.find(f"{_ATOM_NS}title")
        title = " ".join(title_el.text.split()) if title_el is not None and title_el.text else None

        author_name_els = entry_el.findall(f"{_ATOM_NS}author/{_ATOM_NS}name")
        first_author = None
        if author_name_els and author_name_els[0].text:
            first_author = author_name_els[0].text.strip()

        entries.append(
            _ArxivEntry(versioned_id=versioned_id, title=title, first_author=first_author)
        )

    resolutions: list[ArxivResolution] = []
    for requested_id in requested_ids:
        match = _match_arxiv_entry(requested_id, entries)
        if match is None:
            resolutions.append(
                ArxivResolution(
                    requested_id=requested_id,
                    resolved=False,
                    resolved_title=None,
                    resolved_detail=None,
                )
            )
            continue
        detail = f"first author {match.first_author}" if match.first_author else None
        resolutions.append(
            ArxivResolution(
                requested_id=requested_id,
                resolved=True,
                resolved_title=match.title,
                resolved_detail=detail,
            )
        )
    return tuple(resolutions)


def fetch_arxiv_batch(requested_ids: Sequence[str]) -> tuple[ArxivResolution, ...]:
    """ONE batched request for every requested arXiv id (task-packets/
    N2-T02a.yaml R2: "one round trip for all of them, not one per id").
    """
    if not requested_ids:
        return ()
    query = urllib.parse.urlencode(
        {"id_list": ",".join(requested_ids), "max_results": str(len(requested_ids))}
    )
    url = f"{ARXIV_QUERY_URL}?{query}"
    try:
        raw = _open_url(url)
    except urllib.error.HTTPError as exc:
        raise UpstreamRefusedError(
            f"arXiv returned HTTP {exc.code} for a batch of {len(requested_ids)} id(s)"
        ) from exc
    return parse_arxiv_atom(raw, requested_ids)


# ---------------------------------------------------------------------------
# Crossref: one GET per DOI, JSON parsing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrossrefResolution:
    doi: str
    resolved: bool
    resolved_title: str | None
    resolved_container: str | None
    resolved_detail: str | None


def _format_crossref_date(date_field: Mapping[str, Any] | None) -> str | None:
    """``{"date-parts": [[2026, 3, 25]]}`` -> ``"2026-03-25"``. Degrades
    gracefully to a shorter ``"YYYY"``/``"YYYY-MM"`` when Crossref supplies
    fewer components, and to ``None`` when the field is absent or empty —
    never a fabricated month/day.
    """
    if not date_field:
        return None
    parts = date_field.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return None
    components = parts[0]
    year = components[0]
    if year is None:
        return None
    text = f"{int(year):04d}"
    if len(components) > 1 and components[1] is not None:
        text += f"-{int(components[1]):02d}"
        if len(components) > 2 and components[2] is not None:
            text += f"-{int(components[2]):02d}"
    return text


def parse_crossref_work(raw_json: bytes, doi: str) -> CrossrefResolution:
    """Pure — no network. Parses an already-fetched, HTTP-200 Crossref
    ``works`` response body.
    """
    try:
        document: Any = json.loads(raw_json.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpstreamRefusedError(
            f"Crossref response for {doi!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise UpstreamRefusedError(f"Crossref response for {doi!r} is not a JSON object")
    message = document.get("message")
    if not isinstance(message, dict):
        raise UpstreamRefusedError(f"Crossref response for {doi!r} has no 'message' object")

    titles = message.get("title")
    resolved_title = titles[0] if isinstance(titles, list) and titles else None

    containers = message.get("container-title")
    resolved_container = containers[0] if isinstance(containers, list) and containers else None

    volume = message.get("volume")
    issue = message.get("issue")
    page = message.get("page")

    published = (
        message.get("published")
        or message.get("published-print")
        or message.get("published-online")
    )
    year: Any = None
    if isinstance(published, dict):
        parts = published.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]

    authors = message.get("author")
    first_author_name = None
    if isinstance(authors, list) and authors and isinstance(authors[0], dict):
        given = authors[0].get("given")
        family = authors[0].get("family")
        name = " ".join(part for part in (given, family) if isinstance(part, str) and part)
        first_author_name = name or None

    published_online = _format_crossref_date(message.get("published-online"))

    segments: list[str] = []
    if volume is not None:
        segment = f"vol {volume}"
        if issue is not None:
            segment += f" ({issue})"
        if page is not None:
            segment += f", pp. {page}"
        if year is not None:
            segment += f", {year}"
        segments.append(segment)
    elif page is not None:
        segment = f"pp. {page}"
        if year is not None:
            segment += f", {year}"
        segments.append(segment)
    elif year is not None:
        segments.append(str(year))
    if first_author_name is not None:
        segments.append(f"first author {first_author_name}")
    if published_online is not None:
        segments.append(f"published online {published_online}")
    resolved_detail = "; ".join(segments) if segments else None

    return CrossrefResolution(
        doi=doi,
        resolved=True,
        resolved_title=resolved_title,
        resolved_container=resolved_container,
        resolved_detail=resolved_detail,
    )


def fetch_crossref_work(doi: str) -> CrossrefResolution:
    """One GET per DOI (task-packets/N2-T02a.yaml R2). HTTP 404 ->
    ``resolved=False``; any other non-200 -> :class:`UpstreamRefusedError`
    naming the status, never a silent ``resolved=False``.
    """
    url = f"{CROSSREF_WORKS_URL}{urllib.parse.quote(doi, safe='')}"
    try:
        raw = _open_url(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return CrossrefResolution(
                doi=doi,
                resolved=False,
                resolved_title=None,
                resolved_container=None,
                resolved_detail=None,
            )
        raise UpstreamRefusedError(f"Crossref returned HTTP {exc.code} for doi {doi!r}") from exc
    return parse_crossref_work(raw, doi)


# ---------------------------------------------------------------------------
# Manifest reading (this script's own minimal, decoupled read — the frozen
# N2-T01 evaluator does its own, separate parse of the same file; this
# script imports nothing from it, R1).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManifestCitation:
    citation_id: str
    arxiv_id: str | None
    doi: str | None


def read_manifest(path: Path) -> tuple[ManifestCitation, ...]:
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ManifestInputError(f"{path}: cannot read file ({exc})") from exc
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestInputError(f"{path}: not valid UTF-8 ({exc})") from exc
    try:
        document: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestInputError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise ManifestInputError(f"{path}: top-level document must be a JSON object")
    raw_citations = document.get("citations")
    if not isinstance(raw_citations, list):
        raise ManifestInputError(f"{path}: 'citations' must be a JSON array")

    citations: list[ManifestCitation] = []
    for raw_entry in raw_citations:
        if not isinstance(raw_entry, dict):
            raise ManifestInputError(f"{path}: a citations[] element is not a JSON object")
        citation_id = raw_entry.get("citation_id")
        if not isinstance(citation_id, str):
            raise ManifestInputError(
                f"{path}: a citations[] element is missing a string 'citation_id'"
            )
        identifiers = raw_entry.get("identifiers")
        if not isinstance(identifiers, dict):
            raise ManifestInputError(
                f"{path}: citations[{citation_id!r}].identifiers must be a JSON object"
            )
        arxiv_id = identifiers.get("arxiv")
        doi = identifiers.get("doi")
        citations.append(
            ManifestCitation(
                citation_id=citation_id,
                arxiv_id=str(arxiv_id) if arxiv_id is not None else None,
                doi=str(doi) if doi is not None else None,
            )
        )
    return tuple(citations)


def group_by_resolver(
    citations: Sequence[ManifestCitation],
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Group manifest citations by resolver (task-packets/N2-T02a.yaml R2:
    "every entry with identifiers.arxiv goes to arXiv, every entry with
    identifiers.doi (and no arxiv) goes to Crossref"). Raises
    :class:`UnresolvableManifestEntryError` for an entry with neither —
    never guessed at. Returns ``(arxiv_group, doi_group)``, each a tuple of
    ``(citation_id, identifier)`` pairs.
    """
    arxiv_group: list[tuple[str, str]] = []
    doi_group: list[tuple[str, str]] = []
    for citation in citations:
        if citation.arxiv_id is not None:
            arxiv_group.append((citation.citation_id, citation.arxiv_id))
        elif citation.doi is not None:
            doi_group.append((citation.citation_id, citation.doi))
        else:
            raise UnresolvableManifestEntryError(citation.citation_id)
    return tuple(arxiv_group), tuple(doi_group)


# ---------------------------------------------------------------------------
# Snapshot assembly + deterministic rendering (task-packets/N2-T02a.yaml R3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolutionRecord:
    citation_id: str
    identifier: str
    resolver: str
    resolved: bool
    resolved_title: str | None
    resolved_container: str | None = None
    resolved_detail: str | None = None


def _resolution_record_to_dict(record: ResolutionRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "citation_id": record.citation_id,
        "identifier": record.identifier,
        "resolver": record.resolver,
        "resolved": record.resolved,
        "resolved_title": record.resolved_title,
    }
    if record.resolved_container is not None:
        payload["resolved_container"] = record.resolved_container
    if record.resolved_detail is not None:
        payload["resolved_detail"] = record.resolved_detail
    return payload


def build_snapshot_document(
    *,
    manifest_relative_path: str,
    fetched_on: str,
    records: Sequence[ResolutionRecord],
) -> dict[str, Any]:
    """Pure — no network, no filesystem, no wall clock (``fetched_on`` is
    given, not computed here). Assembles the committed
    ``citation-resolution-snapshot.v1`` shape (task-packets/N2-T02a.yaml R3),
    ``resolutions`` sorted by ``citation_id``, so calling this twice over an
    equal ``records`` sequence yields an equal document regardless of the
    caller's own ordering.
    """
    ordered = sorted(records, key=lambda record: record.citation_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest": manifest_relative_path,
        "fetched_on": fetched_on,
        "resolvers": {
            "arxiv": _ARXIV_RESOLVER_DESCRIPTION,
            "doi": _DOI_RESOLVER_DESCRIPTION,
        },
        "note": _EXISTENCE_NOTE,
        "resolutions": [_resolution_record_to_dict(record) for record in ordered],
    }


def render_snapshot_json(document: Mapping[str, Any]) -> str:
    """Deterministic rendering: ``sort_keys=True`` plus a trailing newline
    (task-packets/N2-T02a.yaml R3) — two calls over an equal ``document``
    produce byte-identical output.
    """
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Orchestration + CLI.
# ---------------------------------------------------------------------------

_EXIT_SUCCESS = 0
_EXIT_OUTPUT_CONFLICT = 1
_EXIT_MANIFEST_INPUT_ERROR = 2
_EXIT_REFUSED = 3


def resolve_all(citations: Sequence[ManifestCitation]) -> tuple[ResolutionRecord, ...]:
    """Fetch resolutions for every manifest citation: one batched arXiv
    request for every arxiv id, then one Crossref GET per DOI. Propagates
    the typed refusals defined above; never silently drops or back-fills a
    citation, and covers every citation passed in (task-packets/N2-T02a.yaml
    R1/R2/R3).
    """
    arxiv_group, doi_group = group_by_resolver(citations)

    records: list[ResolutionRecord] = []

    if arxiv_group:
        arxiv_ids = [arxiv_id for _citation_id, arxiv_id in arxiv_group]
        arxiv_resolutions = fetch_arxiv_batch(arxiv_ids)
        for (citation_id, arxiv_id), resolution in zip(arxiv_group, arxiv_resolutions, strict=True):
            records.append(
                ResolutionRecord(
                    citation_id=citation_id,
                    identifier=f"arxiv:{arxiv_id}",
                    resolver="arxiv",
                    resolved=resolution.resolved,
                    resolved_title=resolution.resolved_title,
                    resolved_detail=resolution.resolved_detail,
                )
            )

    for citation_id, doi in doi_group:
        crossref_resolution = fetch_crossref_work(doi)
        records.append(
            ResolutionRecord(
                citation_id=citation_id,
                identifier=f"doi:{doi}",
                resolver="doi",
                resolved=crossref_resolution.resolved,
                resolved_title=crossref_resolution.resolved_title,
                resolved_container=crossref_resolution.resolved_container,
                resolved_detail=crossref_resolution.resolved_detail,
            )
        )

    return tuple(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_citation_resolutions.py",
        description=(
            "Gated, keyless, stdlib-only fetch of citation resolutions against the open "
            "arXiv and Crossref metadata APIs (task-packets/N2-T02a.yaml). Run once against "
            "a committed citation manifest; the output is committed to the repository as "
            "archive and is never regenerated to change a result."
        ),
    )
    parser.add_argument(
        "--manifest", required=True, type=Path, help="Path to the citation manifest to resolve."
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path the resolution snapshot is written to. Must not already exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path: Path = args.manifest
    output_path: Path = args.output

    if output_path.exists():
        print(
            f"fetch_citation_resolutions: --output {output_path} already exists — refusing to "
            "overwrite a committed snapshot (AGENTS.md: never silently overwrite a prior "
            "revision; re-derive a new path if a fresh fetch is genuinely intended).",
            file=sys.stderr,
        )
        return _EXIT_OUTPUT_CONFLICT

    try:
        citations = read_manifest(manifest_path)
    except ManifestInputError as exc:
        print(f"fetch_citation_resolutions: {exc}", file=sys.stderr)
        return _EXIT_MANIFEST_INPUT_ERROR

    try:
        records = resolve_all(citations)
    except (EgressRefusedError, UnresolvableManifestEntryError, UpstreamRefusedError) as exc:
        print(f"fetch_citation_resolutions: refused — {exc}", file=sys.stderr)
        return _EXIT_REFUSED

    fetched_on = date.today().isoformat()
    manifest_relative_path = os.path.relpath(manifest_path, start=output_path.parent)
    document = build_snapshot_document(
        manifest_relative_path=manifest_relative_path,
        fetched_on=fetched_on,
        records=records,
    )
    rendered = render_snapshot_json(document)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    summary = {
        "output": str(output_path),
        "citations": len(records),
        "resolved": sum(1 for record in records if record.resolved),
    }
    print(json.dumps(summary, sort_keys=True))
    return _EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
