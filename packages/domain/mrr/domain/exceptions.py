"""Exception hierarchy for mrr.domain identity and hashing-policy primitives."""

from __future__ import annotations

from datetime import datetime


class DomainError(Exception):
    """Base class for all mrr.domain errors."""


class InvalidEntityError(DomainError):
    """Raised when a URN entity segment does not match ``[a-z0-9-]+``."""


class InvalidUrnError(DomainError):
    """Raised when a value does not match the exact ``$defs.urn`` pattern in
    schemas/common.schema.json (``^urn:mrr:[a-z0-9-]+:[0-9A-HJKMNP-TV-Z]{26}$``).
    """


class InvalidTransitionError(DomainError):
    """Raised by ``mrr.domain.lifecycles.StateMachine.assert_transition`` when
    ``(from_state, to_state)`` is not a declared legal edge for that machine.

    Carries the three fields task-packets/E1-T04.yaml requires on the typed
    error ("the typed error exposes machine, from-state, and to-state"):
    ``machine`` (a state machine's ``name``, e.g. ``"Claim"``), ``from_state``,
    and ``to_state``. Raising always happens before any state is written
    anywhere, so a caller that lets this propagate leaves no partial state
    behind (task-packets/E1-T04.yaml invariant "invalid transitions ...
    create no partial state").
    """

    def __init__(self, machine: str, from_state: str, to_state: str) -> None:
        self.machine = machine
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"{machine}: illegal transition {from_state!r} -> {to_state!r}")


class RevisionConflictError(DomainError):
    """Raised by ``mrr.domain.repositories.ObjectRepository.insert_revision``
    when a caller's ``expected_current_revision`` does not match the
    object's actual current revision — whether caught by the pre-insert
    check or by a same-instant concurrent writer colliding on the
    ``(id, revision)`` primary key (see
    ``packages/persistence/mrr/persistence/repositories.py``). Exactly one
    concurrent writer with the same expectation wins; every other loses with
    this error, never a boolean.

    Carries ``id`` (the object identifier), ``expected`` (the caller's
    ``expected_current_revision``, or ``None`` for "must be new"), and
    ``actual`` (the object's real current revision at the time of the
    conflict, or ``None`` if the object still does not exist).
    """

    def __init__(self, id: str, expected: int | None, actual: int | None) -> None:
        self.id = id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"revision conflict for {id!r}: expected current revision {expected!r}, "
            f"actual {actual!r}"
        )


class UnknownEdgeTypeError(DomainError):
    """Raised when an edge type is not one of
    ``mrr.domain.repositories.EDGE_VOCABULARY`` (docs/spec/02_DOMAIN_MODEL.md
    section 3). Fail-closed: checked in code before any database write, in
    addition to the database's own CHECK constraint on the same vocabulary.
    """

    def __init__(self, edge_type: str) -> None:
        self.edge_type = edge_type
        super().__init__(f"unknown edge type: {edge_type!r}")


class ObjectNotFoundError(DomainError):
    """Raised by ``mrr.domain.repositories.ObjectRepository.get_latest`` and
    ``get_revision`` when no matching stored object exists. Carries ``id``
    and, for ``get_revision``, the specific ``revision`` that was requested
    (``None`` when raised from ``get_latest``, which has no specific
    revision to report).
    """

    def __init__(self, id: str, revision: int | None = None) -> None:
        self.id = id
        self.revision = revision
        if revision is None:
            super().__init__(f"no object found for id {id!r}")
        else:
            super().__init__(f"no object found for id {id!r} at revision {revision!r}")


class InvalidContentHashError(DomainError):
    """Raised when a ``content_hash`` argument does not match the exact
    ``$defs.sha256`` pattern (``mrr.crypto.hashing.SHA256_PATTERN``,
    ``sha256:<64 lowercase hex>``).

    Checked before any store lookup happens (``mrr.domain.artifacts.
    require_valid_content_hash``), so a malformed key fails closed instead of
    being silently treated as "not found" — collapsing "malformed input" and
    "absent key" into one generic error is exactly what AGENTS.md's
    prohibited-shortcuts list warns against ("collapsing `unknown`,
    `not_found`, `contradicted`, and `failed` into one generic error").
    """

    def __init__(self, content_hash: str) -> None:
        self.content_hash = content_hash
        super().__init__(f"not a valid sha256: content hash: {content_hash!r}")


class ArtifactNotFoundError(DomainError):
    """Raised by ``mrr.domain.artifacts.ArtifactStore.get`` and ``stat`` when
    no stored artifact exists for the requested (well-formed) content hash.
    Carries ``content_hash``. Never returns ``None`` or a boolean for a
    missing artifact.
    """

    def __init__(self, content_hash: str) -> None:
        self.content_hash = content_hash
        super().__init__(f"no artifact found for content hash {content_hash!r}")


