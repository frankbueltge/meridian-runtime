#!/usr/bin/env python3
"""The field watch: what appeared in Meridian's own subject since last night.

This is the READ-ONLY half of the second nightly routine — the meta-research
routine the capability roadmap of 2026-07-24 named, and the half that can be
built before a gold standard exists because it measures nothing and proposes
nothing. It answers exactly one question: *which sources in this field are new
to us?*

It does NOT decide what they mean, does not classify them, does not propose a
change, and never calls a model. Those belong to the routine's other half,
which needs a frozen evaluator and is not built.

--- Who reads this, and the fact that nobody does yet ---------------------

An observation is written into THIS repository, and the practice that would
act on it — field-research — cannot read this repository: its sessions are
scoped to their own (`journal/2026-07-26.md`: "This session's repository access
was scoped to frankbueltge/field-research"). So the watch currently produces a
record with no reader, and that is stated here rather than left for someone to
discover.

It is the same gap that stopped the labelling commission on 2026-08-01, and it
has the same shape of fix: an artefact has to be LANDED where its reader can
open it, not merely produced somewhere true. Until that is decided, the watch
is an honest instrument pointed at a wall — worth running, because the register
it maintains is what makes a later delivery cheap, and worth saying plainly.

--- Fail-closed, and why that is the load-bearing property -----------------

A watch that reports "nothing new" when a fetch quietly failed is worse than no
watch: silence and a quiet field look identical, and the routine's whole value
is that its silence means something. So ANY search that fails aborts the run
with a non-zero exit and writes nothing. There is no partial observation, no
"13 of 14 searches succeeded", no best-effort mode.

This is the same discipline the roadmap demanded of Routine 2 in the first
place — "hash-verankerte Quellen, fail-closed ... dass Quellenausfall
deterministisch endet, bevor ein LLM ihn sieht" — arriving one step earlier
than expected, because there is no LLM in this half at all.

--- What counts as new ---------------------------------------------------

Two conditions, both required: the id is absent from the register AND the
paper was submitted on or after the register's ``watch_from`` date. The second
exists because the register was seeded from a relevance-ranked pool while this
script sweeps by submission date, so the two see different slices — without the
floor the first sweep reported 260 older papers as news. Backlog is drawn
deliberately, as the MB-CLS candidate pool was; it is never dribbled in by a
nightly pretending it is fresh.

--- Never recompute the same thing --------------------------------------

The handoff of 2026-08-01 warned against a nightly that recomputes: a run over
pinned inputs yields the same bytes forever and counterfeits freshness. This
script writes an observation file ONLY when the register gained ids. A night
with nothing new produces no file, no commit and no pull request, and says so
on stdout.

--- The searches are frozen ---------------------------------------------

``corpora/field-watch/searches.v1.json`` is a frozen input, for the same reason
the gold standard is frozen: a watch that may change what it looks for cannot
tell you the field changed, only that it looked elsewhere. Changing the set is
a new version with a recorded reason.

--- Outside the runtime, like its two siblings ---------------------------

Lives under ``scripts/``, imports nothing from ``mrr.*``, and gives no runtime
path network capability — the same construction as
``scripts/fetch_source_content.py`` and ``scripts/fetch_citation_resolutions.py``.
Its allowlist is its own so it stays correct if theirs ever changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 # every parse below is preceded by _refuse_if_dtd_declared, which refuses DOCTYPE/ENTITY before parsing; test_billion_laughs_is_refused_without_reaching_the_parser proves ET.fromstring is unreached on a bomb — remove that guard and this reason no longer holds
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCH_DIR = REPO_ROOT / "corpora" / "field-watch"

#: The single host this script may reach. Checked on the parsed hostname, never
#: as a substring of the raw URL, so a lookalike host is refused rather than
#: waved through.
ALLOWED_HOST = "export.arxiv.org"
ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 45
#: arXiv asks for one request every few seconds. Politeness is not optional on
#: an endpoint that costs someone else money.
SECONDS_BETWEEN_REQUESTS = 3.5

_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_ARXIV_ID = re.compile(r"abs/([0-9]{4}\.[0-9]{4,5})")

_EXIT_OK = 0
_EXIT_FETCH_FAILED = 2
_EXIT_REFUSED = 3


class EgressRefusedError(Exception):
    """A URL outside the allowlist was about to be opened. Raised BEFORE any
    socket opens.
    """


class SearchFailedError(Exception):
    """One search did not complete. Fatal by design — see the module
    docstring's fail-closed section.
    """


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, including to an allowlisted host. A redirect is a
    request to fetch something other than what was asked for, and an allowlist
    that follows redirects is not an allowlist.
    """

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise EgressRefusedError("refused: the endpoint attempted a redirect")


