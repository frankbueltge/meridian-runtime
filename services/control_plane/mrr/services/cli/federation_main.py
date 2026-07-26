"""``mrr federation outbox write`` / ``mrr federation inbox accept`` /
``mrr federation envelope sign`` (task-packets/E5-T08.yaml R5, extended by
task-packets/E5-T10.yaml): a thin argparse CLI over the UNCHANGED E5-T06
core (``mrr.domain.offline_bundle.build_outbox_bundle``/
``validate_inbound_bundle``) plus the new
``mrr.adapters.federation.local.LocalFilesystemBundleTransport``/
``FileBackedReplayLedger``, and — since E5-T10 — the UNCHANGED E5-T10 core
(``mrr.domain.envelope_signing.build_signed_envelope``) — mirrors
``mrr.services.cli.release_main``'s own shape (a sibling file, not inlined
into ``main.py``) and ``mrr.services.cli.main``'s MRR-NFR-012 "explicit
degradation, never a fabricated substitute" discipline. No signing, hashing,
expiry, or accept rule lives here: this module only parses arguments,
checks dependencies in cheapest-first order, calls the core exactly once
per command, and reports the result or a clear, typed refusal.

--- ``mrr federation envelope sign`` (task-packets/E5-T10.yaml) ------------

Closes the one gap docs/design/2026-07-26-e5-t10-derivation-envelope-kante.md
names: before this addition, nothing in the repository could turn an
arbitrary, already-content-hashed payload into a signed
``NodeMessageEnvelope`` from the command line — the only builder was a
private, ``CorrectionNotification``-bound method inside the correction
service. This subcommand is a thin wrapper over the new, payload-agnostic
``mrr.domain.envelope_signing.build_signed_envelope`` — no payload contract
is imported here, no closed set of ``--payload-kind`` values is enforced,
and every identity, timestamp, and the signing key are explicit, required
flags (no generation fallback, mirroring R6 below for the exact same
reason: minting an identity here risks minting one FOR ANOTHER PRACTICE).
Its exit-code map mirrors ``outbox write``'s own exactly (0 success, 2 a
missing/malformed/unassemblable dependency, 3 only for an existing
``--output``) rather than ``inbox accept``'s five-condition refusal map,
because this command — like ``outbox write`` — assembles and signs a LOCAL
artifact; it evaluates no inbound trust decision at all.

--- No ``--generate-key`` anywhere (R6, a hard stop) ------------------------

Unlike ``mrr run``'s ``--origin-key-file``/``--node-key-file`` (which
generate an EPHEMERAL key for a same-process demonstration run when
omitted), ``mrr federation outbox write``'s ``--key-file`` is REQUIRED, with
no generation fallback of any kind. Minting a key here would risk minting an
identity FOR ANOTHER PRACTICE, which task-packets/E5-T08.yaml R6 names as a
hard stop, not a shortcut: "Meridian may NOT mint an identity or key for
another practice — that is the forgery of exactly the independence this
system asserts." The operator supplies their own already-existing private
key; this command never invents one. ``mrr federation envelope sign``'s own
``--key-file`` is REQUIRED for the identical reason (task-packets/
E5-T10.yaml explicitly_not: "no invented identity ... no key ... minted for
Meridian and certainly none for Ulysses") — no ``--generate-key`` exists on
it either.

--- Transport is not trust ---------------------------------------------------

``mrr federation inbox accept``'s ``--trusted-sender-practice`` is an
EXPLICIT, required flag naming which practice's own published ``Practice``
document anchors trust for THIS invocation — never a default, never inferred
from the bundle's own claims. An accepted bundle proves it was signed by a
key that practice already declared, addressed to this node, in its validity
window, and not yet processed; it does not prove who the sender is in the
world (task-packets/E5-T08.yaml derived_decisions (c)).

--- The five accept conditions, never collapsed -----------------------------

``mrr.domain.offline_bundle.validate_inbound_bundle`` raises one of EIGHT
distinct typed exceptions across its five checked conditions (wrong
recipient; outside the validity window; already processed/replay; signer
mismatch, unknown key, or key not valid at receipt — three ways condition
four can fail; a bad/unsupported-algorithm signature; an entry content-hash
mismatch). ``_run_inbox_accept_command`` below has one ``except`` clause per
exception type, each printing its OWN distinct message and returning exit
3 — this module never collapses them into a single generic "rejected"
(AGENTS.md's prohibited-shortcuts list). ``coarse_bundle_rejection_reason``
is not used here at all: this CLI reports the SPECIFIC failure, never a
substitute for it (task-packets/E5-T08.yaml R3).

--- The second stage is not skipped or faked (R4) ---------------------------

A successful ``mrr federation inbox accept`` prints the accepted envelopes'
own ids and states, in both the machine-readable JSON payload
(``"envelope_validated": false``) and a plain-language stderr note, that
these envelopes are bundle-verified only — each still requires its OWN
``mrr.domain.envelope_validation.validate_inbound_envelope`` call before any
payload is acted on. This command never deserialises or interprets a
carried envelope's nested payload beyond what
``mrr.domain.offline_bundle.validate_inbound_bundle`` already did.

--- Ledger discipline: recorded only after full acceptance ------------------

``FileBackedReplayLedger.record`` is called EXACTLY ONCE, and ONLY after
``validate_inbound_bundle`` has already returned successfully. Every one of
the eight ``except`` branches below returns BEFORE reaching that call, so a
refused bundle's ledger file is left byte-identical
(task-packets/E5-T08.yaml invariants).

--- The exit-code map ---------------------------------------------------------

``mrr federation outbox write``:

- ``0``: the bundle was assembled, signed, and written. Prints a single JSON
  line: ``output``, ``bundle_id``, ``bundle_sha256`` (of the bytes actually
  on disk), ``envelope_count``, ``recipient_node_id``, ``sender_practice_id``.
- ``2``: a DEPENDENCY is unavailable — an ``--envelope`` file or
  ``--key-file`` is missing, unreadable, not valid UTF-8/JSON, does not
  validate as a ``NodeMessageEnvelope``/Ed25519 private key, OR the given
  flags cannot even be assembled into a structurally valid ``OfflineBundle``
  (e.g. ``--expires-at`` not strictly after ``--created-at``). No in-memory
  or partial fallback exists for any of these (MRR-NFR-012).
- ``3``: ``--output`` already exists (checked FIRST, before anything else,
  and re-checked immediately before the atomic write) — this command NEVER
  overwrites an existing bundle file.

``mrr federation envelope sign`` (task-packets/E5-T10.yaml):

- ``0``: the envelope was built, signed, and written. Prints a single JSON
  line: ``output``, ``message_id``, ``payload_kind``, ``sender_node_id``,
  ``sender_practice_id``, ``recipient_node_id``, ``envelope_sha256`` (of
  the bytes actually on disk).
- ``2``: a DEPENDENCY is unavailable — ``--payload`` or ``--key-file`` is
  missing, unreadable, not valid UTF-8/JSON (``--payload`` must additionally
  be a JSON OBJECT), or does not validate as an Ed25519 private key, OR the
  given flags cannot even be assembled into a valid, signed
  ``NodeMessageEnvelope`` — including the payload carrying no own
  ``content_hash`` (``mrr.domain.exceptions.
  EnvelopePayloadMissingContentHashError``) and any contract-level
  ``pydantic.ValidationError`` (e.g. ``--expires-at`` not strictly after
  ``--sent-at``). No in-memory or partial fallback exists for any of these
  (MRR-NFR-012) — this bucket mirrors ``outbox write``'s own identical
  "cannot assemble a structurally valid object from the given inputs"
  category exactly.
- ``3``: ``--output`` already exists (checked FIRST, before anything else,
  and re-checked immediately before the atomic write) — this command NEVER
  overwrites an existing envelope file.

``mrr federation inbox accept``:

- ``0``: the bundle was accepted (all five conditions held) and the ledger
  was durably updated. Prints a single JSON line: ``bundle_id``,
  ``accepted_envelope_ids``, ``envelope_validated`` (always ``false``),
  ``note`` (the plain-language "not yet envelope-validated" statement) — and
  prints the same note to stderr as well.
- ``2``: a DEPENDENCY is unavailable — ``--bundle`` or
  ``--trusted-sender-practice`` is missing/unreadable/malformed, OR the
  replay ledger at ``--ledger`` EXISTS but is malformed (a distinct failure
  from "this bundle was refused" — see
  ``mrr.adapters.federation.local.ReplayLedgerCorruptError``'s own
  docstring for why a malformed ledger never silently degrades to "nothing
  processed yet"). Also returned if the bundle passed validation but the
  ledger could not be durably updated afterward.
- ``3``: the bundle was REFUSED by one of ``validate_inbound_bundle``'s five
  conditions — see "The five accept conditions" above for the full,
  never-collapsed exception-to-message mapping.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

task-packets/E5-T08.yaml R5's own explicit ordering: for ``outbox write``,
``--output`` must not already exist (a plain filesystem stat, the cheapest
possible check) BEFORE any file is even read; then every ``--envelope``
file and ``--key-file`` are read and parsed (still no cryptography); only
THEN does ``build_outbox_bundle`` run the one actual signing operation. For
``inbox accept``, ``--trusted-sender-practice`` and ``--bundle`` are read
and parsed first (structural checks only, no trust evaluation for the
bundle — see ``LocalFilesystemBundleTransport.read_bundle``'s own
docstring); only then does ``validate_inbound_bundle`` run the one actual
cryptographic verification. ``envelope sign`` (task-packets/E5-T10.yaml)
follows the identical shape as ``outbox write``: ``--output`` existence
first, then ``--payload``/``--key-file`` are read and parsed, and only then
does ``build_signed_envelope`` run the one actual signing operation.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.adapters.federation.local import (
    BundleReadError,
    BundleWriteConflictError,
    FileBackedReplayLedger,
    LocalFilesystemBundleTransport,
    ReplayLedgerError,
)
from mrr.contracts.node_message_envelope import NodeMessageEnvelope
from mrr.contracts.practice import Practice
from mrr.crypto.canonical import canonicalize
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
from mrr.crypto.hashing import content_hash
from mrr.domain.envelope_signing import build_signed_envelope
from mrr.domain.exceptions import (
    BundleAlreadyProcessedError,
    BundleEntryHashMismatchError,
    BundleKeyNotValidError,
    BundleNotWithinValidityWindowError,
    BundleRecipientMismatchError,
    BundleSignerMismatchError,
    EnvelopePayloadMissingContentHashError,
    UnknownKeyIdError,
)
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.offline_bundle import build_outbox_bundle, validate_inbound_bundle
from pydantic import ValidationError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3


# ---------------------------------------------------------------------------
# Small, local input-loading helpers. Each raises a plain ValueError naming
# the offending flag/path/reason — the CLI's own dependency-unavailable
# tier, mirroring mrr.services.cli.release_main._load_classification_file's
# identical "ValueError naming the flag" convention.
# ---------------------------------------------------------------------------


def _parse_aware_datetime(value: str) -> datetime:
    """argparse ``type=`` callable: an ISO 8601, timezone-AWARE datetime.
    Never defaults to a wall-clock reading — every temporal field this
    command's ``outbox write`` accepts is explicit (task-packets/
    E5-T08.yaml R5: "none is minted, guessed, or defaulted to a wall
    clock").
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a valid ISO 8601 datetime ({exc})"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} must include a UTC offset (an aware datetime), e.g. "
            "'2026-07-25T12:00:00+00:00' or '2026-07-25T12:00:00Z'"
        )
    return parsed


