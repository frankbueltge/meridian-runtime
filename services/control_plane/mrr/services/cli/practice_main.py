"""``mrr practice init`` (task-packets/E5-T11.yaml): a thin argparse CLI over
the UNCHANGED E5-T11 core (``mrr.domain.practice_identity.
build_self_signed_practice``) — mirrors ``mrr.services.cli.federation_main``'s
own shape (a sibling file, not inlined into ``main.py``) and
``mrr.services.cli.main``'s MRR-NFR-012 "explicit degradation, never a
fabricated substitute" discipline. No hashing, signing, or kid-derivation
logic lives here: this module only parses arguments, loads a PEM key, mints
this document's own identity and reads the creation instant, calls the
domain core exactly once, and reports the result or a clear, typed refusal.

--- Why a NEW top-level ``mrr practice`` group, not ``mrr federation practice`` ---

``Practice`` is a first-class entity (task-packets/E5-T01.yaml), not a
transport concern: ``mrr.domain.manifest_trust.practice_key_ring`` feeds
``task_trust``/``crate_trust``/``transfer_trust`` as much as it feeds
federation. ``mrr.services.cli.main.build_parser`` already registers
fourteen subcommands through the identical ``register_*_subcommand``
pattern (``synthesis``, ``verification``, ``export``, ``report``,
``release``, ``validate``, ``audit``, ``observe``, ``federation``, ...); a
fifteenth, sibling top-level group is that established form, not a special
case (docs/design/2026-07-26-e5-t11-ableitung-praxis-identitaet.md, "Warum
ein eigenes Top-Level-Kommando").

--- One command, not two ------------------------------------------------------

A separate ``mrr practice key-info`` (reporting only ``kid``/
``encoded_public_key``) was considered and rejected: the written ``Practice``
JSON already carries ``kid`` authoritatively at ``keys[0].kid``, and a
second command would create a second source for the same truth. ``init``'s
own JSON result payload reports the ``kid`` directly (see the exit-code map
below) — enough for the real, named need (``--key-id`` for
``mrr federation envelope sign``) without building surface on spec
(task-packets/E5-T11.yaml reviewer_resolution, point (4)).

--- The PEM load and the identity mint both belong to the CLI, not the domain ---

``mrr.domain.practice_identity.build_self_signed_practice`` takes an
already-loaded ``Ed25519PrivateKey`` and caller-supplied
``practice_id``/``created_at`` — it never touches a filesystem and never
reads a clock (see that module's own "Determinism" section). This command
is where both of those genuinely real-world facts belong instead:

- ``--key-file`` is loaded via a LOCAL ``_load_signing_key``, mirroring
  ``mrr.services.cli.federation_main._load_signing_key`` line for line
  (that function lives in a module outside this task's ``allowed_paths``,
  so it is reimplemented here rather than imported — the same choice
  ``federation_main.py``'s own ``_write_bytes_atomically_never_overwriting``
  already made for ``mrr.adapters.federation.local``'s identical write
  discipline, for the identical reason).
- ``practice_id`` is minted via the UNCHANGED
  ``mrr.domain.identity.new_urn("practice")`` — a fresh ULID for a genuinely
  NEW identity document (there is no existing Meridian ``Practice`` to
  reuse an id from; docs/design/2026-07-26-e5-t11-ableitung-praxis-
  identitaet.md's fact-lock: "die real runs minted a fresh practice ULID
  each, so no stable Meridian practice id exists to reuse").
- ``created_at`` is read via ``datetime.now(UTC)`` — mirroring
  ``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal``'s
  own identical choice to read the wall clock only at the service/CLI
  boundary, never inside a pure domain function, and mirroring
  ``mrr federation inbox accept --at``'s own documented justification for
  its identical default ("receipt time is genuinely a real-world instant")
  — the instant a Meridian operator actually runs this command IS the
  instant this identity document is genuinely created. Unlike
  ``inbox accept``'s ``--at``, there is no override flag here at all: the
  packet's own binding flag list (task-packets/E5-T11.yaml objective) names
  no ``--created-at``, and a caller wanting deterministic control over this
  value already has one — calling
  ``mrr.domain.practice_identity.build_self_signed_practice`` directly, as
  every test in ``tests/unit/domain/test_practice_identity.py`` does.

--- No ``--generate-key`` here either -----------------------------------------

``--key-file`` is REQUIRED, with no generation fallback of any kind —
mirroring ``mrr federation outbox write``/``envelope sign``'s own identical
``--key-file`` discipline (task-packets/E5-T08.yaml R6), for a related but
distinct reason: this command's entire job is to PUBLISH an identity for a
key that already exists, not to bring one into being. The Verwahrungs-
Entscheidung this packet's own derivation names (2026-07-25) treats key
generation as a deliberate, separate, secret-handling act — "generated
locally; the private half is entered into the GitHub secret and then
deleted locally, the public half committed" — that this command's single
run must not casually fold into itself. An operator who has not yet
generated a key runs some other, explicit key-generation step first (e.g.
via ``mrr.crypto.keys.generate_ed25519_keypair`` in a short local script, or
any standard ``openssl``/``ssh-keygen``-equivalent tool that emits a PKCS8
PEM); this command only ever loads one.

--- No private key material ever reaches the output ---------------------------

``signing_key`` (the loaded ``Ed25519PrivateKey``) is used for exactly one
call into ``build_self_signed_practice`` and is never serialized, printed,
or logged anywhere in this module — the written ``--output`` file and the
JSON result payload below carry only PUBLIC data (the practice's own public
key, its ``kid``, and its owner content). A dependency failure while loading
``--key-file`` reports the PATH and the parse error, never the file's own
bytes (mirrors ``_load_signing_key``'s identical discipline in
``federation_main.py``).

--- The exit-code map ---------------------------------------------------------

``mrr practice init``:

- ``0``: the ``Practice`` was built, self-signed, and written. Prints a
  single JSON line: ``output``, ``id`` (== ``practice_id`` — a practice
  belongs to itself), ``name``, ``created_at``, ``kid`` (this document's own
  ``keys[0].kid`` — the value a subsequent ``mrr federation envelope sign
  --key-id`` call needs), and ``practice_sha256`` (the content hash of the
  bytes actually written to disk).
- ``2``: a DEPENDENCY is unavailable — ``--key-file`` is missing,
  unreadable, or does not validate as an Ed25519 private key, OR the given
  flags cannot even be assembled into a structurally valid, self-signed
  ``Practice`` (e.g. ``--valid-until`` not strictly after ``--valid-from``,
  a malformed ``--created-by`` URN, an empty ``--name``/``--description``).
  No in-memory or partial fallback exists for any of these (MRR-NFR-012) —
  this bucket mirrors ``mrr federation outbox write``/``envelope sign``'s
  own identical "cannot assemble a structurally valid object from the given
  inputs" category. Every content flag below is ``required=True`` with no
  default; argparse itself reports a MISSING one with this same exit code
  (task-packets/E5-T11.yaml reviewer_resolution, point (5): "a missing one
  is a typed refusal, never a silently filled slot").
- ``3``: ``--output`` already exists (checked FIRST, before anything else,
  and re-checked immediately before the atomic write) — this command NEVER
  overwrites an existing practice document.

--- Ordering invariant (MRR-NFR-012): cheapest, local checks before expensive ones ---

Mirrors ``envelope sign``'s own identical ordering exactly:
``--output`` existence (a plain filesystem stat, the cheapest possible
check) is checked FIRST, before any file is even read; then ``--key-file``
is read and parsed (still no cryptography); only THEN does
``build_self_signed_practice`` run the one actual hash-and-sign operation.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.crypto.canonical import canonicalize
from mrr.crypto.hashing import content_hash
from mrr.domain.identity import new_urn
from mrr.domain.practice_identity import build_self_signed_practice
from pydantic import ValidationError

#: See the module docstring's "exit-code map" section.
_EXIT_DEPENDENCY_UNAVAILABLE = 2
_EXIT_REFUSED = 3

#: ``Practice.disclosure.max_disclosure``'s own three-value vocabulary
#: (``mrr.contracts.research_score.MaxDisclosure``), duplicated here only as
#: an argparse ``choices=`` list — importing the ``Literal`` alias itself
#: would not give argparse a runtime-checkable choice set, and this module
#: otherwise imports no ``mrr.contracts`` at all (the domain core is the
#: only place that constructs contract objects).
_MAX_DISCLOSURE_CHOICES = ("INTERNAL", "PARTNER_RESTRICTED", "PUBLIC")


# ---------------------------------------------------------------------------
# Small, local input-loading/writing helpers — reimplemented rather than
# imported from mrr.services.cli.federation_main (outside this task's
# allowed_paths) or mrr.adapters.federation.local (the same). See the module
# docstring for why duplication, not a cross-module import of a private
# helper, is this codebase's own established choice here.
# ---------------------------------------------------------------------------


def _parse_aware_datetime(value: str) -> datetime:
    """argparse ``type=`` callable: an ISO 8601, timezone-AWARE datetime.
    Mirrors ``mrr.services.cli.federation_main._parse_aware_datetime``
    exactly — never defaults to a wall-clock reading; every temporal flag
    this command accepts (``--valid-from``/``--valid-until``) is explicit.
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


