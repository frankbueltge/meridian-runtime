#!/usr/bin/env python3
"""``scripts/fetch_source_content.py`` (task-packets/N2-T03a.yaml): a
standalone, stdlib-only, gated fetch script OUTSIDE the runtime that
captures the ABSTRACT of every citation in the committed
``corpora/research-records/citations.manifest.json`` against the same two
open, keyless metadata APIs ``scripts/fetch_citation_resolutions.py`` (T02a)
already uses, and writes the result as a deterministic
``source-content-snapshot.v1`` document — the checkable excerpt the offline,
no-network support audit (N2-T03b, ``mrr audit support``) reads.

--- Why this script exists at all (task-packets/N2-T03a.yaml reviewer_resolution) ---

The T02a resolution snapshot carries only ``resolved``/``resolved_title``/
``resolved_detail``/``resolver`` per citation — no abstract, no full text.
T02a's own script never reads the arXiv Atom ``summary`` element at all. A
support audit needs SOME checkable source content to compare a claim
against, and none existed in the repository before this script. This module
supplies exactly one thing: the ABSTRACT, and nothing more — no PDF
download, no scraping, no ``arxiv.org/pdf/...``. An abstract covers only a
fraction of a paper's own claims (measured at the N2-T03 derivation,
docs/design/2026-07-25-n2-t03-derivation.md, at roughly 28 percent of this
corpus's numeric-token claims); this snapshot names that narrowness in its
own ``note`` field rather than pretending the excerpt is the whole source —
exactly the "would invite the pretended check N2-T02b refused" failure mode
the task packet warns against.

--- Outside the runtime, by construction (mirrors T02a) --------------------

This module lives under ``scripts/``, not ``packages/`` or ``services/``,
and imports NOTHING from ``mrr.*`` and NOTHING from
``scripts.fetch_citation_resolutions`` either — a second, fully independent,
decoupled read/fetch, exactly matching that script's own "this script's own
minimal, decoupled read" precedent applied one level further (T02a did not
import from the N2-T01 evaluator; this script does not import from T02a).
No runtime path can import this module and no runtime path gains network
capability from its existence. This is run ONCE, by hand, its output
committed as archive — never a scheduled or runtime capability.

--- The gate: https + the SAME two-host allowlist, enforced before any socket opens ---

Every outbound request goes through :func:`_open_url`, which calls
:func:`_check_allowlisted` FIRST: the URL's scheme must be ``https`` and its
host must be exactly ``export.arxiv.org`` or ``api.crossref.org`` — anything
else raises :class:`EgressRefusedError`, a typed refusal, before
``urllib.request.urlopen`` is ever called. The host check uses
``urllib.parse.urlsplit(...).hostname`` (never a substring/prefix check on
the raw URL string), so a lookalike host that merely CONTAINS an allowlisted
name is refused, not waved through — identical guard to T02a's, verified
here by its own egress test (never imported from T02a; this script's
allowlist is its own, so it stays correct even if T02a's ever changed).

--- Keyless, by design -------------------------------------------------------

Both APIs are open. This script reads no environment variable, accepts no
``--token``/``--api-key`` flag, and sends no ``Authorization`` header — only
a descriptive ``User-Agent``.

--- Batching ------------------------------------------------------------------

Every arXiv id is resolved in ONE batched request (:func:`fetch_arxiv_batch`,
``id_list=<comma-joined ids>``) — not one round trip per id. The single DOI
in this manifest is resolved with its own Crossref GET
(:func:`fetch_crossref_work`), since Crossref's ``works`` endpoint has no
batch form (mirrors T02a exactly).

--- Matching discipline: versioned ids stay versioned (mirrors T02a) ---------

A requested id ABSENT from the arXiv Atom response is recorded with
``excerpt_available=False`` and a typed reason (never dropped, never
back-filled). A VERSIONED requested id (e.g. ``2502.14297v3``) is matched
ONLY against an entry with that EXACT versioned id — never a different
version of the same paper, and never normalised to the base id first. An
UNVERSIONED requested id matches an entry by its base id regardless of which
version arXiv's API currently returns for it.

--- JATS tag stripping is a declared, deterministic transformation ----------

The Crossref ``abstract`` field for the one Nature DOI in this manifest is
JATS XML (e.g. ``<jats:title>Abstract</jats:title><jats:p>... <jats:sup>1,2
</jats:sup> ...</jats:p>``), not plain text. :func:`strip_jats_tags` is a
PURE, separately testable function: it drops the ``<jats:title>...
</jats:title>`` section-label block entirely (it is structural markup
naming the section "Abstract", not abstract content), and replaces every
other ``jats:``-namespaced tag with a single space while KEEPING its inner
text (e.g. the reference-marker text inside a ``<jats:sup>`` element is
preserved) — a declared, deterministic cleanup, never silent. This is
exactly the transformation that makes the JATS reference-marker text (e.g.
``1,2`` / ``3–5`` / ``6,7``) visible as plain digits in the excerpt text —
which is precisely the source of the anchor-term requirement documented in
``corpora/research-records/claims.manifest.json``'s own ``anchor_note``: a
bare numeric comparison over this same excerpt produced 3 false supports at
the N2-T03 derivation from exactly these reference markers. This script does
not interpret that risk away — it records the excerpt exactly as the API
returns it (after the one declared, deterministic tag-stripping
transformation); guarding against the false-support risk is N2-T03b's job,
not this fetch script's.

--- Whitespace normalisation ---------------------------------------------------

:func:`_normalize_excerpt_whitespace` collapses every run of whitespace
(including the JATS response's own indentation/newlines, and Unicode
no-break spaces such as the Nature abstract's own ``"a\\xa0top-tier"``) to a
single ASCII space and strips the ends — mirrors T02a's identical
``" ".join(title_el.text.split())`` normalisation of arXiv Atom
``<title>`` text, applied here to ``<summary>``/JATS-abstract text instead.
Applied uniformly to both resolvers' excerpt text, so a downstream character-
offset-based anchor window (N2-T03b) measures distances in the SAME
normalised text this script commits, not in raw, differently-whitespaced
API output.

--- Nothing is interpreted, scored, or repaired ------------------------------

This script transcribes what the two APIs return, after exactly the two
declared, deterministic transformations above (JATS-tag stripping,
whitespace normalisation) — it does not judge whether an excerpt supports
any claim, does not score anything, and does not retry a different
identifier when one fails. That classification is N2-T03b's job
(``mrr audit support``), run separately, over this script's already-
committed output.

--- Determinism ---------------------------------------------------------------

:func:`build_snapshot_document` is pure (no network, no filesystem, no wall
clock — ``fetched_on`` is a given string, not computed inside it) and sorts
its ``excerpts`` by ``citation_id``. :func:`render_snapshot_json` renders
with ``sort_keys=True`` plus a trailing newline. Calling both twice over an
equal set of already-fetched records produces byte-identical output.

--- XML hardening: refuse a DTD before any parser sees it (task-packets/S1-T01.yaml) ---

Every response :func:`parse_arxiv_summaries` parses is checked FIRST, by
:func:`_refuse_if_dtd_declared`, for a ``<!DOCTYPE`` or ``<!ENTITY``
declaration anywhere in the document — a pure, separately testable,
case-insensitive scan of the raw bytes, run BEFORE ``ET.fromstring`` ever
sees them (declared independently here, not imported from
``scripts/fetch_citation_resolutions.py`` — mirrors that module's identical
guard, see this module's own "decoupled read" precedent applied one level
further). Verified empirically at the S1-T01 derivation
(docs/design/2026-07-26-s1-derivation-script-edge-security.md): a 4-level
"billion laughs" entity-expansion document EXPANDS to 30,000 characters on
this interpreter (a real memory-exhaustion vector), while the real arXiv
Atom response carries neither declaration at all (Atom never needs a DTD;
Crossref's transport is JSON) — so a document that declares one is always
anomalous at this edge, and refusing it outright is stricter than
``defusedxml``'s safe-parse approach and needs no new dependency.

This closes the entity-EXPANSION half of the XML-attack surface ONLY. On
this interpreter, ``ET.fromstring`` already raises ``ParseError: undefined
entity`` for an external-entity reference (e.g. ``file:///etc/passwd``)
without resolving it — XXE is not reachable here to begin with, and this
refusal makes no claim to have fixed it. Saying otherwise would be theatre.

--- Response-size limit: a bounded read, not read-then-measure (task-packets/S1-T01.yaml) ---

:func:`_open_url` reads at most :data:`MAX_RESPONSE_BYTES` + 1 bytes
(``response.read(MAX_RESPONSE_BYTES + 1)``) and refuses with
:class:`ResponseTooLargeError` if that many bytes came back. The read itself
is bounded — never "read everything with a bare ``response.read()``, then
measure a ``len()``", which would already have done the memory-exhaustion
damage a compromised or spoofed allowlisted host could inflict before any
check ran. A real arXiv response for one id is 5,426 bytes (measured at the
S1-T01 derivation); the limit is a generous single-digit-megabyte module
constant, not a literal buried in the call.
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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# The gate: https + a hard, two-host allowlist (task-packets/N2-T03a.yaml
# egress_frame — the SAME two hosts T02a uses, declared independently here
# rather than imported, so this script's guard stays correct on its own).
# ---------------------------------------------------------------------------

#: The ONLY two hosts this script will ever open a connection to. Not
#: configurable via any flag or environment variable — widening this set is
#: a code change, reviewable like any other.
ALLOWED_HOSTS: frozenset[str] = frozenset({"export.arxiv.org", "api.crossref.org"})

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
CROSSREF_WORKS_URL = "https://api.crossref.org/works/"

#: Every request carries an explicit timeout.
REQUEST_TIMEOUT_SECONDS = 30

#: task-packets/S1-T01.yaml: a named module constant, not a literal buried in
#: a call. A real arXiv response for one id is 5,426 bytes (measured at the
#: S1-T01 derivation); a 20-id batch is in the low six figures. 8 MiB is a
#: generous single-digit-megabyte limit — plenty for any real batch this
#: script sends, and orders of magnitude below a multi-gigabyte body a
#: compromised or spoofed allowlisted host could otherwise serve.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

#: Descriptive, non-secret. No API key, token, or Authorization header is
#: ever constructed anywhere in this module.
USER_AGENT = (
    "meridian-research-runtime-source-content-fetch/1.0 "
    "(+scripts/fetch_source_content.py; gated N2-T03a fetch; no credentials sent)"
)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_ABS_PREFIX = "http://arxiv.org/abs/"
_ARXIV_VERSION_SUFFIX = re.compile(r"v\d+$")

SCHEMA_VERSION = "source-content-snapshot.v1"

#: The one excerpt kind this script ever produces (task-packets/N2-T03a.yaml
#: objective: "the excerpt is the ABSTRACT and nothing else"). Not a closed
#: set of alternatives — there is exactly one value, named as a constant so
#: it appears identically in every record rather than being retyped.
EXCERPT_KIND = "abstract"

_ARXIV_RESOLVER_DESCRIPTION = (
    "arXiv API — https://export.arxiv.org/api/query?id_list=<id> (Atom 'summary' element; a "
    "requested id absent from the response, or an entry with no usable summary text, is "
    "recorded as excerpt_available=false with a typed reason)"
)
_CROSSREF_RESOLVER_DESCRIPTION = (
    "Crossref REST — https://api.crossref.org/works/<doi> (JSON 'abstract' field, JATS tags "
    "stripped per strip_jats_tags(); 404, or a work with no usable abstract field, is recorded "
    "as excerpt_available=false with a typed reason)"
)

_NOTE = (
    "Point-in-time capture of the ABSTRACT ONLY for each cited identifier in "
    "citations.manifest.json (task-packets/N2-T03a.yaml) — the arXiv Atom 'summary' element, "
    "or the Crossref 'abstract' field (JATS-tag-stripped, a declared deterministic "
    "transformation) for the one Nature DOI. Re-runnable: the same batched id_list / per-DOI "
    "queries reproduce these results, so this snapshot is verifiable, not asserted. It records "
    "EXACTLY what the two APIs returned, after the declared JATS-stripping and whitespace-"
    "normalisation transformations only — no scoring, no supplementation, no repair of a "
    "missing abstract, no full-text retrieval of any kind (no PDF, no scraping). An abstract "
    "covers only a fraction of a paper's own claims (measured at the N2-T03 derivation at "
    "roughly 28 percent numeric-token coverage across this corpus) — this snapshot is the "
    "input a separate, offline, no-network audit (N2-T03b, 'mrr audit support') checks claims "
    "against; presence in the abstract is not substantive support, and absence from the "
    "abstract is not refutation. A citation whose abstract could not be retrieved is recorded "
    "here with excerpt_available=false and a typed unavailable_reason — never omitted, never "
    "filled with a placeholder."
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
    identifier — an entry with neither is written out as a typed refusal,
    never guessed at (mirrors T02a's identical error).
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
    status, never silently folded into ``excerpt_available=False``. An
    upstream failure of this kind is a REFUSAL of the whole run, not a
    per-citation "unavailable" outcome — it means the API did not answer the
    documented contract at all, which is a different fact than "the API
    answered and there is no abstract."
    """


