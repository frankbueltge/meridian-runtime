"""``mrr release create`` / ``mrr release verify`` (docs/spec/adr/ADR-0011-
RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md / task-packets/E8-T04.yaml): a thin
argparse CLI over ``mrr.services.release.bundle.assemble_and_release`` and
``mrr.services.release.verify.verify_rebuild``/``verify_bundle_dir``,
mirroring ``mrr.services.cli.report_main``'s/``export_main``'s own shape (a
sibling file, not inlined into ``main.py`` — the E2-T07 CLI-law precedent
every CLI module here follows) and ``mrr.services.cli.main``'s MRR-NFR-012
"explicit degradation, never a fabricated substitute" discipline. No domain
behavior lives here: the four typed refusals, the bundle-assembly order and
its one named inconsistent state, and the verify comparison algorithm all
remain exclusively ``mrr.services.release``'s business — this module never
inspects a crate body, a claim, or a bundle byte itself.

--- The A4 act has NO default, anywhere on this command (ADR-0011 decision 2) ---

``--approved-by``, ``--approval-statement-file``, and ``--approval-mode``
are each declared with ``default=None`` and ``required=False`` at the
argparse level — deliberately NOT ``required=True`` — so their absence is
caught by THIS module's own explicit check and reported with a custom
message that NAMES MRR-FR-102 and states plainly that no default exists by
design (task-packets/E8-T04.yaml R4), rather than argparse's own generic
"the following arguments are required" text. Supplying them IS the recorded
human act; the flags' own ``help=`` text says so too. ``--approval-mode``
DOES carry ``choices=("single_human", "dual")`` — "dual" is schema-valid
(docs/spec/01_SYSTEM_SPEC.md section 5: "explicit human or dual approval")
and must reach ``ReleaseService.create`` to be refused there with the
derived_decisions (b) message, not blocked earlier by argparse.

--- Two disclosed additions beyond task-packets/E8-T04.yaml R4's own flag list ---

R4's own text lists ``mrr release create``'s flags without
``--policy-version``/``--correlation-id`` — every OTHER event-writing CLI in
this codebase (``mrr verification record``) requires ``--policy-version``
explicitly ("no default: recording ... is a governance act, and the caller
states the policy version explicitly every time") and accepts an optional
``--correlation-id`` (generated if omitted); the two read-only CLIs
(``mrr report render``/``mrr export ro-crate``) have neither, because
NEITHER writes an event. ``ReleaseService.create`` DOES write one
(``release.approved``), so this module adds both flags, mirroring
``verification_main``'s own precedent exactly — flagged here, and in this
task's own delivery report, as a disclosed, necessary addition rather than a
silent deviation. (There is deliberately NO ``--actor`` flag: ADR-0011
decision 2 is explicit that the event's actor IS ``approved_by`` — see
``mrr.services.release.service``'s own module docstring.)

Similarly, ``mrr release verify``'s rebuild mode adds ``--classification-file``
(required only when the resolved record's own ``disclosure`` is
``"public"``; forbidden with ``--bundle-dir`` mode, since that mode never
re-renders anything) — mirroring ``mrr report render``'s own identical rule
— see ``mrr.services.release.verify``'s own module docstring for why a
public rebuild cannot faithfully reproduce the original bundle without it.

--- The exit-code map ---------------------------------------------------------

``mrr release create``:

- ``0``: the bundle was assembled and the ReleaseRecord persisted. Prints a
  single JSON line: ``release_id``, ``revision``, ``crate_id``,
  ``disclosure``, ``approval_mode``, ``root_hash``, ``file_count``,
  ``output_dir``.
- ``2``: a DEPENDENCY is unavailable — ``--approval-statement-file``
  unreadable, ``--classification-file`` missing/forbidden/unreadable/
  invalid, ``--artifact-root`` missing/not-a-directory, or the PostgreSQL
  database unreachable. No in-memory fallback exists for any of these
  (MRR-NFR-012).
- ``3``: the release was REFUSED — ``--output-dir`` already exists (checked
  FIRST, before anything else); ``--approved-by``/
  ``--approval-statement-file``/``--approval-mode`` absent (naming
  MRR-FR-102); or any ``mrr.domain.exceptions.DomainError``/plain
  ``ValueError`` ``assemble_and_release`` lets propagate, INCLUDING the one
  named inconsistent state
  (``mrr.services.release.errors.ReleaseBundleFinalizationError``, printed
  verbatim — its own message already names the exact state and the
  recovery path).

``mrr release verify``:

- ``0``: the independently-computed bytes match the stored record. Prints a
  single JSON line: ``release_id``, ``mode``, ``root_hash``, ``matched``.
- ``2``: a DEPENDENCY is unavailable — ``--bundle-dir`` missing/not-a-
  directory (bundle-dir mode), ``--artifact-root`` missing/not-a-directory
  (rebuild mode), ``--classification-file`` missing/forbidden/unreadable/
  invalid (rebuild mode, resolved AFTER the database round trip that learns
  the record's own disclosure — see the module docstring's own ordering
  note below), or the PostgreSQL database unreachable.
- ``3``: ``--release-id`` does not resolve, or resolves to a non-
  ``ReleaseRecord`` kind (a REFUSAL, matching ``export_main``'s/
  ``report_main``'s own "unknown id" precedent); or the comparison itself
  found a MISMATCH — every differing path is named, tagged
  missing/extra/changed.

--- Ordering note: verify's classification-file check runs AFTER the DB round trip ---

Unlike ``mrr release create``/``mrr report render`` (where ``--disclosure``
is a caller-supplied FLAG, known before any I/O), ``mrr release verify``
only learns the resolved record's own ``disclosure`` by reading it from the
database — so, for rebuild mode, the classification-file
required/forbidden check necessarily runs AFTER the database is reached and
``--release-id`` resolves, not before. Every other NFR-012 dependency check
(bundle-dir/artifact-root readability, database reachability) still runs
cheapest-first, ahead of that database round trip.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlalchemy as sa
from mrr.adapters.object_store.local import LocalFilesystemArtifactStore
from mrr.domain.artifacts import Classification
from mrr.domain.exceptions import DomainError
from mrr.domain.identity import new_urn
from mrr.persistence.repositories import (
    PostgresEdgeRepository,
    PostgresEventLog,
    PostgresObjectRepository,
)
from mrr.persistence.unit_of_work import bind_unit_of_work
from mrr.services.release.bundle import assemble_and_release, output_dir_conflict
from mrr.services.release.errors import ReleaseBundleFinalizationError
from mrr.services.release.verify import (
    PathDiff,
    resolve_release_record,
    verify_bundle_dir,
    verify_rebuild,
)
from sqlalchemy.exc import SQLAlchemyError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

_DISCLOSURE_INTERNAL = "internal"
_DISCLOSURE_PUBLIC = "public"

#: The exact five-value mrr.domain.artifacts.Classification enum, transcribed
#: here (not imported as a runtime iterable — Classification is a
#: typing.Literal) — mirrors mrr.services.cli.report_main's own identical
#: transcription and rationale.
_VALID_CLASSIFICATIONS: frozenset[str] = frozenset(
    {"PUBLIC", "INTERNAL", "RESTRICTED", "SENSITIVE", "PARTICIPANT_IDENTIFIABLE"}
)


def _load_classification_file(path: Path) -> dict[str, Classification]:
    """Read, parse, and validate ``path`` as a JSON object mapping urn ->
    one of the five declared ``Classification`` values. Mirrors
    ``mrr.services.cli.report_main._load_classification_file`` exactly (not
    imported from there — that function is private to its own module,
    matching this codebase's own per-module Protocol/helper convention).

    Raises:
        ValueError: ``path`` cannot be read, is not valid JSON, is not a
            JSON object, or any value is not one of the five declared
            classification levels.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read --classification-file {path} ({exc})") from exc

    try:
        raw_document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--classification-file {path} is not valid JSON ({exc})") from exc

    if not isinstance(raw_document, dict):
        raise ValueError(
            f"--classification-file {path} must be a JSON object (urn -> classification), "
            f"got {type(raw_document).__name__}"
        )

    classification_by_object_id: dict[str, Classification] = {}
    for object_id, value in raw_document.items():
        if value not in _VALID_CLASSIFICATIONS:
            raise ValueError(
                f"--classification-file {path}: {object_id!r} maps to {value!r}, which is not "
                f"one of {sorted(_VALID_CLASSIFICATIONS)}"
            )
        classification_by_object_id[object_id] = value
    return classification_by_object_id


# ---------------------------------------------------------------------------
# Argument parsing.
# ---------------------------------------------------------------------------


def _add_create_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    create_parser = subparsers.add_parser(
        "create",
        help=(
            "Assemble a deterministic release bundle (RO-Crate export + report renders) for "
            "one sealed EvidenceCrate and persist its ReleaseRecord, atomically with the "
            "release.approved A4 event (task-packets/E8-T04.yaml)."
        ),
    )
    create_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    create_parser.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
        help=(
            "Root directory of an existing, readable local content-addressed artifact store "
            "(never created or written to — mirrors `mrr export ro-crate`'s own identical flag)."
        ),
    )
    create_parser.add_argument(
        "--crate-id",
        required=True,
        help="URN of the sealed EvidenceCrate this release is rooted on.",
    )
    create_parser.add_argument(
        "--disclosure",
        required=True,
        choices=(_DISCLOSURE_INTERNAL, _DISCLOSURE_PUBLIC),
        help=(
            "'internal' (full content) or 'public' (MRR-FR-095 fail-closed redaction). "
            "'public' REQUIRES --classification-file; 'internal' FORBIDS it."
        ),
    )
    create_parser.add_argument(
        "--classification-file",
        type=Path,
        default=None,
        help=(
            "Path to a JSON object mapping object urn -> one of PUBLIC/INTERNAL/RESTRICTED/"
            "SENSITIVE/PARTICIPANT_IDENTIFIABLE. REQUIRED with --disclosure public; FORBIDDEN "
            "with --disclosure internal (mirrors `mrr report render`'s own identical rule)."
        ),
    )
    create_parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help=(
            "Directory the finished bundle is written into. Must not already exist as a file "
            "or as a non-empty directory (refused before any other check runs)."
        ),
    )
    create_parser.add_argument(
        "--approved-by",
        default=None,
        help=(
            "Person URN of the human approving this release (urn:mrr:person:...). NO DEFAULT "
            "EXISTS by design (MRR-FR-102/ADR-0011): supplying this flag IS the recorded human "
            "approval act. Its absence is a refusal, not a fallback."
        ),
    )
    create_parser.add_argument(
        "--approval-statement-file",
        type=Path,
        default=None,
        help=(
            "Path to a UTF-8 text file whose content IS the human's own approval statement, "
            "recorded verbatim. NO DEFAULT EXISTS by design (MRR-FR-102): its absence is a "
            "refusal, not a fallback."
        ),
    )
    create_parser.add_argument(
        "--approval-mode",
        choices=("single_human", "dual"),
        default=None,
        help=(
            "'single_human' (this practice's only IMPLEMENTED mode) or 'dual' (schema-valid, "
            "but service-refused — no second-approver workflow exists yet). NO DEFAULT EXISTS "
            "by design: its absence is a refusal, not a fallback."
        ),
    )
    create_parser.add_argument(
        "--policy-version",
        required=True,
        help=(
            "Policy version recorded on the release.approved event. No default: approving a "
            "real release is a governance act, and the caller states the policy version "
            "explicitly every time (mirrors `mrr verification record`'s own identical rule)."
        ),
    )
    create_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Correlation id for this release. Generated if omitted.",
    )


