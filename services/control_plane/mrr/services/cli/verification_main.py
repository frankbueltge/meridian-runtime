"""``mrr verification record`` (task-packets/K1-T05.yaml): a thin argparse
CLI over ``mrr.services.cli.verification_orchestration.record_verification``,
mirroring ``mrr.services.cli.synthesis_main``'s own shape (a sibling file, not
inlined into ``main.py``, so that module's own diff stays a one-line, additive
``"verification"`` subparser registration) and ``mrr.services.cli.main``'s
MRR-NFR-012 "explicit degradation, never a fabricated substitute" discipline.

This closes the tooling gap the verification design memo names: an approved
verifier candidate can now persist a ``VerificationResult`` against an
existing claim with one command, instead of only through
``VerificationService.record`` called from Python. The CLI is thin — it
parses arguments, validates the input file against the existing
``VerificationResult`` contract, checks dependencies, calls
``record_verification`` exactly once, and reports the result or a clear,
typed failure. No domain behavior lives here (task-packets/E2-T07.yaml's CLI
law): the rule-8 self-verification refusal, the target-kind checking implied
by ``VerificationService.record``'s own guards, and the failed-recommendation
claim-status policy all remain exclusively that service's (and
``ClaimService``'s) business.

--- Ordering invariant: a contract-invalid file never opens a database connection ---

task-packets/K1-T05.yaml's own invariant. ``record_command`` therefore reads
and validates ``--verification-file`` against the ``VerificationResult``
contract FIRST, entirely before ``sqlalchemy.create_engine`` is ever called —
mirroring ``mrr.services.cli.main``'s own "artifact-root writability checked
before any service call" ordering, just one step earlier here because the
file check needs no I/O dependency the CLI could accidentally skip.

--- The exit-code map (task-packets/K1-T05.yaml derived_decisions (d)) --------

- ``0``: a verification was actually persisted (``VerificationService
  .record`` returned).
- ``2``: a DEPENDENCY is unavailable — the PostgreSQL database is
  unreachable, or ``--verification-file`` is missing/unreadable/not valid
  JSON/not a contract-valid ``VerificationResult``. No in-memory fallback
  exists for either (MRR-NFR-012); the run simply does not happen, and says
  so.
- ``3``: recording was REFUSED by everything downstream of a successful
  dependency check — the rule-8 self-verification gate, an unknown
  ``--claim-id``, a target-kind mismatch (either the file's own
  ``target_kind`` or the resolved object's own ``kind`` — see
  ``verification_orchestration``'s own module docstring), or any other
  ``mrr.domain.exceptions.DomainError``/plain ``ValueError``
  ``record_verification`` lets propagate.
- argparse's own built-in failures (a bad flag, a missing required argument)
  use argparse's own exit code, ``2`` — the same overlap
  ``mrr.services.cli.main``/``synthesis_main`` already have between their own
  dependency-unavailable code and argparse's usage-error code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlalchemy as sa
from mrr.contracts import VerificationResult
from mrr.domain.exceptions import DomainError
from mrr.services.cli.verification_orchestration import record_verification
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_RECORDING_REFUSED = 3


def _add_record_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    record_parser = subparsers.add_parser(
        "record",
        help=(
            "Persist a VerificationResult JSON document against an existing claim, atomically "
            "with its verification.recorded event (task-packets/K1-T05.yaml)."
        ),
    )
    record_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    record_parser.add_argument(
        "--verification-file",
        required=True,
        type=Path,
        help=(
            "Path to a JSON document, parsed and validated as the VerificationResult contract "
            "BEFORE any database connection is opened. Never assembled from loose flags: the "
            "verification is authored by the verifier, not the transport."
        ),
    )
    record_parser.add_argument(
        "--claim-id",
        required=True,
        help="URN of the claim this verification targets, loaded from the generic object store.",
    )
    record_parser.add_argument(
        "--run-executor-id",
        default=None,
        help=(
            "The producing run's executor identity, if known. An explicit, optional flag — "
            "NEVER derived by this CLI from stored state."
        ),
    )
    record_parser.add_argument(
        "--actor",
        required=True,
        help=(
            "Actor URN recorded on the verification.recorded event — the transport identity "
            "(who operates the CLI), distinct from the file's own reviewer_id (the epistemic "
            "identity, who verified). Never cross-checked against reviewer_id."
        ),
    )
    record_parser.add_argument(
        "--policy-version",
        required=True,
        help=(
            "Policy version recorded on the event. No default: recording a verification against "
            "a real claim is a governance act, and the caller states the policy version "
            "explicitly every time."
        ),
    )
    record_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Correlation id for this recording. Generated if omitted.",
    )


def register_verification_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr verification record`` — everything else about this subcommand's
    flags and behavior lives in this module.
    """
    verification_parser = subparsers.add_parser(
        "verification",
        help="Verification recording (task-packets/K1-T05.yaml).",
    )
    verification_subparsers = verification_parser.add_subparsers(
        dest="verification_command", required=True
    )
    _add_record_subparser(verification_subparsers)


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr verification record`` — called
    both by ``mrr.services.cli.main.main`` (the real, nested
    ``mrr verification record`` entry point) and by this module's own
    standalone ``main`` (below), which both parse the SAME flags via
    ``_add_record_subparser``. Unconditional, like
    ``mrr.services.cli.synthesis_main.run_command`` — "verification" has
    exactly one nested subcommand ("record"), so by the time either caller
    reaches this function, that is the only possibility argparse's own
    ``required=True`` subparsers already enforced.
    """
    # --- 1. Read + parse + validate --verification-file BEFORE any database
    #        connection is opened (task-packets/K1-T05.yaml invariant).
    try:
        raw_text = args.verification_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            "mrr verification record: cannot read --verification-file "
            f"{args.verification_file} ({exc}). Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        raw_document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(
            f"mrr verification record: --verification-file {args.verification_file} is not "
            f"valid JSON ({exc}).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        verification = VerificationResult.model_validate(raw_document)
    except ValidationError as exc:
        print(
            f"mrr verification record: --verification-file {args.verification_file} does not "
            f"satisfy the VerificationResult contract:\n{exc}",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 2. Dependency check: the database must be reachable (MRR-NFR-012).
    try:
        engine = sa.create_engine(args.database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr verification record: cannot reach the PostgreSQL database at the given "
            f"--database-url ({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. Resolve the claim and record the verification exactly once.
    try:
        result = record_verification(
            engine=engine,
            verification=verification,
            claim_id=args.claim_id,
            run_executor_id=args.run_executor_id,
            actor=args.actor,
            policy_version=args.policy_version,
            correlation_id=args.correlation_id,
        )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr verification record: recording refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_RECORDING_REFUSED
    finally:
        engine.dispose()

    payload = {
        "verification_id": result.verification_id,
        "revision": result.revision,
        "claim_id": result.claim_id,
        "claim_status": result.claim_status,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr verification"``),
    usable directly (``python -m mrr.services.cli.verification_main record
    ...``) without going through ``mrr.services.cli.main`` at all — mirrors
    ``mrr.services.cli.synthesis_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr verification",
        description="Verification recording (task-packets/K1-T05.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_record_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "record":
        return run_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