class XmlDtdRefusedError(FetchScriptError):
    """Raised by :func:`_refuse_if_dtd_declared`, BEFORE
    ``xml.etree.ElementTree.fromstring`` ever sees the bytes, when a
    response body declares a ``<!DOCTYPE`` or ``<!ENTITY`` (task-packets/
    S1-T01.yaml). The real arXiv Atom response never carries either — a
    document that does is always anomalous at this edge, structural refusal,
    not a bomb-shaped heuristic.
    """

    def __init__(self, declaration: str) -> None:
        self.declaration = declaration
        super().__init__(
            f"refusing to parse XML: document declares a <!{declaration} ...> — this edge "
            "never legitimately receives one (task-packets/S1-T01.yaml)"
        )


class ResponseTooLargeError(FetchScriptError):
    """Raised by :func:`_open_url` when a response body reaches
    :data:`MAX_RESPONSE_BYTES` + 1 on a bounded read (task-packets/
    S1-T01.yaml) — the read is capped before this error can even be raised,
    never an unbounded read followed by a length check.
    """

    def __init__(self, url: str, limit: int) -> None:
        self.url = url
        self.limit = limit
        super().__init__(f"refusing {url!r}: response body exceeds the {limit}-byte limit")


# ---------------------------------------------------------------------------
# The gate itself (byte-identical guard to T02a's, declared independently).
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
    descriptive ``User-Agent`` and no credential of any kind. Reads AT MOST
    :data:`MAX_RESPONSE_BYTES` + 1 bytes (task-packets/S1-T01.yaml) — a
    bounded ``response.read(n)``, not an unbounded read followed by a
    length check, so the limit actually caps memory use rather than being
    checked only after the damage is done.
    """
    _check_allowlisted(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # nosec B310 # scheme+host checked by _check_allowlisted above, tested
        body: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ResponseTooLargeError(url, MAX_RESPONSE_BYTES)
    return body


# ---------------------------------------------------------------------------
# Declared deterministic transformations: JATS-tag stripping and whitespace
# normalisation. Both pure, no I/O, separately testable over already-read
# text (task-packets/N2-T03a.yaml acceptance_criteria).
# ---------------------------------------------------------------------------

#: Dropped ENTIRELY (tag + content): a JATS abstract's own section-label
#: block, e.g. ``<jats:title>Abstract</jats:title>`` — structural markup
#: naming the section, not abstract content.
_JATS_TITLE_BLOCK_RE = re.compile(r"<jats:title>.*?</jats:title>", re.DOTALL)

#: Every OTHER jats:-namespaced tag (open, close, or self-closing, with or
#: without attributes) is replaced with a single space — its inner text is
#: kept (e.g. a ``<jats:sup>1,2</jats:sup>`` reference marker's digits
#: survive as plain text), only the markup itself is removed. Scoped
#: strictly to ``jats:``-prefixed tag names, never a generic ``<[^>]+>``
#: sweep, so this function does exactly what its name says and nothing more.
_JATS_TAG_RE = re.compile(r"</?jats:[a-zA-Z0-9_-]+(?:\s[^>]*)?/?>")


def strip_jats_tags(raw: str) -> str:
    """Pure — no I/O. Removes JATS XML markup from an already-read Crossref
    ``abstract`` field: drops any ``<jats:title>...</jats:title>`` block
    entirely, and replaces every other ``jats:``-namespaced tag with a
    single space while preserving its inner text. A string with no JATS
    markup at all passes through unchanged (both regexes simply match
    nothing) — this function is a safe no-op over plain text.
    """
    without_title = _JATS_TITLE_BLOCK_RE.sub("", raw)
    return _JATS_TAG_RE.sub(" ", without_title)


def _normalize_excerpt_whitespace(text: str) -> str:
    """Pure — no I/O. Collapses every run of whitespace (spaces, tabs,
    newlines, and Unicode whitespace such as U+00A0 NO-BREAK SPACE) to a
    single ASCII space and strips the ends. Mirrors
    ``scripts/fetch_citation_resolutions.py``'s identical
    ``" ".join(title_el.text.split())`` normalisation of Atom ``<title>``
    text, applied here to excerpt text from either resolver.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# XML hardening: refuse a DTD before any parser sees it (task-packets/
