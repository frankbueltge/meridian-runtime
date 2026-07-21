"""``CapabilityRegistry`` (task-packets/E2-T02.yaml, extended by
task-packets/E5-T02.yaml): the application-layer service
docs/spec/01_SYSTEM_SPEC.md section 7.3 describes as the Capability
Registry — "Stores signed node manifests and compatibility metadata. It does
not grant permission." — implementing MRR-FR-020/021.

Four operations:

- ``register``: verify a signed ``NodeManifest``'s signature against a
  CALLER-SUPPLIED verifying key (fail closed, persist nothing on failure),
  then persist it as the next append-only revision for its ``node_id`` plus
  a ``node_manifest.registered`` event, atomically. Unchanged since E2-T02 —
  this task-packets/E5-T02.yaml addition never modifies it.
- ``receive`` (E5-T02): the TRUST-ANCHORED accept path — resolves the
  manifest's OWN claimed signer/key id against a caller-supplied trusted
  sender ``mrr.contracts.practice.Practice``'s ``KeyRing``
  (``mrr.domain.manifest_trust.resolve_trusted_manifest_key``, fail closed
  with a distinct typed error per reason) and, only on success, delegates to
  ``register`` with the resolved key — reusing all of its persistence/event/
  revision behavior verbatim. On any trust-resolution failure, nothing is
  persisted and a ``node_manifest.rejected`` event carrying only a COARSE
  reason category is recorded (docs/spec/04_SECURITY_AND_POLICY.md section
  8.3: "a coarse reason code", detail kept local) via the event-only
  ``RecordEvent`` path (ADR-0007's ``record_event`` — no content revision is
  ever written for a rejection). This closes the trust-anchoring gap
  E2-T02's ``register`` deliberately left open: an object-layer verification
  primitive that trusted whatever key the caller handed it, with no
  resolution against a trusted key set of its own.
- ``get_current_manifest``: resolve the latest revision for a node and
  assert it is currently within its declared validity window.
- ``find_nodes_with_capability``: a pure matching primitive over currently
  valid manifests — "which nodes declare capability X and are in-window
  right now", **never** an authorization verdict (section 7.3's "It does
  not grant permission" applies to this whole service, and to this method
  above all — see its docstring).

Wiring shape is copied deliberately from
``mrr.services.research_score.service.ResearchScoreService`` (E2-T01), which
this task packet names explicitly as the pattern to reuse:

- reads go through ``mrr.domain.repositories.ObjectRepository`` (a
  ``Protocol``, structurally satisfied by both
  ``mrr.persistence.repositories.PostgresObjectRepository`` and a
  hand-written unit-test fake);
- the atomic write goes through ``RecordRevisionWithEvent`` — the same
  ``Callable`` shape ``ResearchScoreService`` depends on, bound to the real
  E1-T06 ``record_object_revision_with_event`` by ``bind_unit_of_work``
  below (production wiring and integration tests), or backed by a
  DB-free fake unit of work (unit tests);
- a minimal local ``_EventJournal`` Protocol (``read_all`` only) covers both
  this service's causation-chain lookup (mirroring
  ``ResearchScoreService._last_event_id_for``) *and* — new to this
  service — ``find_nodes_with_capability``'s node discovery (see that
  method's docstring for why reading the event log is how this service
  learns which node ids exist at all, given that
  ``mrr.domain.repositories.ObjectRepository`` offers no "list every
  object id" operation and this task must not add one — persistence
  internals are reuse-as-is, task-packets/E2-T02.yaml forbidden_changes).

``bind_unit_of_work`` here is a local copy of the module-level
``bind_unit_of_work`` function in
``mrr.services.research_score.service``'s few lines, not an import of it or
a shared extraction — task-packets/E2-T02.yaml says to replicate the small
pattern locally rather than refactor merged E2-T01 code. (A future task
could lift both into one shared helper once a third caller shows the
duplication is real, rather than guessing at the right shared shape now
from just two.) ``bind_event_unit_of_work`` (E5-T02) is the same kind of
local copy, this time of
``mrr.services.task_bundle.service.bind_event_unit_of_work`` — the
EVENT-ONLY path (ADR-0007's ``mrr.persistence.unit_of_work.record_event``)
``receive`` needs to record a rejection without writing any content
revision.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts import NodeManifest, Practice, Urn
from mrr.crypto.exceptions import SignatureVerificationError, UnsupportedAlgorithmError
from mrr.domain.exceptions import (
    ManifestKeyNotDeclaredError,
    ManifestKeyNotValidError,
    ManifestSignerMismatchError,
    NodeManifestNotFoundError,
    NodeManifestValidityError,
    ObjectNotFoundError,
    UnknownKeyIdError,
)
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.identity import new_urn
from mrr.domain.manifest_trust import practice_key_ring, resolve_trusted_manifest_key
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog
from mrr.persistence.unit_of_work import (
    RecordRevisionWithEvent as RecordRevisionWithEvent,
)
from mrr.persistence.unit_of_work import (
    bind_unit_of_work as bind_unit_of_work,
)
from mrr.persistence.unit_of_work import (
    record_event,
)
from mrr.provenance.events import DomainEvent
from mrr.provenance.log import AppendedEvent
from sqlalchemy import Engine

#: docs/spec/01_SYSTEM_SPEC.md MRR-FR-020's event: every successful
#: registration writes exactly one of these
#: (task-packets/E2-T02.yaml invariant: "every registration records a
#: domain event with full NFR-001 provenance, atomically with the persisted
#: revision"). ``find_nodes_with_capability`` also uses this constant to
#: recognize which events name a node id worth resolving — see that
#: method's docstring.
_REGISTERED_EVENT_TYPE = "node_manifest.registered"

#: E5-T02's refusal event: every ``receive()`` trust-resolution failure
#: writes exactly one of these, event-only (ADR-0007's ``record_event`` —
#: no content revision), carrying only a coarse ``reason_category``
#: (docs/spec/04_SECURITY_AND_POLICY.md section 8.3: "a coarse reason
#: code"). Never written by ``register`` itself, which is unmodified.
_REJECTED_EVENT_TYPE = "node_manifest.rejected"

#: See the module docstring's E5-T02 ``receive`` entry: a minimal, coarse,
#: NOT schema/spec-defined vocabulary (docs/spec/04 section 8.3 only says "a
#: coarse reason code", without naming the set) — this task's own proposal,
#: matching ``mrr.services.task_bundle.service.RefusalReason``'s own
#: precedent and its "flagged as an open specification question" caveat.
#: Each member names exactly one of ``resolve_trusted_manifest_key``'s five
#: fail-closed conditions (see ``mrr.domain.manifest_trust``'s module
#: docstring); the SPECIFIC reason (kid, timestamps, claimed vs trusted
#: practice id, ...) stays on the raised typed exception and is never
#: written to the event log — only this coarse category is.
ManifestRejectionReason = Literal[
    "signer_mismatch",
    "unknown_key",
    "key_not_valid",
    "key_not_declared",
    "signature_invalid",
]

#: Maps each of ``resolve_trusted_manifest_key``'s five distinct typed
#: failure exceptions to its coarse ``ManifestRejectionReason`` — the one
#: place this service translates a specific typed reason into the public,
#: coarse category recorded on a ``node_manifest.rejected`` event.
#: ``SignatureVerificationError``/``UnsupportedAlgorithmError`` both map to
#: ``"signature_invalid"``: condition (e) failing either because the
#: signature does not verify or because the algorithm is unsupported is, at
#: the coarse-refusal level, the same "this signature is not acceptable"
#: category — docs/spec/04 section 8.3 asks only for a coarse code, not a
#: crypto-internals distinction.
_REJECTION_REASON_BY_ERROR_TYPE: dict[type[Exception], ManifestRejectionReason] = {
    ManifestSignerMismatchError: "signer_mismatch",
    UnknownKeyIdError: "unknown_key",
    ManifestKeyNotValidError: "key_not_valid",
    ManifestKeyNotDeclaredError: "key_not_declared",
    SignatureVerificationError: "signature_invalid",
    UnsupportedAlgorithmError: "signature_invalid",
}

#: The exact exception types ``receive()`` treats as a trust-anchoring
#: refusal (every one of ``resolve_trusted_manifest_key``'s five fail-closed
#: conditions) — the tuple form ``except`` needs, kept as the single source
#: of truth alongside ``_REJECTION_REASON_BY_ERROR_TYPE`` so the two can
#: never drift apart (a ``KeyError`` on an uncaught type would be a bug in
#: this module, not silently swallowed).
_MANIFEST_TRUST_FAILURES: tuple[type[Exception], ...] = tuple(
    _REJECTION_REASON_BY_ERROR_TYPE.keys()
)

#: The callable shape ``mrr.persistence.unit_of_work.record_event`` takes
#: once its ``engine``/``event_log`` arguments are bound — the EVENT-ONLY
#: path ``receive`` uses to record a ``node_manifest.rejected`` event
#: without writing any content revision (E5-T02, mirroring
#: ``mrr.services.task_bundle.service.RecordEvent``'s own shape/rationale).
RecordEvent = Callable[[DomainEvent], AppendedEvent]


class _EventJournal(Protocol):
    """The one read operation this service needs from an event log. Same
    shape and same rationale as
    ``mrr.services.research_score.service._EventJournal``: deliberately
    smaller than the generic ``mrr.provenance.log.EventLog[TTx]`` (this
    service never calls ``append`` directly — that only happens inside
    ``bind_unit_of_work``'s closure, atomically with the object write), and
    not ``@runtime_checkable`` for the same reason ``EventLog`` itself is
    not (an ``isinstance`` check on a ``Protocol`` compares method names
    only, never signatures — mypy's static structural check is the real
    conformance guarantee).
    """

    def read_all(self) -> list[AppendedEvent]: ...


def bind_event_unit_of_work(engine: Engine, event_log: PostgresEventLog) -> RecordEvent:
    """Bind ``mrr.persistence.unit_of_work.record_event`` (ADR-0007's
    EVENT-ONLY path — no content revision) to a concrete
    ``sqlalchemy.Engine``/``PostgresEventLog`` pair, producing the
    ``RecordEvent`` callable ``CapabilityRegistry.receive`` depends on to
    record a ``node_manifest.rejected`` event. Production wiring and
    integration tests call this once; DB-free unit tests pass their own
    trivial callable of the same shape, backed by an in-memory fake event
    log, instead.
    """

    def _record_event(event: DomainEvent) -> AppendedEvent:
        return record_event(engine, event_log, event)

    return _record_event


def _manifest_to_stored_object(manifest: NodeManifest) -> StoredObject:
    """Convert an already-valid, already-signed ``NodeManifest`` into the
    generic ``StoredObject`` ``ObjectRepository`` persists.

    ``id=manifest.node_id`` — **not** ``manifest.id`` — per
    task-packets/E2-T02.yaml derived_decisions: "the registry is keyed by
    node_id, which is the object id". ``manifest.id`` is the manifest
    revision's own identity (a ``node-manifest`` urn, part of its signed
    body); ``node_id`` is the physical node the manifest describes, and is
    what revisions accumulate under — exactly analogous to how
    ``mrr.services.research_score.service._score_to_stored_object`` keys on
    ``score.id``, except here that role is played by a different field.

    ``body`` is the full schema-shaped JSON object (``model_dump_json``
    round-tripped through ``json.loads``, matching
    ``_score_to_stored_object``'s own pattern and
    ``scripts/check_contracts.py``'s round-trip convention) — the
    authoritative full payload ``StoredObject.body`` is documented to carry.
    Per ADR-0004 (docs/spec/adr/ADR-0004-CANONICAL-OBJECT-SERIALIZATION.md,
    applied by task-packets/E5-T00.yaml), this is now the SAME
    ``exclude_none=True`` serialization ``register`` feeds to
    ``verify_object_signature`` (see that method's docstring): absent/None
    optional fields (``data_residency``, an optional non-nullable string,
    among others) are OMITTED here and at verification alike, never
    emitted as JSON ``null`` — one byte-definition of the manifest for
    signing and persistence.
    """
    body: dict[str, Any] = json.loads(manifest.model_dump_json(exclude_none=True))
    return StoredObject(
        id=manifest.node_id,
        api_version=manifest.api_version,
        kind=manifest.kind,
        practice_id=manifest.practice_id,
        revision=manifest.revision,
        created_at=manifest.created_at,
        created_by=manifest.created_by,
        content_hash=manifest.content_hash,
        supersedes=manifest.supersedes,
        labels=manifest.labels,
        body=body,
    )


def _parse_window(body: dict[str, Any]) -> tuple[datetime, datetime]:
    """Parse a stored manifest body's ``valid_from``/``valid_until`` back
    into aware ``datetime`` objects. Both are always present (schema
    ``required``) and always ISO-8601 strings with an explicit offset —
    whether the body came straight from ``NodeManifest.model_dump_json``
    (``AwareDatetime`` serializes to a ``Z``/offset-suffixed string, which
    ``datetime.fromisoformat`` parses natively on Python 3.11+) or round-
    tripped through PostgreSQL JSONB (which stores/returns the same JSON
    string, not a native timestamp type).
    """
    return datetime.fromisoformat(body["valid_from"]), datetime.fromisoformat(body["valid_until"])


class CapabilityRegistry:
    """docs/spec/01_SYSTEM_SPEC.md section 7.3 ("Capability Registry: Stores
    signed node manifests and compatibility metadata. It does not grant
    permission."), implemented per task-packets/E2-T02.yaml.

    Constructed with exactly the dependencies its writes and reads need,
    same as ``ResearchScoreService`` — see the module docstring.
    """

    def __init__(
        self,
        object_repository: ObjectRepository,
        event_log: _EventJournal,
        record: RecordRevisionWithEvent,
    ) -> None:
        self._object_repository = object_repository
        self._event_log = event_log
        self._record = record

    # ------------------------------------------------------------------
    # Registration (MRR-FR-020).
    # ------------------------------------------------------------------

    def register(
        self,
        manifest: NodeManifest,
        verifying_key: Ed25519PublicKey,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> StoredObject:
        """Verify ``manifest``'s signature, then persist it as the next
        append-only revision for ``manifest.node_id`` plus a
        ``node_manifest.registered`` event, atomically.

        Signature verification (task-packets/E2-T02.yaml invariant:
        "registration fails closed on an invalid signature - the manifest
        is not persisted and a typed error is raised") happens FIRST, as
        the very first statement in this method, before any
        ``StoredObject``/``DomainEvent`` is constructed and before the
        object repository is even read. ``verify_object_signature``
        (E1-T02) never returns a boolean — a failed verification always
        raises ``mrr.crypto.exceptions.SignatureVerificationError`` (bad
        signature/tampered payload) or ``UnsupportedAlgorithmError`` (an
        algorithm other than the schema's ``const "Ed25519"``), and this
        method deliberately does not catch either: they propagate
        unmodified to the caller, and since nothing has been written yet
        when they can occur, "persist nothing" holds for free rather than
        needing a rollback.

        The dict handed to ``verify_object_signature`` is
        ``json.loads(manifest.model_dump_json(exclude_none=True))`` — the
        SAME canonical ``exclude_none=True`` form ``_manifest_to_stored_object``'s
        ``body`` uses, per ADR-0004 (docs/spec/adr/
        ADR-0004-CANONICAL-OBJECT-SERIALIZATION.md, applied by
        task-packets/E5-T00.yaml): absent or ``None`` optional fields (e.g.
        ``data_residency``, ``labels``, ``supersedes``) are OMITTED, never
        emitted as JSON ``null``. This must be built the same way the
        signer built the bytes they signed
        (``mrr.domain.hashing_policy.verify_object_signature`` internally
        applies ``prepare_for_signature``, which drops only the
        ``signature`` field — ``content_hash`` and everything else stays
        and gets canonicalized) —
        ``mrr.services.cli.orchestration._build_node_manifest`` signs over
        exactly this same ``exclude_none=True`` body, never a fresh
        null-including ``model_dump(mode="json")``. The manifest is
        re-dumped here (rather than reusing a caller-supplied dict) because
        ``register`` only ever receives the reconstructed ``NodeManifest``
        model, not yet persisted — contrast
        ``mrr.services.task_bundle.service._authorize_and_verify``, which
        verifies a stored, already-persisted ``TaskBundle`` directly
        against its own persisted ``body``. A test
        (``test_register_accepts_a_validly_signed_manifest`` in the unit
        test module) constructs a real signature this same way and
        confirms the round trip succeeds, then tampers a field and
        confirms it fails.

        The verifying key is caller-supplied and not validated against any
        trust store — key management, trust anchoring, and revocation are
        E5 (docs/spec/04_SECURITY_AND_POLICY.md section 8.4), out of scope
        for this task per its forbidden_changes. This method proves the
        verify-closed *mechanism*, not a trust hierarchy around it.

        ``manifest.revision`` MUST already equal the next revision number
        for ``manifest.node_id`` (1 for a node with no prior manifest, else
        the latest stored revision + 1) — resolved here by reading the
        object repository, exactly like
        ``ResearchScoreService.revise`` requires
        ``new_score.revision == latest.revision + 1``. This keeps the
        revision number embedded in the manifest's own signed body (and
        therefore covered by the very signature just verified) in lock
        step with the revision number ``ObjectRepository.insert_revision``
        records — the registry never silently substitutes a different
        revision number than the one the signer actually signed.

        Raises:
            mrr.crypto.exceptions.SignatureVerificationError: invalid or
                tampered signature.
            mrr.crypto.exceptions.UnsupportedAlgorithmError:
                ``manifest.signature.algorithm`` is not ``"Ed25519"``.
            ValueError: ``manifest.revision`` does not equal the expected
                next revision for ``manifest.node_id``.
            mrr.domain.exceptions.RevisionConflictError: a concurrent
                writer won the race for the same expected revision.
        """
        verify_object_signature(
            verifying_key,
            json.loads(manifest.model_dump_json(exclude_none=True)),
            manifest.signature.value,
            algorithm=manifest.signature.algorithm,
        )

        expected_current_revision = self._current_revision_or_none(manifest.node_id)
        expected_new_revision = (
            1 if expected_current_revision is None else expected_current_revision + 1
        )
        if manifest.revision != expected_new_revision:
            raise ValueError(
                f"manifest.revision must be {expected_new_revision!r} (the next revision for "
                f"node_id {manifest.node_id!r}), got {manifest.revision!r}"
            )

        obj = _manifest_to_stored_object(manifest)
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_REGISTERED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(manifest.node_id),
            correlation_id=correlation_id,
            object_id=manifest.node_id,
            object_revision=manifest.revision,
            payload={"capability_names": [capability.name for capability in manifest.capabilities]},
        )
        stored, _ = self._record(obj, expected_current_revision, event)
        return stored

    # ------------------------------------------------------------------
    # Trust-anchored acceptance (task-packets/E5-T02.yaml, MRR-FR-020).
    # ------------------------------------------------------------------

    def receive(
        self,
        manifest: NodeManifest,
        trusted_sender: Practice,
        record_rejected_event: RecordEvent,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
        at: datetime | None = None,
    ) -> StoredObject:
        """Trust-anchor ``manifest`` to ``trusted_sender`` and, only once a
        trusted verifying key is resolved, register it via the EXISTING
        ``register`` — reusing all of its persistence/event/revision
        behavior verbatim. This is the ADDITIVE accept path E2-T02's
        ``register`` deliberately left for E5: ``register`` verifies
        against a CALLER-SUPPLIED bare key with no trust resolution of its
        own; ``receive`` is what actually decides whether a manifest's own
        claimed signer/key id anchors to a practice this caller trusts,
        before that key is ever handed to ``register``.

        Trust resolution (``mrr.domain.manifest_trust.
        resolve_trusted_manifest_key`` — see that module's docstring for the
        full five-condition accept rule and its typed failure family) runs
        FIRST, before ``register`` — and therefore before any object
        repository read or write — is even reached. On success, this method
        calls ``register`` exactly as any other caller would (with the
        resolved key); every persistence/provenance guarantee ``register``
        already provides (atomic write, append-only revisioning, complete
        NFR-001 event provenance) applies unchanged, because it IS the same
        method, not a reimplementation of it.

        On any trust-resolution failure, NOTHING is persisted (no
        ``StoredObject`` revision is ever constructed for a rejected
        manifest — the object repository is never even read) and exactly
        one ``node_manifest.rejected`` event is recorded via
        ``record_rejected_event`` (ADR-0007's event-only path — no content
        revision), carrying only a COARSE ``reason_category``
        (docs/spec/04_SECURITY_AND_POLICY.md section 8.3: "a coarse reason
        code"; see ``_REJECTION_REASON_BY_ERROR_TYPE`` for the mapping from
        each specific typed error to its coarse category) — the specific
        reason (which kid, which timestamps, the claimed vs. trusted
        practice id, ...) stays on the raised exception and is never
        written to the event log. The exception itself is always re-raised
        after the rejected event is recorded, so a caller sees the same
        typed error either way.

        Args:
            manifest: the received, not-yet-trusted ``NodeManifest``.
            trusted_sender: the practice this caller actually trusts as
                ``manifest``'s sender — CALLER-SUPPLIED, exactly as
                ``register``'s ``verifying_key`` is (task-packets/
                E5-T02.yaml forbidden_changes: no new persisted practice
                registry; this method builds and stores nothing about
                ``trusted_sender`` beyond this one call).
            record_rejected_event: the event-only ``RecordEvent`` callable
                (``bind_event_unit_of_work`` in production, an in-memory
                fake in unit tests) this method uses ONLY on a
                trust-resolution failure — never called at all on the
                success path, since ``register`` already records its own
                ``node_manifest.registered`` event.
            actor, policy_version, correlation_id: forwarded to ``register``
                on success, and used to construct the rejected event on
                failure — same provenance fields either way.
            at: the evaluation instant for the resolver's validity-window
                check. Defaults to ``datetime.now(UTC)`` (the RECEIPT
                instant, docs/spec/04 section 8.4), caller-overridable for
                deterministic testing, mirroring ``get_current_manifest``'s
                own ``at`` parameter.

        Returns:
            The persisted ``StoredObject`` — identical to what ``register``
            itself would return for the same (already-trusted) key.

        Raises:
            mrr.domain.exceptions.ManifestSignerMismatchError,
            mrr.domain.exceptions.UnknownKeyIdError,
            mrr.domain.exceptions.ManifestKeyNotValidError,
            mrr.domain.exceptions.ManifestKeyNotDeclaredError,
            mrr.crypto.exceptions.SignatureVerificationError,
            mrr.crypto.exceptions.UnsupportedAlgorithmError: trust
                resolution failed for the corresponding reason (see
                ``mrr.domain.manifest_trust.resolve_trusted_manifest_key``);
                a ``node_manifest.rejected`` event was recorded first.
            ValueError, mrr.domain.exceptions.RevisionConflictError: raised
                by the delegated ``register`` call for its OWN pre-existing
                reasons (wrong revision number, concurrent writer) — these
                are unrelated to trust anchoring, so no rejected event is
                recorded for them; ``register``'s own behavior is unchanged.
        """
        ring = practice_key_ring(trusted_sender)
        try:
            verifying_key = resolve_trusted_manifest_key(manifest, trusted_sender.id, ring, at=at)
        except _MANIFEST_TRUST_FAILURES as exc:
            reason_category = _REJECTION_REASON_BY_ERROR_TYPE[type(exc)]
            self._record_manifest_rejected(
                manifest,
                record_rejected_event,
                reason_category,
                actor=actor,
                policy_version=policy_version,
                correlation_id=correlation_id,
            )
            raise

        return self.register(
            manifest,
            verifying_key,
            actor=actor,
            policy_version=policy_version,
            correlation_id=correlation_id,
        )

    def _record_manifest_rejected(
        self,
        manifest: NodeManifest,
        record_rejected_event: RecordEvent,
        reason_category: ManifestRejectionReason,
        *,
        actor: Urn,
        policy_version: str,
        correlation_id: Urn,
    ) -> None:
        """Append exactly one ``node_manifest.rejected`` event (ADR-0007
        event-only path — no content revision) carrying only
        ``reason_category``. ``object_id``/``object_revision`` name the node
        and revision the rejected manifest claimed for itself, mirroring
        ``register``'s own event construction, so a rejected attempt is
        still attributable to a node/revision even though nothing was
        persisted for it.
        """
        event = DomainEvent(
            id=new_urn("domain-event"),
            event_type=_REJECTED_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            actor=actor,
            policy_version=policy_version,
            causation_id=self._last_event_id_for(manifest.node_id),
            correlation_id=correlation_id,
            object_id=manifest.node_id,
            object_revision=manifest.revision,
            payload={"reason_category": reason_category},
        )
        record_rejected_event(event)

    # ------------------------------------------------------------------
    # Resolution (MRR-FR-021's prerequisite: knowing the CURRENT manifest).
    # ------------------------------------------------------------------

    def get_current_manifest(self, node_id: Urn, *, at: datetime | None = None) -> StoredObject:
        """Resolve the latest revision for ``node_id`` and assert it is
        currently within its declared validity window.

        Args:
            at: the evaluation instant. Defaults to ``datetime.now(UTC)``.
                Must be timezone-aware if supplied (comparisons throughout
                this service are aware-datetime-only, matching the schema's
                ``valid_from``/``valid_until`` timestamps, which are both
                required aware timestamps).

        Raises:
            mrr.domain.exceptions.NodeManifestNotFoundError: ``node_id`` has
                no stored manifest at all.
            mrr.domain.exceptions.NodeManifestValidityError: a manifest
                exists but ``at`` falls outside
                ``[valid_from, valid_until]`` — either not yet valid or
                expired (task-packets/E2-T02.yaml invariant: "a manifest
                whose validity window does not include the evaluation
                instant is expired/not-yet-valid and is never returned by
                lookup or match, though it remains stored and historically
                addressable" — the manifest is untouched in storage; only
                this read is refused).
        """
        latest = self._get_latest_or_raise(node_id)
        evaluation_instant = at if at is not None else datetime.now(UTC)
        valid_from, valid_until = _parse_window(latest.body)
        if not (valid_from <= evaluation_instant <= valid_until):
            raise NodeManifestValidityError(node_id, valid_from, valid_until, evaluation_instant)
        return latest

    def list_capabilities(self, node_id: Urn, *, at: datetime | None = None) -> list[str]:
        """Capability names declared by ``node_id``'s CURRENT manifest (same
        resolution and validity rules as ``get_current_manifest``, which
        this delegates to).

        A read helper only, kept for test and caller convenience — like
        ``find_nodes_with_capability``, this is a listing, never an
        authorization decision (section 7.3: "It does not grant
        permission").
        """
        latest = self.get_current_manifest(node_id, at=at)
        return [capability["name"] for capability in latest.body["capabilities"]]

    # ------------------------------------------------------------------
    # Matching (MRR-FR-021: "match tasks to capabilities without assuming
    # permission").
    # ------------------------------------------------------------------

    def find_nodes_with_capability(
        self, capability_name: str, *, at: datetime | None = None
    ) -> list[Urn]:
        """Return the node ids whose CURRENT manifest (latest revision, and
        currently within its validity window at ``at``) declares a
        capability named ``capability_name``.

        This is a **matching primitive, not an authorization decision**
        (docs/spec/01_SYSTEM_SPEC.md section 7.3: "It does not grant
        permission"; MRR-FR-021: "The orchestrator MUST match tasks to
        capabilities without assuming permission"). The return value
        answers "which nodes declare capability X and are currently valid"
        — nothing here checks policy, approval mode, autonomy ceiling, data
        classification, or any other gating concern; a caller MUST NOT
        treat membership in this list as permission to route a task to
        that node. Returns node ids, never a verdict, and never a boolean.

        Node discovery: ``mrr.domain.repositories.ObjectRepository`` offers
        no "list every object id" operation (by design — E1-T05 is an
        append-only revision store, not an index), and this task must not
        add one (task-packets/E2-T02.yaml forbidden_changes: persistence
        internals are reuse-as-is). Instead, this method reads the event
        log (``_EventJournal.read_all()``, the same read surface
        ``_last_event_id_for`` already uses) and collects the distinct
        ``object_id``s of every ``node_manifest.registered`` event — every
        node this registry has ever registered a manifest for, by
        construction of ``register`` above, which always writes exactly
        one such event per successful registration. Each candidate node id
        is then re-resolved through ``ObjectRepository.get_latest`` (the
        actual current-state source of truth, not the event log) before
        being checked for validity and capability membership, so a node
        that has since had further revisions is judged by its true latest
        state, not by a stale event.
        """
        evaluation_instant = at if at is not None else datetime.now(UTC)
        matching_node_ids: list[str] = []
        seen_node_ids: set[str] = set()
        for appended in self._event_log.read_all():
            if appended.event.event_type != _REGISTERED_EVENT_TYPE:
                continue
            node_id = appended.event.object_id
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            try:
                latest = self._object_repository.get_latest(node_id)
            except ObjectNotFoundError:  # pragma: no cover - defensive only;
                # every node_manifest.registered event corresponds to a
                # revision `register` just persisted in the SAME atomic
                # write, so this should be unreachable in practice.
                continue

            valid_from, valid_until = _parse_window(latest.body)
            if not (valid_from <= evaluation_instant <= valid_until):
                continue

            capability_names = {capability["name"] for capability in latest.body["capabilities"]}
            if capability_name in capability_names:
                matching_node_ids.append(node_id)
        return matching_node_ids

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _get_latest_or_raise(self, node_id: str) -> StoredObject:
        try:
            return self._object_repository.get_latest(node_id)
        except ObjectNotFoundError:
            raise NodeManifestNotFoundError(node_id) from None

    def _current_revision_or_none(self, node_id: str) -> int | None:
        try:
            return self._object_repository.get_latest(node_id).revision
        except ObjectNotFoundError:
            return None

    def _last_event_id_for(self, node_id: str) -> str | None:
        """The id of the most recently appended event for ``node_id``, or
        ``None`` if there is none yet — the ``causation_id`` for the next
        event in that node's own causal chain (MRR-NFR-001), distinct from
        ``correlation_id`` (caller-supplied). Identical logic to
        ``ResearchScoreService._last_event_id_for``: ``read_all()`` returns
        events oldest-first, so the last match is the most recent.
        """
        matching_ids = [
            appended.event.id
            for appended in self._event_log.read_all()
            if appended.event.object_id == node_id
        ]
        return matching_ids[-1] if matching_ids else None
