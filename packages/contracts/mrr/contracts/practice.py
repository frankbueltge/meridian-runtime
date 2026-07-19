"""Mirrors schemas/practice.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.1, "Practice": "Represents an autonomous research practice.").
Sixteenth entity schema/model pair in this repository; first task of Epic E5
(task-packets/E5-T01.yaml).

A ``Practice`` is a research practice's own published identity: who it is,
which PUBLIC keys currently speak for it, how to reach its governance
contacts, which policy versions it supports, and (optionally) where its
public capability-registry endpoint lives. Like every other first-class MRR
object it is content-hashed via the inherited ``BaseObject.content_hash``,
and it MAY additionally carry a ``signature`` — the practice SELF-signing
its own identity with one of its own listed keys (signer_practice_id ==
this Practice's own ``id``), built and verified over the ADR-0004
``exclude_none=True`` canonical form via the EXISTING
``mrr.domain.hashing_policy`` (``compute_content_hash``/``sign_object``/
``verify_object_signature``), never a second hashing/signing
implementation. Deciding whether a ``Practice`` is TRUSTED (trust-anchoring
another practice's self-signed identity) is deferred to E5-T02+ — this
module only proves the shape is signable and verifiable, exactly like
task-packets/E5-T00.yaml proved for the three pre-existing signed objects.

The field is named ``signature`` — matching ``NodeManifest``/``TaskBundle``/
``EvidenceCrate``'s own field name exactly, deliberately NOT
``self_signature`` — because reusing ``mrr.domain.hashing_policy`` UNCHANGED
(this task's own forbidden_changes: "reuse them unchanged") requires it:
``prepare_for_hash``/``prepare_for_signature`` exclude the literal field
name ``"signature"`` (and, separately, ``"content_hash"``), per that
module's own docstring, which explains at length why it does NOT also
exclude a differently-named field such as ``signatures`` — the same
reasoning applies to a field named ``self_signature``. Only the "self" part
is semantic (this Practice's own model_validator below checks
``signature.signer_practice_id == id``), not a different field identifier.

--- ``keys``: PUBLIC key material only --------------------------------------

``PublicKeyDescriptor.encoded_public_key`` is a standard-base64-encoded raw
Ed25519 PUBLIC key (``mrr.crypto.keys.encode_public_key``'s own ADR-0003
convention) — never a private key; nothing in this module can hold one.
``kid`` is verified, in ``_kid_matches_key_and_window_is_ordered`` below, to
equal ``mrr.crypto.keys.derive_key_id``'s own deterministic output for
``encoded_public_key``: the same check ``mrr.domain.key_management.
PublicKeyDescriptor.__post_init__`` performs at the domain layer, duplicated
here because a Pydantic model field cannot hold an arbitrary frozen
dataclass and still generate the JSON Schema ``scripts/check_contracts.py``
cross-validates against — mirroring this package's existing "enforced
twice" precedent (e.g. ``mrr.contracts.model_profile.ModelProfile``'s
determinism/temperature check, both in this file's schema ``if``/``then``
and in a ``model_validator``). The same class name is deliberately reused
across the domain and contracts layers, exactly as
``mrr.domain.artifacts.Classification`` documents doing for
``mrr.contracts.common.Classification`` — two different layers, one
concept, no import cycle (``mrr.contracts`` already depends on
``mrr.domain`` and ``mrr.crypto``; the reverse would be a cycle).

--- ``disclosure``: an open specification question --------------------------

docs/spec/02_DOMAIN_MODEL.md section 2.1 names a required "disclosure and
trust metadata" field with no further elaboration anywhere in the spec.
This is a deliberately minimal, conservative reading — flagged as an open
specification question in this task's PR body, mirroring
``mrr.contracts.evidence_anchor.RecomputationStatus``'s own "not
spec-derived" precedent: ``max_disclosure`` reuses the existing three-value
vocabulary ``mrr.contracts.research_score.MaxDisclosure`` already defines
for exactly this concept (a ceiling disclosure level), and
``trust_statement`` is a free-text field for whatever qualitative trust
posture a practice wants to publish about itself. Both keys are required to
be present (may be an empty string for ``trust_statement``), matching this
package's established "the schema requires the key, not a non-empty value"
convention (see ``mrr.contracts.research_score.MethodsPolicy``'s docstring).

--- ``trust_chain_ref``: carried, not validated -----------------------------

docs/spec/02_DOMAIN_MODEL.md section 1.3 lists an "optional certificate or
trust-chain reference" as part of what a signature/identity MAY carry;
task-packets/E5-T01.yaml's forbidden_changes explicitly permits "the
Practice/signature schema" to carry this as an OPTIONAL field while ruling
out any certificate/PKI/trust-chain VALIDATION. Deliberately added only to
``Practice`` (not to the shared ``mrr.contracts.common.Signature`` every
other signed object also uses) to keep this task's change surface isolated
to the new entity — NodeManifest/TaskBundle/EvidenceCrate are unmodified.
This field is opaque here: no format is imposed, and nothing in this module
ever resolves, fetches, or verifies a chain from it.
"""