# S1-T01.yaml) — see the module docstring's "XML hardening" section. Byte-
# identical guard to fetch_citation_resolutions.py's, declared independently
# here (mirrors this module's own "decoupled read" precedent).
# ---------------------------------------------------------------------------

#: Matches a ``<!DOCTYPE`` or ``<!ENTITY`` declaration ANYWHERE in the raw
#: bytes — not anchored to the start, so leading whitespace before it never
#: hides it — case-insensitively, so a lenient/lowercased variant is still
#: caught even though the XML spec itself requires the uppercase form.
_DTD_OR_ENTITY_DECLARATION_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _refuse_if_dtd_declared(raw_xml: bytes) -> None:
    """Pure, separately testable (task-packets/S1-T01.yaml). Raises
    :class:`XmlDtdRefusedError` if ``raw_xml`` carries a ``<!DOCTYPE`` or
    ``<!ENTITY`` declaration anywhere, BEFORE any caller passes it to
    ``ET.fromstring``. The real arXiv Atom response never carries either
    (verified against the live API at the S1-T01 derivation) — this is a
    structural refusal, not a bomb-shaped heuristic: a document carrying
    only a bare ``<!DOCTYPE`` with no entity bomb at all is refused too.
    """
    match = _DTD_OR_ENTITY_DECLARATION_RE.search(raw_xml)
    if match is not None:
        raise XmlDtdRefusedError(match.group(1).decode("ascii").upper())


