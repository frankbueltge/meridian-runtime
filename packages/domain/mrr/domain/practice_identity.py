"""Pure construction of a self-signed ``Practice`` identity from an Ed25519
private key — task-packets/E5-T11.yaml, deliberately built as the identity-
layer twin of E5-T10's ``mrr.domain.envelope_signing.build_signed_envelope``,
one level deeper: where that packet made a wrong ``payload_content_hash``
structurally impossible (read from the payload, never a parameter), this
packet makes a wrong ``kid`` structurally impossible the same way.

--- The gap this closes -----------------------------------------------------

``mrr federation inbox accept --trusted-sender-practice`` (task-packets/
E5-T08.yaml) requires a FILE — a published ``Practice`` document. Before
this packet, nothing in the repository produced one:
``generate_ed25519_keypair`` (task-packets/E5-T01.yaml) has thirty-five test
callers and no others (docs/design/2026-07-26-e5-t11-ableitung-praxis-
identitaet.md's fact-lock, reviewer_resolution in task-packets/E5-T11.yaml),
there is no ``keys`` and no ``practice`` subcommand among the fourteen
``mrr`` registers, and ``mrr federation envelope sign --key-id`` accepts an
UNCHECKED opaque string with nothing to validate it against. A Meridian key
was, at the moment of its own creation, unusable — Ulysses had no file to
trust it with, and Meridian itself had no command naming its own ``kid``.

--- The one hard rule: no parameter can carry a wrong kid --------------------

``kid`` and ``encoded_public_key`` are DERIVED, never accepted as
parameters. :func:`build_self_signed_practice` takes only the
``Ed25519PrivateKey`` itself, obtains the public half via the cryptography
library's own ``private_key.public_key()``, and derives both values from
that public key alone, via the UNCHANGED ``mrr.crypto.keys``:

- ``encoded_public_key = mrr.crypto.keys.encode_public_key(public_key)``
- ``kid = mrr.crypto.keys.derive_key_id(public_key)``

This is the exact sibling of E5-T10's rule that
``NodeMessageEnvelope.payload_content_hash`` is READ from the payload, never
computed independently or accepted as a free caller parameter (see
``mrr.domain.envelope_signing``'s own module docstring, "The one hard rule:
refuse what the receiver would reject") — there, the sender refuses what the
receiver's own consistency check would reject; here, there is no channel at
all through which a caller could hand this function a ``kid`` that
disagrees with the key it is actually signing with, because no such
parameter exists. ``mrr.contracts.practice.PublicKeyDescriptor``'s own
``model_validator`` (``_kid_matches_key_and_window_is_ordered``) re-derives
and re-checks ``kid == derive_key_id(encoded_public_key)`` a SECOND time, at
construction — the same "enforced twice" precedent that module's own
docstring documents for exactly this reason (a Pydantic model field cannot
hold an arbitrary frozen dataclass, so the domain-layer
``mrr.domain.key_management.PublicKeyDescriptor.__post_init__`` check is
mirrored, not replaced, at the contracts layer). A ``Practice`` built by
this function therefore cannot structurally carry a ``kid`` that does not
belong to its own ``keys[0].encoded_public_key`` — not "discouraged", not
"validated after the fact", but impossible to construct in the first place,
which is the load-bearing distinction the governance commit draws between
"mitigating" the pre-existing ``--key-id`` defect and "making it
structurally impossible" (task-packets/E5-T11.yaml reviewer_resolution,
point (1)).

--- ``practice_id``: a Practice belongs to itself, by construction ----------

``mrr.contracts.common.BaseObject`` gives every first-class object both an
``id`` (this object's own identity) and a ``practice_id`` (the practice that
owns/created it). For a ``Practice`` document, those two are always the SAME
value — a practice's own identity document is owned by nobody but itself
(``examples/practice.example.json``: ``"id"`` and ``"practice_id"`` are
identical; ``Practice``'s own ``_signature_signer_is_this_practice_with_a_
listed_key`` validator requires ``signature.signer_practice_id == id``).
Rather than accept ``id`` and ``practice_id`` as two independent parameters
that a caller could — by a copy-paste slip, nothing more sinister required —
pass two DIFFERENT values for, this function takes exactly ONE
``practice_id`` parameter and uses it for both fields. This mirrors the
``kid``-derivation rule immediately above at a different seam: removing the
parameter through which an inconsistency could enter is preferred, in this
whole task packet, over accepting the parameter and then checking it.

--- The self-signature is not optional --------------------------------------

This function ALWAYS signs — there is no way to obtain an unsigned
``Practice`` from it. ``Practice.signature`` is optional at the contract
layer (a practice's self-signature is meaningful only once its content is
final), but a trust anchor that does not self-sign proves nothing about
possession of the private key it claims — and possession is the entire
point of the file ``mrr federation inbox accept --trusted-sender-practice``
loads (docs/design/2026-07-26-e5-t11-ableitung-praxis-identitaet.md, "Die
Selbstsignatur ist nicht optional").

--- Reuse, not reimplementation: the exact ADR-0004 procedure ---------------

Both ``content_hash`` and ``signature`` are produced by the UNCHANGED
``mrr.domain.hashing_policy`` (``compute_content_hash``, ``sign_object``) —
never a second hashing or signing implementation (task-packets/E5-T11.yaml
forbidden_changes: ``hashing_policy.py``). ``Practice`` is a
``BaseObject`` — unlike ``NodeMessageEnvelope``/``OfflineBundle`` (E5-T10's
own ``build_signed_envelope``/E5-T06's ``build_outbox_bundle``, which carry
no ``content_hash`` field at all and therefore only ever sign), a
``Practice`` carries BOTH its own ``content_hash`` AND an optional
``signature``, exactly like ``mrr.contracts.evidence_crate.EvidenceCrate``.
The procedure below therefore mirrors
``mrr.services.node_runtime.evidence_crate.EvidenceCrateSealer.seal``'s own
draft-then-recompute convention (the closest existing precedent for a
BaseObject entity that both hashes and signs itself) rather than either
signing-only precedent, composed with the placeholder-then-replace signature
convention every signing site in this codebase already shares
(``build_signed_envelope``, ``build_outbox_bundle``,
``CorrectionImpactService._build_and_sign_envelope``,
``EvidenceCrateSealer.seal``):

1. Build a DRAFT ``Practice`` with a placeholder ``content_hash`` and a
   placeholder ``Signature`` (``mrr.contracts.common.Signature`` with a
   dummy ``value`` long enough to satisfy that field's own
   ``min_length=40`` — the same ``_PLACEHOLDER_SIGNATURE_VALUE`` convention
   every sibling above uses, redefined locally since that name is private
   to each module).
2. ``json.loads(draft.model_dump_json(exclude_none=True))`` — the exact
   ADR-0004 canonical body every hash/sign operation in this codebase
   operates over.
3. ``compute_content_hash(body)`` — the UNCHANGED
   ``mrr.domain.hashing_policy`` function, whose own ``prepare_for_hash``
   strips BOTH ``content_hash`` and ``signature`` before hashing, so neither
   placeholder above ever influences the real ``content_hash``. The result
   replaces the placeholder in ``body``.
4. ``sign_object(private_key, body)`` — the UNCHANGED
   ``mrr.domain.hashing_policy`` function, whose own
   ``prepare_for_signature`` strips only ``signature`` (keeping the just-
   computed REAL ``content_hash`` — per docs/spec/02_DOMAIN_MODEL.md section
   1.3, "the signature covers the canonical payload and content hash"), so
   the placeholder signature value never influences what gets signed, and
   the real signature covers the real content hash.
5. Replace the placeholder signature's ``value`` with the real one and run
   ``Practice.model_validate`` on the result once more — proving the fully
   assembled, SIGNED practice still satisfies every contract-level
   invariant (``PublicKeyDescriptor``'s kid/window check,
   ``_signature_signer_is_this_practice_with_a_listed_key``) with its real
   ``content_hash``/``signature`` in place, not merely its placeholder
   draft.

--- Determinism: no clock, no randomness anywhere here -----------------------

Every identity and timestamp is caller-supplied — ``practice_id`` AND
``created_at`` included (task-packets/E5-T11.yaml acceptance criteria:
"both id minting and created_at are supplied by the caller"). There is no
``datetime.now(UTC)``, no ``uuid``/``secrets``/``ulid`` call anywhere in
this module, mirroring ``mrr.domain.envelope_signing.build_signed_envelope``
and ``mrr.domain.offline_bundle.build_outbox_bundle``'s identical
discipline. Calling this function twice with identical arguments —
including the SAME private key, ``practice_id``, and ``created_at`` — yields
a byte-identical ``Practice`` (proved directly by
``tests/unit/domain/test_practice_identity.py``). Minting ``practice_id``
(``mrr.domain.identity.new_urn("practice")``) and reading the wall clock for
``created_at`` are both the CALLER's responsibility — in practice,
``mrr.services.cli.practice_main``'s ``init`` command, exactly as
``EvidenceCrateSealer.seal`` reads ``datetime.now(UTC)`` only at the
service/CLI boundary, never inside a pure domain function.

--- Owner content: required, no defaults, nothing guessed --------------------

``name``, ``description``, ``governance_contacts``,
``supported_policy_versions``, ``max_disclosure``, ``trust_statement``,
``valid_from``/``valid_until`` (the published key's own validity window),
and ``created_by`` are OWNER CONTENT — this function has no default for any
of them and invents none (task-packets/E5-T11.yaml explicitly_not: "No
default, guessed, or example value for any owner content field";
reviewer_resolution point (5)). Omitting any one of them is a plain Python
``TypeError`` (a required, defaultless keyword-only parameter), mirroring
every other pure domain builder in this codebase
(``build_signed_envelope``/``build_outbox_bundle`` have zero defaults for
their own identity/content parameters either). ``capability_registry_
endpoint`` is the one field this module's own contract
(``mrr.contracts.practice.Practice``) itself declares optional — "public
capability registry endpoint if any" — and stays optional here too, for the
same reason. ``revision`` defaults to ``1`` (this is always a FIRST
publication, never a republication of an existing revision — the same
structural-default treatment ``build_outbox_bundle`` gives ``encryption``);
it is not owner content and this task's CLI never exposes it as a flag.

--- What this module deliberately does NOT do --------------------------------

No persistence, no I/O, no network — a pure function over already-in-memory
values, CI-testable with no database. It does not load a PEM file (that is
``mrr.services.cli.practice_main``'s job, mirroring
``mrr.services.cli.federation_main._load_signing_key``'s identical
division of labor) and does not mint ``practice_id`` itself (that is
``mrr.domain.identity.new_urn("practice")``, called by the CLI, per the
determinism section above). It does not decide whether the resulting
``Practice`` is TRUSTED by anyone (that remains E5-T02's
``mrr.domain.manifest_trust``/E5-T06's ``mrr.domain.offline_bundle``
concern, both UNCHANGED and both forbidden paths for this task) and
performs no exchange of any kind — it only builds and signs the one
document a Meridian operator can then choose to publish. It never mints an
identity, a key, or a Practice for any OTHER practice — every field here is
either derived from the caller's own private key or supplied by the caller
as their own content (task-packets/E5-T11.yaml explicitly_not: "No identity
for a foreign practice").
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts.common import Signature
from mrr.contracts.practice import DisclosureAndTrust, Practice, PublicKeyDescriptor
from mrr.contracts.research_score import MaxDisclosure
from mrr.crypto.keys import derive_key_id, encode_public_key
from mrr.domain.hashing_policy import compute_content_hash, sign_object

__all__ = ["build_self_signed_practice"]

#: Placeholder ``content_hash`` used only while assembling the draft
#: practice below, before the real value is computed. Never the value
#: actually persisted: ``mrr.domain.hashing_policy.prepare_for_hash``
#: strips the entire ``content_hash`` field before hashing, so this
#: placeholder can never leak into what gets hashed. A well-formed
#: ``sha256:<64 hex>`` string so the DRAFT itself still satisfies
#: ``mrr.contracts.common.Sha256``'s own pattern before it is replaced.
_PLACEHOLDER_CONTENT_HASH = "sha256:" + "0" * 64

#: Placeholder ``Signature.value`` used only while assembling the draft
#: practice below, before the real signature is computed. Never the value
#: actually signed over or returned: ``prepare_for_signature`` strips the
#: entire ``signature`` field before signing, so this placeholder can never
#: leak into what gets hashed or signed. Mirrors every other signing site in
#: this codebase's own identical ``_PLACEHOLDER_SIGNATURE_VALUE`` convention
#: (``min_length=40`` on ``mrr.contracts.common.Signature.value``),
#: redefined locally rather than imported since that name is private to
#: each of those modules.
_PLACEHOLDER_SIGNATURE_VALUE = "0" * 44


def build_self_signed_practice(
    private_key: Ed25519PrivateKey,
    *,
    practice_id: str,
    created_at: datetime,
    created_by: str,
    name: str,
    description: str,
    governance_contacts: list[str],
    supported_policy_versions: list[str],
    max_disclosure: MaxDisclosure,
    trust_statement: str,
    valid_from: datetime,
    valid_until: datetime,
    capability_registry_endpoint: str | None = None,
    revision: int = 1,
    algorithm: Literal["Ed25519"] = "Ed25519",
) -> Practice:
    """Build and self-sign a ``Practice`` identity document for the practice
    that owns ``private_key``. See the module docstring for the full design
    — in particular why ``kid``/``encoded_public_key`` cannot be passed in,
    why ``practice_id`` alone (not a separate ``id``) is accepted, and the
    exact hash-then-sign procedure below.

    Args:
        private_key: the practice's own Ed25519 private key. Its public
            half (``private_key.public_key()``) is the ONLY source of
            ``keys[0].encoded_public_key``/``keys[0].kid`` — see the module
            docstring's "The one hard rule" section for why no parameter
            for either exists.
        practice_id: this practice's own identifier. Used for BOTH
            ``Practice.id`` and ``Practice.practice_id`` (a practice belongs
            to itself — see the module docstring) and as
            ``signature.signer_practice_id``. Caller-supplied, never minted
            here (e.g. ``mrr.domain.identity.new_urn("practice")`` — minting
            it is the caller's decision, so the same call with the same
            ``practice_id`` reproducibly yields the same document).
        created_at: this document's own creation instant. Also used as the
            self-signature's own ``signed_at`` and the sole listed key's
            ``valid_from``... no — see ``valid_from``/``valid_until`` below;
            ``created_at`` governs only the document's own ``created_at``
            and the signature's ``signed_at``, mirroring
            ``build_outbox_bundle``'s identical "sender signs at the
            document's own creation instant" choice.
        created_by: the URN of the person or agent role that authored this
            publication (``Practice.created_by`` — see docs/design/2026-07-
            26-e5-t11-ableitung-praxis-identitaet.md's "offener Punkt" for
            why this function decides neither answer and simply carries
            whatever URN the caller supplies).
        name: this practice's own published name. Owner content — no
            default.
        description: this practice's own published description. Owner
            content — no default.
        governance_contacts: reachable governance contact references (e.g.
            ``mailto:``/``https:`` URIs). Owner content — no default;
            carried verbatim, in order.
        supported_policy_versions: the policy versions this practice
            currently supports. Owner content — no default; carried
            verbatim, in order.
        max_disclosure: the ceiling disclosure level this practice
            publishes for itself (``mrr.contracts.research_score.
            MaxDisclosure``). Owner content — no default.
        trust_statement: free-text qualitative trust posture this practice
            publishes about itself. Owner content — no default (may be an
            empty string, but the caller must say so explicitly; this
            function never supplies one).
        valid_from: the published key's own validity window start. Owner
            content — no default.
        valid_until: the published key's own validity window end (must be
            strictly after ``valid_from`` — enforced by
            ``PublicKeyDescriptor``'s own contract-level validator). Owner
            content — no default.
        capability_registry_endpoint: this practice's public capability-
            registry endpoint, if any. The one field ``Practice`` itself
            declares optional; defaults to ``None`` here for the same
            reason.
        revision: this document's own revision number. Defaults to ``1`` —
            every call to this function builds a first publication, never a
            republication of an existing revision (see the module
            docstring). Not owner content.
        algorithm: the signature/key algorithm. Defaults to ``"Ed25519"``,
            the only value ``mrr.contracts.common.Signature.algorithm`` and
            ``mrr.contracts.practice.PublicKeyDescriptor.algorithm`` accept.

    Returns:
        The fully assembled, self-signed ``Practice`` — content-hashed and
        signed by ``private_key``, carrying exactly one listed key: the
        public half of ``private_key`` itself.

    Raises:
        pydantic.ValidationError: propagated, unwrapped, from ``Practice``'s
            own contract-level validation — e.g. ``name``/``description`` is
            empty, ``valid_from`` is not strictly before ``valid_until``, an
            identity is not a well-formed URN, or ``created_by`` is
            malformed. This function does not soften, catch, or reinterpret
            any of those checks.
    """
    public_key = private_key.public_key()
    encoded_public_key = encode_public_key(public_key)
    kid = derive_key_id(public_key)

    key_descriptor = PublicKeyDescriptor(
        kid=kid,
        algorithm=algorithm,
        encoded_public_key=encoded_public_key,
        valid_from=valid_from,
        valid_until=valid_until,
        state="active",
    )
    disclosure = DisclosureAndTrust(max_disclosure=max_disclosure, trust_statement=trust_statement)

    placeholder_signature = Signature(
        signer_practice_id=practice_id,
        key_id=kid,
        algorithm=algorithm,
        signed_at=created_at,
        value=_PLACEHOLDER_SIGNATURE_VALUE,
    )
    draft = Practice(
        id=practice_id,
        api_version="mrr/v1alpha1",
        kind="Practice",
        practice_id=practice_id,
        revision=revision,
        created_at=created_at,
        created_by=created_by,
        content_hash=_PLACEHOLDER_CONTENT_HASH,
        name=name,
        description=description,
        keys=[key_descriptor],
        governance_contacts=list(governance_contacts),
        supported_policy_versions=list(supported_policy_versions),
        capability_registry_endpoint=capability_registry_endpoint,
        disclosure=disclosure,
        signature=placeholder_signature,
    )

    # ADR-0004: content_hash and signature are computed over the SAME
    # exclude_none=True body that gets persisted/transmitted — never a
    # second, null-including representation. See the module docstring's
    # "Reuse, not reimplementation" section for why this is a five-step
    # draft -> real content_hash -> real signature -> re-validate
    # procedure, not a shortcut.
    body: dict[str, Any] = json.loads(draft.model_dump_json(exclude_none=True))
    body["content_hash"] = compute_content_hash(body)

    # sign_object's own prepare_for_signature strips only "signature",
    # keeping the just-computed REAL content_hash — the placeholder
    # signature value above never influences what gets signed.
    signature_value = sign_object(private_key, body, algorithm=algorithm)
    signature = Signature(
        signer_practice_id=practice_id,
        key_id=kid,
        algorithm=algorithm,
        signed_at=created_at,
        value=signature_value,
    )
    body["signature"] = signature.model_dump(mode="json")
    return Practice.model_validate(body)
