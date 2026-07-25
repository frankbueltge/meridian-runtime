"""Local filesystem transport for a signed ``OfflineBundle``
(task-packets/E5-T08.yaml), plus a file-backed replay ledger implementing
``mrr.domain.offline_bundle``'s caller-supplied ``already_processed``
predicate.

This module closes the ONE thing task-packets/E5-T06.yaml explicitly left
open: "the physical air-gap transfer medium (file/USB/media byte I/O) ...
are marked infra-dependent and are NOT built or CI-tested in this packet."
It builds and signs nothing, evaluates no trust, and re-implements no accept
condition — ``mrr.domain.offline_bundle.build_outbox_bundle``/
``validate_inbound_bundle`` stay the ONLY place that logic lives
(task-packets/E5-T08.yaml R3); this module is I/O only.

--- Round-trip byte identity is the load-bearing property here ------------

``write_bundle`` serialises an already-built, already-signed
``OfflineBundle`` to the EXACT ADR-0004 ``exclude_none=True`` canonical form
the core signs over — ``json.loads(bundle.model_dump_json(exclude_none=True))``
passed straight into the EXISTING ``mrr.crypto.canonical.canonicalize``
(the same RFC 8785 canonicalisation ``mrr.domain.hashing_policy.sign_object``/
``verify_object_signature`` already use internally) — never a second,
hand-rolled serialisation (e.g. plain ``json.dumps``). A second serialisation
recipe could byte-differ from what was actually signed over in ways that
never matter for signature verification (whitespace, key order) but WOULD
matter for this module's OWN "byte-stable, reviewable archive artefact"
goal, or worse, could subtly diverge from RFC 8785 in some edge case and
silently corrupt what a future reader parses back. Reusing the one
canonicalisation function the core already trusts is the only way to
guarantee neither risk.

``read_bundle`` does the reverse — parse bytes back into an ``OfflineBundle``
— and performs ABSOLUTELY NO trust evaluation: no signature check, no
recipient/validity-window/replay check. Parsing and validating are two
strictly separate steps (task-packets/E5-T08.yaml derived_decisions (f),
"the same discipline R2-T01 established with its gate-before-evaluator
ordering"), so a caller holding a freshly-``read_bundle``'d ``OfflineBundle``
can never mistake it for an ACCEPTED one — that requires a separate,
explicit call to ``mrr.domain.offline_bundle.validate_inbound_bundle``
(``mrr.services.cli.federation_main`` is the one caller in this packet that
does both, in that order, never conflating them).

--- No ``root`` — unlike ``LocalFilesystemArtifactStore`` -----------------

``mrr.adapters.object_store.local.LocalFilesystemArtifactStore`` takes a
``root`` directory because it derives a CONTENT-ADDRESSED path from a hash
it computes itself. ``LocalFilesystemBundleTransport`` has no equivalent
derivation to do: ``write_bundle``/``read_bundle`` each take an explicit
``path`` naming exactly where the bundle lives (task-packets/E5-T08.yaml
R1's own signature), so a constructor-level root would be redundant state
with nothing to anchor. The class still mirrors the adapter SHAPE this
codebase's precedent establishes — a small class wrapping filesystem
operations behind typed errors, holding no global/module-level mutable
state of its own.

--- Never overwrite: an atomic create-only write, not a plain overwrite ----

``LocalFilesystemArtifactStore._atomic_write`` uses ``os.replace``
unconditionally, because that store's re-put rule is deliberately
overwrite-tolerant for identical content (first-writer-metadata-wins,
distinct blob key already guarantees identical bytes). ``write_bundle`` has
the OPPOSITE requirement (task-packets/E5-T08.yaml R1: "written atomically
... and NEVER over an existing file") — a bundle file at a given path is
not content-addressed, so two different callers could otherwise silently
clobber one another's outbox artefact. ``write_bundle`` therefore checks
``path`` does not already exist BEFORE doing any work (the cheapest
possible check, mirroring every CLI module's own NFR-012 "--output must not
exist" precedent), writes the canonical bytes to a temp file in the SAME
directory, fsyncs it, re-checks existence immediately before the final
``os.replace`` (closing most of the race window a caller could otherwise
hit), and raises :class:`BundleWriteConflictError` rather than overwriting
if the path exists at either check. A crash between the temp write and the
final replace leaves at most an orphaned ``.tmp`` file, cleaned up on the
same code path, and never a partially written bundle at the target path —
the same crash-safety ``LocalFilesystemArtifactStore._atomic_write``
documents for its own writes.

--- The replay ledger: a committed, reviewable, fail-closed JSON document --

:class:`FileBackedReplayLedger` implements
``mrr.domain.offline_bundle.BundleAlreadyProcessed`` (``Callable[[str],
bool]``) over a single JSON file: ``{"schema_version": 1,
"processed_bundle_ids": [...]}``, the ids list ALWAYS sorted, ALWAYS unique,
rewritten via ``json.dumps(..., sort_keys=True)`` plus a trailing newline —
a byte-stable archive artefact fit to commit to git (task-packets/E5-T08.yaml
R2, derived_decisions (d): "git is the archive").

A ledger file that does not yet exist is treated as legitimately empty —
"nothing has ever been recorded here yet" is an honest state a fresh
deployment starts in, exactly like ``LocalFilesystemArtifactStore``'s own
constructor creates its root directory on first use rather than demanding
one already exist. A ledger file that DOES exist but fails ANY of its own
shape checks (not valid UTF-8, not valid JSON, not a JSON object, a wrong or
missing ``schema_version``, ``processed_bundle_ids`` not a list of strings,
containing a duplicate, or not sorted) is a DIFFERENT situation entirely —
data that was supposed to be trustworthy and is not — and
:meth:`FileBackedReplayLedger.already_processed` raises
:class:`ReplayLedgerCorruptError` rather than falling back to "nothing
processed yet" (task-packets/E5-T08.yaml invariants: "A malformed ledger
raises; it never defaults to 'nothing processed yet'" — silently treating
corruption as an empty ledger would disable replay protection exactly when
it matters most).

:meth:`FileBackedReplayLedger.record` is the ONLY mutating method. Callers
in this packet (``mrr.services.cli.federation_main``) call it EXACTLY ONCE,
and ONLY after ``mrr.domain.offline_bundle.validate_inbound_bundle`` has
already returned successfully (i.e. after full acceptance, all five
conditions held) — never before, and never for a refused bundle. Every
raised condition inside ``validate_inbound_bundle`` returns control to the
caller via an exception, so a refused bundle's code path simply never
reaches a ``record`` call; this module does not need to (and does not)
defend against that itself, but the ledger file is provably untouched by
any failed ``already_processed`` read, since that method never writes.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from mrr.contracts.offline_bundle import OfflineBundle
from mrr.crypto.canonical import canonicalize
from pydantic import ValidationError

__all__ = [
    "BundleReadError",
    "BundleTransportError",
    "BundleWriteConflictError",
    "FileBackedReplayLedger",
    "LocalFilesystemBundleTransport",
    "ReplayLedgerCorruptError",
    "ReplayLedgerError",
]

#: The ledger document's own schema version — bumped only if this module's
#: on-disk ledger shape ever changes; a ledger declaring any other value is
#: malformed (task-packets/E5-T08.yaml R2).
_LEDGER_SCHEMA_VERSION = 1


class BundleTransportError(Exception):
    """Base error for :class:`LocalFilesystemBundleTransport` failures
    (task-packets/E5-T08.yaml R1) — always names the offending ``path`` and
    a human-readable ``reason``; never a silent ``None``/skip.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class BundleWriteConflictError(BundleTransportError):
    """Raised by :meth:`LocalFilesystemBundleTransport.write_bundle` when
    ``path`` already exists — this transport NEVER overwrites an existing
    bundle file (task-packets/E5-T08.yaml R1).
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path, "already exists — refusing to write over it")


class BundleReadError(BundleTransportError):
    """Raised by :meth:`LocalFilesystemBundleTransport.read_bundle` when
    ``path`` is missing, not valid UTF-8, not valid JSON, or does not
    validate as an :class:`~mrr.contracts.offline_bundle.OfflineBundle`
    (task-packets/E5-T08.yaml R1). Never raised for a TRUST failure — this
    transport performs no trust evaluation at all; see the module docstring.
    """


class ReplayLedgerError(Exception):
    """Base error for :class:`FileBackedReplayLedger` failures
    (task-packets/E5-T08.yaml R2) — always names the offending ``path`` and
    a human-readable ``reason``.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class ReplayLedgerCorruptError(ReplayLedgerError):
    """Raised when a ledger file EXISTS but does not match this module's own
    on-disk shape — never raised for a ledger file that simply does not
    exist yet (see the module docstring's "The replay ledger" section for
    why those two situations are handled differently).
    """


