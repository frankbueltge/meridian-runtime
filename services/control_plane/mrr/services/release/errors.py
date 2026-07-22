"""Typed refusals for ``mrr.services.release`` (task-packets/E8-T04.yaml,
docs/spec/adr/ADR-0011-RELEASE-RECORD-AND-A4-APPROVAL-EVENT.md). Mirrors
``mrr.services.export.service``'s own precedent of declaring service-local
``mrr.domain.exceptions.DomainError`` subclasses (``MissingArtifactBytesError``)
rather than reaching for a generic ``ValueError`` — task-packets/E8-T04.yaml
R2's own "the service refuses (typed errors) ..." names exactly the four
conditions below (``NonPersonApproverError``, ``EmptyApprovalStatementError``,
``ReleaseCrateKindError``, ``BundleRootHashMismatchError``);
``DualApprovalNotSupportedError`` is derived_decisions (b)'s own separate
refusal (schema-valid ``"dual"``, service-refused); ``ReleaseRecordKindError``
and ``ReleaseBundleFinalizationError`` are this module's own additions for
``mrr release verify`` and the one named inconsistent state
reviewer_resolution (5) requires the CLI to name verbatim — see
``mrr.services.release.bundle``'s own module docstring for the full
atomicity analysis.
"""

from __future__ import annotations

from mrr.domain.exceptions import DomainError


class NonPersonApproverError(DomainError):
    """ADR-0011 decision 1 / MRR-FR-110 ("no inferring A4 permission from a
    lower-autonomy identity"): ``approval.approved_by`` is not a
    person-segment URN. Checked by ``ReleaseService.create`` via
    ``mrr.domain.identity.URN_PATTERN`` directly against the RAW string a
    caller supplies — see that service module's own docstring for why this
    check is intentionally redundant with the identical pattern
    ``mrr.contracts.release_record.PersonUrn`` already enforces at
    construction time.
    """

    def __init__(self, approved_by: str) -> None:
        self.approved_by = approved_by
        super().__init__(
            f"approval.approved_by {approved_by!r} is not a person-segment URN "
            "(MRR-FR-102 / MRR-FR-110: the A4 approval event's actor must be the "
            "approving human, never an agent-role, node, or practice identity) — "
            "nothing was persisted"
        )


class EmptyApprovalStatementError(DomainError):
    """MRR-FR-102: the human's own words are the recorded act; a blank (or
    whitespace-only) statement is refused even though the contract's own
    ``minLength: 1`` would already reject a literally EMPTY string — this
    service-level check additionally catches whitespace-only content a bare
    length check would miss (the CLI reads this value straight from a file's
    raw bytes, ``--approval-statement-file``).
    """

    def __init__(self) -> None:
        super().__init__(
            "approval.approval_statement is blank (MRR-FR-102: no default exists "
            "for the human's own words by design) — nothing was persisted"
        )


class DualApprovalNotSupportedError(DomainError):
    """task-packets/E8-T04.yaml derived_decisions (b): ``approval_mode ==
    "dual"`` is schema-valid (docs/spec/01_SYSTEM_SPEC.md section 5's
    autonomy model offers "explicit human or dual approval") but this
    practice implements only ``"single_human"`` — no second-approver
    workflow exists yet, and recording ``"dual"`` without one would silently
    misrepresent how many humans actually approved this release.
    """

    def __init__(self, approval_mode: str) -> None:
        self.approval_mode = approval_mode
        super().__init__(
            f"approval_mode {approval_mode!r} is schema-valid but this practice "
            "only implements 'single_human' (task-packets/E8-T04.yaml "
            "derived_decisions (b): no second-approver workflow exists yet) — "
            "nothing was persisted"
        )


