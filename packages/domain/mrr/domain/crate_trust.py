"""Trust-anchored resolution of a received ``EvidenceCrate``'s signer key —
task-packets/E5-T05.yaml.

docs/spec/01_SYSTEM_SPEC.md section 2.2 ("Signed result outbox") and section
4.6 (MRR-FR-050: "every completed or materially failed run MUST produce an
EvidenceCrate or a signed failure crate") describe the result-flow half of
docs/spec/04_SECURITY_AND_POLICY.md section 8.1 ("Trust is per practice and
capability, not universal") and section 8.4 ("A practice can revoke a node
or key. New objects are rejected after revocation."). E2-T06
(``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer``) already
SEALS and SIGNS an ``EvidenceCrate`` on the executing node, and its own
module docstring states explicitly: "This module SIGNS an EvidenceCrate but
does not verify one -- ... verify_object_signature for a received
EvidenceCrate is E5-T05's scope". This module closes that gap at the OBJECT
layer only -- no network, no persisted practice registry (task-packets/
E5-T05.yaml forbidden_changes): it is the exact trust-anchoring step
task-packets/E5-T04.yaml (``mrr.domain.task_trust``) closed for
``TaskBundle``, applied here to ``EvidenceCrate`` instead -- the
reverse-direction sibling of that resolver, since a result flows FROM the
executing node back TO the origin.

--- The accept rule: all four conditions must hold, fail closed ------------

:func:`resolve_trusted_crate_key` returns a trusted ``Ed25519PublicKey``
ONLY when ALL of the following hold, checked in this order, each with its
own DISTINCT typed error -- never collapsed into one generic failure
(AGENTS.md's prohibited-shortcuts list: "collapsing ``unknown``,
``not_found``, ``contradicted``, and ``failed`` into one generic error"):

(a) ``crate.signature.signer_practice_id == trusted_node_practice_id`` --
    the practice the caller trusts as THIS crate's producer -- else
    :class:`mrr.domain.exceptions.CrateSignerMismatchError`.
(b) ``crate.signature.key_id`` resolves to a descriptor in ``ring`` -- else
    :class:`mrr.domain.exceptions.UnknownKeyIdError` (reused verbatim -- see
    that error's own docstring for why, unlike the signer-mismatch error, it
    is shared rather than mirrored by a distinctly-named sibling).
(c) that descriptor is ``ring.is_valid_at(evaluation_instant, kid)`` --
    active AND inside its validity window, evaluated at the EVALUATION
    instant (default ``datetime.now(UTC)``, caller-overridable via ``at``,
    mirroring ``resolve_trusted_task_key``'s own ``at`` parameter) -- else
    :class:`mrr.domain.exceptions.CrateKeyNotValidError`. This is what makes
    a key that was genuinely active when the crate was sealed, but has since
    been revoked or rotated, fail closed even though it still resolves in
    step (b) -- trust anchoring beyond raw crypto (docs/spec/04 section 8.4:
    "New objects are rejected after revocation").
(d) ``mrr.domain.hashing_policy.verify_object_signature`` (E1-T02,
    UNCHANGED) passes, over the exact ADR-0004 ``exclude_none`` canonical
    form, under the RESOLVED descriptor's key -- else the same
    ``mrr.crypto.exceptions.SignatureVerificationError`` /
    ``UnsupportedAlgorithmError`` that function already raises; no new type
    is needed for "bad signature" because that function already fails
    closed with its own typed errors. Decoding the RESOLVED descriptor's own
    key (never any key the crate itself claims) is what makes a
    key-substitution attack fail: an attacker who signs with their own key
    while claiming a trusted kid still fails verification against the
    RING's real key for that kid.

Exactly like ``resolve_trusted_task_key``, there is no analog of
``resolve_trusted_manifest_key``'s condition (d) ("the descriptor's key is
one of the manifest's own declared ``public_keys``") here -- task-packets/
E5-T05.yaml derived_decisions is explicit that this manifest-specific check
does NOT apply to an ``EvidenceCrate``: a crate carries no ``public_keys``
list of its own to check against.

Every precondition is checked BEFORE the next, and the function returns only
after all four hold -- there is no path that returns a key for a failing
precondition (proved directly by
``tests/property/test_crate_trust_properties.py``).

--- Direction: the RESULT flow, origin verifying the executing node --------

Unlike ``resolve_trusted_task_key`` (symmetric -- the identical function
authenticates either leg of the MRR-FR-022/023 task negotiation round
trip), this resolver has exactly one direction: the ORIGIN (or whichever
party receives a result) calls it with the EXECUTING NODE's own practice id
and ring, to authenticate a result crate that node sealed and signed
(docs/spec/01_SYSTEM_SPEC.md section 7.5: "... executes approved work,
seals outputs, and signs result crates"). ``trusted_node_practice_id`` is
caller-supplied, exactly as every prior resolver's trusted practice is --
the trust decision about WHICH node practice to trust as this result's
producer is the caller's, not this function's.

--- Practice -> KeyRing -----------------------------------------------------

:func:`mrr.domain.manifest_trust.practice_key_ring` (task-packets/
E5-T02.yaml) is reused UNCHANGED -- it is not specific to ``NodeManifest``
in any way; it only ever looks at ``practice.keys``. Not re-exported here
(unlike ``mrr.domain.task_trust``'s own re-export of it) -- a caller needing
both a task and a crate ring already imports
``mrr.domain.manifest_trust.practice_key_ring`` directly, or via
``mrr.domain.task_trust``; adding a second re-export site would only
multiply where the same one function is importable from, with no new
behavior gained.

--- What this module deliberately does NOT do -------------------------------

No persistence, no I/O, no network -- a pure function over already-in-memory
values, CI-testable with no database (task-packets/E5-T05.yaml:
"framework-free"). It does not decide whether a practice itself is trusted
(that remains caller-supplied, exactly as every prior resolver's own
``trusted_*_practice_id`` is); it does not build, load, or persist a
practice registry; it enforces no replay, expiry, or cross-runtime
revocation SWEEP over already-accepted objects (E5-T07's scope) -- only
whether the key resolves and is valid AT the evaluation instant this call
is given; it does not build an offline bundle (E5-T06); and it does not
decide what to DO with an accepted crate -- no result-intake service or
run-linkage is built here (assembled at E2E-002). It also does not alter,
wrap, or reimplement anything the E2-T06 ``EvidenceCrateSealer`` does -- the
sealer signs, this module verifies; the two are additive, separate
modules that never import one another.
"""