def _load_signing_key(path: Path) -> Ed25519PrivateKey:
    """Mirrors ``mrr.services.cli.federation_main._load_signing_key``
    exactly. Never returns, prints, or logs the raw PEM bytes on failure —
    only the path and the parser's own error.
    """
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
    """Mirrors ``mrr.services.cli.federation_main.
    _write_bytes_atomically_never_overwriting`` exactly: write ``data`` to
    ``path`` atomically, NEVER overwriting an existing file — checked once
    before any write starts, and once more immediately before the final
    atomic replace, so a concurrent writer racing this command still cannot
    cause a silent overwrite.

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


def _add_practice_init_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    init_parser = subparsers.add_parser(
        "init",
        help=(
            "Build and self-sign a Practice identity document for the practice that owns "
            "--key-file, via the unchanged E5-T11 build_self_signed_practice "
            "(task-packets/E5-T11.yaml). kid/encoded_public_key are always derived from "
            "--key-file, never accepted as flags. Never overwrites an existing --output."
        ),
    )
    init_parser.add_argument(
        "--key-file",
        required=True,
        type=Path,
        help=(
            "PEM-encoded Ed25519 PRIVATE key file for this practice. REQUIRED — this command "
            "never generates one; the published Practice's own kid/public key are always "
            "derived from this file, never from a flag."
        ),
    )
    init_parser.add_argument("--name", required=True, help="This practice's own published name.")
    init_parser.add_argument(
        "--description", required=True, help="This practice's own published description."
    )
    init_parser.add_argument(
        "--governance-contact",
        dest="governance_contact",
        action="append",
        required=True,
        help=(
            "A reachable governance contact reference (e.g. a mailto: or https: URI). "
            "Repeatable; at least one is required."
        ),
    )
    init_parser.add_argument(
        "--policy-version",
        dest="policy_version",
        action="append",
        required=True,
        help=(
            "A policy version this practice currently supports. Repeatable; at least one is "
            "required."
        ),
    )
    init_parser.add_argument(
        "--max-disclosure",
        required=True,
        choices=_MAX_DISCLOSURE_CHOICES,
        help="The ceiling disclosure level this practice publishes for itself.",
    )
    init_parser.add_argument(
        "--trust-statement",
        required=True,
        help=(
            "Free-text qualitative trust posture this practice publishes about itself. May be "
            "an empty string, but must be given explicitly — this command never supplies one."
        ),
    )
    init_parser.add_argument(
        "--valid-from",
        required=True,
        type=_parse_aware_datetime,
        help="The published key's own validity window start (ISO 8601, timezone-aware).",
    )
    init_parser.add_argument(
        "--valid-until",
        required=True,
        type=_parse_aware_datetime,
        help=(
            "The published key's own validity window end (ISO 8601, timezone-aware; must be "
            "strictly after --valid-from)."
        ),
    )
    init_parser.add_argument(
        "--created-by",
        required=True,
        help="URN of the person or agent role authoring this publication.",
    )
    init_parser.add_argument(
        "--capability-registry-endpoint",
        default=None,
        help="This practice's public capability-registry endpoint, if any. Optional.",
    )
    init_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="File path the self-signed Practice JSON is written into. Must not already exist.",
    )


def register_practice_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The ONE call ``mrr.services.cli.main.build_parser`` makes to register
    ``mrr practice init`` — everything else about this subcommand's flags
    and behavior lives in this module.
    """
    practice_parser = subparsers.add_parser(
        "practice",
        help=(
            "Build and publish a Practice identity document (task-packets/E5-T11.yaml). No "
            "network, no database connection."
        ),
    )
    practice_subparsers = practice_parser.add_subparsers(dest="practice_command", required=True)
    _add_practice_init_subparser(practice_subparsers)


