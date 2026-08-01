"""``mrr classify relations`` (task-packets/N1-T04.yaml R5): drive a model
over a blind classification commission and write the proposal set that
``mrr validate gold --predictions`` then measures.

Ordering, exit codes and the atomic write mirror
:mod:`mrr.services.cli.validation_main` exactly — cheapest local check first,
then the loads, then the work:

* **2** — a DEPENDENCY problem: an input file missing, unreadable, or the
  wrong shape; a requested adapter that cannot be built.
* **3** — a REFUSAL that is a RESULT: the output path already exists, the
  cases file carries labelling output, or the run finished with cases that
  produced no schema-valid verdict.

--- Why incomplete coverage refuses ----------------------------------------

A case whose generation failed yields no prediction. Writing the file anyway
would produce a predictions set covering fewer cases than the standard, and
``mrr validate gold`` would then refuse it with ``MismatchedRatersError`` —
correctly, but one step too late to say WHICH case broke or why.

So the refusal happens where the gap is created, names every affected case
and its distinct failure status, and writes nothing. A partial run that looks
like a whole one is precisely the quiet result this whole apparatus exists to
prevent.

An UNDECIDABLE case is not such a gap. The criteria's
``R-undecidable-is-a-finding`` makes declining to decide a legitimate outcome
that is counted rather than scored, and the gold standard itself holds three
of its sixty out of the matrix on the same rule. An undecidable case yields
no prediction and does not fail the run.

--- Why the model name is required -----------------------------------------

``--model-name`` has no default. A measurement whose system under test is
recorded as "Gemini" names nothing that could be measured again next month,
and provider model ids move under a marketing name without notice. The
concrete id is required here and is written into the artefact.

--- Why the default adapter is `none` --------------------------------------

``--adapter none`` is the default and refuses. Defaulting to a live provider
would make an accidental invocation spend quota and produce a number, and
this command exists to produce numbers people will quote. Choosing to reach a
model is an explicit act.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from mrr.adapters.llm.gemini import GeminiModelAdapter
from mrr.adapters.llm.transport import UrllibHTTPTransport
from mrr.domain.exceptions import DomainError
from mrr.domain.model_adapter import ModelAdapter
from mrr.domain.relation_proposal import render_json
from mrr.services.classification.relation_service import (
    CasesNotBlindError,
    ClassificationInputError,
    RelationClassificationService,
)

_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_ADAPTER_NONE = "none"
_ADAPTER_GEMINI = "gemini"

#: Seconds between cases. The free tier this runs on is rate-limited, and a
#: run that trips the limit turns into a wall of ``error`` statuses that look
#: like a finding about the model. Not configurable downward to zero from the
#: command line on purpose — see ``--pause-seconds``.
_DEFAULT_PAUSE_SECONDS = 2.0


def output_file_conflict(output: Path) -> bool:
    """``True`` iff ``output`` already exists (file or directory) — mirrors
    :func:`mrr.services.cli.validation_main.output_file_conflict`.
    """
    return output.exists()


def register_classify_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr classify relations`` — every flag and behaviour lives in this
    module.

    A group of its own rather than a third sibling under ``validate``:
    ``validate`` reports on something that already happened, this RUNS a
    model. Putting an act that spends quota under a group whose every other
    member is read-only would be a misleading shape.
    """
    classify_parser = subparsers.add_parser(
        "classify",
        help="Run a model over a blind commission and write its proposals (N1-T04).",
    )
    classify_subparsers = classify_parser.add_subparsers(dest="classify_command", required=True)
    _add_relations_subparser(classify_subparsers)


def _add_relations_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "relations",
        help=(
            "Classify each case of a blind commission into an evidence relation, under "
            "frozen criteria, and write a proposal set that is also a "
            "`mrr validate gold --predictions` input."
        ),
    )
    parser.add_argument(
        "--cases",
        required=True,
        type=Path,
        help=(
            "Path to the blind commission. Refused if any case carries labelling output "
            "(expected_relation, decided_by, tie_with, ...) — a commission that answers "
            "itself is not one."
        ),
    )
    parser.add_argument(
        "--criteria",
        required=True,
        type=Path,
        help=(
            "Path to the frozen criteria file. Its definitions and rules go into the "
            "prompt verbatim: a system asked in other words answers a different question "
            "than the one the standard's labels answered."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="File the proposal set is written into. Must not already exist.",
    )
    parser.add_argument(
        "--system-id",
        required=True,
        help=(
            "How this system is named in the measurement. Should identify the "
            "configuration, not the vendor."
        ),
    )
    parser.add_argument(
        "--adapter",
        choices=(_ADAPTER_NONE, _ADAPTER_GEMINI),
        default=_ADAPTER_NONE,
        help=(
            "Which model adapter to construct. Default 'none' REFUSES — reaching a live "
            "provider is an explicit act, never a default."
        ),
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help=(
            "The concrete provider model id (e.g. 'gemini-2.0-flash'). Required with a "
            "real adapter and never defaulted: 'Gemini' names no object that could be "
            "measured again."
        ),
    )
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=1,
        help=(
            "Additional calls allowed after a schema-invalid response, per case (E4-T02's "
            "bounded repair). 0 means one call and no repair."
        ),
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=_DEFAULT_PAUSE_SECONDS,
        help=(
            "Seconds to wait between cases, to stay inside the provider's rate limit. A "
            "tripped limit produces a wall of 'error' statuses that reads like a finding "
            "about the model and is not one."
        ),
    )