# ---------------------------------------------------------------------------
# arXiv: one batched request, Atom parsing, summary extraction.
# ---------------------------------------------------------------------------

UnavailableReason = Literal[
    "arxiv_entry_not_found",
    "arxiv_summary_absent",
    "crossref_work_not_found",
    "crossref_abstract_absent",
]


@dataclass(frozen=True, slots=True)
class ArxivExcerptResult:
    requested_id: str
    excerpt_available: bool
    excerpt_text: str | None
    unavailable_reason: UnavailableReason | None


@dataclass(frozen=True, slots=True)
class _ArxivEntry:
    versioned_id: str
    summary: str | None


def _arxiv_base_id(versioned_id: str) -> str:
    return _ARXIV_VERSION_SUFFIX.sub("", versioned_id)


def _has_version_suffix(identifier: str) -> bool:
    return _ARXIV_VERSION_SUFFIX.search(identifier) is not None


def _match_arxiv_entry(requested_id: str, entries: Sequence[_ArxivEntry]) -> _ArxivEntry | None:
    """A versioned ``requested_id`` matches ONLY an entry with that exact
    versioned id. An unversioned ``requested_id`` matches an entry by base id
    regardless of the (always concrete) version arXiv returns for it.
    Mirrors ``scripts/fetch_citation_resolutions.py._match_arxiv_entry``
    exactly (declared independently here, not imported — see the module
    docstring's "decoupled read" section).
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


def parse_arxiv_summaries(
    raw_xml: bytes, requested_ids: Sequence[str]
) -> tuple[ArxivExcerptResult, ...]:
    """Pure — no network. Parses an already-fetched arXiv Atom response and
    maps every id in ``requested_ids`` (in the given order) to an excerpt
    result. A requested id with no matching entry becomes
    ``excerpt_available=False`` with reason ``"arxiv_entry_not_found"``
    (never dropped, never back-filled). An entry that IS matched but carries
    no usable ``<summary>`` text becomes ``excerpt_available=False`` with
    reason ``"arxiv_summary_absent"``.

    :func:`_refuse_if_dtd_declared` runs FIRST (task-packets/S1-T01.yaml):
    a document declaring a ``<!DOCTYPE`` or ``<!ENTITY`` is refused before
    ``ET.fromstring`` ever sees it — see the module docstring's "XML
    hardening" section.
    """
    _refuse_if_dtd_declared(raw_xml)
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
            # below ends up excerpt_available=False on its own — nothing
            # here is special-cased, dropped, or back-filled.
            continue
        versioned_id = full_id[len(_ARXIV_ABS_PREFIX) :]

        summary_el = entry_el.find(f"{_ATOM_NS}summary")
        summary = (
            summary_el.text if summary_el is not None and summary_el.text is not None else None
        )

        entries.append(_ArxivEntry(versioned_id=versioned_id, summary=summary))

    results: list[ArxivExcerptResult] = []
    for requested_id in requested_ids:
        match = _match_arxiv_entry(requested_id, entries)
        if match is None:
            results.append(
                ArxivExcerptResult(
                    requested_id=requested_id,
                    excerpt_available=False,
                    excerpt_text=None,
                    unavailable_reason="arxiv_entry_not_found",
                )
            )
            continue
        normalized = _normalize_excerpt_whitespace(match.summary) if match.summary else ""
        if not normalized:
            results.append(
                ArxivExcerptResult(
                    requested_id=requested_id,
                    excerpt_available=False,
                    excerpt_text=None,
                    unavailable_reason="arxiv_summary_absent",
                )
            )
            continue
        results.append(
            ArxivExcerptResult(
                requested_id=requested_id,
                excerpt_available=True,
                excerpt_text=normalized,
                unavailable_reason=None,
            )
        )
    return tuple(results)


def fetch_arxiv_batch(requested_ids: Sequence[str]) -> tuple[ArxivExcerptResult, ...]:
    """ONE batched request for every requested arXiv id — not one round
    trip per id (mirrors T02a).
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
    return parse_arxiv_summaries(raw, requested_ids)


