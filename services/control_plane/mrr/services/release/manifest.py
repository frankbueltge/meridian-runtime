"""Pure bundle-manifest computation (docs/spec/adr/ADR-0011-RELEASE-RECORD-
AND-A4-APPROVAL-EVENT.md decision 1/3): the deterministic file list and
``root_hash`` a release bundle directory's own content files hash to,
EXCLUDING ``release-manifest.json`` and ``release-record.json`` (the ADR's
own exclusion rule — those two files DESCRIBE the bundle, they are not its
content; task-packets/E8-T04.yaml derived_decisions (c): this is what makes
embedding ``release-record.json`` verbatim inside the bundle non-circular).

Used identically by three call sites this task-packet implements —
``mrr.services.release.bundle.assemble_and_release`` (compute the manifest
of a freshly-assembled temp directory, before ``release-manifest.json``/
``release-record.json`` even exist there), ``mrr.services.release.service
.ReleaseService.create`` (recompute ``root_hash`` from a caller-supplied
``files`` list, never trusting it), and ``mrr.services.release.verify``'s two
comparison modes (rebuild-from-archive, and an existing ``--bundle-dir``) —
so "root_hash is recomputed, never trusted from input, at every boundary"
(task-packets/E8-T04.yaml invariant) is one function, not four independent
copies that could drift.

No filesystem writes, no database, no repository/service import anywhere in
this module — a pure, framework-free computation over bytes already on
disk, mirroring ``mrr.domain.ro_crate``'s own "pure shaping, I/O performed
elsewhere" split.

--- The exact hash-line format (a disclosed, concrete design choice) --------

ADR-0011 decision 1 says only "root_hash = sha256 over the sorted per-file
hash lines" without pinning an exact byte format — no sibling precedent in
this codebase computes a "root hash over an ordered checksums list" the way
``mrr.domain.hashing_policy.compute_content_hash`` does for a single JSON
object, so this module defines the format concretely, flagged here for
reviewer scrutiny: each file contributes exactly one line, ``"<sha256>
<path>\\n"`` (two ASCII spaces between the hash and the path — the
conventional ``sha256sum``-style checksums-file separator), concatenated in
path-sorted order and hashed with ``mrr.crypto.hashing.content_hash`` over
the resulting UTF-8 bytes. Deliberately NOT
``mrr.crypto.canonical.canonicalize`` over a JSON array: there is no JSON
object here, just an ordered list of already-computed per-file hashes, and
canonical JSON's own re-sorting-by-key behavior would be redundant work over
a structure this module already sorts explicitly by its own chosen key
(``path``) rather than relying on RFC 8785's array-order-preserving,
object-key-reordering semantics.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mrr.crypto.hashing import content_hash

#: The two files a release bundle directory carries that DESCRIBE the
#: bundle rather than being content of it (ADR-0011 decision 1: root_hash
#: "covers content files only, excludes release-manifest.json and
#: release-record.json"). Matched by path relative to the manifest's own
#: root, at any depth — this task's own directory layout only ever places
#: them at the bundle root (see ``mrr.services.release.bundle``'s own
#: module docstring), but matching by relative path rather than
#: "root-level only" is the more conservative, defensible reading of the
#: ADR's own unqualified exclusion.
EXCLUDED_FILENAMES: frozenset[str] = frozenset({"release-manifest.json", "release-record.json"})


@dataclass(frozen=True, slots=True)
class BundleFileEntry:
    """One content file's relative path (POSIX-style, from the manifest
    root) and its ``sha256:<hex>`` content hash.
    """

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BundleManifest:
    """``files`` sorted by path (never trust caller/filesystem iteration
    order); ``root_hash`` computed from exactly that sorted list via
    :func:`compute_root_hash`.
    """

    files: tuple[BundleFileEntry, ...]
    root_hash: str


def compute_root_hash(files: Iterable[tuple[str, str]]) -> str:
    """Pure: the ``root_hash`` for an already-known ``(path, sha256)`` pair
    sequence, independent of any filesystem access — used both by
    :func:`compute_bundle_manifest` (fresh from disk) and by
    ``ReleaseService.create``/``mrr.services.release.verify`` (recomputing
    from an already-in-memory ``files`` list, e.g.
    ``mrr.contracts.release_record.Bundle.files``, never trusting its own
    ``root_hash`` field). See the module docstring for the exact line
    format. Sorts its own input by path before hashing, so an
    already-sorted or not-yet-sorted ``files`` sequence produces the
    identical result either way.
    """
    sorted_files = sorted(files, key=lambda pair: pair[0])
    lines = "".join(f"{sha256}  {path}\n" for path, sha256 in sorted_files)
    return content_hash(lines.encode("utf-8"))


def compute_bundle_manifest(
    root_dir: Path, *, excluded_filenames: frozenset[str] = EXCLUDED_FILENAMES
) -> BundleManifest:
    """Walk every regular file under ``root_dir`` (recursively), excluding
    any file whose path relative to ``root_dir`` is a member of
    ``excluded_filenames``, and return the deterministic
    :class:`BundleManifest`.
    """
    entries: list[BundleFileEntry] = []
    for path in root_dir.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root_dir).as_posix()
        if relative_path in excluded_filenames:
            continue
        entries.append(BundleFileEntry(path=relative_path, sha256=content_hash(path.read_bytes())))

    entries.sort(key=lambda entry: entry.path)
    root_hash = compute_root_hash((entry.path, entry.sha256) for entry in entries)
    return BundleManifest(files=tuple(entries), root_hash=root_hash)