class LocalFilesystemBundleTransport:
    """Write/read a signed :class:`OfflineBundle` to/from the local
    filesystem. See the module docstring for the full atomicity, byte-
    identity, and parse/trust-separation design.

    Holds no state of its own — every operation takes its own explicit
    ``path`` — so a single instance may be reused freely across calls.
    """

    def write_bundle(self, bundle: OfflineBundle, path: Path) -> None:
        """Serialise ``bundle`` to the ADR-0004 ``exclude_none=True``
        canonical JSON bytes the E5-T06 core signs over, and write them
        atomically to ``path``.

        Raises:
            BundleWriteConflictError: if ``path`` already exists, either at
                the initial check or immediately before the final atomic
                replace. ``path`` is left untouched in either case.
        """
        path = Path(path)
        if path.exists():
            raise BundleWriteConflictError(path)

        canonical_bytes = canonicalize(json.loads(bundle.model_dump_json(exclude_none=True)))

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(canonical_bytes)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            if path.exists():
                raise BundleWriteConflictError(path)
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise

    def read_bundle(self, path: Path) -> OfflineBundle:
        """Parse ``path`` back into an :class:`OfflineBundle`. Performs NO
        trust evaluation whatsoever — see the module docstring.

        Raises:
            BundleReadError: ``path`` does not exist, its bytes are not
                valid UTF-8, its text is not valid JSON, or the parsed
                document does not validate as an ``OfflineBundle`` (missing/
                malformed fields, or one of that contract's own structural
                ``model_validator`` checks fails).
        """
        path = Path(path)
        if not path.is_file():
            raise BundleReadError(path, "file does not exist")

        raw_bytes = path.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BundleReadError(path, f"not valid UTF-8 ({exc})") from exc

        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BundleReadError(path, f"not valid JSON ({exc})") from exc

        try:
            return OfflineBundle.model_validate(body)
        except ValidationError as exc:
            raise BundleReadError(path, f"does not validate as an OfflineBundle ({exc})") from exc