def _add_verify_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    verify_parser = subparsers.add_parser(
        "verify",
        help=(
            "Check a persisted ReleaseRecord's own bundle against independently-computed "
            "bytes — either rebuilt fresh from the archive, or read from an existing "
            "--bundle-dir (task-packets/E8-T04.yaml)."
        ),
    )
    verify_parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )
    verify_parser.add_argument(
        "--release-id",
        required=True,
        help="URN of the ReleaseRecord to verify, loaded from the generic object store.",
    )
    verify_parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help=(
            "Root directory of an existing, readable local content-addressed artifact store. "
            "REQUIRED for rebuild mode (no --bundle-dir given); unused and ignored with "
            "--bundle-dir."
        ),
    )
    verify_parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=None,
        help=(
            "An existing on-disk bundle directory to check directly against the stored record "
            "(no export, no render, no artifact store touched). Omit to rebuild fresh from the "
            "archive instead."
        ),
    )
    verify_parser.add_argument(
        "--classification-file",
        type=Path,
        default=None,
        help=(
            "Path to a JSON object mapping object urn -> classification, RE-SUPPLIED so a "
            "rebuild of a 'public' disclosure record renders the SAME attestation the original "
            "release used. REQUIRED for rebuild mode when the resolved record's own disclosure "
            "is 'public'; FORBIDDEN with --bundle-dir (that mode never re-renders anything)."
        ),
    )