class ScoreNotFoundError(DomainError):
    """Raised by ``mrr.services.research_score.service.ResearchScoreService``
    when a referenced ``ResearchScore`` id resolves to no stored object at
    all (docs/spec/01_SYSTEM_SPEC.md MRR-FR-004: "The system MUST reject
    execution when the referenced score is missing ..."). Carries
    ``score_id``. Never returns ``None`` or a boolean for a missing score.
    """

    def __init__(self, score_id: str) -> None:
        self.score_id = score_id
        super().__init__(f"no ResearchScore found for id {score_id!r}")


class ScoreNotApprovedError(DomainError):
    """Raised by ``ResearchScoreService.ensure_can_start_work`` when the
    latest revision of a ``ResearchScore`` exists but its status is not one
    of ``APPROVED``/``ACTIVE`` (MRR-FR-004: "Only APPROVED and ACTIVE
    revisions may start work", docs/spec/01_SYSTEM_SPEC.md section 6.1).
    Carries ``score_id`` and the ``actual_status`` observed, so a caller can
    react to *why* the score is not eligible without parsing a message
    string.
    """

    def __init__(self, score_id: str, actual_status: str) -> None:
        self.score_id = score_id
        self.actual_status = actual_status
        super().__init__(
            f"ResearchScore {score_id!r} cannot start work: status is "
            f"{actual_status!r}, must be APPROVED or ACTIVE"
        )


class ApprovalRequiredError(DomainError):
    """Raised by ``ResearchScoreService.approve`` when the score being
    approved carries no recorded approval reference (task-packets/
    E2-T01.yaml invariant: "APPROVED requires at least one recorded approval
    reference"). Carries ``score_id``.
    """

    def __init__(self, score_id: str) -> None:
        self.score_id = score_id
        super().__init__(
            f"ResearchScore {score_id!r} cannot be approved: no approval reference recorded"
        )


class ArtifactIntegrityError(DomainError):
    """Raised by ``mrr.domain.artifacts.ArtifactStore.get``/``stat``/``put``
    when a stored artifact's bytes no longer hash to its key — corruption,
    bit rot, or tampering (docs/spec/01_SYSTEM_SPEC.md MRR-FR-056 acceptance,
    "Recomputing a sealed artifact hash yields the stored value"). Carries
    ``expected`` (the requested/claimed key) and ``actual`` (the hash
    actually recomputed from the on-disk bytes). Reads fail closed: this is
    raised instead of returning the mismatched bytes.
    """

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"artifact integrity check failed: expected content hash {expected!r}, "
            f"computed {actual!r} from stored bytes"
        )


class NodeManifestNotFoundError(DomainError):
    """Raised by ``mrr.services.capability_registry.service.CapabilityRegistry``
    (task-packets/E2-T02.yaml) when a referenced node id resolves to no
    stored ``NodeManifest`` revision at all. Carries ``node_id``. Never
    returns ``None`` or a boolean for a missing manifest.
    """

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"no NodeManifest found for node_id {node_id!r}")


class NodeManifestValidityError(DomainError):
    """Raised by ``CapabilityRegistry.get_current_manifest`` (and by
    ``list_capabilities``, which delegates to it) when the latest stored
    ``NodeManifest`` for a node exists but its validity window
    (``valid_from``..``valid_until``) does not include the evaluation
    instant. ``find_nodes_with_capability`` checks the same window but does
    not raise this error for an out-of-window node — it simply excludes
    that node id from its result list, since a match query has no single
    node to report an error about (task-packets/E2-T02.yaml invariant: "a
    manifest whose validity window does not include the evaluation instant
    is expired/not-yet-valid and is never returned by lookup or match,
    though it remains stored and historically addressable").

    A single typed error covers both the "not yet valid" (``at`` before
    ``valid_from``) and "expired" (``at`` after ``valid_until``) cases —
    the message distinguishes which one occurred, and ``node_id``,
    ``valid_from``, ``valid_until``, and ``at`` are all carried as fields so
    a caller can tell the two apart without parsing the message string.
    """

    def __init__(
        self, node_id: str, valid_from: datetime, valid_until: datetime, at: datetime
    ) -> None:
        self.node_id = node_id
        self.valid_from = valid_from
        self.valid_until = valid_until
        self.at = at
        reason = (
            f"not yet valid (valid_from {valid_from.isoformat()!r})"
            if at < valid_from
            else f"expired (valid_until {valid_until.isoformat()!r})"
        )
        super().__init__(
            f"NodeManifest for node_id {node_id!r} is {reason} at evaluation instant "
            f"{at.isoformat()!r}"
        )