def _load_json_document(path: Path, *, flag: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{flag} {path}: cannot read ({exc})") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{flag} {path}: not valid JSON ({exc})") from exc


def _load_envelope(path: Path) -> NodeMessageEnvelope:
    data = _load_json_document(path, flag="--envelope")
    try:
        return NodeMessageEnvelope.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"--envelope {path}: does not validate as a NodeMessageEnvelope ({exc})"
        ) from exc


def _load_practice(path: Path) -> Practice:
    data = _load_json_document(path, flag="--trusted-sender-practice")
    try:
        return Practice.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"--trusted-sender-practice {path}: does not validate as a Practice ({exc})"
        ) from exc


def _load_payload(path: Path) -> dict[str, Any]:
    """Load ``--payload`` (task-packets/E5-T10.yaml): any JSON OBJECT, never
    interpreted as a specific payload contract here — this command is as
    payload-agnostic as ``mrr.domain.envelope_signing.build_signed_envelope``
    itself. A JSON array/string/number/bool/null at the top level is a
    dependency failure, since ``NodeMessageEnvelope.payload`` (and
    ``build_signed_envelope``'s own ``payload.get("content_hash")`` lookup)
    both require a mapping.
    """
    data = _load_json_document(path, flag="--payload")
    if not isinstance(data, dict):
        raise ValueError(f"--payload {path}: must be a JSON object, got {type(data).__name__}")
    return data


