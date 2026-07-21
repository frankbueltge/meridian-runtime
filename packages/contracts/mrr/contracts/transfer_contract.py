"""Mirrors schemas/transfer-contract.schema.json (docs/spec/02_DOMAIN_MODEL.md
section 2.14, "TransferContract": a versioned, sender-signed cross-practice
object binding exact source object ids/hashes with the purpose, permitted
uses, disclosure/attribution rules, caveats, correction subscription, and
structural obligation stubs that MUST travel with it — task-packets/
E6-T01.yaml, the nineteenth schema/contract/example entity).

--- Field-vs-precedent shape choices --------------------------------------

``sender_practice_id``/``receiver_practice_id`` are both required ``Urn``s.
``practice_id`` (inherited from ``BaseObject``) is expected by convention to
equal ``sender_practice_id`` — mirroring ``mrr.contracts.task_bundle.
TaskBundle``'s identical, NOT code-enforced, ``practice_id`` ==
``origin_practice_id`` convention (task-packets/E6-T01.yaml's own framing:
"practice_id from BaseObject duplicates sender_practice_id, mirroring
TaskBundle's practice_id==origin_practice_id precedent"). No
``model_validator`` checks this here, exactly as none does on ``TaskBundle``
— the service layer (``mrr.services.transfer.service.TransferService``)
mints both fields identically at construction time, the same convention
every builder of a signed cross-practice object in this codebase already
follows.

``transferred_objects`` is shaped exactly like
``mrr.contracts.correction_event.AffectedObjectRef`` — a ``{id,
content_hash}`` pair, ``minItems: 1`` — because both express the identical
concept: "exact object identifiers and their hashes, bound once, never
silently rewritten" (docs/spec/02_DOMAIN_MODEL.md section 4.9 acceptance:
"The recipient cannot silently replace the source hash").

``obligations`` carries ONLY the structural stub task-packets/E6-T01.yaml's
``derived_decisions`` describes: ``kind`` (drawn verbatim, snake_cased, from
docs/spec/02_DOMAIN_MODEL.md section 2.15's "Kinds include" prose list) plus
an optional ``deadline``. This is deliberately NOT the persisted
``Obligation`` aggregate domain 2.15 also describes (its own lifecycle,
responsible practice/role, trigger, resolution evidence, escalation
policy) — that aggregate, its own state machine, and any
``subject_to_obligation`` edge are E6-T02's scope
(task-packets/E6-T01.yaml forbidden_changes). ``TaskBundle`` already
establishes the precedent this mirrors: it carries ``resource_limits``/
``network_policy`` as inline structural fields on its own body, never as
separate aggregates.

``disclosure_rules``/``attribution_rules`` are open ``{"type": "object"}``
JSON objects with no further schema-level structure — mirroring
``mrr.contracts.task_bundle.TaskBundle.instructions``'s identical
"genuinely open-ended JSON object" shape. docs/spec/02_DOMAIN_MODEL.md
sections 2.14/2.15 give these only as unstructured prose
("disclosure and attribution rules"), with no schema-level detail
anywhere in the specification — task-packets/E6-T01.yaml's own
``specification_gaps`` names this as "the schema-design axis with the most
implementer latitude," bounded only by the stage-9 acceptance criterion that
this data remain visible/queryable, never silently dropped by a later
``respond`` event (see ``mrr.services.transfer.service``, which never
touches these fields after ``create``).

``correction_subscription`` is a plain ``bool`` — domain 2.14 names
"correction subscription" as a bare field with no further elaboration;
whether/how a subscription is actually acted on (E6-T03's cross-practice
correction notification) is out of this task's scope, so this field is
carried here only as the structural flag that DOES/DOESN'T travel with the
transfer, per MRR-FR-083.

--- status is ADR-0007's creation-time snapshot, not the live status --------

Exactly like ``TaskBundle`` (docs/spec/adr/ADR-0007-TASK-BUNDLE-TRANSITIONS-
ARE-EVENTS.md), ``TransferContract`` is both cross-practice, origin-signed
(MRR-NFR-007) AND lifecycle-bearing (MRR-FR-081) — the same structural
situation the ADR documents for ``TaskBundle``, and UNLIKE
``ResearchScore``/``Claim``/``CorrectionEvent`` (unsigned — their own
lifecycle transitions ARE new revisions, harmlessly, because nothing
verifies a signature over them). So the ADR-0007 asymmetry applies here
too: this ``status`` field is that ONE stored content record's creation-time
snapshot only — it will read ``"created"`` for every ``TransferContract`` in
this task's scope, because ``offer``/``respond`` are event-only transitions
(``mrr.services.transfer.service.TransferService``) that never mint a new
content revision. The LIVE status is event-derived (the latest
``transfer.offered``/``transfer.responded`` domain event, falling back to
this body field when no transition event exists yet).

--- Casing: lowercase, taken verbatim from MRR-FR-081's own prose -----------

Unlike ``TaskBundle`` (whose ALL-CAPS states are anchored in the
docs/spec/01_SYSTEM_SPEC.md section 6.2 diagram), no section-6 diagram
exists for ``TransferContract`` (task-packets/E6-T01.yaml
``specification_gaps`` item 1) — MRR-FR-081's own prose ("accepted",
"adapted", "rejected", "deferred", "unresolved") is the only available
casing anchor, and it is lowercase. This mirrors ``Claim``'s identical
reasoning in ``mrr.domain.lifecycles`` for the same reason: the FR text
itself, not a diagram, is the anchor.

--- signature is singular, not the domain-model's unelaborated "signatures" -

docs/spec/02_DOMAIN_MODEL.md section 2.14 lists "signatures" (plural) with
no further elaboration of cardinality or shape. Every existing schema's
``signature`` field (``schemas/common.schema.json`` ``$defs.signature``) is
singular; this module defaults to ONE sender-origin signature (mirrors
``TaskBundle``), with the recipient's response authenticated only via its
own domain-event actor/practice_id fields — NOT a second object-level
signature slot (task-packets/E6-T01.yaml ``specification_gaps`` item 2;
flagged for reviewer scrutiny there, not resolved unilaterally here).
"""