class TaskBundleNotFoundError(DomainError):
    """Raised by ``mrr.services.task_bundle.service.TaskBundleService`` and
    ``NodeTaskDecisionService`` (task-packets/E2-T03.yaml) when a referenced
    ``TaskBundle`` id resolves to no stored object at all. Carries
    ``bundle_id``. Never returns ``None`` or a boolean for a missing bundle.
    """

    def __init__(self, bundle_id: str) -> None:
        self.bundle_id = bundle_id
        super().__init__(f"no TaskBundle found for id {bundle_id!r}")


class NodeAuthorityError(DomainError):
    """Raised by ``mrr.services.task_bundle.service.NodeTaskDecisionService``
    (task-packets/E2-T03.yaml, MRR-FR-022: "The target node MUST make the
    authoritative accept, modify, defer, or reject decision") when the
    identity attempting a node decision (``accept``/``propose_modification``/
    ``defer``/``reject``) is not the bundle's own ``target_node_id``. This is
    the structural enforcement of MRR-FR-022: there is no accept-style method
    on the origin-facing ``TaskBundleService`` at all, and every method on
    ``NodeTaskDecisionService`` raises this before doing anything else if the
    caller-supplied ``deciding_node_id`` does not equal the stored bundle's
    ``target_node_id`` — nothing is persisted for a rejected attempt.

    Carries ``bundle_id``, ``target_node_id`` (the sole authorized identity),
    and ``attempted_node_id`` (who actually called), so a caller can tell the
    attempted identity from the authorized one without parsing the message
    string.
    """

    def __init__(self, bundle_id: str, target_node_id: str, attempted_node_id: str) -> None:
        self.bundle_id = bundle_id
        self.target_node_id = target_node_id
        self.attempted_node_id = attempted_node_id
        super().__init__(
            f"TaskBundle {bundle_id!r}: only target_node_id {target_node_id!r} may decide "
            f"on this bundle, not {attempted_node_id!r}"
        )


class CapabilityNotDeclaredError(DomainError):
    """Raised by ``mrr.services.task_bundle.service.TaskBundleService.create``
    (task-packets/E2-T03.yaml) when the target node's CURRENT ``NodeManifest``
    (``mrr.services.capability_registry.service.CapabilityRegistry``) does
    not declare the exact ``{name, version}`` capability the ``TaskBundle``
    requests. This is a declaration check only (docs/spec/01_SYSTEM_SPEC.md
    section 7.3: "It does not grant permission") — it does not evaluate
    policy, approval mode, or any other gating concern. Carries ``node_id``,
    ``capability_name``, and ``capability_version``.
    """

    def __init__(self, node_id: str, capability_name: str, capability_version: str) -> None:
        self.node_id = node_id
        self.capability_name = capability_name
        self.capability_version = capability_version
        super().__init__(
            f"node {node_id!r} current manifest does not declare capability "
            f"{capability_name!r} version {capability_version!r}"
        )


class ClaimNotFoundError(DomainError):
    """Raised by ``mrr.services.claim.service.ClaimService`` (task-packets/
    E3-T02.yaml) when a referenced ``Claim`` id resolves to no stored object
    at all. Carries ``claim_id``. Never returns ``None`` or a boolean for a
    missing claim — matches ``ScoreNotFoundError``/``TaskBundleNotFoundError``'s
    own precedent for a first-class object lookup.
    """

    def __init__(self, claim_id: str) -> None:
        self.claim_id = claim_id
        super().__init__(f"no Claim found for id {claim_id!r}")


class MissingSupportEdgeError(DomainError):
    """Raised by ``ClaimService.to_supported`` (task-packets/E3-T02.yaml
    derived_decisions: "the service additionally enforces the matching
    typed support edges exist") when one or more ``evidence_relations`` URNs
    have no matching typed ``supports`` edge from the claim to that target.

    This is on top of, not instead of, the Claim contract's own
    ``model_validator`` (``evidence_relations``/``verification_ids`` must be
    non-empty for status ``supported``, E1-T03) — that check catches an
    EMPTY evidence_relations list; this one catches a NON-EMPTY list whose
    entries are not actually backed by a graph edge docs/spec/01_SYSTEM_SPEC.md
    MRR-FR-062 requires ("at least one valid support relation"). Carries
    ``claim_id`` and ``missing_targets`` (the evidence_relations URNs with no
    matching edge), so a caller can tell exactly which references are
    unbacked without parsing the message string.
    """

    def __init__(self, claim_id: str, missing_targets: list[str]) -> None:
        self.claim_id = claim_id
        self.missing_targets = missing_targets
        super().__init__(
            f"Claim {claim_id!r} cannot become supported: no typed 'supports' edge "
            f"from this claim to {missing_targets!r} — add_evidence_edge(..., "
            "edge_type='supports') first"
        )