def _build_adapter(args: argparse.Namespace) -> ModelAdapter:
    """Construct the requested adapter, or raise ``ValueError`` naming why not.

    The only place in this packet where a concrete adapter is built. The
    service itself never constructs one — see
    :mod:`mrr.services.classification.relation_service`.
    """
    if args.adapter == _ADAPTER_NONE:
        raise ValueError(
            "--adapter none is the default and does nothing. Pass --adapter gemini to "
            "reach a live provider; running a model is an explicit act, not a fallback."
        )
    if not args.model_name:
        raise ValueError(
            f"--adapter {args.adapter} requires --model-name. A measurement whose system "
            "under test is recorded as a vendor name cannot be repeated."
        )
    return GeminiModelAdapter(transport=UrllibHTTPTransport(), model_name=args.model_name)


def _write_atomically(output: Path, rendered: str) -> None:
    """Write via a temp file in the SAME directory with ``os.replace`` as the
    last act — identical discipline to
    :func:`mrr.services.cli.validation_main._write_atomically`.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.classify-tmp-")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(rendered)
        os.replace(tmp_path, output)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def run_relations_command(args: argparse.Namespace, *, adapter: ModelAdapter | None = None) -> int:
    """``mrr classify relations``.

    Args:
        adapter: injected by tests so no test in any tier constructs a real
            provider adapter or reads an API key. ``None`` means "build the
            one ``--adapter`` names", which is what the command line does.
    """
    # --- 1. --output conflict check FIRST — the cheapest of all checks, and
    #        the one that must never be lost by getting to it late.
    if output_file_conflict(args.output):
        print(
            f"mrr classify relations: --output {args.output} already exists — refusing to "
            "write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    service = RelationClassificationService()

    # --- 2. Load both inputs before building anything that costs money.
    try:
        criteria = service.load_criteria(args.criteria)
        cases = service.load_cases(args.cases)
    except CasesNotBlindError as exc:
        print(f"mrr classify relations: refused — {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except ClassificationInputError as exc:
        print(
            f"mrr classify relations: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. Build the adapter only once the inputs are known good.
    if adapter is None:
        try:
            adapter = _build_adapter(args)
        except ValueError as exc:
            print(f"mrr classify relations: {exc}", file=sys.stderr)
            return _EXIT_DEPENDENCY_UNAVAILABLE

    pause_seconds = max(0.0, args.pause_seconds)

    def _pause() -> None:
        if pause_seconds:
            time.sleep(pause_seconds)

    # --- 4. Run. The service never raises on a model failure; a failed case
    #        comes back carrying its own distinct status.
    try:
        proposal_set = service.classify(
            adapter=adapter,
            cases=cases,
            criteria=criteria,
            model_name=args.model_name or "unnamed-model",
            system_id=args.system_id,
            max_repair_attempts=args.max_repair_attempts,
            pause=_pause,
        )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr classify relations: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 5. Coverage. A gap here refuses, and says which cases and why —
    #        see this module's docstring.
    failed = proposal_set.failed_case_ids()
    if failed:
        by_case = {
            proposal.case_id: proposal.generation_status
            for proposal in proposal_set.proposals
            if not proposal.has_proposal
        }
        detail = ", ".join(f"{case_id} ({by_case[case_id]})" for case_id in failed)
        print(
            f"mrr classify relations: refused — {len(failed)} of {len(proposal_set.proposals)} "
            f"cases produced no schema-valid verdict: {detail}. Writing a shorter "
            "predictions file would measure a subset as though it were the whole set. "
            "Nothing was written.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    _write_atomically(args.output, render_json(proposal_set))

    undecidable = proposal_set.undecidable_case_ids()
    ties = proposal_set.tie_broken_case_ids()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "system_id": proposal_set.system_id,
                "model_name": proposal_set.model_name,
                "model_profile_id": proposal_set.model_profile_id,
                "prompt_template_sha256": proposal_set.prompt_template_sha256,
                "commission_sha256": proposal_set.commission_sha256,
                "criteria_sha256": proposal_set.criteria_sha256,
                "cases": len(proposal_set.proposals),
                "predictions": len(proposal_set.predictions()),
                "undecidable": list(undecidable),
                "tie_broken": list(ties),
            },
            sort_keys=True,
        )
    )
    return 0


def run_command(args: argparse.Namespace) -> int:
    """Dispatch within ``mrr classify`` — called by
    ``mrr.services.cli.main.main`` and by this module's own ``main``.
    """
    return run_relations_command(args)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr classify"``),
    usable without going through ``mrr.services.cli.main`` — mirrors
    :func:`mrr.services.cli.validation_main.build_parser`'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr classify",
        description="Model-assisted evidence-relation classification (task-packets/N1-T04.yaml).",
    )
    subparsers = parser.add_subparsers(dest="classify_command", required=True)
    _add_relations_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.classify_command == "relations":
        return run_relations_command(args)

    parser.print_help()  # pragma: no cover - unreachable while the subcommand is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