def register_release_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr release create``/``mrr release verify`` — everything else about
    this subcommand's flags and behavior lives in this module.
    """
    release_parser = subparsers.add_parser(
        "release",
        help="Publication approval and immutable release bundle (task-packets/E8-T04.yaml).",
    )
    release_subparsers = release_parser.add_subparsers(dest="release_command", required=True)
    _add_create_subparser(release_subparsers)
    _add_verify_subparser(release_subparsers)


# ---------------------------------------------------------------------------
# `mrr release create`.
# ---------------------------------------------------------------------------


def _run_create_command(args: argparse.Namespace) -> int:
    # --- 1. --output-dir conflict FIRST — the cheapest of all checks.
    if output_dir_conflict(args.output_dir):
        print(
            f"mrr release create: --output-dir {args.output_dir} already exists (as a file, "
            "or as a non-empty directory) — refusing to write over or into it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. The A4 act's three inputs: NO default, absence is a refusal.
    missing = [
        flag
        for flag, value in (
            ("--approved-by", args.approved_by),
            ("--approval-statement-file", args.approval_statement_file),
            ("--approval-mode", args.approval_mode),
        )
        if value is None
    ]
    if missing:
        print(
            "mrr release create: "
            + ", ".join(missing)
            + " must be given explicitly — no default exists by design (MRR-FR-102: "
            "supplying these flags IS the recorded human approval act).",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 3. Read --approval-statement-file (a dependency, not a refusal).
    try:
        approval_statement = args.approval_statement_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            "mrr release create: cannot read --approval-statement-file "
            f"{args.approval_statement_file} ({exc}). Refusing to fabricate a substitute "
            "result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 4. --classification-file: required-with-public / forbidden-with-internal.
    disclosure = args.disclosure
    if disclosure == _DISCLOSURE_PUBLIC and args.classification_file is None:
        print(
            "mrr release create: --disclosure public requires --classification-file (an "
            "explicit governance input, never defaulted). Refusing to fabricate a substitute "
            "result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    if disclosure == _DISCLOSURE_INTERNAL and args.classification_file is not None:
        print(
            "mrr release create: --disclosure internal forbids --classification-file — an "
            "internal render ignores any attestation and must never appear to depend on one.",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    classification_by_object_id: dict[str, Classification] = {}
    if args.classification_file is not None:
        try:
            classification_by_object_id = _load_classification_file(args.classification_file)
        except ValueError as exc:
            print(
                f"mrr release create: {exc}. Refusing to fabricate a substitute result "
                "(MRR-NFR-012).",
                file=sys.stderr,
            )
            return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 5. --artifact-root must already exist and be readable.
    if not args.artifact_root.is_dir():
        print(
            f"mrr release create: --artifact-root {args.artifact_root} does not exist or is "
            "not a directory. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 6. Dependency check: the database must be reachable.
    try:
        engine = sa.create_engine(args.database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr release create: cannot reach the PostgreSQL database at the given "
            f"--database-url ({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 7. Assemble and persist exactly once.
    correlation_id = args.correlation_id or new_urn("research-run")
    try:
        object_repository = PostgresObjectRepository(engine)
        edge_repository = PostgresEdgeRepository(engine)
        event_log = PostgresEventLog(engine)
        artifact_store = LocalFilesystemArtifactStore(args.artifact_root)
        record = bind_unit_of_work(engine, object_repository, event_log)

        result = assemble_and_release(
            object_repository=object_repository,
            edge_repository=edge_repository,
            event_log=event_log,
            artifact_store=artifact_store,
            record=record,
            crate_id=args.crate_id,
            disclosure=disclosure,
            classification_by_object_id=classification_by_object_id,
            approved_by=args.approved_by,
            approval_statement=approval_statement,
            approval_mode=args.approval_mode,
            policy_version=args.policy_version,
            correlation_id=correlation_id,
            output_dir=args.output_dir,
        )
    except ReleaseBundleFinalizationError as exc:
        print(f"mrr release create: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except (DomainError, ValueError) as exc:
        print(
            f"mrr release create: release refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    finally:
        engine.dispose()

    payload = {
        "release_id": result.release_id,
        "revision": result.revision,
        "crate_id": result.crate_id,
        "disclosure": result.disclosure,
        "approval_mode": result.approval_mode,
        "root_hash": result.root_hash,
        "file_count": result.file_count,
        "output_dir": str(result.output_dir),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# `mrr release verify`.
# ---------------------------------------------------------------------------


def _format_diffs(diffs: tuple[PathDiff, ...]) -> str:
    return "; ".join(f"{diff.kind}: {diff.path}" for diff in diffs)


def _run_verify_command(args: argparse.Namespace) -> int:
    bundle_dir_mode = args.bundle_dir is not None

    # --- 1. Cheap, local filesystem checks first.
    if bundle_dir_mode:
        if not args.bundle_dir.is_dir():
            print(
                f"mrr release verify: --bundle-dir {args.bundle_dir} does not exist or is not "
                "a directory. Refusing to fabricate a substitute result (MRR-NFR-012).",
                file=sys.stderr,
            )
            return _EXIT_DEPENDENCY_UNAVAILABLE
        if args.classification_file is not None:
            print(
                "mrr release verify: --classification-file is forbidden with --bundle-dir — "
                "that mode reads bytes directly from disk and never re-renders anything.",
                file=sys.stderr,
            )
            return _EXIT_DEPENDENCY_UNAVAILABLE
    else:
        if args.artifact_root is None or not args.artifact_root.is_dir():
            print(
                "mrr release verify: rebuild mode (no --bundle-dir) requires --artifact-root, "
                "an existing, readable directory. Refusing to fabricate a substitute result "
                "(MRR-NFR-012).",
                file=sys.stderr,
            )
            return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 2. Dependency check: the database must be reachable.
    try:
        engine = sa.create_engine(args.database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr release verify: cannot reach the PostgreSQL database at the given "
            f"--database-url ({exc}). Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    object_repository = PostgresObjectRepository(engine)
    edge_repository = PostgresEdgeRepository(engine)
    event_log = PostgresEventLog(engine)

    try:
        if bundle_dir_mode:
            result = verify_bundle_dir(object_repository, args.release_id, args.bundle_dir)
        else:
            # --- 3. Resolve first (needed to learn the record's own
            #        disclosure, which --classification-file's own
            #        requirement depends on — see the module docstring's
            #        "Ordering note" section).
            body = resolve_release_record(object_repository, args.release_id)
            disclosure = body["disclosure"]

            classification_by_object_id: dict[str, Classification] | None = None
            if disclosure == _DISCLOSURE_PUBLIC and args.classification_file is None:
                print(
                    "mrr release verify: rebuilding a 'public' disclosure release requires "
                    "--classification-file (re-supplying the SAME attestation the original "
                    "release used). Refusing to fabricate a substitute result (MRR-NFR-012).",
                    file=sys.stderr,
                )
                return _EXIT_DEPENDENCY_UNAVAILABLE
            if disclosure == _DISCLOSURE_INTERNAL and args.classification_file is not None:
                print(
                    "mrr release verify: --classification-file is forbidden when rebuilding an "
                    "'internal' disclosure release — an internal render ignores any attestation "
                    "and must never appear to depend on one.",
                    file=sys.stderr,
                )
                return _EXIT_DEPENDENCY_UNAVAILABLE
            if args.classification_file is not None:
                try:
                    classification_by_object_id = _load_classification_file(
                        args.classification_file
                    )
                except ValueError as exc:
                    print(
                        f"mrr release verify: {exc}. Refusing to fabricate a substitute result "
                        "(MRR-NFR-012).",
                        file=sys.stderr,
                    )
                    return _EXIT_DEPENDENCY_UNAVAILABLE

            artifact_store = LocalFilesystemArtifactStore(args.artifact_root)
            result = verify_rebuild(
                object_repository=object_repository,
                edge_repository=edge_repository,
                event_log=event_log,
                artifact_store=artifact_store,
                release_id=args.release_id,
                tmp_parent=args.artifact_root.parent,
                classification_by_object_id=classification_by_object_id,
            )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr release verify: verification refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    finally:
        engine.dispose()

    if not result.matched:
        print(
            f"mrr release verify: MISMATCH for release {args.release_id!r} "
            f"(mode={result.mode}) — {_format_diffs(result.diffs)}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    payload = {
        "release_id": result.release_id,
        "mode": result.mode,
        "root_hash": result.root_hash,
        "matched": result.matched,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr release create``/``mrr release
    verify`` — called both by ``mrr.services.cli.main.main`` (the real,
    nested entry point) and by this module's own standalone ``main`` (below).
    """
    if args.release_command == "create":
        return _run_create_command(args)
    if args.release_command == "verify":
        return _run_verify_command(args)
    raise AssertionError(  # pragma: no cover - unreachable while required=True
        f"unknown release_command: {args.release_command!r}"
    )


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr release"``),
    usable directly without going through ``mrr.services.cli.main`` at all —
    mirrors ``mrr.services.cli.report_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr release",
        description="Publication approval and immutable release bundle (task-packets/E8-T04.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_create_subparser(subparsers)
    _add_verify_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "create":
        return _run_create_command(args)
    if args.command == "verify":
        return _run_verify_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
