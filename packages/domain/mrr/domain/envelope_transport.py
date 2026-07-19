"""Abstract, transport-neutral delivery PORT for one signed
``NodeMessageEnvelope`` — task-packets/E5-T03.yaml, mirroring
``mrr.domain.model_adapter.ModelAdapter`` PRECISELY: a ``runtime_checkable``
Protocol plus frozen, self-validating request/result value objects, and NO
concrete implementation.

This module is framework- and network-free (no socket, TLS/mTLS, or
web/workflow framework import anywhere in it — MRR-NFR-004/010, enforced by
the import-linter contract in pyproject.toml and by
tests/unit/architecture/test_import_boundaries.py). It mirrors the
``ModelAdapter``/``ArtifactStore`` precedent exactly: a ``Protocol`` plus
frozen, self-validating value objects, and NO concrete implementation.

--- The real mTLS transport is INFRA-DEPENDENT and explicitly deferred -----

docs/spec/04_SECURITY_AND_POLICY.md section 4.1 requires "mTLS between
federated nodes" for the concrete online transport. Building or CI-testing
a real socket/TLS/mTLS client or server needs a real network stack this
repository's task packet deliberately does not build here (no local
sockets/containers on this machine; task-packets/E5-T03.yaml
forbidden_changes: "any real network, socket, TLS, mTLS, certificate
handling, HTTP client/server, or FastAPI endpoint"). This module builds
only the PORT the eventual concrete mTLS implementation will satisfy — it
never simulates network behavior itself (no artificial latency, no fake
failure injection here); an in-test fake implementing this Protocol lives
in the test suite, not in this module, mirroring
``mrr.domain.model_adapter``'s own "tests use only an in-test fake"
precedent.

--- Delivery outcome vs. task decision: two different concerns -------------

``DeliveryStatus`` describes whether an envelope reached the recipient's
transport layer at all — it is never a claim about whether the recipient
node subsequently accepted, modified, deferred, or rejected the TASK
carried inside (that decision is task-packets/E5-T04.yaml, entirely out of
this port's concern), nor about whether the recipient's own inbound
validator (``mrr.domain.envelope_validation.validate_inbound_envelope``)
accepted the envelope itself. Kept deliberately coarse (two values only,
"delivered"/"failed") — a concrete mTLS implementation is free to log its
own detailed failure reason locally; this abstract port does not enumerate
transport-specific failure modes it cannot yet know (connection refused,
timeout, handshake failure, ... are all just "failed" here).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from mrr.contracts.node_message_envelope import NodeMessageEnvelope

#: The terminal outcome of one delivery ATTEMPT. See the module docstring's
#: "Delivery outcome vs. task decision" section for why this is not, and
#: must never become, a proxy for the recipient's task decision.
DeliveryStatus = Literal["delivered", "failed"]


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvelopeDeliveryRequest:
    """Everything a concrete transport (the deferred mTLS client) or an
    in-test fake needs to attempt delivering one already-signed
    ``NodeMessageEnvelope`` to its recipient node.

    ``recipient_endpoint`` is an OPAQUE address string — no URL/host:port
    format is imposed or parsed here (this module never resolves, connects
    to, or otherwise interprets it network-wise); a concrete transport
    defines its own address shape.
    """

    envelope: NodeMessageEnvelope
    recipient_endpoint: str

    def __post_init__(self) -> None:
        if not self.recipient_endpoint:
            raise ValueError("recipient_endpoint must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvelopeDeliveryOutcome:
    """One delivery attempt's outcome. ``message_id`` echoes
    ``EnvelopeDeliveryRequest.envelope.message_id`` so a caller can match an
    outcome back to its request without holding the original request
    object.
    """

    status: DeliveryStatus
    message_id: str

    def __post_init__(self) -> None:
        if not self.message_id:
            raise ValueError("message_id must not be empty")


@runtime_checkable
class EnvelopeTransport(Protocol):
    """The abstract, transport-neutral port every concrete delivery
    mechanism implements (MRR-NFR-004, MRR-NFR-010). No concrete
    implementation exists in this module or anywhere under
    ``packages/``/``adapters/`` yet — the real mTLS client/server is
    infra-dependent and explicitly deferred (this task packet's own
    forbidden_changes). Tests use only an in-test fake implementing this
    Protocol.
    """

    def send(self, request: EnvelopeDeliveryRequest) -> EnvelopeDeliveryOutcome:
        """Attempt (or, for a fake, simulate) delivering exactly one
        envelope and return its delivery outcome. A conforming
        implementation reports ``"failed"`` for any transport-level problem
        rather than raising — mirroring ``ModelAdapter.invoke``'s own
        "return an outcome, do not raise for an ordinary terminal outcome"
        shape.
        """
        ...


__all__ = [
    "DeliveryStatus",
    "EnvelopeDeliveryOutcome",
    "EnvelopeDeliveryRequest",
    "EnvelopeTransport",
]