class ReleaseCrateKindError(DomainError):
    """``--crate-id`` resolves to a stored object, but not one of kind
    ``EvidenceCrate``. Mirrors ``mrr.services.export.service.ExportService
    .resolve_closure``'s own identical check (there a plain ``ValueError``,
    here a dedicated typed error per task-packets/E8-T04.yaml R2's own
    "typed errors" wording for this exact case). Every persisted
    ``EvidenceCrate`` is already SEALED by construction
    (``schemas/evidence-crate.schema.json``'s own ``sealed: const true``),
    so resolving to the right KIND is the only additional fact this error
    needs to report — "does not resolve to a sealed EvidenceCrate" (R2's own
    wording) collapses, for any object this store can actually hold, into
    "does not resolve to an EvidenceCrate at all".
    """

    def __init__(self, crate_id: str, actual_kind: str) -> None:
        self.crate_id = crate_id
        self.actual_kind = actual_kind
        super().__init__(
            f"--crate-id {crate_id!r} resolves to a stored object of kind "
            f"{actual_kind!r}, not 'EvidenceCrate' — nothing was persisted"
        )


class BundleRootHashMismatchError(DomainError):
    """The given bundle's ``root_hash`` does not match the value
    ``mrr.services.release.manifest.compute_root_hash`` recomputes from its
    own ``files`` list — "root_hash is recomputed, never trusted from
    input, at every boundary" (task-packets/E8-T04.yaml invariant). In the
    ordinary ``mrr release create`` flow this should never actually fire
    (the manifest was honestly computed moments earlier by the SAME
    function this check reuses) — it exists as defense-in-depth against a
    caller who calls ``ReleaseService.create`` directly with a
    hand-assembled or stale ``BundleManifest``.
    """

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"bundle.root_hash {expected!r} does not match the root_hash "
            f"recomputed from bundle.files ({actual!r}) — the record never stores "
            "an unchecked hash; nothing was persisted"
        )


class ReleaseRecordKindError(DomainError):
    """``--release-id`` (``mrr release verify``) resolves to a stored
    object, but not one of kind ``ReleaseRecord``. Mirrors
    ``ReleaseCrateKindError``'s identical shape for the sibling lookup.
    """

    def __init__(self, release_id: str, actual_kind: str) -> None:
        self.release_id = release_id
        self.actual_kind = actual_kind
        super().__init__(
            f"--release-id {release_id!r} resolves to a stored object of kind "
            f"{actual_kind!r}, not 'ReleaseRecord'"
        )


class ReleaseBundleFinalizationError(DomainError):
    """The ONE inconsistent state ``mrr.services.release.bundle
    .assemble_and_release`` can produce, per task-packets/E8-T04.yaml
    reviewer_resolution (5)'s fixed order (assemble-content -> persist ->
    finalize): the ``ReleaseRecord`` revision and its ``release.approved``
    event were ALREADY durably persisted (``ReleaseService.create``
    returned) when writing ``release-manifest.json``/``release-record.json``
    or the final atomic rename onto ``--output-dir`` failed. No partial
    directory is ever left on disk in this case either (the temp directory
    is still removed) — only the database record exists, with no
    corresponding bundle directory anywhere. Carries ``release_id`` and
    ``revision`` so ``mrr.services.cli.release_main`` can name the exact
    inconsistent state verbatim and point at ``mrr release verify
    --release-id <id>`` as the recovery path, per reviewer_resolution (5)'s
    own "recoverable by mrr release verify reporting the mismatch loudly".
    """

    def __init__(self, release_id: str, revision: int, *, cause: BaseException) -> None:
        self.release_id = release_id
        self.revision = revision
        self.cause = cause
        super().__init__(
            f"ReleaseRecord {release_id!r} revision {revision!r} was persisted (the "
            "release.approved event is durable) but finalizing its bundle directory "
            f"failed ({type(cause).__name__}: {cause}). This is the ONE known "
            "inconsistent state this command can produce: the record exists, the "
            "bundle directory does not — no partial directory was left on disk "
            "anywhere. Recover by running 'mrr release verify --release-id "
            f"{release_id}' against the archive."
        )