from __future__ import annotations

from typing import Literal, Self

from mrr.contracts.common import BaseObject, MRRModel, Signature
from mrr.contracts.research_score import MaxDisclosure
from mrr.crypto.exceptions import InvalidPublicKeyError
from mrr.crypto.keys import decode_public_key, derive_key_id
from mrr.domain.key_management import KeyState
from pydantic import AwareDatetime, Field, model_validator

__all__ = [
    "DisclosureAndTrust",
    "Practice",
    "PublicKeyDescriptor",
]


class PublicKeyDescriptor(MRRModel):
    """Mirrors schemas/practice.schema.json ``$defs.publicKeyDescriptor``:
    one PUBLIC key's identity, validity window, and lifecycle state — the
    same four semantic fields ``mrr.domain.key_management.
    PublicKeyDescriptor`` defines, reusing its ``KeyState`` vocabulary
    directly (no separate contracts-layer enum) rather than importing
    ``mrr.contracts`` into ``mrr.domain`` (which would be a cycle).
    """

    kid: str = Field(min_length=1)
    algorithm: Literal["Ed25519"]
    encoded_public_key: str = Field(min_length=40)
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    state: KeyState

    @model_validator(mode="after")
    def _kid_matches_key_and_window_is_ordered(self) -> Self:
        try:
            public_key = decode_public_key(self.encoded_public_key)
        except InvalidPublicKeyError as exc:
            raise ValueError(
                f"encoded_public_key is not a valid Ed25519 public key: {exc}"
            ) from exc

        expected_kid = derive_key_id(public_key)
        if self.kid != expected_kid:
            raise ValueError(
                f"kid {self.kid!r} does not match the deterministic kid derived from "
                f"encoded_public_key ({expected_kid!r})"
            )
        if not self.valid_from < self.valid_until:
            raise ValueError("valid_from must be strictly before valid_until")
        return self


class DisclosureAndTrust(MRRModel):
    """Mirrors the ``disclosure`` object — see this module's docstring for
    why this is a deliberately minimal reading of docs/spec/02_DOMAIN_MODEL.md
    section 2.1's "disclosure and trust metadata".
    """

    max_disclosure: MaxDisclosure
    trust_statement: str


class Practice(BaseObject):
    """Mirrors schemas/practice.schema.json.

    Every property in the schema's top-level ``required`` list is mandatory
    here except ``capability_registry_endpoint`` ("public capability
    registry endpoint if any" — explicitly optional per docs/spec/02 section
    2.1's own wording), ``trust_chain_ref``, and ``signature``.
    ``keys`` requires at least one entry (``minItems: 1``, mirroring
    ``mrr.contracts.node_manifest.NodeManifest.public_keys``'s own
    precedent) — a practice with zero keys could sign nothing and verify
    nothing signed by it, so an empty list is not a meaningful identity.
    """

    kind: Literal["Practice"]
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    keys: list[PublicKeyDescriptor] = Field(min_length=1)
    governance_contacts: list[str]
    supported_policy_versions: list[str]
    capability_registry_endpoint: str | None = None
    disclosure: DisclosureAndTrust
    trust_chain_ref: str | None = Field(default=None, min_length=1)
    signature: Signature | None = None

    @model_validator(mode="after")
    def _signature_signer_is_this_practice_with_a_listed_key(self) -> Self:
        if self.signature is None:
            return self
        if self.signature.signer_practice_id != self.id:
            raise ValueError(
                "signature.signer_practice_id must equal this Practice's own id "
                f"({self.id!r}); got {self.signature.signer_practice_id!r} — a "
                "self-signature's signer must be the practice itself"
            )
        known_kids = {key.kid for key in self.keys}
        if self.signature.key_id not in known_kids:
            raise ValueError(
                f"signature.key_id {self.signature.key_id!r} is not one of this "
                f"Practice's own listed keys {sorted(known_kids)!r}"
            )
        return self