# ---------------------------------------------------------------------------
# Crossref: one GET per DOI, JSON parsing, abstract extraction.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrossrefExcerptResult:
    doi: str
    excerpt_available: bool
    excerpt_text: str | None
    unavailable_reason: UnavailableReason | None


def parse_crossref_abstract(raw_json: bytes, doi: str) -> CrossrefExcerptResult:
    """Pure — no network. Parses an already-fetched, HTTP-200 Crossref
    ``works`` response body and extracts its ``message.abstract`` field, if
    any, applying :func:`strip_jats_tags` then
    :func:`_normalize_excerpt_whitespace`. A work with no ``abstract`` field
    (or one that normalises to empty text) becomes ``excerpt_available=False``
    with reason ``"crossref_abstract_absent"``.
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

    raw_abstract = message.get("abstract")
    if not isinstance(raw_abstract, str) or not raw_abstract.strip():
        return CrossrefExcerptResult(
            doi=doi,
            excerpt_available=False,
            excerpt_text=None,
            unavailable_reason="crossref_abstract_absent",
        )

    normalized = _normalize_excerpt_whitespace(strip_jats_tags(raw_abstract))
    if not normalized:
        return CrossrefExcerptResult(
            doi=doi,
            excerpt_available=False,
            excerpt_text=None,
            unavailable_reason="crossref_abstract_absent",
        )

    return CrossrefExcerptResult(
        doi=doi, excerpt_available=True, excerpt_text=normalized, unavailable_reason=None
    )


def fetch_crossref_abstract(doi: str) -> CrossrefExcerptResult:
    """One GET per DOI. HTTP 404 -> ``excerpt_available=False`` with reason
    ``"crossref_work_not_found"``; any other non-200 ->
    :class:`UpstreamRefusedError`, never a silent unavailable.
    """
    url = f"{CROSSREF_WORKS_URL}{urllib.parse.quote(doi, safe='')}"
    try:
        raw = _open_url(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return CrossrefExcerptResult(
                doi=doi,
                excerpt_available=False,
                excerpt_text=None,
                unavailable_reason="crossref_work_not_found",
            )
        raise UpstreamRefusedError(f"Crossref returned HTTP {exc.code} for doi {doi!r}") from exc
    return parse_crossref_abstract(raw, doi)


# ---------------------------------------------------------------------------
# Manifest reading (this script's own minimal, decoupled read — see the
# module docstring's "decoupled read" section).
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
    """Group manifest citations by resolver: every entry with
    ``identifiers.arxiv`` goes to arXiv, every entry with ``identifiers.doi``
    (and no arxiv) goes to Crossref. Raises
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
# Snapshot assembly + deterministic rendering.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExcerptRecord:
    citation_id: str
    resolver: Literal["arxiv", "crossref"]
    excerpt_kind: str
    excerpt_available: bool
    excerpt_text: str | None
    excerpt_sha256: str | None
    unavailable_reason: UnavailableReason | None


