"""``CapabilityRegistry`` (task-packets/E2-T02.yaml): the application-layer
service docs/spec/01_SYSTEM_SPEC.md section 7.3 describes as the Capability
Registry — "Stores signed node manifests and compatibility metadata. It does
not grant permission." — implementing MRR-FR-020/021.

Three operations:

- ``register``: verify a signed ``NodeManifest``'s signature (fail closed,
  persist nothing on failure), then persist it as the next append-only
  revision for its ``node_id`` plus a ``node_manifest.registered`` event,
  atomically.
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
from just two.)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from mrr.contracts import NodeManifest, Urn
from mrr.domain.exceptions import (
    NodeManifestNotFoundError,
    NodeManifestValidityError,
    ObjectNotFoundError,
)
from mrr.domain.hashing_policy import verify_object_signature
from mrr.domain.identity import new_urn
from mrr.domain.repositories import ObjectRepository, StoredObject
from mrr.persistence.repositories import PostgresEventLog, PostgresObjectRepository
from mrr.persistence.unit_of_work import record_object_revision_with_event
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

#: The callable shape ``mrr.persistence.unit_of_work.record_object_revision_with_event``
#: takes once its ``engine``/``object_repository``/``event_log`` arguments
#: are bound. Identical in shape to
#: ``mrr.services.research_score.service.RecordRevisionWithEvent`` — see the
#: module docstring for why this is a local copy, not a shared import.
RecordRevisionWithEvent = Callable[
    [StoredObject, int | None, DomainEvent], tuple[StoredObject, AppendedEvent]
]


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


def bind_unit_of_work(
    engine: Engine,
    object_repository: PostgresObjectRepository,
    event_log: PostgresEventLog,
) -> RecordRevisionWithEvent:
    """Bind ``record_object_revision_with_event`` to a concrete
    ``sqlalchemy.Engine``/``PostgresObjectRepository``/``PostgresEventLog``
    triple, producing the ``RecordRevisionWithEvent`` callable
    ``CapabilityRegistry`` depends on for atomic writes. Production wiring
    and integration tests call this once; DB-free unit tests pass their own
    trivial callable of the same shape, backed by in-memory fakes, instead.
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
    This is deliberately a *separate* serialization from the one
    ``register`` feeds to ``verify_object_signature`` (see that method's
    docstring for why): ``exclude_none=True`` here keeps ``body`` schema-
    valid (``data_residency``, an optional non-nullable string, must be
    entirely absent when unset, never JSON ``null``), which is not a
    concern for the signature-verification input since that dict is never
    itself schema-validated, only canonicalized and hashed.
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
        ``manifest.model_dump(mode="json")`` — a JSON-compatible
        serialization of the *entire* manifest, exactly as
        task-packets/E2-T02.yaml's approved design specifies. This must be
        built the same way the signer built the bytes they signed
        (``mrr.domain.hashing_policy.verify_object_signature`` internally
        applies ``prepare_for_signature``, which drops only the
        ``signature`` field — ``content_hash`` and everything else,
        including any field currently ``None``, stays and gets
        canonicalized). Unlike ``_manifest_to_stored_object``'s ``body``
        (built with ``exclude_none=True`` so the persisted payload stays
        schema-valid), this dict is never schema-validated — it exists only
        to be canonicalized and hash/signature-checked — so keeping
        ``None`` fields present as explicit JSON ``null`` here is correct:
        it is whatever a signer using the same ``NodeManifest`` model and
        the same ``model_dump(mode="json")`` call would have canonicalized
        and signed. A test (``test_register_accepts_a_validly_signed_manifest``
        in the unit test module) constructs a real signature this same way
        and confirms the round trip succeeds, then tampers a field and
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
            manifest.model_dump(mode="json"),
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