from __future__ import annotations

from typing import Any, Literal

from mrr.contracts.common import BaseObject, MRRModel, Sha256, Signature, Urn
from pydantic import AwareDatetime, Field

#: Mirrors the top-level `status` enum. ADR-0007: a creation-time snapshot
#: only — see the module docstring's "status is ADR-0007's creation-time
#: snapshot" section. Lowercase — see "Casing" above.
TransferStatus = Literal[
    "created",
    "offered",
    "accepted",
    "adapted",
    "rejected",
    "deferred",
    "unresolved",
]

#: Mirrors `obligations[].kind`, drawn verbatim (snake_cased) from
#: docs/spec/02_DOMAIN_MODEL.md section 2.15's "Kinds include" prose list:
#: review correction; preserve attribution; retain caveat; delete or
#: restrict data; notify downstream recipients; obtain human approval;
#: re-run analysis; respond to transfer.
ObligationKind = Literal[
    "review_correction",
    "preserve_attribution",
    "retain_caveat",
    "delete_or_restrict_data",
    "notify_downstream_recipients",
    "obtain_human_approval",
    "re_run_analysis",
    "respond_to_transfer",
]


class TransferredObjectRef(MRRModel):
    """Mirrors a `transferred_objects[]` entry; both properties required —
    shaped exactly like `mrr.contracts.correction_event.AffectedObjectRef`.
    """

    id: Urn
    content_hash: Sha256


class ObligationStub(MRRModel):
    """A STRUCTURAL obligation stub — `kind` plus an optional `deadline`
    only. NOT the persisted `Obligation` aggregate
    (docs/spec/02_DOMAIN_MODEL.md section 2.15), which carries its own
    responsible practice/role, trigger, status, resolution evidence, and
    escalation policy — that aggregate, and any propagation machinery for
    it, is E6-T02's scope (task-packets/E6-T01.yaml forbidden_changes). See
    the module docstring's own "obligations carries ONLY the structural
    stub" section.
    """

    kind: ObligationKind
    deadline: AwareDatetime | None = None


class TransferContract(BaseObject):
    """Mirrors schemas/transfer-contract.schema.json.

    Every property is in the schema's top-level `required` list — unlike
    several sibling entities (e.g. `TaskBundle`'s `tools`/`secret_refs`),
    `TransferContract` has no schema-optional property of its own: even an
    empty transfer carries an explicit (possibly empty) `permitted_uses`/
    `caveats`/`obligations` list and an explicit `correction_subscription`
    boolean, rather than omitting them — MRR-FR-083's "obligations, caveats,
    disclosure limits, attribution, and correction subscriptions MUST travel
    with the transfer" is read as "always present as an explicit field,"
    not merely "present when non-default."

    `disclosure_rules`/`attribution_rules` mirror `{"type": "object"}` with
    no `properties` or `additionalProperties` restriction — a genuinely
    open-ended JSON object, like `TaskBundle.instructions` — so they are
    `dict[str, Any]` rather than a closed `MRRModel`.
    """

    kind: Literal["TransferContract"]
    sender_practice_id: Urn
    receiver_practice_id: Urn
    transferred_objects: list[TransferredObjectRef] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    permitted_uses: list[str]
    disclosure_rules: dict[str, Any]
    attribution_rules: dict[str, Any]
    caveats: list[str]
    correction_subscription: bool
    obligations: list[ObligationStub]
    nonce: str = Field(min_length=16)
    expires_at: AwareDatetime
    signature: Signature
    status: TransferStatus