def _excerpt_sha256(text: str) -> str:
    return f"sha256:{sha256(text.encode('utf-8')).hexdigest()}"


def _excerpt_record_to_dict(record: ExcerptRecord) -> dict[str, Any]:
    return {
        "citation_id": record.citation_id,
        "resolver": record.resolver,
        "excerpt_kind": record.excerpt_kind,
        "excerpt_available": record.excerpt_available,
        "excerpt_text": record.excerpt_text,
        "excerpt_sha256": record.excerpt_sha256,
        "unavailable_reason": record.unavailable_reason,
    }


def build_snapshot_document(
    *,
    manifest_relative_path: str,
    fetched_on: str,
    records: Sequence[ExcerptRecord],
) -> dict[str, Any]:
    """Pure — no network, no filesystem, no wall clock (``fetched_on`` is
    given, not computed here). Assembles the committed
    ``source-content-snapshot.v1`` shape, ``excerpts`` sorted by
    ``citation_id``, so calling this twice over an equal ``records``
    sequence yields an equal document regardless of the caller's own
    ordering.
    """
    ordered = sorted(records, key=lambda record: record.citation_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest": manifest_relative_path,
        "fetched_on": fetched_on,
        "excerpt_kind": EXCERPT_KIND,
        "resolvers": {
            "arxiv": _ARXIV_RESOLVER_DESCRIPTION,
            "crossref": _CROSSREF_RESOLVER_DESCRIPTION,
        },
        "note": _NOTE,
        "excerpts": [_excerpt_record_to_dict(record) for record in ordered],
    }


