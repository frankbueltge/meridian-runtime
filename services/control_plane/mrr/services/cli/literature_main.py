"""``mrr literature commission`` / ``mrr literature build``
(task-packets/N1-T05.yaml R6): the two operator steps of the literature
channel that are not already commands.

The channel end to end:

1. ``scripts/draw_backlog.py``          — a batch manifest (outside the runtime)
2. ``scripts/fetch_source_content.py``  — anchored, hashed excerpts (unchanged)
3. ``mrr literature commission``        — the BLIND commission            (here)
4. ``mrr classify relations``           — the proposals (N1-T04, unchanged)
5. ``mrr literature build``             — the corpus directory            (here)

Steps 1, 2 and 4 already existed. This module adds only the two joins between
them, which is why the channel needs no new network path, no new prompt and no
new classification logic.

Ordering, exit codes and the atomic write mirror
:mod:`mrr.services.cli.classification_main` exactly:

* **2** — a DEPENDENCY problem: an input missing, unreadable, or the wrong
  shape.
* **3** — a REFUSAL that is a RESULT: the output already exists, the batch is
  too small to clear the protocol's own kill condition, or the proposals do
  not cover the sources they claim to.

--- Why a batch too small refuses here -------------------------------------

``_classify_analysis`` gates on ``min_included_sources`` and returns
``insufficient_evidence`` below it (``synthesis_executor.py:688-707``). A
corpus that cannot clear its own gate is a question nobody asked: it would be
committed, opened as a pull request, merged, run overnight, and answered with
"not enough evidence" — two days after the shortfall was visible and cheap to
name.

So the shortfall is named now, and nothing is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from mrr.domain.exceptions import DomainError
from mrr.services.literature.corpus_builder import (
    REQUIRED_CORPUS_FILES,
    CorpusBuildError,
    CorpusBuildRefusedError,
    LiteratureCorpusBuilder,
)

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

#: Repeated in every corpus this channel writes, so the number travels with
#: the data instead of living only in a design note somebody has to find.
_ACCURACY_NOTE = (
    "This classifier's measured accuracy against the frozen gold standard is 0.5439 "
    "(majority-class floor 0.4211, Cohen's kappa 0.3084); a second run over identical "
    "frozen inputs returned 0.5263 (kappa 0.2792). Roughly every second proposed relation "
    "is therefore wrong, and the eligibility thresholds count exactly these relations. Any "
    "verdict derived from this corpus inherits that error rate."
)


def _sha256_of_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _render(document: Any) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _write_atomically(output: Path, rendered: str) -> None:
    """Temp file in the SAME directory, ``os.replace`` last — identical
    discipline to :func:`mrr.services.cli.classification_main._write_atomically`.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.lit-tmp-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        os.replace(tmp_path, output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def register_literature_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes for this
    packet — every flag and behaviour lives in this module.
    """
    parser = subparsers.add_parser(
        "literature",
        help="Turn anchored sources into a corpus, and therefore into a question (N1-T05).",
    )
    sub = parser.add_subparsers(dest="literature_command", required=True)
    _add_commission_subparser(sub)
    _add_build_subparser(sub)


def _add_commission_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "commission",
        help=(
            "Build the BLIND commission the classifier reads, from a batch manifest and its "
            "anchored content snapshot."
        ),
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument(
        "--criteria",
        required=True,
        type=Path,
        help="The frozen criteria file; embedded verbatim so the run is comparable.",
    )
    parser.add_argument(
        "--claim-text",
        required=True,
        help="The claim every source is classified against. One claim per batch.",
    )
    parser.add_argument("--batch", required=True)
    parser.add_argument("--output", required=True, type=Path, help="Must not already exist.")


def _add_build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "build",
        help=(
            "Join manifest, snapshot and proposals into a complete corpus directory — all "
            "five files research-run.yml requires."
        ),
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument(
        "--proposals",
        required=True,
        type=Path,
        help="The `mrr classify relations` artefact for exactly this batch.",
    )
    parser.add_argument("--batch", required=True)
    parser.add_argument(
        "--claim-text",
        required=True,
        help="The claim the corpus is about; travels into the question model.",
    )
    parser.add_argument(
        "--analysis",
        default=None,
        help="Analysis group name. Defaults to '<batch>-evidence-relations'.",
    )
    parser.add_argument(
        "--claim-type",
        choices=("observational", "interpretive"),
        default="interpretive",
        help=(
            "Every entry in one analysis group MUST declare the same claim_type "
            "(synthesis_executor._group_entries_by_analysis), so it is set per batch."
        ),
    )
    parser.add_argument(
        "--min-included-sources",
        type=int,
        default=3,
        help=(
            "The protocol's own kill condition. A batch that cannot reach it refuses now "
            "rather than answering 'insufficient_evidence' two nights later."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="The corpus directory to create, e.g. corpora/<batch>. Must not already exist.",
    )


def run_commission_command(args: argparse.Namespace) -> int:
    if args.output.exists():
        print(
            f"mrr literature commission: --output {args.output} already exists — refusing to "
            "write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    builder = LiteratureCorpusBuilder()
    try:
        citations = builder.load_manifest(args.manifest)
        anchored, unanchored, _fetched_on = builder.load_snapshot(args.snapshot)
        document = builder.build_commission(
            citations=citations,
            anchored=anchored,
            criteria_path=args.criteria,
            claim_text=args.claim_text,
            batch=args.batch,
        )
    except CorpusBuildRefusedError as exc:
        print(f"mrr literature commission: refused — {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except CorpusBuildError as exc:
        print(
            f"mrr literature commission: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    _write_atomically(args.output, _render(document))
    print(
        json.dumps(
            {
                "output": str(args.output),
                "batch": args.batch,
                "cases": len(document["cases"]),
                "unanchored": list(unanchored),
            },
            sort_keys=True,
        )
    )
    return 0


def run_build_command(args: argparse.Namespace) -> int:
    # --- 1. --output-dir conflict check FIRST: cheapest, and the one that
    #        must never be lost by being reached late.
    if args.output_dir.exists():
        print(
            f"mrr literature build: --output-dir {args.output_dir} already exists — refusing "
            "to write over a committed corpus.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    builder = LiteratureCorpusBuilder()
    analysis = args.analysis or f"{args.batch}-evidence-relations"

    try:
        citations = builder.load_manifest(args.manifest)
        anchored, unanchored, fetched_on = builder.load_snapshot(args.snapshot)
        proposals, provenance = builder.load_proposals(args.proposals)
        entries, undecidable = builder.build_entries(
            citations=citations,
            anchored=anchored,
            unanchored=unanchored,
            proposals=proposals,
            provenance=provenance,
            analysis=analysis,
            claim_type=args.claim_type,
            snapshot_path=str(args.snapshot),
            proposals_path=str(args.proposals),
            accuracy_note=_ACCURACY_NOTE,
            fetched_on=fetched_on,
        )
    except CorpusBuildRefusedError as exc:
        print(f"mrr literature build: refused — {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except CorpusBuildError as exc:
        print(
            f"mrr literature build: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 2. The kill condition, checked BEFORE anything is written.
    if len(entries) < args.min_included_sources:
        print(
            f"mrr literature build: refused — {len(entries)} usable entries, below the "
            f"protocol's min_included_sources of {args.min_included_sources} "
            f"({len(unanchored)} could not be anchored, {len(undecidable)} were undecidable). "
            "A corpus that cannot clear its own kill condition would be committed, merged, "
            "run overnight and answered 'insufficient_evidence'. Nothing was written.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    siblings = builder.build_siblings(
        batch=args.batch,
        claim_text=args.claim_text,
        analysis=analysis,
        claim_type=args.claim_type,
        manifest_path=str(args.manifest),
        manifest_sha256=_sha256_of_file(args.manifest),
        snapshot_path=str(args.snapshot),
        snapshot_sha256=_sha256_of_file(args.snapshot),
        proposals_path=str(args.proposals),
        provenance=provenance,
        accuracy_note=_ACCURACY_NOTE,
        fetched_on=fetched_on,
        min_included_sources=args.min_included_sources,
    )

    relation_counts: dict[str, int] = {}
    for entry in entries:
        relation = str(entry["evidence_relation"])
        relation_counts[relation] = relation_counts.get(relation, 0) + 1

    report = {
        "batch": args.batch,
        "drawn": len(citations),
        "anchored": len(anchored),
        "unverifiable": list(unanchored),
        "undecidable": list(undecidable),
        "entries": len(entries),
        "relation_counts": relation_counts,
    }

    # --- 3. Write the directory as a unit: build it complete under a temp
    #        name, then move it into place. A half-written corpus directory
    #        would satisfy research-run.yml's five-file test on its next tick
    #        while missing half its entries.
    staging = Path(
        tempfile.mkdtemp(
            dir=args.output_dir.parent if args.output_dir.parent.exists() else None,
            prefix=f".{args.output_dir.name}.lit-staging-",
        )
    )
    try:
        (staging / "corpus-entries.json").write_text(_render(entries), encoding="utf-8")
        for name, document in siblings.items():
            (staging / name).write_text(_render(document), encoding="utf-8")
        (staging / "batch-report.json").write_text(_render(report), encoding="utf-8")
        missing = [name for name in REQUIRED_CORPUS_FILES if not (staging / name).is_file()]
        if missing:  # pragma: no cover - a construction bug, not an input problem
            raise CorpusBuildRefusedError(
                f"the built directory is missing {missing!r}, so research-run.yml would skip "
                "it as 'not a synthesis corpus'."
            )
        os.replace(staging, args.output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(json.dumps({**report, "output_dir": str(args.output_dir)}, sort_keys=True))
    return 0


def run_command(args: argparse.Namespace) -> int:
    """Dispatch within ``mrr literature`` — called by
    ``mrr.services.cli.main.main`` and by this module's own ``main``.
    """
    if args.literature_command == "commission":
        return run_commission_command(args)
    return run_build_command(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mrr literature",
        description="The literature channel (task-packets/N1-T05.yaml).",
    )
    sub = parser.add_subparsers(dest="literature_command", required=True)
    _add_commission_subparser(sub)
    _add_build_subparser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_command(args)
    except DomainError as exc:  # pragma: no cover - defence in depth
        print(f"mrr literature: refused — {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