class FileBackedReplayLedger:
    """A committed JSON file recording every bundle id this node has fully
    accepted, implementing ``mrr.domain.offline_bundle.BundleAlreadyProcessed``
    (task-packets/E5-T08.yaml R2). See the module docstring for the full
    fail-closed design.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def _load(self) -> list[str]:
        """Read and validate this ledger's current ``processed_bundle_ids``.

        A ledger file that does not exist yet returns an empty list — see
        the module docstring for why this differs from a ledger file that
        exists but is malformed.

        Raises:
            ReplayLedgerCorruptError: the file exists but is not valid
                UTF-8/JSON, is not a JSON object, declares the wrong (or no)
                ``schema_version``, ``processed_bundle_ids`` is not a list
                of strings, or that list contains a duplicate or is not
                sorted.
        """
        if not self._path.is_file():
            return []

        raw_bytes = self._path.read_bytes()
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReplayLedgerCorruptError(self._path, f"not valid UTF-8 ({exc})") from exc

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReplayLedgerCorruptError(self._path, f"not valid JSON ({exc})") from exc

        if not isinstance(document, dict):
            raise ReplayLedgerCorruptError(self._path, "top-level JSON value is not an object")

        schema_version = document.get("schema_version")
        if schema_version != _LEDGER_SCHEMA_VERSION:
            raise ReplayLedgerCorruptError(
                self._path,
                f"schema_version is {schema_version!r}, expected {_LEDGER_SCHEMA_VERSION!r}",
            )

        ids = document.get("processed_bundle_ids")
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ReplayLedgerCorruptError(
                self._path, "processed_bundle_ids is not a JSON array of strings"
            )
        if len(set(ids)) != len(ids):
            raise ReplayLedgerCorruptError(
                self._path, "processed_bundle_ids contains a duplicate id"
            )
        if ids != sorted(ids):
            raise ReplayLedgerCorruptError(self._path, "processed_bundle_ids is not sorted")

        return ids

    def already_processed(self, bundle_id: str) -> bool:
        """``True`` iff ``bundle_id`` is already recorded in this ledger.
        Matches ``mrr.domain.offline_bundle.BundleAlreadyProcessed``'s exact
        signature, so a bound reference to this method
        (``ledger.already_processed``) may be passed directly as
        ``validate_inbound_bundle``'s ``already_processed`` argument.

        Raises:
            ReplayLedgerCorruptError: see :meth:`_load`.
        """
        return bundle_id in self._load()

    def record(self, bundle_id: str) -> None:
        """Durably record ``bundle_id`` as processed. The ONLY mutating
        method on this class — callers MUST call this only after a bundle
        has been FULLY accepted (task-packets/E5-T08.yaml R2/invariants:
        "an id is recorded only after full acceptance").

        The rewritten file always holds a sorted, duplicate-free id list,
        serialised with ``sort_keys=True`` plus a trailing newline, written
        atomically (temp file in the same directory, then ``os.replace``) —
        a byte-stable, reviewable archive artefact.

        Raises:
            ReplayLedgerCorruptError: the ledger's PRE-EXISTING content (if
                any) fails :meth:`_load`'s own validation — this method
                refuses to silently repair or overwrite corrupt history.
        """
        ids = self._load()
        updated_ids = sorted({*ids, bundle_id})
        document = {"schema_version": _LEDGER_SCHEMA_VERSION, "processed_bundle_ids": updated_ids}
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(text)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, self._path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise
