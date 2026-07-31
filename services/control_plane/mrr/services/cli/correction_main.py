"""``mrr correction record | impact | notify | status``
(task-packets/I1-T01.yaml): a thin argparse CLI over
``mrr.services.cli.correction_orchestration``, mirroring
``mrr.services.cli.verification_main``'s shape exactly — a sibling file, not
inlined into ``main.py``, so that module's own diff stays a one-line additive
subparser registration.

This closes the gap
docs/design/2026-07-26-wegkarte-erster-ecology-austausch.md names: the
correction lifecycle was complete and testable but reachable from nothing, so
the one ``payload_kind`` the federation actually carries could only be
produced from inside a service. No domain behavior lives here (E2-T07's CLI
law): the lifecycle's legal edges, per-recipient idempotence, the
``DELIVERY_PENDING`` hop on a failed delivery, and the new-revision-never-an-
overwrite discipline all stay ``CorrectionImpactService``'s business.

--- Ordering invariant: a contract-invalid file never opens a connection ---

``record`` reads and validates ``--correction-file`` against the
``CorrectionEvent`` contract BEFORE ``create_engine`` is called, and
``notify`` likewise validates its recipients file and reads its key file
first — the same ordering ``verification_main`` established, for the same
reason: a malformed input should cost nothing.

--- Why the notification is delivered to a DIRECTORY ----------------------

``notify`` hands ``notify_affected_practices`` the offline
``LocalFilesystemEnvelopeTransport``, whose endpoint is an outbox directory;
each delivered envelope lands at ``<endpoint>/<message_id>.json``. That is
exactly the shape ``mrr federation outbox write --envelope`` already
consumes, so a delivered notification is bundle-ready with no intermediate
step. The real mTLS transport stays deferred
(docs/spec/04_SECURITY_AND_POLICY.md 4.1); nothing here opens a socket.

Each recipient carries its OWN endpoint (``NotificationRecipient
.recipient_endpoint``), so the recipients file — not a global flag — decides
where an envelope goes. Resolving WHICH practices to notify stays
caller-supplied by that contract's own design.

--- The exit-code map (mirrors verification_main's) -----------------------

- ``0``: the operation was performed.
- ``2``: a DEPENDENCY is unavailable — PostgreSQL unreachable, or an input
  file missing/unreadable/not valid JSON/not contract-valid. No fallback
  exists (MRR-NFR-012); the operation simply does not happen, and says so.
- ``3``: the operation was REFUSED downstream of a successful dependency
  check — an unknown id, an illegal lifecycle transition, a missing
  dependency the service itself reports, or any other ``DomainError``/
  ``ValueError``.
- argparse's own usage errors use argparse's ``2``, the same documented
  overlap ``verification_main`` already carries.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from mrr.adapters.federation.local import LocalFilesystemEnvelopeTransport
from mrr.contracts import CorrectionEvent
from mrr.domain.exceptions import DomainError
from mrr.services.cli.correction_orchestration import (
    load_correction,
    notify_correction_recipients,
    propagate_correction_impact,
    record_correction,
)
from mrr.services.correction.service import NotificationRecipient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_OPERATION_REFUSED = 3


def _add_provenance_arguments(parser: argparse.ArgumentParser) -> None:
    """MRR-NFR-001 provenance, identical on every subcommand. ``--actor`` and
    ``--policy-version`` are required everywhere they are recorded: driving a
    correction forward is a governance act, and the caller states both
    explicitly every time (``verification_main``'s own reasoning).
    """
    parser.add_argument(
        "--actor",
        required=True,
        help="Actor URN recorded on every domain event this command causes.",
    )
    parser.add_argument(
        "--policy-version",
        required=True,
        help="Policy version recorded on every event. No default, by design.",
    )
    parser.add_argument(
        "--correlation-id",
        default=None,
        help="Correlation id for this operation. Generated if omitted.",
    )


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy PostgreSQL URL, e.g. postgresql+psycopg://user:pass@host/db",
    )


def _add_record_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "record",
        help=(
            "Persist a CorrectionEvent JSON document as revision 1, atomically with its "
            "own event (MRR-FR-090)."
        ),
    )
    _add_database_argument(parser)
    parser.add_argument(
        "--correction-file",
        required=True,
        type=Path,
        help=(
            "Path to a JSON document, parsed and validated as the CorrectionEvent contract "
            "BEFORE any database connection is opened. Never assembled from loose flags: the "
            "correction is authored by whoever issues it, not by the transport."
        ),
    )
    _add_provenance_arguments(parser)


def _add_impact_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "impact",
        help="Propagate a recorded correction's impact and report the flagged dependents.",
    )
    _add_database_argument(parser)
    parser.add_argument(
        "--correction-id",
        required=True,
        help="URN of the already-recorded CorrectionEvent.",
    )
    _add_provenance_arguments(parser)


def _add_notify_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "notify",
        help=(
            "Mint, sign and deliver one CorrectionNotification per pending recipient into "
            "that recipient's own outbox directory, driving the correction lifecycle forward."
        ),
    )
    _add_database_argument(parser)
    parser.add_argument(
        "--correction-id",
        required=True,
        help="URN of the already-recorded CorrectionEvent.",
    )
    parser.add_argument(
        "--recipients-file",
        required=True,
        type=Path,
        help=(
            "JSON array of recipients, each with recipient_practice_id, recipient_node_id, "
            "recipient_endpoint (this transport reads it as an outbox DIRECTORY) and "
            "notified_object_ids. Caller-supplied by NotificationRecipient's own design — "
            "never resolved from contract or obligation data here."
        ),
    )
    parser.add_argument(
        "--sender-node-id",
        required=True,
        help="This practice's own sending node id (the envelope's sender_node_id).",
    )
    parser.add_argument(
        "--notifying-practice-id",
        required=True,
        help="This practice's own id (MRR-FR-094). Never defaulted.",
    )
    parser.add_argument(
        "--key-file",
        required=True,
        type=Path,
        help=(
            "PEM-encoded Ed25519 private key that signs BOTH the notification and its "
            "wrapping envelope. Read from disk, never from an environment variable."
        ),
    )
    parser.add_argument(
        "--key-id",
        required=True,
        help=(
            "The key id for --key-file. Stated explicitly: a mistyped kid produces a "
            "well-signed envelope the other side discards, and the error only shows up in "
            "someone else's run."
        ),
    )
    parser.add_argument(
        "--sent-at",
        required=True,
        help="Shared sent_at for every notification and envelope (ISO 8601, timezone-aware).",
    )
    parser.add_argument(
        "--expires-at",
        required=True,
        help="Shared expires_at (ISO 8601, timezone-aware; strictly after --sent-at).",
    )
    _add_provenance_arguments(parser)


def _add_status_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "status",
        help="Report a recorded correction's current persisted state (read-only).",
    )
    _add_database_argument(parser)
    parser.add_argument(
        "--correction-id",
        required=True,
        help="URN of the CorrectionEvent to report.",
    )


def register_correction_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr correction`` — every flag and behavior lives in this module.
    """
    correction_parser = subparsers.add_parser(
        "correction",
        help=(
            "Correction lifecycle: record, propagate impact, notify affected practices "
            "offline, report status (task-packets/I1-T01.yaml)."
        ),
    )
    correction_subparsers = correction_parser.add_subparsers(
        dest="correction_command", required=True
    )
    _add_record_subparser(correction_subparsers)
    _add_impact_subparser(correction_subparsers)
    _add_notify_subparser(correction_subparsers)
    _add_status_subparser(correction_subparsers)


def _read_json_file(path: Path, what: str) -> object | None:
    """Read + parse one JSON input. Returns ``None`` after reporting, so a
    caller maps that to ``_EXIT_DEPENDENCY_UNAVAILABLE`` — no exception
    escapes into argparse's own error channel.

    A bare ``null`` document is rejected here rather than returned: JSON's
    ``null`` parses to Python's ``None``, which is this function's own
    "already reported a failure" sentinel. Letting it through would make a
    file holding ``null`` indistinguishable from an unreadable one and
    report the wrong reason — neither command accepts ``null`` anyway.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"mrr correction: cannot read {what} {path} ({exc}). Refusing to fabricate a "
            "substitute (MRR-NFR-012).",
            file=sys.stderr,
        )
        return None
    try:
        document: object = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"mrr correction: {what} {path} is not valid JSON ({exc}).", file=sys.stderr)
        return None
    if document is None:
        print(
            f"mrr correction: {what} {path} holds a bare JSON null, not a document.",
            file=sys.stderr,
        )
        return None
    return document


def _connected_engine(database_url: str) -> sa.Engine | None:
    try:
        engine = sa.create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(
            "mrr correction: cannot reach the PostgreSQL database at the given --database-url "
            f"({exc}). Refusing to fabricate a substitute (MRR-NFR-012).",
            file=sys.stderr,
        )
        return None
    return engine


def _parse_recipients(raw: object, path: Path) -> list[NotificationRecipient] | None:
    if not isinstance(raw, list) or not raw:
        print(
            f"mrr correction notify: --recipients-file {path} must hold a non-empty JSON array "
            "of recipient objects.",
            file=sys.stderr,
        )
        return None

    recipients: list[NotificationRecipient] = []
    required = ("recipient_practice_id", "recipient_node_id", "recipient_endpoint")
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            print(
                f"mrr correction notify: --recipients-file {path} entry {index} is not an object.",
                file=sys.stderr,
            )
            return None
        missing = [key for key in required if not entry.get(key)]
        if missing:
            print(
                f"mrr correction notify: --recipients-file {path} entry {index} is missing "
                f"{', '.join(missing)}.",
                file=sys.stderr,
            )
            return None
        object_ids = entry.get("notified_object_ids", [])
        if not isinstance(object_ids, list):
            print(
                f"mrr correction notify: --recipients-file {path} entry {index} has a "
                "notified_object_ids that is not an array.",
                file=sys.stderr,
            )
            return None
        recipients.append(
            NotificationRecipient(
                recipient_practice_id=str(entry["recipient_practice_id"]),
                recipient_node_id=str(entry["recipient_node_id"]),
                recipient_endpoint=str(entry["recipient_endpoint"]),
                notified_object_ids=[str(one) for one in object_ids],
            )
        )
    return recipients


def _stored_payload(stored: object) -> dict[str, object]:
    """The one report shape every mutating subcommand prints — the stored
    object's own identity and revision, never a summary that could drift
    from it.
    """
    body = getattr(stored, "body", {}) or {}
    return {
        "correction_id": getattr(stored, "id", None),
        "revision": getattr(stored, "revision", None),
        "status": body.get("status"),
        "impact_objects": body.get("impact_objects", []),
    }


def _run_record(args: argparse.Namespace) -> int:
    raw_document = _read_json_file(args.correction_file, "--correction-file")
    if raw_document is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE
    try:
        correction = CorrectionEvent.model_validate(raw_document)
    except ValidationError as exc:
        print(
            f"mrr correction record: --correction-file {args.correction_file} does not satisfy "
            f"the CorrectionEvent contract:\n{exc}",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    engine = _connected_engine(args.database_url)
    if engine is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE
    try:
        stored = record_correction(
            engine,
            correction=correction,
            actor=args.actor,
            policy_version=args.policy_version,
            correlation_id=args.correlation_id,
        )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr correction record: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_OPERATION_REFUSED
    finally:
        engine.dispose()

    print(json.dumps(_stored_payload(stored), sort_keys=True))
    return 0


def _run_impact(args: argparse.Namespace) -> int:
    engine = _connected_engine(args.database_url)
    if engine is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE
    try:
        stored = propagate_correction_impact(
            engine,
            correction_id=args.correction_id,
            actor=args.actor,
            policy_version=args.policy_version,
            correlation_id=args.correlation_id,
        )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr correction impact: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_OPERATION_REFUSED
    finally:
        engine.dispose()

    print(json.dumps(_stored_payload(stored), sort_keys=True))
    return 0


def _run_notify(args: argparse.Namespace) -> int:
    raw_recipients = _read_json_file(args.recipients_file, "--recipients-file")
    if raw_recipients is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE
    recipients = _parse_recipients(raw_recipients, args.recipients_file)
    if recipients is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        signing_key = load_pem_private_key(args.key_file.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        print(
            f"mrr correction notify: cannot load --key-file {args.key_file} as a PEM Ed25519 "
            f"private key ({exc}).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        sent_at = datetime.fromisoformat(args.sent_at)
        expires_at = datetime.fromisoformat(args.expires_at)
    except ValueError as exc:
        print(
            f"mrr correction notify: --sent-at/--expires-at must be ISO 8601 ({exc}).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    engine = _connected_engine(args.database_url)
    if engine is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE
    try:
        stored = notify_correction_recipients(
            engine,
            correction_id=args.correction_id,
            recipients=recipients,
            transport=LocalFilesystemEnvelopeTransport(),
            sender_node_id=args.sender_node_id,
            notifying_practice_id=args.notifying_practice_id,
            signing_key=signing_key,  # type: ignore[arg-type]
            signing_key_id=args.key_id,
            sent_at=sent_at,
            expires_at=expires_at,
            actor=args.actor,
            policy_version=args.policy_version,
            correlation_id=args.correlation_id,
        )
    except (DomainError, ValueError) as exc:
        print(
            f"mrr correction notify: refused — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_OPERATION_REFUSED
    finally:
        engine.dispose()

    payload = _stored_payload(stored)
    payload["outbox_directories"] = sorted({one.recipient_endpoint for one in recipients})
    print(json.dumps(payload, sort_keys=True))
    return 0


def _run_status(args: argparse.Namespace) -> int:
    engine = _connected_engine(args.database_url)
    if engine is None:
        return _EXIT_DEPENDENCY_UNAVAILABLE
    try:
        correction = load_correction(engine, args.correction_id)
    except (DomainError, ValueError) as exc:
        print(
            f"mrr correction status: cannot report — {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_OPERATION_REFUSED
    finally:
        engine.dispose()

    # Dumped through the contract in JSON mode, never through json.dumps over
    # the model objects: affected_objects holds AffectedObjectRef instances,
    # and a plain dumps raises TypeError on them. Letting the contract render
    # itself also keeps this report from drifting from the stored shape.
    document = correction.model_dump(mode="json")
    print(
        json.dumps(
            {
                "correction_id": correction.id,
                "revision": correction.revision,
                "status": correction.status,
                "correction_type": correction.correction_type,
                "severity": correction.severity,
                "affected_objects": document.get("affected_objects", []),
                "impact_objects": document.get("impact_objects") or [],
            },
            sort_keys=True,
        )
    )
    return 0


_COMMANDS = {
    "record": _run_record,
    "impact": _run_impact,
    "notify": _run_notify,
    "status": _run_status,
}


def run_command(args: argparse.Namespace) -> int:
    """Dispatch for ``mrr correction <subcommand>`` — called both by
    ``mrr.services.cli.main.main`` and by this module's own standalone
    ``main``, which parse the SAME flags via the ``_add_*_subparser``
    helpers.
    """
    command = getattr(args, "correction_command", None) or getattr(args, "command", None)
    handler = _COMMANDS.get(str(command))
    if handler is None:  # pragma: no cover - argparse's required=True prevents this
        print(f"mrr correction: unknown subcommand {command!r}.", file=sys.stderr)
        return 1
    return handler(args)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr correction"``),
    usable directly without going through ``mrr.services.cli.main`` —
    mirrors ``verification_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr correction",
        description="Correction lifecycle (task-packets/I1-T01.yaml).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_record_subparser(subparsers)
    _add_impact_subparser(subparsers)
    _add_notify_subparser(subparsers)
    _add_status_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_command(build_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