def render_snapshot_json(document: dict[str, Any]) -> str:
    """Deterministic rendering: ``sort_keys=True`` plus a trailing newline —
    two calls over an equal ``document`` produce byte-identical output.
    """
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Orchestration + CLI.
# ---------------------------------------------------------------------------

#: Exit semantics mirror N2-T01 (task-packets/N2-T03a.yaml acceptance_criteria):
#: 0 success, 2 input error, 3 refusal. An already-existing --out is a
#: REFUSAL (3), not a distinct fourth code — mirrors
#: mrr.services.cli.anchoring_integrity_main's identical "existing --output
#: ... is a REFUSAL, exit 3, never a crash" convention.
_EXIT_SUCCESS = 0
_EXIT_MANIFEST_INPUT_ERROR = 2
_EXIT_REFUSED = 3


def fetch_all_excerpts(citations: Sequence[ManifestCitation]) -> tuple[ExcerptRecord, ...]:
    """Fetch excerpts for every manifest citation: one batched arXiv request
    for every arxiv id, then one Crossref GET per DOI. Propagates the typed
    refusals defined above; never silently drops or back-fills a citation,
    and covers every citation passed in.
    """
    arxiv_group, doi_group = group_by_resolver(citations)

    records: list[ExcerptRecord] = []

    if arxiv_group:
        arxiv_ids = [arxiv_id for _citation_id, arxiv_id in arxiv_group]
        arxiv_results = fetch_arxiv_batch(arxiv_ids)
        for (citation_id, _arxiv_id), result in zip(arxiv_group, arxiv_results, strict=True):
            records.append(
                ExcerptRecord(
                    citation_id=citation_id,
                    resolver="arxiv",
                    excerpt_kind=EXCERPT_KIND,
                    excerpt_available=result.excerpt_available,
                    excerpt_text=result.excerpt_text,
                    excerpt_sha256=(
                        _excerpt_sha256(result.excerpt_text)
                        if result.excerpt_text is not None
                        else None
                    ),
                    unavailable_reason=result.unavailable_reason,
                )
            )

    for citation_id, doi in doi_group:
        crossref_result = fetch_crossref_abstract(doi)
        records.append(
            ExcerptRecord(
                citation_id=citation_id,
                resolver="crossref",
                excerpt_kind=EXCERPT_KIND,
                excerpt_available=crossref_result.excerpt_available,
                excerpt_text=crossref_result.excerpt_text,
                excerpt_sha256=(
                    _excerpt_sha256(crossref_result.excerpt_text)
                    if crossref_result.excerpt_text is not None
                    else None
                ),
                unavailable_reason=crossref_result.unavailable_reason,
            )
        )

    return tuple(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_source_content.py",
        description=(
            "Gated, keyless, stdlib-only fetch of source ABSTRACTS against the open arXiv and "
            "Crossref metadata APIs (task-packets/N2-T03a.yaml). Run once against a committed "
            "citation manifest; the output is committed to the repository as archive and is "
            "never regenerated to change a result."
        ),
    )
    parser.add_argument(
        "--manifest", required=True, type=Path, help="Path to the citation manifest to resolve."
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Path the content snapshot is written to. Must not already exist.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path: Path = args.manifest
    output_path: Path = args.out

    if output_path.exists():
        print(
            f"fetch_source_content: --out {output_path} already exists — refusing to overwrite "
            "a committed snapshot (AGENTS.md: never silently overwrite a prior revision; "
            "re-derive a new path if a fresh fetch is genuinely intended).",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    try:
        citations = read_manifest(manifest_path)
    except ManifestInputError as exc:
        print(f"fetch_source_content: {exc}", file=sys.stderr)
        return _EXIT_MANIFEST_INPUT_ERROR

    try:
        records = fetch_all_excerpts(citations)
    except (
        EgressRefusedError,
        UnresolvableManifestEntryError,
        UpstreamRefusedError,
        XmlDtdRefusedError,
        ResponseTooLargeError,
    ) as exc:
        print(f"fetch_source_content: refused — {exc}", file=sys.stderr)
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
        "excerpts_available": sum(1 for record in records if record.excerpt_available),
    }
    print(json.dumps(summary, sort_keys=True))
    return _EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
