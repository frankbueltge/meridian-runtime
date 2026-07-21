"""Trust-anchored resolution of a ``TaskBundle``'s signer key — task-packets/
E5-T04.yaml.

docs/spec/01_SYSTEM_SPEC.md MRR-FR-022 ("The target node MUST make the
authoritative accept, modify, defer, or reject decision"), MRR-FR-023
("Modified tasks MUST be returned as a new signed revision and explicitly
accepted by the origin before execution"), and MRR-FR-031 ("A cross-practice
TaskBundle MUST be signed by the origin practice") describe the negotiation
E2-T03 (``mrr.services.task_bundle.service.NodeTaskDecisionService.accept/
defer/reject/propose_modification`` and ``TaskBundleService.
accept_modification``) already implements, correctly, as a LOCAL state
machine — but every one of those decision methods verifies the bundle's
signature against a CALLER-SUPPLIED bare ``verifying_key``, with no
resolution of the bundle's own claimed signer practice / key id against a
trusted key set at all. This module closes that gap at the OBJECT layer
only — no network, no persisted practice registry (task-packets/
E5-T04.yaml forbidden_changes): it is the exact trust-anchoring step
task-packets/E5-T02.yaml (``mrr.domain.manifest_trust``) closed for
``NodeManifest``, applied here to ``TaskBundle`` instead.

--- The accept rule: all four conditions must hold, fail closed ------------

:func:`resolve_trusted_task_key` returns a trusted ``Ed25519PublicKey`` ONLY
when ALL of the following hold, checked in this order, each with its own
DISTINCT typed error — never collapsed into one generic failure (AGENTS.md's
prohibited-shortcuts list: "collapsing ``unknown``, ``not_found``,
``contradicted``, and ``failed`` into one generic error"):

(a) ``bundle.signature.signer_practice_id == trusted_signer_practice_id`` —
    else :class:`mrr.domain.exceptions.TaskSignerMismatchError`.
(b) ``bundle.signature.key_id`` resolves to a descriptor in ``ring`` — else
    :class:`mrr.domain.exceptions.UnknownKeyIdError` (reused verbatim — see
    that error's own docstring for why, unlike the signer-mismatch error, it
    is shared rather than mirrored by a distinctly-named sibling).
(c) that descriptor is ``ring.is_valid_at(evaluation_instant, kid)`` — active
    AND inside its validity window, evaluated at the EVALUATION instant
    (default ``datetime.now(UTC)``, caller-overridable via ``at``, mirroring
    ``resolve_trusted_manifest_key``'s own ``at`` parameter) — else
    :class:`mrr.domain.exceptions.TaskKeyNotValidError`. This is what makes
    a revoked, rotated, or expired key fail closed even though it still
    resolves in step (b) — trust anchoring beyond raw crypto (docs/spec/04
    section 8.4: "New objects are rejected after revocation").
(d) ``mrr.domain.hashing_policy.verify_object_signature`` (E1-T02, UNCHANGED)
    passes, over the exact ADR-0004 ``exclude_none`` canonical form, under
    the resolved key — else the same
    ``mrr.crypto.exceptions.SignatureVerificationError`` /
    ``UnsupportedAlgorithmError`` that function already raises; no new type
    is needed for "bad signature" because that function already fails
    closed with its own typed errors.

Unlike :func:`mrr.domain.manifest_trust.resolve_trusted_manifest_key`'s five
conditions, there is no analog of that resolver's condition (d) ("the
descriptor's key is one of the manifest's own declared ``public_keys``")
here — task-packets/E5-T04.yaml derived_decisions is explicit that this
manifest-specific check does NOT apply to a ``TaskBundle``: a bundle carries
no ``public_keys`` list of its own to check against.

Every precondition is checked BEFORE the next, and the function returns only
after all four hold — there is no path that returns a key for a failing
precondition (proved directly by
``tests/property/test_task_trust_properties.py``).

--- Symmetric by construction, not by special-casing -----------------------

This one function authenticates BOTH legs of MRR-FR-022/023's round trip,
distinguished only by which practice+ring the CALLER passes as trusted for
THIS message:

- a receiving NODE calls it with the ORIGIN practice's id + ring to
  authenticate a received (origin-signed) task before deciding
  accept/defer/reject/propose_modification (MRR-FR-022);
- an ORIGIN calls it with the NODE practice's id + ring to authenticate a
  node-proposed modification — the new signed revision MRR-FR-023 requires
  the origin explicitly accept before execution.

Nothing in this module knows or cares which direction a given call is; the
symmetry is entirely a property of ``bundle``/``trusted_signer_practice_id``/
``ring`` being caller-supplied for whichever counterparty this call is
authenticating.

--- Practice -> KeyRing -----------------------------------------------------

:func:`mrr.domain.manifest_trust.practice_key_ring` (task-packets/
E5-T02.yaml) is reused UNCHANGED — it is not specific to ``NodeManifest`` in
any way; it only ever looks at ``practice.keys``. Re-exported here for
callers that only need the task-trust surface (so
``from mrr.domain.task_trust import practice_key_ring,
resolve_trusted_task_key`` works without a second import from
``mrr.domain.manifest_trust``), matching the ADR-0009 amendment's own
precedent for what "reuse unchanged" looks like in this codebase.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network — a pure function over already-in-memory
values, CI-testable with no database (task-packets/E5-T04.yaml: "framework-
free"). It does not decide whether a practice itself is trusted (that
remains caller-supplied, exactly as
``mrr.domain.manifest_trust.resolve_trusted_manifest_key``'s own
``trusted_practice_id`` is); it does not build, load, or persist a practice
registry; it enforces no replay, expiry, or cross-runtime revocation SWEEP
over already-accepted objects (E5-T07's scope) — only whether the key
resolves and is valid AT the evaluation instant this call is given; and it
does not alter, wrap, or reimplement any of the E2-T03 decision transitions
themselves (accept/defer/reject/propose_modification/accept_modification) —
that thin wiring lives in
``mrr.services.task_bundle.service``, additive and separate from this
module, per task-packets/E5-T04.yaml forbidden_changes.
"""