def _load_signing_key(path: Path) -> Ed25519PrivateKey:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"--key-file {path}: cannot read ({exc})") from exc
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError as exc:
        raise ValueError(f"--key-file {path}: not a valid PEM-encoded private key ({exc})") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"--key-file {path}: does not contain an Ed25519 private key")
    return key


def _write_bytes_atomically_never_overwriting(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, NEVER overwriting an existing
    file — the identical temp-write-then-rename discipline
    ``mrr.adapters.federation.local.LocalFilesystemBundleTransport.
    write_bundle`` already implements for bundles (that adapter module is
    outside this task's allowed_paths, so ``envelope sign`` reimplements the
    same guarantee locally rather than importing or modifying it): checked
    once before any write starts, and once more immediately before the
    final atomic replace, so a concurrent writer racing this command still
    cannot cause a silent overwrite.

    Raises:
        FileExistsError: ``path`` already exists, at either check. ``path``
            is left untouched in either case, and any temp file created
            along the way is cleaned up.
    """
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        if path.exists():
            raise FileExistsError(path)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


# ---------------------------------------------------------------------------
# Argument parsing.
# ---------------------------------------------------------------------------


def _add_outbox_write_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    write_parser = subparsers.add_parser(
        "write",
        help=(
            "Assemble and sign an OfflineBundle from already-signed NodeMessageEnvelope "
            "files via the unchanged E5-T06 build_outbox_bundle, then write it as canonical "
            "JSON bytes (task-packets/E5-T08.yaml). Never overwrites an existing --output."
        ),
    )
    write_parser.add_argument(
        "--envelope",
        dest="envelope",
        action="append",
        required=True,
        type=Path,
        help=(
            "Path to an already-signed NodeMessageEnvelope JSON file, in the order it should "
            "appear in the bundle. Repeatable; at least one is required."
        ),
    )
    write_parser.add_argument("--bundle-id", required=True, help="This bundle's own identifier.")
    write_parser.add_argument(
        "--bundle-nonce", required=True, help="This bundle's own replay nonce (min 16 characters)."
    )
    write_parser.add_argument("--sender-node-id", required=True, help="The sending node's own id.")
    write_parser.add_argument(
        "--sender-practice-id", required=True, help="The sending node's own practice id."
    )
    write_parser.add_argument(
        "--recipient-node-id",
        required=True,
        help="The single recipient node this bundle is addressed to.",
    )
    write_parser.add_argument(
        "--created-at",
        required=True,
        type=_parse_aware_datetime,
        help="This bundle's own creation instant (ISO 8601, timezone-aware).",
    )
    write_parser.add_argument(
        "--expires-at",
        required=True,
        type=_parse_aware_datetime,
        help=(
            "This bundle's own expiry instant (ISO 8601, timezone-aware; must be strictly "
            "after --created-at)."
        ),
    )
    write_parser.add_argument(
        "--key-file",
        required=True,
        type=Path,
        help=(
            "PEM-encoded Ed25519 PRIVATE key file for the sending node. REQUIRED — this "
            "command never generates one (task-packets/E5-T08.yaml R6)."
        ),
    )
    write_parser.add_argument(
        "--key-id", required=True, help="The sending node's own key id for --key-file."
    )
    write_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="File path the canonical-JSON bundle is written into. Must not already exist.",
    )


def _add_inbox_accept_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    accept_parser = subparsers.add_parser(
        "accept",
        help=(
            "Read an OfflineBundle file and validate it fail-closed via the unchanged E5-T06 "
            "validate_inbound_bundle against a caller-declared trusted sender practice, this "
            "node's own id, and a committed replay ledger (task-packets/E5-T08.yaml). Accepted "
            "envelopes are bundle-verified only — NOT yet envelope-validated."
        ),
    )
    accept_parser.add_argument(
        "--bundle", required=True, type=Path, help="Path to the received OfflineBundle file."
    )
    accept_parser.add_argument(
        "--this-node-id", required=True, help="This receiving node's own id."
    )
    accept_parser.add_argument(
        "--trusted-sender-practice",
        required=True,
        type=Path,
        help=(
            "Path to the SENDER practice's own published Practice document — its keys anchor "
            "trust for this call. An explicit operator input, never a default (transport is "
            "not trust)."
        ),
    )
    accept_parser.add_argument(
        "--ledger",
        required=True,
        type=Path,
        help="Path to the committed replay-protection ledger JSON file.",
    )
    accept_parser.add_argument(
        "--at",
        default=None,
        type=_parse_aware_datetime,
        help=(
            "Evaluation instant (ISO 8601, timezone-aware). Defaults to the real receipt "
            "instant (now) when omitted — receipt time is genuinely a real-world instant."
        ),
    )


def _add_envelope_sign_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    sign_parser = subparsers.add_parser(
        "sign",
        help=(
            "Build and sign a NodeMessageEnvelope from an arbitrary payload carrying its own "
            "content_hash, via the unchanged E5-T10 build_signed_envelope (task-packets/"
            "E5-T10.yaml). Payload-agnostic: no --payload-kind is special-cased. Never "
            "overwrites an existing --output."
        ),
    )
    sign_parser.add_argument(
        "--payload",
        required=True,
        type=Path,
        help=(
            "Path to a JSON file holding the payload object to carry, verbatim. MUST carry "
            "its own 'content_hash' field — a payload without one is refused before any "
            "envelope is built (EnvelopePayloadMissingContentHashError)."
        ),
    )
    sign_parser.add_argument(
        "--payload-kind",
        required=True,
        help="A free-form, non-empty tag identifying the carried payload's shape.",
    )
    sign_parser.add_argument("--message-id", required=True, help="This envelope's own identifier.")
    sign_parser.add_argument("--sender-node-id", required=True, help="The sending node's own id.")
    sign_parser.add_argument(
        "--sender-practice-id", required=True, help="The sending node's own practice id."
    )
    sign_parser.add_argument(
        "--recipient-node-id",
        required=True,
        help="The single recipient node this envelope is addressed to.",
    )
    sign_parser.add_argument(
        "--sent-at",
        required=True,
        type=_parse_aware_datetime,
        help="This envelope's own send instant (ISO 8601, timezone-aware).",
    )
    sign_parser.add_argument(
        "--expires-at",
        required=True,
        type=_parse_aware_datetime,
        help=(
            "This envelope's own expiry instant (ISO 8601, timezone-aware; must be strictly "
            "after --sent-at)."
        ),
    )
    sign_parser.add_argument(
        "--key-file",
        required=True,
        type=Path,
        help=(
            "PEM-encoded Ed25519 PRIVATE key file for the sending node. REQUIRED — this "
            "command never generates one (mirrors task-packets/E5-T08.yaml R6)."
        ),
    )
    sign_parser.add_argument(
        "--key-id", required=True, help="The sending node's own key id for --key-file."
    )
    sign_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="File path the signed envelope JSON is written into. Must not already exist.",
    )


def _add_outbox_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    outbox_parser = subparsers.add_parser(
        "outbox", help="Assemble and write a signed OfflineBundle for offline transfer."
    )
    outbox_subparsers = outbox_parser.add_subparsers(dest="outbox_command", required=True)
    _add_outbox_write_subparser(outbox_subparsers)


def _add_inbox_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    inbox_parser = subparsers.add_parser(
        "inbox", help="Read and fail-closed validate a received OfflineBundle."
    )
    inbox_subparsers = inbox_parser.add_subparsers(dest="inbox_command", required=True)
    _add_inbox_accept_subparser(inbox_subparsers)


def _add_envelope_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    envelope_parser = subparsers.add_parser(
        "envelope",
        help="Build and sign a NodeMessageEnvelope from an arbitrary content-hashed payload.",
    )
    envelope_subparsers = envelope_parser.add_subparsers(dest="envelope_command", required=True)
    _add_envelope_sign_subparser(envelope_subparsers)


def register_federation_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr federation outbox write``/``mrr federation inbox accept``/
    ``mrr federation envelope sign`` — everything else about this
    subcommand's flags and behavior lives in this module.
    """
    federation_parser = subparsers.add_parser(
        "federation",
        help=(
            "Local filesystem store-and-forward transport for signed offline bundles, plus "
            "signed single-envelope construction (task-packets/E5-T08.yaml, E5-T10.yaml). No "
            "network, no database connection."
        ),
    )
    federation_subparsers = federation_parser.add_subparsers(
        dest="federation_command", required=True
    )
    _add_outbox_subparser(federation_subparsers)
    _add_inbox_subparser(federation_subparsers)
    _add_envelope_subparser(federation_subparsers)


# ---------------------------------------------------------------------------
# `mrr federation outbox write`.
# ---------------------------------------------------------------------------


def _run_outbox_write_command(args: argparse.Namespace) -> int:
    # --- 1. --output must not exist — the cheapest of all checks, before
    #        any file is even read.
    if args.output.exists():
        print(
            f"mrr federation outbox write: --output {args.output} already exists — refusing "
            "to write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Read and parse every declared input — still no cryptography.
    try:
        envelopes = [_load_envelope(path) for path in args.envelope]
        signing_key = _load_signing_key(args.key_file)
    except ValueError as exc:
        print(
            f"mrr federation outbox write: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. Assemble and sign — the UNCHANGED E5-T06 core; the only
    #        cryptographic operation this command performs.
    try:
        bundle = build_outbox_bundle(
            envelopes,
            bundle_id=args.bundle_id,
            bundle_nonce=args.bundle_nonce,
            sender_node_id=args.sender_node_id,
            sender_practice_id=args.sender_practice_id,
            recipient_node_id=args.recipient_node_id,
            created_at=args.created_at,
            expires_at=args.expires_at,
            signing_key=signing_key,
            key_id=args.key_id,
        )
    except (ValidationError, ValueError) as exc:
        print(
            "mrr federation outbox write: cannot assemble an OfflineBundle from the given "
            f"inputs — {type(exc).__name__}: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 4. Write atomically; never over an existing file.
    transport = LocalFilesystemBundleTransport()
    try:
        transport.write_bundle(bundle, args.output)
    except BundleWriteConflictError as exc:
        print(f"mrr federation outbox write: {exc}", file=sys.stderr)
        return _EXIT_REFUSED

    written_bytes = args.output.read_bytes()
    payload = {
        "output": str(args.output),
        "bundle_id": bundle.bundle_id,
        "bundle_sha256": content_hash(written_bytes),
        "envelope_count": len(envelopes),
        "recipient_node_id": bundle.recipient_node_id,
        "sender_practice_id": bundle.sender_practice_id,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# `mrr federation inbox accept`.
# ---------------------------------------------------------------------------


def _run_inbox_accept_command(args: argparse.Namespace) -> int:
    transport = LocalFilesystemBundleTransport()

    # --- 1. Read and parse the trusted sender's Practice document and the
    #        bundle itself — structural checks only, no trust evaluation
    #        for the bundle (LocalFilesystemBundleTransport.read_bundle
    #        never checks a signature).
    try:
        trusted_practice = _load_practice(args.trusted_sender_practice)
    except ValueError as exc:
        print(
            f"mrr federation inbox accept: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    try:
        bundle = transport.read_bundle(args.bundle)
    except BundleReadError as exc:
        print(
            f"mrr federation inbox accept: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    ring = practice_key_ring(trusted_practice)
    ledger = FileBackedReplayLedger(args.ledger)

    # --- 2. Run the UNCHANGED E5-T06 accept rule — the only place any
    #        cryptography or replay check happens. Each of the eight typed
    #        failures gets its OWN message; never collapsed (see the module
    #        docstring's "The five accept conditions" section).
    try:
        envelopes = validate_inbound_bundle(
            bundle,
            this_node_id=args.this_node_id,
            trusted_sender_practice_id=trusted_practice.id,
            ring=ring,
            already_processed=ledger.already_processed,
            at=args.at,
        )
    except ReplayLedgerError as exc:
        print(
            f"mrr federation inbox accept: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE
    except BundleRecipientMismatchError as exc:
        print(f"mrr federation inbox accept: refused — wrong recipient: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except BundleNotWithinValidityWindowError as exc:
        print(
            f"mrr federation inbox accept: refused — outside validity window: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    except BundleAlreadyProcessedError as exc:
        print(
            f"mrr federation inbox accept: refused — already processed (replay): {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    except BundleSignerMismatchError as exc:
        print(
            f"mrr federation inbox accept: refused — untrusted signer practice: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    except UnknownKeyIdError as exc:
        print(f"mrr federation inbox accept: refused — unknown signing key: {exc}", file=sys.stderr)
        return _EXIT_REFUSED
    except BundleKeyNotValidError as exc:
        print(
            f"mrr federation inbox accept: refused — signing key not valid at receipt: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    except (SignatureVerificationError, UnsupportedAlgorithmError) as exc:
        print(
            f"mrr federation inbox accept: refused — signature does not verify: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED
    except BundleEntryHashMismatchError as exc:
        print(
            f"mrr federation inbox accept: refused — entry content hash mismatch: {exc}",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 3. Record ONLY after full acceptance — every refusal above
    #        returned before reaching this line.
    try:
        ledger.record(bundle.bundle_id)
    except ReplayLedgerError as exc:
        print(
            f"mrr federation inbox accept: bundle {bundle.bundle_id!r} passed validation, but "
            f"the replay ledger could not be durably updated ({exc}) — refusing to report "
            "acceptance without a durable record.",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    note = (
        "these envelopes are bundle-verified (signature, recipient, validity window, replay) "
        "but NOT yet envelope-validated — each still requires its own "
        "validate_inbound_envelope call before any payload is acted on."
    )
    payload: dict[str, Any] = {
        "bundle_id": bundle.bundle_id,
        "accepted_envelope_ids": [envelope.message_id for envelope in envelopes],
        "envelope_validated": False,
        "note": note,
    }
    print(json.dumps(payload, sort_keys=True))
    print(f"mrr federation inbox accept: {note}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# `mrr federation envelope sign` (task-packets/E5-T10.yaml).
# ---------------------------------------------------------------------------


def _run_envelope_sign_command(args: argparse.Namespace) -> int:
    # --- 1. --output must not exist — the cheapest of all checks, before
    #        any file is even read (mirrors outbox write's own ordering).
    if args.output.exists():
        print(
            f"mrr federation envelope sign: --output {args.output} already exists — refusing "
            "to write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Read and parse every declared input — still no cryptography.
    try:
        payload = _load_payload(args.payload)
        signing_key = _load_signing_key(args.key_file)
    except ValueError as exc:
        print(
            f"mrr federation envelope sign: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. Build and sign — the UNCHANGED E5-T10 core; the only
    #        cryptographic operation this command performs. A payload with
    #        no own content_hash is refused here too
    #        (EnvelopePayloadMissingContentHashError) — the sender refuses
    #        exactly what validate_inbound_envelope's own condition 3 would
    #        refuse to accept.
    try:
        envelope = build_signed_envelope(
            payload,
            payload_kind=args.payload_kind,
            message_id=args.message_id,
            sender_node_id=args.sender_node_id,
            sender_practice_id=args.sender_practice_id,
            recipient_node_id=args.recipient_node_id,
            sent_at=args.sent_at,
            expires_at=args.expires_at,
            signing_key=signing_key,
            key_id=args.key_id,
        )
    except (EnvelopePayloadMissingContentHashError, ValidationError, ValueError) as exc:
        print(
            "mrr federation envelope sign: cannot assemble a NodeMessageEnvelope from the "
            f"given inputs — {type(exc).__name__}: {exc}. Refusing to fabricate a substitute "
            "result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 4. Write atomically; never over an existing file. The exact
    #        ADR-0004/RFC-8785 canonical bytes — the same bytes _load_envelope
    #        will parse back and the same body validate_inbound_envelope's
    #        own condition 5 verifies the signature against.
    canonical_bytes = canonicalize(json.loads(envelope.model_dump_json(exclude_none=True)))
    try:
        _write_bytes_atomically_never_overwriting(args.output, canonical_bytes)
    except FileExistsError:
        print(
            f"mrr federation envelope sign: --output {args.output} already exists — refusing "
            "to write over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    written_bytes = args.output.read_bytes()
    result_payload = {
        "output": str(args.output),
        "message_id": envelope.message_id,
        "payload_kind": envelope.payload_kind,
        "sender_node_id": envelope.sender_node_id,
        "sender_practice_id": envelope.sender_practice_id,
        "recipient_node_id": envelope.recipient_node_id,
        "envelope_sha256": content_hash(written_bytes),
    }
    print(json.dumps(result_payload, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr federation outbox write``/
    ``mrr federation inbox accept``/``mrr federation envelope sign`` —
    called both by ``mrr.services.cli.main.main`` (the real, nested entry
    point) and by this module's own standalone ``main`` (below).
    """
    if args.federation_command == "outbox" and args.outbox_command == "write":
        return _run_outbox_write_command(args)
    if args.federation_command == "inbox" and args.inbox_command == "accept":
        return _run_inbox_accept_command(args)
    if args.federation_command == "envelope" and args.envelope_command == "sign":
        return _run_envelope_sign_command(args)
    raise AssertionError(  # pragma: no cover - unreachable while required=True
        f"unknown federation_command {args.federation_command!r}"
    )


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr federation"``),
    usable directly (``python -m mrr.services.cli.federation_main outbox
    write ...``) without going through ``mrr.services.cli.main`` at all —
    mirrors ``mrr.services.cli.release_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr federation",
        description=(
            "Local filesystem store-and-forward transport for signed offline bundles, plus "
            "signed single-envelope construction (task-packets/E5-T08.yaml, E5-T10.yaml). No "
            "network, no database connection."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_outbox_subparser(subparsers)
    _add_inbox_subparser(subparsers)
    _add_envelope_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "outbox" and args.outbox_command == "write":
        return _run_outbox_write_command(args)
    if args.command == "inbox" and args.inbox_command == "accept":
        return _run_inbox_accept_command(args)
    if args.command == "envelope" and args.envelope_command == "sign":
        return _run_envelope_sign_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
