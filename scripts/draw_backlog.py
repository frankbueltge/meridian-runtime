#!/usr/bin/env python3
"""``scripts/draw_backlog.py`` (task-packets/N1-T05.yaml R1): draw a named
batch of sources for the literature channel and write it as a
``citations.manifest.v1`` document — the exact input
``scripts/fetch_source_content.py`` already reads, so the anchoring step needs
no change and gains no new capability.

--- Two inputs, one output --------------------------------------------------

The channel has two mouths and one throat:

* ``--pool`` draws from the committed candidate pool
  (``corpora/gold-classification/candidate-pool.v1.json``) — the DELIBERATE
  backlog draw.
* ``--observation`` reads a night's field-watch observation
  (``corpora/field-watch/observations/<date>.json``) — what the field actually
  produced.

Both yield the same thing: a list of arXiv identifiers. Everything downstream
is shared, which is why the watch trigger can be wired before the watch has
ever produced anything.

--- Why the backlog may be drawn at all -------------------------------------

The field watch reports what APPEARED since it began watching. Anything older
is backlog, and ``corpora/field-watch/seen.json`` says in its own note what may
be done with it:

    "backlog is drawn deliberately (as the candidate pool was), never dribbled
    in by a nightly pretending it is fresh."

That is the licence this script operates under, and the whole reason it takes
a batch NAME: a deliberate draw is an act somebody signed, not a side effect.

--- The draw rule, and why it has no judgement in it ------------------------

``sorted by sha256(<salt> + arxiv_id)``, first N taken — the shape of the gold
set's own rule, recorded in the pool as:

    "sorted by sha256('mb-cls-v1' + arxiv_id), first 60 taken. Reproducible
    from this file; no judgement, no selection for an expected label."

The same property is what matters here. A batch picked by a reader is a batch
picked to produce an answer, and no later reviewer could tell the difference.
The salt makes successive batches disjoint without making either one a choice.

--- The sixty are excluded, and this is not a detail ------------------------

Every candidate carrying ``drawn: true`` is the blind commission's own
material. A corpus built from it would entangle the standard with the thing
measured against it: the same excerpts would appear both as the yardstick and
as the evidence, and no number taken afterwards would mean what it said.

--- Outside the runtime, stdlib only ----------------------------------------

Placed under ``scripts/`` and importing nothing from ``mrr.*``, matching
``fetch_source_content.py``'s own argued-for placement. No network: this script
reads committed files and writes one. The network belongs to the anchoring
step, behind its existing allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EXIT_OK = 0
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

#: arXiv abs page for a bare identifier — the ``cited_url`` the manifest
#: carries, matching the gold set's own manifest byte for byte in shape.
_ARXIV_ABS = "https://arxiv.org/abs/"


class DrawInputError(Exception):
    """An input is missing, unreadable, or the wrong shape (exit 2)."""


class DrawRefusedError(Exception):
    """The draw is well-formed but cannot be honoured (exit 3)."""


@dataclass(frozen=True)
class Candidate:
    """One drawable source. ``title`` may be empty; the manifest carries it as
    ``claimed_title``, which the anchoring step then checks against what the
    resolver actually returns.
    """

    arxiv: str
    title: str


def _read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DrawInputError(f"{path}: cannot read file ({exc})") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DrawInputError(f"{path}: not valid UTF-8 ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DrawInputError(f"{path}: not valid JSON ({exc})") from exc


def read_pool(path: Path) -> tuple[Candidate, ...]:
    """Undrawn candidates from the committed pool, in file order.

    Candidates marked ``drawn`` are dropped HERE rather than filtered later, so
    no code path downstream ever holds one — see the module docstring.
    """
    document = _read_json(path)
    if not isinstance(document, dict):
        raise DrawInputError(f"{path}: top-level document must be a JSON object")
    raw = document.get("candidates")
    if not isinstance(raw, list):
        raise DrawInputError(f"{path}: 'candidates' must be a JSON array")

    candidates: list[Candidate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise DrawInputError(f"{path}: a candidates[] element is not a JSON object")
        arxiv = entry.get("arxiv")
        if not isinstance(arxiv, str) or not arxiv:
            raise DrawInputError(f"{path}: a candidates[] element has no string 'arxiv'")
        if entry.get("drawn") is True:
            continue
        title = entry.get("title")
        candidates.append(Candidate(arxiv=arxiv, title=title if isinstance(title, str) else ""))
    return tuple(candidates)


def read_observation(path: Path) -> tuple[Candidate, ...]:
    """Every source a night's field watch reported, in the order it recorded
    them.

    No filtering and no draw rule: the watch already applied the frozen
    inclusion filter and already excluded everything in its register. Drawing
    from a night's findings a second time would silently discard an
    observation, and "nothing new" would stop meaning what it says.
    """
    document = _read_json(path)
    if not isinstance(document, dict):
        raise DrawInputError(f"{path}: top-level document must be a JSON object")
    raw = document.get("new")
    if not isinstance(raw, list):
        raise DrawInputError(f"{path}: 'new' must be a JSON array (a field-watch observation)")

    candidates: list[Candidate] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise DrawInputError(f"{path}: a new[] element is not a JSON object")
        arxiv = entry.get("arxiv")
        if not isinstance(arxiv, str) or not arxiv:
            raise DrawInputError(f"{path}: a new[] element has no string 'arxiv'")
        title = entry.get("title")
        candidates.append(Candidate(arxiv=arxiv, title=title if isinstance(title, str) else ""))
    return tuple(candidates)


def draw(candidates: tuple[Candidate, ...], *, salt: str, size: int) -> tuple[Candidate, ...]:
    """The reproducible, judgement-free draw: sort by ``sha256(salt + arxiv)``
    and take the first ``size``.

    Refuses rather than wrapping or repeating when the pool is too small. A
    batch quietly shorter than requested would look like a field that ran out,
    which is a different and much more interesting claim than "the pool was
    too small".
    """
    if size < 1:
        raise DrawRefusedError(f"--size {size} is not a batch; ask for at least one source.")
    if len(candidates) < size:
        raise DrawRefusedError(
            f"asked for {size} sources but only {len(candidates)} are available to draw. "
            "Refusing to return a shorter batch: a short batch reads as an exhausted field."
        )
    ordered = sorted(
        candidates,
        key=lambda candidate: hashlib.sha256((salt + candidate.arxiv).encode("utf-8")).hexdigest(),
    )
    return tuple(ordered[:size])


def build_manifest(
    drawn: tuple[Candidate, ...], *, batch: str, salt: str, size: int, source: str
) -> dict[str, Any]:
    """Render the batch as a ``citations.manifest.v1`` document.

    Deliberately the SAME schema version the gold set's manifest carries, so
    ``scripts/fetch_source_content.py`` reads it unchanged. A new manifest
    dialect for this channel would have meant touching the anchoring script,
    which is a new egress surface and belongs to a different packet.

    No wall-clock byte anywhere: when the batch was drawn is a property of the
    commit, not of a field that can drift away from the act it describes
    (N1-T02's invariant, and the reason is on the record — a hand-typed
    timestamp inside something that gates on time blocked its own gate within
    hours).
    """
    return {
        "schema_version": "citations.manifest.v1",
        "audit_target": (
            f"Literature-channel batch {batch!r} — sources drawn for a synthesis corpus."
        ),
        "cited_by": f"corpora/{batch}/",
        "provenance": (
            f"Drawn by scripts/draw_backlog.py from {source} with salt {salt!r}, size {size}. "
            "Draw rule: sorted by sha256(salt + arxiv_id), first N taken — reproducible from "
            "this file, no judgement, no selection for an expected relation. Candidates "
            "already drawn for the blind commission are excluded, so no source appears both "
            "as the yardstick and as the evidence. Abstracts are fetched, hashed and "
            "committed by scripts/fetch_source_content.py against arXiv only; this manifest "
            "names WHICH sources, never what they say."
        ),
        "citations": [
            {
                "citation_id": f"lit-{candidate.arxiv}",
                "cited_as": candidate.title,
                "cited_url": f"{_ARXIV_ABS}{candidate.arxiv}",
                "identifiers": {"arxiv": candidate.arxiv},
                "claimed_title": candidate.title,
            }
            for candidate in drawn
        ],
    }


def render_manifest(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_atomically(output: Path, rendered: str) -> None:
    """Temp file in the SAME directory, ``os.replace`` last — the discipline
    every writing command in this repository uses.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.draw-tmp-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        os.replace(tmp_path, output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="draw_backlog.py",
        description=(
            "Draw a named batch of sources for the literature channel, from the committed "
            "candidate pool or from a field-watch observation, and write it as a "
            "citations.manifest.v1 document (task-packets/N1-T05.yaml R1)."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pool",
        type=Path,
        help=(
            "Draw from this candidate pool. Candidates marked drawn:true are excluded — "
            "they are the blind commission's own material."
        ),
    )
    group.add_argument(
        "--observation",
        type=Path,
        help=(
            "Take every source a field-watch observation reported. No draw rule is "
            "applied: the watch already filtered and already excluded its register."
        ),
    )
    parser.add_argument(
        "--batch",
        required=True,
        help=(
            "Name of this batch. Becomes the corpus directory name and is recorded in the manifest."
        ),
    )
    parser.add_argument(
        "--size",
        type=int,
        default=16,
        help=(
            "How many sources to draw (--pool only). Default 16: comfortably above the "
            "protocol's min_included_sources, under a quarter-hour of classification, and "
            "small enough that a human really reads the pull request."
        ),
    )
    parser.add_argument(
        "--salt",
        default=None,
        help=(
            "Draw salt. Defaults to the batch name, which makes successive batches "
            "disjoint without anybody choosing what lands in them."
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Path the manifest is written to. Must not already exist.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    # --- 1. --out conflict check FIRST: the cheapest check there is, and the
    #        one that must never be lost by being reached late.
    if args.out.exists():
        print(
            f"draw_backlog: --out {args.out} already exists — refusing to write over a "
            "committed batch manifest.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    salt = args.salt or args.batch
    try:
        if args.pool is not None:
            candidates = read_pool(args.pool)
            drawn = draw(candidates, salt=salt, size=args.size)
            source = str(args.pool)
            size = args.size
        else:
            candidates = read_observation(args.observation)
            if not candidates:
                raise DrawRefusedError(
                    f"{args.observation} reports no sources. A night that found nothing "
                    "writes no observation at all, so an empty one is a malformed input, "
                    "not a quiet field."
                )
            drawn = candidates
            source = str(args.observation)
            size = len(drawn)
    except DrawInputError as exc:
        print(
            f"draw_backlog: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except DrawRefusedError as exc:
        print(f"draw_backlog: refused — {exc}", file=sys.stderr)
        return _EXIT_REFUSED

    document = build_manifest(drawn, batch=args.batch, salt=salt, size=size, source=source)
    write_atomically(args.out, render_manifest(document))
    print(
        json.dumps(
            {
                "batch": args.batch,
                "manifest": str(args.out),
                "drawn": len(drawn),
                "available": len(candidates),
                "salt": salt,
                "ids": [candidate.arxiv for candidate in drawn],
            },
            sort_keys=True,
        )
    )
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover - exercised via tests
    raise SystemExit(main())