class UntrustedIsolationNotAvailableError(DomainError):
    """Raised by ``mrr.services.node_runtime.executor.ReferenceTaskExecutor.__init__``
    (task-packets/E2-T04.yaml, MRR-FR-041) when constructed with
    ``require_isolation=True`` — an explicit, generic caller request for
    untrusted-code isolation guarantees (non-root, read-only base
    filesystem, explicit writable mounts, deny-by-default network egress,
    cgroup CPU/memory/disk limits).

    This is a programmer error, not a run outcome: the reference executor is
    for the TRUSTED deterministic reference task only (MRR-FR-044) and never
    provides such isolation — isolation is the deferred OCI-executor
    adapter's responsibility. Raised at construction time, before any task
    ever runs, so a caller cannot accidentally end up trusting this executor
    for untrusted code and only discover the gap after the fact. See
    ``ReferenceTaskExecutor``'s own docstring for the full honesty-boundary
    rationale.
    """

    def __init__(self) -> None:
        super().__init__(
            "ReferenceTaskExecutor does not provide untrusted-code isolation "
            "(MRR-FR-041) — that is the deferred OCI-executor adapter's "
            "responsibility. Refusing to construct an instance that silently "
            "pretends otherwise."
        )


class CorrectionNotFoundError(DomainError):
    """Raised by ``mrr.services.correction.service.CorrectionImpactService``
    (task-packets/E3-T06.yaml) when a referenced ``CorrectionEvent`` id
    resolves to no stored object at all. Carries ``correction_id``. Never
    returns ``None`` or a boolean for a missing correction — matches
    ``ClaimNotFoundError``/``ScoreNotFoundError``'s own precedent for a
    first-class object lookup.
    """

    def __init__(self, correction_id: str) -> None:
        self.correction_id = correction_id
        super().__init__(f"no CorrectionEvent found for id {correction_id!r}")


class UnknownKeyIdError(DomainError):
    """Raised by ``mrr.domain.key_management.rotate``/``revoke`` when the
    given kid does not resolve to any descriptor in the ``KeyRing`` — there
    is nothing to supersede or revoke. Carries ``kid``. Never silently
    no-ops on an unknown kid (fail closed, matching this codebase's other
    "unknown identifier" lookups, e.g. ``NodeManifestNotFoundError``).
    """

    def __init__(self, kid: str) -> None:
        self.kid = kid
        super().__init__(f"no key descriptor found for kid {kid!r}")


class SelfVerificationError(DomainError):
    """Raised by ``mrr.services.verification.service.VerificationService.record``
    (task-packets/E3-T04.yaml, MRR-FR-070: "The proposer and executor MUST
    NOT issue the final verification decision for their own claim" —
    AGENTS.md rule 8: "No executor may approve or verify its own result").
    This is the packet's headline gate, checked FIRST, before anything is
    persisted: a caught instance always means nothing was written, neither
    the ``VerificationResult`` revision nor its event.

    Raised in either of two cases — the reviewer identity equals the
    claim's own ``proposer_id``, or (when a producing run's executor
    identity is known) the reviewer identity equals that run's
    ``executor_id``. ``violated`` names which check actually fired
    (``"proposer"`` or ``"executor"``) so a caller can tell the two apart
    without parsing the message string; ``proposer_id`` and ``executor_id``
    are both carried for context (whichever one did NOT trigger the
    rejection is carried as supplied — ``executor_id`` is ``None`` if no
    run executor identity was given to ``record`` at all).
    """

    def __init__(
        self,
        reviewer_id: str,
        *,
        proposer_id: str,
        executor_id: str | None,
        violated: str,
    ) -> None:
        self.reviewer_id = reviewer_id
        self.proposer_id = proposer_id
        self.executor_id = executor_id
        self.violated = violated
        trigger = (
            f"equals the claim's proposer_id {proposer_id!r}"
            if violated == "proposer"
            else f"equals the run's executor_id {executor_id!r}"
        )
        super().__init__(
            f"self-verification prohibited: reviewer_id {reviewer_id!r} {trigger} "
            "(MRR-FR-070 / AGENTS.md rule 8) — nothing was persisted"
        )