def _check_allowlisted(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise EgressRefusedError(f"refused: scheme {parts.scheme!r} is not https")
    if parts.hostname != ALLOWED_HOST:
        raise EgressRefusedError(f"refused: host {parts.hostname!r} is not {ALLOWED_HOST!r}")


def _open(url: str) -> bytes:
    _check_allowlisted(url)
    opener = urllib.request.build_opener(_NoRedirectHandler)
    request = urllib.request.Request(
        url, headers={"User-Agent": "meridian-runtime field watch (nightly, read-only)"}
    )
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return bytes(response.read(MAX_RESPONSE_BYTES + 1)[:MAX_RESPONSE_BYTES])


@dataclass(frozen=True, slots=True)
class Entry:
    arxiv: str
    title: str
    published: str
    abstract: str


def _refuse_if_dtd_declared(payload: bytes) -> None:
    """Refuse a response declaring a DOCTYPE or an ENTITY, BEFORE any parser
    sees it.

    Named identically to the guard in ``scripts/fetch_source_content.py`` and
    ``scripts/fetch_citation_resolutions.py`` on purpose: the repository's
    nosec convention requires a B314/B405 suppression to name its guard, and a
    third spelling would make that check weaker across the three scripts
    rather than stronger.
    """
    head = payload[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise SearchFailedError("refused: response declares a DOCTYPE or ENTITY")


def parse_atom(payload: bytes) -> tuple[Entry, ...]:
    """Parse an arXiv Atom response into entries, after the DTD guard."""
    _refuse_if_dtd_declared(payload)
    try:
        root = ET.fromstring(payload)  # nosec B314 # reached only because _refuse_if_dtd_declared above already refused any DOCTYPE/ENTITY; test_billion_laughs_is_refused_without_reaching_the_parser proves this — remove that guard call and this reason no longer holds
    except ET.ParseError as exc:
        raise SearchFailedError(f"response is not parseable XML ({exc})") from exc

    entries: list[Entry] = []
    for entry in root.findall("a:entry", _ATOM):
        raw_id = (entry.findtext("a:id", default="", namespaces=_ATOM) or "").strip()
        match = _ARXIV_ID.search(raw_id)
        if match is None:
            continue
        entries.append(
            Entry(
                arxiv=match.group(1),
                title=" ".join(
                    (entry.findtext("a:title", default="", namespaces=_ATOM) or "").split()
                ),
                published=(entry.findtext("a:published", default="", namespaces=_ATOM) or "")[:10],
                abstract=" ".join(
                    (entry.findtext("a:summary", default="", namespaces=_ATOM) or "").split()
                ),
            )
        )
    return tuple(entries)


def passes_inclusion(title: str, abstract: str, inclusion: dict[str, Any]) -> bool:
    """The frozen, objective filter. Nothing in it selects for what an abstract
    concludes — only for whether it speaks about checking AND about automated
    research at all.
    """
    if len(abstract) < int(inclusion["min_abstract_chars"]):
        return False
    text = f"{title} {abstract}"
    return bool(
        re.search(inclusion["must_mention_checking"], text, re.I)
        and re.search(inclusion["must_mention_automation"], text, re.I)
    )


def _search(query: str, max_results: int) -> tuple[Entry, ...]:
    url = f"{ARXIV_QUERY_URL}?" + urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    try:
        payload = _open(url)
    except EgressRefusedError:
        raise
    except (URLError, OSError, TimeoutError) as exc:
        raise SearchFailedError(f"{query}: {type(exc).__name__} {exc}") from exc
    return parse_atom(payload)


def run(*, max_results: int, dry_run: bool, today: str) -> int:
    searches = json.loads((WATCH_DIR / "searches.v1.json").read_text(encoding="utf-8"))
    register = json.loads((WATCH_DIR / "seen.json").read_text(encoding="utf-8"))
    seen: set[str] = set(register["ids"])
    inclusion = searches["inclusion"]
    # A watch reports what APPEARED since it began watching. Without this floor
    # the first sweep reports the backlog the register happens to miss — 260
    # papers on the night this was built — and calls it news. Backlog is drawn
    # deliberately, never dribbled in by a nightly pretending it is fresh.
    watch_from: str = str(register["watch_from"])

    found: dict[str, Entry] = {}
    for index, query in enumerate(searches["queries"]):
        if index:
            time.sleep(SECONDS_BETWEEN_REQUESTS)
        # Fail-closed: one failed search ends the run. A partial sweep reported
        # as an observation would make "nothing new" ambiguous forever after.
        # One request per search — the abstract arrives in the same Atom
        # response as the id, so fetching twice would double the load on
        # somebody else's endpoint for nothing.
        for entry in _search(query, max_results):
            if entry.arxiv in seen or entry.arxiv in found:
                continue
            if entry.published < watch_from:
                continue
            if not passes_inclusion(entry.title, entry.abstract, inclusion):
                continue
            found[entry.arxiv] = entry

    if not found:
        # The honest empty night. No file, no commit, no pull request — and a
        # line saying the sweep completed, so silence is never mistaken for a
        # sweep that did not run.
        print(
            json.dumps(
                {
                    "date": today,
                    "searches_completed": len(searches["queries"]),
                    "new": 0,
                    "note": "sweep completed, nothing new — no observation written",
                },
                sort_keys=True,
            )
        )
        return _EXIT_OK

    entries = sorted(found.values(), key=lambda e: e.arxiv)
    observation = {
        "schema_version": "field-watch-observation.v1",
        "date": today,
        "searches_version": searches["schema_version"],
        "watch_from": watch_from,
        "subject": searches["subject"],
        "_note": (
            "What appeared in this practice's own subject and was not already accounted for. "
            "An OBSERVATION, not a reading: nothing here has been classified, and nothing here "
            "implies a change to anything. Deciding what a finding means is the other half of "
            "this routine, which needs a frozen evaluator and is not built."
        ),
        "new_count": len(entries),
        "new": [{"arxiv": e.arxiv, "title": e.title, "published": e.published} for e in entries],
    }

    if dry_run:
        print(json.dumps(observation, indent=2, ensure_ascii=False))
        return _EXIT_OK

    out = WATCH_DIR / "observations" / f"{today}.json"
    if out.exists():
        print(
            f"watch_field: {out} already exists — refusing to overwrite a committed observation.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    out.write_text(json.dumps(observation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    register["ids"] = sorted(seen | set(found))
    (WATCH_DIR / "seen.json").write_text(
        json.dumps(register, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {"date": today, "new": len(entries), "observation": str(out.relative_to(REPO_ROOT))},
            sort_keys=True,
        )
    )
    return _EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watch_field.py",
        description=(
            "The read-only field watch: which sources in this practice's subject are new. "
            "Fail-closed; writes nothing on an empty night; never calls a model."
        ),
    )
    parser.add_argument("--today", required=True, help="The observation date (YYYY-MM-DD).")
    parser.add_argument(
        "--max-results", type=int, default=60, help="Results per search (default 60)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the observation instead of writing it and advancing the register.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(max_results=args.max_results, dry_run=args.dry_run, today=args.today)
    except EgressRefusedError as exc:
        print(f"watch_field: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except SearchFailedError as exc:
        print(
            f"watch_field: a search did not complete ({exc}). Refusing to report a partial "
            "sweep — 'nothing new' has to mean the field was quiet, not that the fetch broke.",
            file=sys.stderr,
        )
        return _EXIT_FETCH_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
