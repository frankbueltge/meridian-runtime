"""``mrr.adapters.federation.local.LocalFilesystemEnvelopeTransport``
(task-packets/I1-T01.yaml) — the first concrete
``mrr.domain.envelope_transport.EnvelopeTransport``.

The oracle here is deliberately written against the PORT's own documented
obligations rather than against the implementation's shape, because the port
is what ``CorrectionImpactService.notify_affected_practices`` relies on:

- structural conformance to the Protocol;
- ``"delivered"`` once the bytes are on disk, at the path
  ``mrr federation outbox write --envelope`` can consume;
- the written bytes are the SAME ADR-0004 canonical form the bundle
  transport writes, so a carried envelope still verifies against its own
  signature;
- every transport-level problem is an OUTCOME, never an exception — the
  load-bearing one, since ``notify_affected_practices`` reads ``"failed"`` as
  its cue to advance the correction to ``DELIVERY_PENDING``;
- a collision does not overwrite an already-delivered envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mrr.adapters.federation.local import LocalFilesystemEnvelopeTransport
from mrr.crypto.canonical import canonicalize
from mrr.domain.envelope_transport import (
    EnvelopeDeliveryRequest,
    EnvelopeTransport,
)


class _StubEnvelope:
    """A minimal stand-in carrying only what the transport actually touches:
    a ``message_id`` and a ``model_dump_json``. Deliberately not a real
    ``NodeMessageEnvelope`` — this transport must not depend on the envelope's
    field set, and a stub proves it does not.
    """

    def __init__(self, message_id: str, body: dict[str, Any] | None = None) -> None:
        self.message_id = message_id
        self._body = body if body is not None else {"message_id": message_id, "payload": None}

    def model_dump_json(self, *, exclude_none: bool = False) -> str:
        body = (
            {key: value for key, value in self._body.items() if value is not None}
            if exclude_none
            else self._body
        )
        return json.dumps(body)


def _request(endpoint: Path, message_id: str = "msg-001") -> EnvelopeDeliveryRequest:
    return EnvelopeDeliveryRequest(
        envelope=_StubEnvelope(message_id),  # type: ignore[arg-type]
        recipient_endpoint=str(endpoint),
    )


def test_satisfies_the_envelope_transport_protocol_structurally() -> None:
    assert isinstance(LocalFilesystemEnvelopeTransport(), EnvelopeTransport)


def test_delivers_to_the_path_the_outbox_command_consumes(tmp_path: Path) -> None:
    outcome = LocalFilesystemEnvelopeTransport().send(_request(tmp_path, "msg-042"))

    assert outcome.status == "delivered"
    assert outcome.message_id == "msg-042"
    assert (tmp_path / "msg-042.json").is_file()


def test_creates_a_missing_outbox_directory(tmp_path: Path) -> None:
    endpoint = tmp_path / "does" / "not" / "exist"

    outcome = LocalFilesystemEnvelopeTransport().send(_request(endpoint))

    assert outcome.status == "delivered"
    assert (endpoint / "msg-001.json").is_file()


def test_writes_the_same_canonical_bytes_the_bundle_transport_writes(tmp_path: Path) -> None:
    envelope = _StubEnvelope("msg-canon", {"b": 2, "a": 1, "dropped": None})
    request = EnvelopeDeliveryRequest(
        envelope=envelope,  # type: ignore[arg-type]
        recipient_endpoint=str(tmp_path),
    )

    LocalFilesystemEnvelopeTransport().send(request)

    expected = canonicalize(json.loads(envelope.model_dump_json(exclude_none=True)))
    assert (tmp_path / "msg-canon.json").read_bytes() == expected


def test_reports_failed_without_raising_when_the_endpoint_is_unwritable(tmp_path: Path) -> None:
    # A FILE where the transport expects a directory: mkdir and the write both
    # fail, and the port forbids raising for it.
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("occupied", encoding="utf-8")

    outcome = LocalFilesystemEnvelopeTransport().send(_request(blocking_file))

    assert outcome.status == "failed"
    assert outcome.message_id == "msg-001"


def test_a_collision_fails_and_leaves_the_delivered_envelope_untouched(tmp_path: Path) -> None:
    already_there = tmp_path / "msg-001.json"
    already_there.write_bytes(b'{"first":"delivery"}')

    outcome = LocalFilesystemEnvelopeTransport().send(_request(tmp_path))

    assert outcome.status == "failed"
    assert already_there.read_bytes() == b'{"first":"delivery"}'


def test_leaves_no_temporary_file_behind_on_success(tmp_path: Path) -> None:
    LocalFilesystemEnvelopeTransport().send(_request(tmp_path))

    assert [one.name for one in tmp_path.iterdir()] == ["msg-001.json"]


@pytest.mark.parametrize("message_id", ["msg-a", "msg-b"])
def test_the_outcome_echoes_its_own_request_message_id(tmp_path: Path, message_id: str) -> None:
    outcome = LocalFilesystemEnvelopeTransport().send(_request(tmp_path, message_id))

    assert outcome.message_id == message_id