from __future__ import annotations

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts.evidence_crate import EvidenceCrate
from mrr.domain.exceptions import CrateKeyNotValidError, CrateSignerMismatchError
from mrr.domain.key_management import KeyRing
from mrr.domain.trust_resolution import resolve_trusted_signer_key, verify_trusted_signature

__all__ = [
    "resolve_trusted_crate_key",
]


def resolve_trusted_crate_key(
    crate: EvidenceCrate,
    trusted_node_practice_id: str,
    ring: KeyRing,
    *,
    at: datetime | None = None,
) -> Ed25519PublicKey:
    """Resolve ``crate``'s signature to a trusted Ed25519 verifying key,
    anchored to ``trusted_node_practice_id``'s own ``ring``. See the module
    docstring for the full four-condition accept rule and its typed failure
    family.

    Args:
        crate: the received, not-yet-trusted ``EvidenceCrate`` -- a result
            the executing node sealed and signed (E2-T06).
        trusted_node_practice_id: the id of the practice the caller actually
            trusts as THIS crate's producer -- the executing node's own
            practice (mirrors E5-T04's caller-supplied
            ``trusted_signer_practice_id`` -- the trust decision about WHICH
            practice to trust is the caller's, not this function's).
        ring: ``trusted_node_practice_id``'s own trusted ``KeyRing`` (build
            one from a ``mrr.contracts.practice.Practice`` with
            ``mrr.domain.manifest_trust.practice_key_ring``).
        at: the evaluation instant for validity-window/lifecycle-state
            checks. Defaults to ``datetime.now(UTC)`` -- the evaluation
            instant, per docs/spec/04 section 8.4 -- and is
            caller-overridable for deterministic testing, mirroring
            ``resolve_trusted_task_key``'s own ``at`` parameter.

    Returns:
        The trusted ``Ed25519PublicKey`` -- returned ONLY once every one of
        the four accept-rule conditions has held; never returned for any
        failing precondition.

    Raises:
        mrr.domain.exceptions.CrateSignerMismatchError: condition (a) fails
            -- the crate's claimed signer is not
            ``trusted_node_practice_id``.
        mrr.domain.exceptions.UnknownKeyIdError: condition (b) fails -- the
            crate's claimed key id does not resolve in ``ring`` at all.
        mrr.domain.exceptions.CrateKeyNotValidError: condition (c) fails --
            the resolved descriptor is not valid at the evaluation instant
            (revoked, rotated, expired, or not yet valid).
        mrr.crypto.exceptions.SignatureVerificationError: condition (d)
            fails -- the Ed25519 signature does not verify (bad or tampered
            signature, including a substituted signing key) under the
            resolved key.
        mrr.crypto.exceptions.UnsupportedAlgorithmError: condition (d) fails
            -- ``crate.signature.algorithm`` is not ``"Ed25519"``.
    """
    verifying_key = resolve_trusted_signer_key(
        crate,
        trusted_node_practice_id,
        ring,
        at=at,
        signer_mismatch_error=CrateSignerMismatchError,
        key_not_valid_error=CrateKeyNotValidError,
    )
    verify_trusted_signature(crate, verifying_key)
    return verifying_key
