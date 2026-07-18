"""``EvidenceCrateSealer`` (task-packets/E2-T06.yaml): assembles and SEALS an
``EvidenceCrate`` (schemas/evidence-crate.schema.json, docs/spec/01_SYSTEM_SPEC.md
section 4.6 "Stage 6 -- Evidence Crate", MRR-FR-050..056) from one executor
``ExecutionResult`` (E2-T04) plus the ``RunManifest`` already sealed for that
same run (E2-T05) and the ``TaskBundle`` (E2-T03) both describe. This is the
node runtime's own final step (docs/spec/01_SYSTEM_SPEC.md section 7.5: "...
executes approved work, seals outputs, and signs result crates").

Scope, precisely: this module builds and seals the crate's OWN fields. It
does NOT:

- populate ``source_records``/``evidence_anchors``/``proposed_claims`` with
  anything beyond an empty array -- the claim graph and evidence anchors are
  E3's responsibility (task-packets/E2-T06.yaml ``forbidden_changes``), and
  inventing their shape here would be exactly what AGENTS.md rule 3 forbids.
  Empty is not a stub for the deterministic reference run this task's own
  executor produces: it faithfully records that no claims/anchors exist yet
  for this run, not a placeholder awaiting removal;
- export RO-Crate/PROV (MRR-FR-055 is E8);
- invoke a model/LLM or interpret ``run_manifest.model_invocations`` (E4);
- expose HTTP/FastAPI;
- offer any mutate/unseal/correct method -- see "Immutability" below.

--- Why this sealer needs no ObjectRepository/EventLog reads --------------

Exactly like ``mrr.services.node_runtime.run_manifest.RunManifestRecorder``
(E2-T05, this module's closest sibling and the reason this class's shape
mirrors it so closely): ``EvidenceCrateSealer`` has exactly one operation,
``seal``, and it always mints a brand-new object identity
(``mrr.domain.identity.new_urn("evidence-crate")``) written once, at
revision 1. There is no prior revision to read and no prior event for that
fresh id to chain from (``causation_id`` is always ``None``). The
constructor therefore only needs the bound ``RecordRevisionWithEvent``
write callable.

--- Both success AND failure runs seal (MRR-FR-050) ------------------------

``seal`` takes ``execution_result.outcome`` as the crate's ``run_state``
unconditionally, for all six ``TerminalOutcome`` values -- there is no
branch anywhere in this module that skips sealing for a non-``completed``
outcome. A materially failed run (``failed``/``timed_out``/``cancelled``/
``policy_denied``/``partial``) is built, signed, and sealed exactly like a
``completed`` one; only its ``failures``/``known_unknowns`` (caller-supplied,
see below) and ``artifacts`` (typically empty for a run that produced no
output) differ in content, never in whether sealing happens.

--- ``failures``/``known_unknowns`` are caller-supplied, not auto-derived --

MRR-FR-054 requires the crate to "preserve null results, errors, exclusions,
and known unknowns", and task-packets/E2-T06.yaml's derived_decisions read
this as "for a completed run these are explicitly empty arrays; for a
failed/partial run they carry the failure detail". This module does NOT,
however, synthesize a ``FailureEntry`` from ``execution_result.outcome``/
``.detail`` itself: ``FailureEntry.category`` is
``mrr.contracts.evidence_crate.FailureCategory`` (``not_found``, ``unknown``,
``null_result``, ``contradicted``, ``underpowered``, ``method_invalidated``,
``source_unavailable``, ``execution_error``, ``policy_denied``) -- a
taxonomy that does not map one-to-one onto
``mrr.services.node_runtime.executor.TerminalOutcome`` (there is no
``cancelled`` or ``timed_out`` category, for instance). Guessing that
mapping inside this module would be inventing domain behavior the
specification does not state (AGENTS.md rule 3). ``seal`` therefore accepts
``failures``/``known_unknowns`` as caller-supplied sequences (defaulting to
empty, matching a clean ``completed`` run with nothing further to report);
the caller -- who has ``execution_result.outcome``/``.detail`` in hand -- is
responsible for constructing the right ``FailureEntry`` values for a
non-``completed`` run. Flagged in the PR as an open specification question:
a future task should either ratify a fixed ``TerminalOutcome`` ->
``FailureCategory`` mapping or an explicit policy for it.

--- ``artifact_refs`` are caller-supplied, not read from the object store --

Similarly, ``artifact_refs`` (defaulting to ``()``, "empty if none") is a
sequence of already-built ``mrr.contracts.ArtifactRef`` -- ``artifact_id`` +
``content_hash`` (+ optional ``classification``) pairs the caller has
already produced, typically by calling ``mrr.domain.artifacts.ArtifactStore
.put()`` (E1-T07) to get a real SHA-256 content hash and minting a fresh
``artifact`` URN for the reference. This module does not talk to an
``ArtifactStore`` itself -- ``ArtifactDescriptor`` has no ``artifact_id``
field at all (the store is keyed purely by content hash), so wrapping one
into a first-class ``ArtifactRef`` requires a URN-minting decision this
task's packet does not assign to the sealer.

--- Immutability: no update/mutate/unseal method ---------------------------

MRR-FR-056: "A sealed crate is immutable; corrections create new objects and
links rather than altering sealed bytes." This class offers exactly one
public method, ``seal``, called exactly once per run -- there is no
``update``/``unseal``/``correct`` method anywhere on it, by construction.
Because the crate is built-then-sealed exactly once and never transitions
through intermediate states (unlike ``TaskBundle``'s multi-state lifecycle,
ADR-0007), the node signature trivially verifies against the one and only
version of the crate that will ever exist under this id -- there is no
historical-revision reconciliation question here. A correction to an
already-sealed crate is a NEW, superseding ``EvidenceCrate`` (presumably
carrying ``supersedes`` set to the original's id) -- out of this task's
scope, left for a future task, exactly as task-packets/E2-T06.yaml's
derived_decisions directs.

--- Signature convention: sign over ``model_dump(mode="json")`` -----------

Per ADR-0004 (pending) this module uses the same construction
E2-T02/T03 already use: ``mrr.domain.hashing_policy.sign_object(private_key,
obj.model_dump(mode="json"))``, and later verification is
``verify_object_signature(public_key, obj.model_dump(mode="json"),
obj.signature.value, algorithm=obj.signature.algorithm)`` -- see
``mrr.services.task_bundle.service.NodeTaskDecisionService
._authorize_and_verify``.

Two DIFFERENT dict representations of the same crate matter here, and
mixing them up would break verification silently:

1. ``crate.model_dump(mode="json")`` -- the full Pydantic dump, including a
   JSON ``null`` for any unset ``Optional`` ``BaseObject`` field
   (``supersedes``, ``labels``; this crate never sets either). This is what
   gets hashed (``compute_content_hash``) and signed (``sign_object``).
2. ``json.loads(crate.model_dump_json(exclude_none=True))`` -- the
   ``None``-dropping form every ``_x_to_stored_object`` helper in this
   codebase persists, because schemas/common.schema.json's ``labels``
   property is a plain ``{"type": "object"}`` with no ``null`` variant: a
   persisted ``"labels": null`` would fail JSON Schema validation even
   though the field is optional. This is what gets written to
   ``StoredObject.body``.

These do not have to be byte-identical to each other, but representation 1
DOES have to be reproducible, byte-for-byte, from representation 2:
reconstructing ``EvidenceCrate.model_validate(stored.body)`` restores the
unset optional fields to their Pydantic default (``None``, since they were
simply absent from the JSON, not present-and-null), so re-dumping the
reconstructed model with ``model_dump(mode="json")`` reproduces exactly the
same dict that was originally hashed/signed -- this is what lets "the
sealed crate's signature still verifies from the database" hold at all
(the integration acceptance test). This module therefore hashes and signs
the crate model directly (``draft.model_dump(mode="json")``), and only
switches to the ``exclude_none`` form at the very last step, to build the
persisted body.

--- ``practice_id`` is not a separate parameter ----------------------------

The crate's own ``practice_id`` (the ``BaseObject`` field naming which
practice owns this object) is always set to ``signer_practice_id`` -- the
node's own practice, since the node is both the executor of this run and
the sole signer of the resulting crate. task-packets/E2-T06.yaml's own
illustrative signature does not list a separate ``practice_id`` parameter,
and introducing one would risk a caller silently passing a practice_id that
disagrees with the practice actually vouching for (signing) this crate,
exactly the redundant-field risk ``mrr.services.node_runtime.run_manifest``
avoids by never accepting a separate ``created_by`` (always ``actor``).

--- Why ``environment.code_revision`` can raise ----------------------------

``mrr.contracts.evidence_crate.EnvironmentInfo.code_revision`` is a required,
non-nullable ``str`` (MRR-FR-053: "Every computational result MUST
reference ... code or workflow version ..."), but the source it is derived
from, ``RunManifest.code_commit``, is nullable
(``mrr.contracts.run_manifest`` documents why: it mirrors
``TaskBundle.execution.code_revision``, itself nullable in the schema).
Fabricating a placeholder string when it is genuinely absent would silently
misrepresent provenance -- exactly what AGENTS.md rule 12 ("no fake
implementations") and this codebase's own honesty conventions forbid.
``seal`` therefore raises ``ValueError`` if ``run_manifest.code_commit`` is
``None`` rather than inventing a value; every ``RunManifest`` this
codebase's own ``RunManifestRecorder`` builds always carries the executed
``TaskBundle.execution.code_revision`` verbatim, so this only fires for a
``RunManifest`` built with a genuinely absent code revision, which this
task cannot represent as a schema-valid crate without guessing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mrr.contracts import (
    ArtifactRef,
    EnvironmentInfo,
    EvidenceCrate,
    FailureEntry,
    RunManifest,
    Signature,
    TaskBundle,
    Urn,
)
from mrr.domain.hashing_policy import compute_content_hash, sign_object
from mrr.domain.identity import new_urn
from mrr.domain.repositories import StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from mrr.services.node_runtime.executor import ExecutionResult
from sqlalchemy import Engine

#: docs/spec/01_SYSTEM_SPEC.md MRR-FR-050's event: every sealed crate writes
#: exactly one of these (task-packets/E2-T06.yaml invariant: "sealing records
#: exactly one domain event with full NFR-001 provenance, atomically with the
#: persisted revision").
_EVENT_SEALED = "evidence_crate.sealed"

#: Placeholder ``Signature.value`` used only while assembling the draft
#: object below, before the real signature is computed. Never persisted --
#: see ``EvidenceCrateSealer.seal``. Any well-formed (``min_length=40``)
#: string works here: ``mrr.domain.hashing_policy.prepare_for_signature``
#: strips the entire ``signature`` field before hashing or signing, so this
#: placeholder's actual value can never leak into what gets hashed or
#: signed.
_PLACEHOLDER_SIGNATURE_VALUE = "0" * 44

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. Identical in shape to every other service's own
#: ``RecordRevisionWithEvent`` -- see ``mrr.services.node_runtime.run_manifest``
#: for why this is a local copy, not a shared import, across separate
#: service modules.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple, producing the ``RecordRevisionWithEvent`` callable
    ``EvidenceCrateSealer`` depends on for its one atomic write. Production
    wiring and integration tests call this once; DB-free unit tests pass
    their own trivial callable of the same shape, backed by an in-memory
    fake, instead.
    """

    def _record(
        obj: StoredObject,
        expected_current_revision: int | None,
        event: DomainEvent,
    ) -> tuple[StoredObject, AppendedEvent]:
        return record_object_revision_with_event(
            engine, object_repository, event_log, obj, expected_current_revision, event
        )

    return _record