from __future__ import annotations

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.task_bundle import TaskBundle
from mrr.domain.exceptions import TaskKeyNotValidError, TaskSignerMismatchError
from mrr.domain.key_management import KeyRing
from mrr.domain.manifest_trust import practice_key_ring
from mrr.domain.trust_resolution import resolve_trusted_signer_key, verify_trusted_signature

__all__ = [
    "practice_key_ring",
    "resolve_trusted_task_key",
]


def resolve_trusted_task_key(
    bundle: TaskBundle,
    trusted_signer_practice_id: str,
    ring: KeyRing,
    *,
    at: datetime | None = None,
) -> Ed25519PublicKey:
    """Resolve ``bundle``'s signature to a trusted Ed25519 verifying key,
    anchored to ``trusted_signer_practice_id``'s own ``ring``. See the
    module docstring for the full four-condition accept rule, its typed
    failure family, and why this one function authenticates both directions
    of the MRR-FR-022/023 negotiation round trip.

    Args:
        bundle: the received, not-yet-trusted ``TaskBundle`` — an
            origin-signed task (node's perspective) or a node-signed
            modification (origin's perspective, MRR-FR-023).
        trusted_signer_practice_id: the id of the practice the caller
            actually trusts as THIS bundle's signer (mirrors E5-T02's
            caller-supplied ``trusted_practice_id`` — the trust decision
            about WHICH practice to trust is the caller's, not this
            function's). The origin practice id when authenticating a
            received task; the node practice id when authenticating a
            node-proposed modification.
        ring: ``trusted_signer_practice_id``'s own trusted ``KeyRing``
            (build one from a ``mrr.contracts.practice.Practice`` with
            :func:`practice_key_ring`).
        at: the evaluation instant for validity-window/lifecycle-state
            checks. Defaults to ``datetime.now(UTC)`` — the evaluation
            instant, per docs/spec/04 section 8.4 — and is
            caller-overridable for deterministic testing, mirroring
            ``resolve_trusted_manifest_key``'s own ``at`` parameter.

    Returns:
        The trusted ``Ed25519PublicKey`` — returned ONLY once every one of
        the four accept-rule conditions has held; never returned for any
        failing precondition.

    Raises:
        mrr.domain.exceptions.TaskSignerMismatchError: condition (a) fails
            — the bundle's claimed signer is not
            ``trusted_signer_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition (b) fails — the
            bundle's claimed key id does not resolve in ``ring`` at all.
        mrr.domain.exceptions.TaskKeyNotValidError: condition (c) fails —
            the resolved descriptor is not valid at the evaluation instant
            (revoked, rotated, expired, or not yet valid).
        mrr.crypto.exceptions.SignatureVerificationError: condition (d)
            fails — the Ed25519 signature does not verify (bad or tampered
            signature, including a substituted signing key) under the
            resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition (d) fails
            — ``bundle.signature.algorithm`` is not ``"Ed25519"``.
    """
    verifying_key = resolve_trusted_signer_key(
        bundle,
        trusted_signer_practice_id,
        ring,
        at=at,
        signer_mismatch_error=TaskSignerMismatchError,
        key_not_valid_error=TaskKeyNotValidError,
    )
    verify_trusted_signature(bundle, verifying_key)
    return verifying_key