# ---------------------------------------------------------------------------
# `mrr practice init`.
# ---------------------------------------------------------------------------


def _run_practice_init_command(args: argparse.Namespace) -> int:
    # --- 1. --output must not exist — the cheapest of all checks, before
    #        any file is even read (mirrors envelope sign's own ordering).
    if args.output.exists():
        print(
            f"mrr practice init: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    # --- 2. Read and parse --key-file — still no cryptography beyond
    #        loading and type-checking the key itself.
    try:
        signing_key = _load_signing_key(args.key_file)
    except ValueError as exc:
        print(
            f"mrr practice init: {exc}. Refusing to fabricate a substitute result (MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 3. This document's own genuinely real-world facts — minted/read
    #        HERE, at the CLI boundary, never inside the pure domain core
    #        (see the module docstring's own section on this).
    practice_id = new_urn("practice")
    created_at = datetime.now(UTC)

    # --- 4. Build and self-sign — the UNCHANGED E5-T11 core; the only
    #        cryptographic operation this command performs.
    try:
        practice = build_self_signed_practice(
            signing_key,
            practice_id=practice_id,
            created_at=created_at,
            created_by=args.created_by,
            name=args.name,
            description=args.description,
            governance_contacts=list(args.governance_contact),
            supported_policy_versions=list(args.policy_version),
            max_disclosure=args.max_disclosure,
            trust_statement=args.trust_statement,
            valid_from=args.valid_from,
            valid_until=args.valid_until,
            capability_registry_endpoint=args.capability_registry_endpoint,
        )
    except (ValidationError, ValueError) as exc:
        print(
            "mrr practice init: cannot assemble a self-signed Practice from the given inputs "
            f"— {type(exc).__name__}: {exc}. Refusing to fabricate a substitute result "
            "(MRR-NFR-012).",
            file=sys.stderr,
        )
        return _EXIT_DEPENDENCY_UNAVAILABLE

    # --- 5. Write atomically; never over an existing file. The exact
    #        ADR-0004/RFC-8785 canonical bytes — the same bytes a later
    #        `--trusted-sender-practice`/`Practice.model_validate` read
    #        parses back and the same body
    #        `Practice`'s own self-signature was computed over.
    canonical_bytes = canonicalize(json.loads(practice.model_dump_json(exclude_none=True)))
    try:
        _write_bytes_atomically_never_overwriting(args.output, canonical_bytes)
    except FileExistsError:
        print(
            f"mrr practice init: --output {args.output} already exists — refusing to write "
            "over it.",
            file=sys.stderr,
        )
        return _EXIT_REFUSED

    written_bytes = args.output.read_bytes()
    result_payload = {
        "output": str(args.output),
        "id": practice.id,
        "name": practice.name,
        "created_at": practice.created_at.isoformat(),
        "kid": practice.keys[0].kid,
        "practice_sha256": content_hash(written_bytes),
    }
    print(json.dumps(result_payload, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------


def run_command(args: argparse.Namespace) -> int:
    """The actual execution logic for ``mrr practice init`` — called both by
    ``mrr.services.cli.main.main`` (the real, nested entry point) and by
    this module's own standalone ``main`` (below).
    """
    if args.practice_command == "init":
        return _run_practice_init_command(args)
    raise AssertionError(  # pragma: no cover - unreachable while required=True
        f"unknown practice_command {args.practice_command!r}"
    )


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser for this module alone (``prog="mrr practice"``),
    usable directly (``python -m mrr.services.cli.practice_main init ...``)
    without going through ``mrr.services.cli.main`` at all — mirrors
    ``mrr.services.cli.federation_main.build_parser``'s identical shape.
    """
    parser = argparse.ArgumentParser(
        prog="mrr practice",
        description=(
            "Build and publish a Practice identity document (task-packets/E5-T11.yaml). No "
            "network, no database connection."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_practice_init_subparser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return _run_practice_init_command(args)

    parser.print_help()  # pragma: no cover - unreachable while "command" is required
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