def _crate_to_stored_object(crate: EvidenceCrate) -> StoredObject:
    """Convert an already-valid, already-sealed, already-signed
    ``EvidenceCrate`` into the generic ``StoredObject``
    ``mrr.domain.repositories.ObjectRepository`` persists. ``body`` is a
    plain ``model_dump_json(exclude_none=True)`` round trip -- no added
    keys -- matching every other service's own ``_*_to_stored_object``
    helper. See the module docstring's "Signature convention" section for
    why this is deliberately NOT the representation the signature itself
    covers.
    """
    body = json.loads(crate.model_dump_json(exclude_none=True))
    return StoredObject(
        id=crate.id,
        api_version=crate.api_version,
        kind=crate.kind,
        practice_id=crate.practice_id,
        revision=crate.revision,
        created_at=crate.created_at,
        created_by=crate.created_by,
        content_hash=crate.content_hash,
        supersedes=crate.supersedes,
        labels=crate.labels,
        body=body,
    )


class EvidenceCrateSealer:
    """docs/spec/01_SYSTEM_SPEC.md section 7.5 / MRR-FR-050..056: assembles
    and seals an immutable, node-signed ``EvidenceCrate`` for one executor
    run. See the module docstring for the full design (why no read
    dependency, why both success and failure runs seal, the
    failures/known_unknowns and artifact_refs caller-supplied boundaries,
    immutability, and the two-representation signature convention).
    """

    def __init__(self, record: RecordRevisionWithEvent) -> None:
        self._record = record

    def seal(
        self,
        run_manifest: RunManifest,
        execution_result: ExecutionResult,
        task_bundle: TaskBundle,
        *,
        artifact_refs: Sequence[ArtifactRef] = (),
        node_signing_key: Ed25519PrivateKey,
        node_key_id: str,
        signer_practice_id: Urn,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        failures: Sequence[FailureEntry] = (),
        known_unknowns: Sequence[str] = (),
    ) -> StoredObject:
        """Build a schema-valid ``EvidenceCrate`` from ``run_manifest``/
        ``execution_result``/``task_bundle``, seal it (node-sign the
        canonical content, set ``content_hash``, ``sealed=True``), and
        persist it as revision 1 plus an ``evidence_crate.sealed`` event,
        atomically. Works identically for a ``completed`` run and for any
        materially failed terminal outcome (MRR-FR-050) -- there is no
        branch here that skips sealing.

        Every field derivable from ``run_manifest``/``execution_result``/
        ``task_bundle`` is derived here, not accepted as a parameter:

        - ``task_id`` <- ``task_bundle.id``.
        - ``run_id`` <- ``run_manifest.id`` (the id
          ``mrr.services.node_runtime.run_manifest.RunManifestRecorder
          .record`` returned for this same run).
        - ``run_state`` <- ``execution_result.outcome`` (all six
          ``TerminalOutcome`` values, unconditionally).
        - ``environment.image_digest``/``.input_hashes`` <-
          ``run_manifest.image_digest``/``.input_hashes`` verbatim.
        - ``environment.code_revision`` <- ``run_manifest.code_commit``
          (see the module docstring's "Why ``environment.code_revision``
          can raise" section).
        - ``environment.model_profiles`` is always ``[]`` -- the
          deterministic reference run this task's own executor produces
          never invokes a model (E4 scope).
        - ``source_records``/``evidence_anchors``/``proposed_claims`` are
          always ``[]`` (E3 scope; a faithful empty, not a stub).
        - ``sealed`` is always ``True``.

        Everything else is caller-supplied: ``artifact_refs``/``failures``/
        ``known_unknowns`` (all default to "nothing to report" -- ``()``
        -- see the module docstring for why these are not auto-derived),
        ``node_signing_key``/``node_key_id``/``signer_practice_id`` (the
        signing node's identity -- key management itself is E5, out of
        this task's scope), and ``actor``/``policy_version``/
        ``correlation_id`` (MRR-NFR-001 provenance for the recorded event).

        Raises:
            ValueError: ``execution_result.task_id``/``.task_revision``,
                or ``run_manifest.task_id``/``.task_revision``, does not
                match ``task_bundle.id``/``.revision`` (the caller passed a
                result/manifest/bundle triple that do not describe the same
                execution); ``run_manifest.sealed`` is not ``True``
                (MRR-FR-042 requires the manifest to already be sealed
                before a crate is sealed for it); ``run_manifest.run_state``
                does not match ``execution_result.outcome``; or
                ``run_manifest.code_commit`` is ``None`` (see the module
                docstring).
        """
        if execution_result.task_id != task_bundle.id:
            raise ValueError(
                f"execution_result.task_id ({execution_result.task_id!r}) does not match "
                f"task_bundle.id ({task_bundle.id!r})"
            )
        if execution_result.task_revision != task_bundle.revision:
            raise ValueError(
                f"execution_result.task_revision ({execution_result.task_revision!r}) does "
                f"not match task_bundle.revision ({task_bundle.revision!r})"
            )
        if run_manifest.task_id != task_bundle.id:
            raise ValueError(
                f"run_manifest.task_id ({run_manifest.task_id!r}) does not match "
                f"task_bundle.id ({task_bundle.id!r})"
            )
        if run_manifest.task_revision != task_bundle.revision:
            raise ValueError(
                f"run_manifest.task_revision ({run_manifest.task_revision!r}) does not match "
                f"task_bundle.revision ({task_bundle.revision!r})"
            )
        if not run_manifest.sealed:
            raise ValueError(
                f"run_manifest {run_manifest.id!r} is not sealed; an evidence crate can only be "
                "sealed for a run whose manifest is already sealed (MRR-FR-042)"
            )
        if run_manifest.run_state != execution_result.outcome:
            raise ValueError(
                f"run_manifest.run_state ({run_manifest.run_state!r}) does not match "
                f"execution_result.outcome ({execution_result.outcome!r})"
            )
        if run_manifest.code_commit is None:
            raise ValueError(
                f"run_manifest {run_manifest.id!r} has no code_commit; "
                "EvidenceCrate.environment.code_revision (MRR-FR-053) requires a value and none "
                "can be inferred without fabricating one"
            )

        now = datetime.now(UTC)
        crate_id = new_urn("evidence-crate")

        environment = EnvironmentInfo(
            image_digest=run_manifest.image_digest,
            code_revision=run_manifest.code_commit,
            input_hashes=list(run_manifest.input_hashes),
            model_profiles=[],
        )
        placeholder_signature = Signature(
            signer_practice_id=signer_practice_id,
            key_id=node_key_id,
            algorithm="Ed25519",
            signed_at=now,
            value=_PLACEHOLDER_SIGNATURE_VALUE,
        )

        draft = EvidenceCrate(
            id=crate_id,
            api_version="mrr/v1alpha1",
            kind="EvidenceCrate",
            practice_id=signer_practice_id,
            revision=1,
            created_at=now,
            created_by=actor,
            content_hash="sha256:" + "0" * 64,  # placeholder; recomputed below
            task_id=task_bundle.id,
            run_id=run_manifest.id,
            run_state=execution_result.outcome,
            artifacts=list(artifact_refs),
            source_records=[],
            evidence_anchors=[],
            proposed_claims=[],
            failures=list(failures),
            known_unknowns=list(known_unknowns),
            environment=environment,
            sealed=True,
            signature=placeholder_signature,
        )

        # Both the content hash and the node signature are computed over
        # draft.model_dump(mode="json") -- NOT the exclude_none form used
        # for persistence below -- see the module docstring's "Signature
        # convention" section for why this specific representation is the
        # one that must be reproducible after a database round trip.
        real_content_hash = compute_content_hash(draft.model_dump(mode="json"))
        draft = draft.model_copy(update={"content_hash": real_content_hash})

        # mrr.domain.hashing_policy.sign_object's prepare_for_signature
        # strips the entire "signature" field before signing (keeping the
        # just-updated real content_hash) -- the placeholder signature
        # value above never influences what gets signed.
        signature_value = sign_object(node_signing_key, draft.model_dump(mode="json"))
        signature = Signature(
            signer_practice_id=signer_practice_id,
            key_id=node_key_id,
            algorithm="Ed25519",
            signed_at=now,
            value=signature_value,
        )
        crate = draft.model_copy(update={"signature": signature})

        # Defensive round trip through the exact persisted (exclude_none)
        # body before writing, mirroring every other service's own
        # "_x_to_stored_object" + "model_validate(body)" pattern.
        persisted_body = json.loads(crate.model_dump_json(exclude_none=True))
        crate = EvidenceCrate.model_validate(persisted_body)

        obj = _crate_to_stored_object(crate)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_EVENT_SEALED,
            occurred_at=now,
            actor=actor,
            policy_version=policy_version,
            causation_id=None,
            correlation_id=correlation_id,
            object_id=crate_id,
            object_revision=1,
            payload={
                "task_id": task_bundle.id,
                "run_id": run_manifest.id,
                "run_state": execution_result.outcome,
                "sealed": True,
            },
        )
        stored, _ = self._record(obj, None, event)
        return stored
